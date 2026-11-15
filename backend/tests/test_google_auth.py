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
    auth.usc_sign_in.return_value = (profile, "new-session")
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
        auth.usc_sign_in.assert_not_called()
        calendar.complete_connection.assert_not_called()
        finish = client.post("/auth/google/finish", json={"left": 10, "top": 20})
        attempt_id = finish.json()["attempt_id"]
        done = client.post("/auth/google/status", json={"attempt_id": attempt_id})
        assert done.status_code == 200
        assert done.json()["account"]["username"] == "ttrojan"
        assert bool(done.json()["detail"]) == calendar_fails
        assert client.cookies.get(COOKIE_NAME) == "new-session"
        assert client.cookies.get(GOOGLE_ATTEMPT_COOKIE) is None
        calendar.complete_connection.assert_called_once_with(profile.id, TOKENS)
        assert client.post("/auth/google/status", json={"attempt_id": attempt_id}).status_code == 400
        assert client.post("/auth/google/start", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    finally:
        app.dependency_overrides.pop(get_auth_service, None)
        app.dependency_overrides.pop(get_calendar_service, None)
