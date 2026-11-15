"""Google-first onboarding uses regular-browser OAuth and verifies USC identity."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from app.api.routes import google_auth as routes
from app.errors import AppError
from app.main import app
from app.models.auth import AccountProfile
from app.services.auth import COOKIE_NAME, get_auth_service
from app.services.calendar import GoogleCalendar, SCOPES, get_calendar_service
from app.services.google_auth import GOOGLE_ATTEMPT_COOKIE, GoogleSignIn
from app.services.usc_auth import verify_brightspace_identity


EMAIL = "ttrojan@usc.edu"
WHOAMI = {"Identifier": "12345", "UniqueName": EMAIL, "FirstName": "Tommy", "LastName": "Trojan"}
TOKENS = {"access_token": "private-access-token", "refresh_token": "private-refresh-token", "expires_at": 9999999999}


@pytest.fixture
def setup():
    settings = SimpleNamespace(google_calendar_client_id="client", google_calendar_client_secret="secret",
        google_calendar_encryption_key=Fernet.generate_key().decode(),
        google_calendar_redirect_uri="http://localhost:8000/calendar/callback",
        google_signin_redirect_uri="http://localhost:8000/auth/google/callback")
    calendar = GoogleCalendar(settings, MagicMock())
    calendar.exchange = MagicMock(return_value=TOKENS)
    calendar.google_identity = MagicMock(return_value=EMAIL)
    calendar.complete_connection = MagicMock()
    usc = MagicMock()
    usc.start.return_value = "usc-attempt"
    usc.take.return_value = SimpleNamespace(status="done", whoami=WHOAMI, detail=None)
    return GoogleSignIn(lambda: calendar, usc), calendar, usc


def begin(flow):
    token, url = flow.start()
    state = parse_qs(urlparse(url).query)["state"][0]
    return token, url, state


def test_google_start_requests_usc_hint_pkce_and_identity_scopes(setup):
    flow, calendar, usc = setup
    token, url, state = begin(flow)
    params = parse_qs(urlparse(url).query)
    assert params["hd"] == ["usc.edu"]
    assert params["redirect_uri"] == [calendar.settings.google_signin_redirect_uri]
    assert params["code_challenge_method"] == ["S256"]
    assert {"openid", "email"} | SCOPES == set(params["scope"][0].split())
    assert token not in url
    usc.start.assert_not_called()
    calendar.complete_connection.assert_not_called()


def test_public_signin_keeps_callback_exchange_and_return_on_the_public_origin(setup, monkeypatch):
    from app.api.routes import auth, integrations
    flow, calendar, usc = setup
    public = "https://tunnel.example"
    settings = SimpleNamespace(mcp_public_url=public, cors_origins=[public], app_env="test",
        google_calendar_frontend_url="http://localhost:5173")
    for module in (routes, auth, integrations):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "google_signin", flow)
    client = TestClient(app, base_url=public, client=("203.0.113.25",1234))
    result = client.post("/auth/google/start", headers={"Origin":public})
    assert result.status_code == 200
    parameters = parse_qs(urlparse(result.json()["url"]).query)
    callback = public + "/auth/google/callback"
    assert parameters["redirect_uri"] == [callback]
    response = client.get("/auth/google/callback", params={"state":parameters["state"][0], "code":"test-code"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == public+"?google_signin=ready"
    assert calendar.exchange.call_args.kwargs["redirect_uri"] == callback
    profile = AccountProfile(id=UUID(int=2), name="Tommy Trojan", username="ttrojan")
    usc.take.return_value.account = profile
    auth_service = MagicMock()
    auth_service.start_session.return_value = "public-session"
    auth_service.current_user.return_value = profile
    monkeypatch.setitem(app.dependency_overrides, get_auth_service, lambda: auth_service)
    monkeypatch.setitem(app.dependency_overrides, get_calendar_service, lambda: calendar)
    headers = {"Origin":public}
    finish = client.post("/auth/google/finish", headers=headers, json={})
    result = client.post("/auth/google/status", headers=headers, json={"attempt_id":finish.json()["attempt_id"]})
    assert result.status_code == 200
    assert client.cookies.get(COOKIE_NAME) == "public-session"
    assert client.get("/auth/me").status_code == 200
    auth_service.current_user.assert_called_with("public-session")


def test_combined_flow_encrypts_tokens_and_starts_brightspace_once(setup):
    flow, calendar, usc = setup
    token, url, state = begin(flow)
    flow.callback(token, state, "code")
    assert TOKENS["access_token"] not in repr(flow.attempts)
    assert TOKENS["refresh_token"] not in repr(flow.attempts)
    assert flow.begin_usc(token, (40, 50)) == "usc-attempt"
    assert flow.begin_usc(token) == "usc-attempt"
    usc.start.assert_called_once_with((40, 50), expected_email=EMAIL)
    result, tokens = flow.poll(token, "usc-attempt")
    assert result.whoami == WHOAMI
    assert tokens == TOKENS
    with pytest.raises(AppError):
        flow.poll(token, "usc-attempt")
    calendar.complete_connection.assert_not_called()


def test_browserbase_google_onboarding_keeps_cookie_progress_and_calendar(setup, monkeypatch):
    from uuid import uuid4
    flow, calendar, desktop = setup
    remote = MagicMock()
    attempt_id = str(uuid4())
    remote.start.return_value = {"attempt_id": attempt_id, "live_url": "https://www.browserbase.com/live/test", "phase": "brightspace"}
    profile = AccountProfile(id=UUID(int=2), name="Tommy Trojan", username="ttrojan")
    remote.finish.side_effect = [
        ({"status": "connecting", "phase": "handshake", "detail": "Connecting sources"}, None),
        ({"status": "done", "account": profile, "whoami": WHOAMI}, None),
    ]
    monkeypatch.setattr(flow, "managed", lambda: True)
    monkeypatch.setattr("app.services.hosted_usc.get_usc_login", lambda: remote)
    monkeypatch.setattr(routes, "google_signin", flow)
    auth = MagicMock()
    auth.start_session.return_value = "final-app-session"
    monkeypatch.setitem(app.dependency_overrides, get_auth_service, lambda: auth)
    monkeypatch.setitem(app.dependency_overrides, get_calendar_service, lambda: calendar)
    with TestClient(app) as client:
        start = client.post("/auth/google/start", json={})
        assert "Path=/;" in start.headers["set-cookie"]
        state = parse_qs(urlparse(start.json()["url"]).query)["state"][0]
        client.get("/auth/google/callback", params={"state": state, "code": "test-code"}, follow_redirects=False)
        opened = client.post("/auth/google/finish", json={})
        assert opened.json()["live_url"] == remote.start.return_value["live_url"]
        remote.start.assert_called_once_with(client.cookies.get(GOOGLE_ATTEMPT_COOKIE), expected_email=EMAIL)
        assert client.post("/auth/google/finish", json={}).json() == opened.json()
        pending = client.post("/auth/google/status", json={"attempt_id": attempt_id})
        assert pending.json()["status"] == "connecting"
        auth.start_session.assert_not_called()
        done = client.post("/auth/google/status", json={"attempt_id": attempt_id, "skip_handshake": True})
        assert done.json()["status"] == "done" and "whoami" not in done.json()
        assert client.cookies.get(COOKIE_NAME) == "final-app-session"
        calendar.complete_connection.assert_called_once_with(profile.id, TOKENS)
        assert all(call.kwargs["issue_session"] is False for call in remote.finish.call_args_list)
        assert remote.finish.call_args.args[4] is True
        assert client.post("/auth/google/status", json={"attempt_id": attempt_id}).status_code == 400
    desktop.start.assert_not_called()


def test_cancelling_browserbase_google_attempt_releases_only_its_browser(setup, monkeypatch):
    from uuid import uuid4
    flow, _, _ = setup
    remote = MagicMock()
    remote.start.return_value = {"attempt_id": str(uuid4()), "live_url": "https://www.browserbase.com/live/test"}
    monkeypatch.setattr(flow, "managed", lambda: True)
    monkeypatch.setattr("app.services.hosted_usc.get_usc_login", lambda: remote)
    token, _, state = begin(flow)
    flow.callback(token, state, "code")
    flow.begin_usc(token)
    flow.cancel(token)
    remote.cancel_current.assert_called_once_with(token)
    with pytest.raises(AppError, match="expired"):
        flow.begin_usc(token)


def test_oauth_state_cookie_and_replay_are_checked_before_exchange(setup):
    flow, calendar, usc = setup
    token, url, state = begin(flow)
    with pytest.raises(AppError):
        flow.callback("another-browser-cookie", state, "code")
    with pytest.raises(AppError):
        flow.callback(token, "wrong-state", "code")
    calendar.exchange.assert_not_called()
    flow.callback(token, state, "code")
    with pytest.raises(AppError):
        flow.callback(token, state, "code")
    assert calendar.exchange.call_count == 1


def test_denial_and_cancellation_do_not_start_connectors(setup):
    flow, calendar, usc = setup
    token, url, state = begin(flow)
    with pytest.raises(AppError):
        flow.callback(token, state, None, "access_denied")
    with pytest.raises(AppError, match="cancelled"):
        flow.begin_usc(token)
    flow.cancel(token)
    with pytest.raises(AppError, match="expired"):
        flow.begin_usc(token)
    usc.start.assert_not_called()
    calendar.exchange.assert_not_called()


def test_expired_google_attempt_cannot_be_resumed(setup):
    flow, _, usc = setup
    token, url, state = begin(flow)
    next(iter(flow.attempts.values())).started -= 601
    with pytest.raises(AppError, match="expired"):
        flow.callback(token, state, "code")
    usc.start.assert_not_called()


def test_google_and_brightspace_must_match_before_linking(setup):
    flow, calendar, usc = setup
    token, url, state = begin(flow)
    flow.callback(token, state, "code")
    flow.begin_usc(token)
    usc.take.return_value = SimpleNamespace(status="done", whoami={**WHOAMI, "UniqueName": "someone_else@usc.edu"})
    with pytest.raises(HTTPException, match="different USC accounts"):
        flow.poll(token, "usc-attempt")
    calendar.complete_connection.assert_not_called()


def test_brightspace_identity_accepts_netid_and_full_usc_email():
    verify_brightspace_identity({"Identifier": "12345", "UniqueName": "TTROJAN"}, EMAIL)
    verify_brightspace_identity(WHOAMI, EMAIL)
    with pytest.raises(HTTPException):
        verify_brightspace_identity({"Identifier": "12345", "UniqueName": "ttrojan@other.edu"}, EMAIL)


def test_numeric_brightspace_unique_name_is_an_identifier_not_an_email():
    verify_brightspace_identity({"Identifier": "12345", "UniqueName": "1234567890"}, EMAIL)


def test_opaque_brightspace_identifiers_do_not_invent_usc_email_aliases():
    verify_brightspace_identity({"Identifier": "12345", "UniqueName": "institutional-identifier"}, EMAIL)


@pytest.mark.parametrize("identity", [{"UniqueName": "1234567890"}, {"Identifier": "12345"},
                                      {"Identifier": "", "UniqueName": EMAIL}])
def test_incomplete_brightspace_identity_is_still_rejected(identity):
    with pytest.raises(HTTPException):
        verify_brightspace_identity(identity, EMAIL)


def test_google_onboarding_accepts_numeric_brightspace_identity_without_account_merging(setup):
    flow, calendar, usc = setup
    token, _, state = begin(flow)
    flow.callback(token, state, "code")
    flow.begin_usc(token)
    whoami = {**WHOAMI, "UniqueName": "1234567890"}
    usc.take.return_value = SimpleNamespace(status="done", whoami=whoami, detail=None)
    result, tokens = flow.poll(token, "usc-attempt")
    assert result.whoami == whoami
    assert result.whoami["Identifier"] == WHOAMI["Identifier"]
    assert tokens == TOKENS
    calendar.complete_connection.assert_not_called()


@pytest.mark.parametrize("change", [
    {"hd": "gmail.com", "email": "ttrojan@gmail.com"}, {"hd": None},
    {"email_verified": False}, {"sub": None}, {"email": "ttrojan@usc.edu.evil.example"},
])
def test_google_hosted_domain_hint_is_not_trusted_as_identity(setup, change):
    _, calendar, _ = setup
    calendar._http = MagicMock(return_value=httpx.Response(200, json={
        "sub": "google-user-id", "email_verified": True, "email": EMAIL, "hd": "usc.edu", **change,
    }))
    with pytest.raises(AppError, match="USC-managed"):
        GoogleCalendar.google_identity(calendar, TOKENS)


@pytest.mark.parametrize("calendar_fails", [False, True])
def test_http_signin_connects_calendar_only_after_brightspace_verification(setup, monkeypatch, calendar_fails):
    flow, calendar, usc = setup
    monkeypatch.setattr(routes, "google_signin", flow)
    auth = MagicMock()
    profile = AccountProfile(id=UUID(int=2), name="Tommy Trojan", username="ttrojan")
    usc.take.return_value.account = profile
    auth.start_session.return_value = "new-session"
    if calendar_fails:
        calendar.complete_connection.side_effect = AppError("MCP not enabled", "calendar_mcp_access", 502)
    app.dependency_overrides[get_auth_service] = lambda: auth
    app.dependency_overrides[get_calendar_service] = lambda: calendar
    try:
        client = TestClient(app)
        start = client.post("/auth/google/start", json={})
        assert start.status_code == 200
        assert "httponly" in start.headers["set-cookie"].lower()
        assert "private-access-token" not in start.text
        state = parse_qs(urlparse(start.json()["url"]).query)["state"][0]
        assert client.get("/auth/google/callback", params={"state": state, "code": "code"}, follow_redirects=False).status_code == 303
        auth.start_session.assert_not_called()
        calendar.complete_connection.assert_not_called()
        finish = client.post("/auth/google/finish", json={"left": 10, "top": 20})
        attempt_id = finish.json()["attempt_id"]
        done = client.post("/auth/google/status", json={"attempt_id": attempt_id})
        assert done.status_code == 200
        assert done.json()["account"]["username"] == "ttrojan"
        assert bool(done.json()["detail"]) == calendar_fails
        assert client.cookies.get(COOKIE_NAME) == "new-session"
        auth.start_session.assert_called_once_with(profile.id, None)
        assert client.cookies.get(GOOGLE_ATTEMPT_COOKIE) is None
        calendar.complete_connection.assert_called_once_with(profile.id, TOKENS)
        assert client.post("/auth/google/status", json={"attempt_id": attempt_id}).status_code == 400
        assert client.post("/auth/google/start", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    finally:
        app.dependency_overrides.pop(get_auth_service, None)
        app.dependency_overrides.pop(get_calendar_service, None)
