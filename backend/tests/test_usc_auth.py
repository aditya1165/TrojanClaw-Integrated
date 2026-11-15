"""Sign in with USC: account matching, the login-attempt lifecycle and the routes."""

import threading
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg2.errors import UniqueViolation

import app.api.routes.auth as auth_routes
from app.main import app
from app.models.auth import AccountProfile
from app.services.auth import AuthService, COOKIE_NAME, get_auth_service, token_hash, usc_usernames
from app.services.usc_auth import UscLogin, handshake_origin
from tests.test_auth import StubDatabase

WHOAMI = {"Identifier": "12345", "FirstName": "Tommy", "LastName": "Trojan", "UniqueName": "ttrojan@usc.edu"}
ACCOUNT = AccountProfile(id=uuid4(), name="Tommy Trojan", username="ttrojan")


def wait_until_finished(login: UscLogin, attempt_id: str):
    for _ in range(100):
        attempt = login._attempts.get(attempt_id)
        if attempt is None or attempt.status != "waiting":
            return
        time.sleep(0.01)


def sign_in(position, identify, ready):
    """A fake popup: the student logs in, the account is found, the page may continue."""
    identify(WHOAMI)
    ready()


def test_usc_usernames_prefer_usc_username_then_brightspace_id():
    assert usc_usernames("ttrojan@usc.edu", "12345") == ["ttrojan", "usc_12345"]
    assert usc_usernames("T.Trojan", "12345") == ["t_trojan", "usc_12345"]
    assert usc_usernames("ab", "12345") == ["usc_12345"]


def test_existing_usc_account_is_found_without_creating_a_user():
    database = StubDatabase((str(uuid4()), "Tommy Trojan", "ttrojan"))
    profile = AuthService(database).find_or_create_usc_account(WHOAMI)
    assert profile.username == "ttrojan"
    sql = [call.args[0] for call in database.cursor.execute.call_args_list]
    assert "brightspace_user_id" in sql[0]
    assert database.cursor.execute.call_args_list[0].args[1] == ("12345",)
    assert not any("INSERT" in statement for statement in sql)  # no user and no session yet


def test_new_usc_account_is_created_with_brightspace_id():
    database = StubDatabase()
    database.cursor.fetchone.side_effect = [None, (str(uuid4()), "Tommy Trojan", "ttrojan")]
    profile = AuthService(database).find_or_create_usc_account(WHOAMI)
    assert profile.name == "Tommy Trojan"
    insert = next(c.args[1] for c in database.cursor.execute.call_args_list if "INSERT INTO public.app_users" in c.args[0])
    assert insert[1:3] == ("Tommy Trojan", "ttrojan")
    assert '"brightspace_user_id": "12345"' in insert[3]


def test_taken_username_falls_back_to_brightspace_id():
    database = StubDatabase()
    database.cursor.fetchone.side_effect = [None, (str(uuid4()), "Tommy Trojan", "usc_12345")]
    inserts = []

    def execute(sql, params=None):
        if "INSERT INTO public.app_users" in sql:
            inserts.append(params[2])
            if len(inserts) == 1:
                raise UniqueViolation()

    database.cursor.execute.side_effect = execute
    profile = AuthService(database).find_or_create_usc_account(WHOAMI)
    assert inserts == ["ttrojan", "usc_12345"]
    assert profile.username == "usc_12345"


def test_whoami_without_identifier_is_rejected():
    with pytest.raises(HTTPException) as exc:
        AuthService(StubDatabase()).find_or_create_usc_account({"UniqueName": "ttrojan"})
    assert exc.value.status_code == 502


def test_start_session_stores_only_the_token_hash():
    database = StubDatabase()
    token = AuthService(database).start_session(ACCOUNT.id, None)
    session_insert = next(c.args[1] for c in database.cursor.execute.call_args_list
                          if "INSERT INTO public.account_sessions" in c.args[0])
    assert session_insert[:2] == (token_hash(token), str(ACCOUNT.id))
    assert token not in repr(database.cursor.execute.call_args_list)


def test_only_one_login_window_at_a_time():
    release = threading.Event()
    login = UscLogin(run_login=lambda position, identify, ready: release.wait(1), find_account=lambda w: ACCOUNT)
    login.start()
    with pytest.raises(HTTPException) as exc:
        login.start()
    assert exc.value.status_code == 409
    release.set()


@pytest.fixture
def usc_routes(monkeypatch):
    positions = []

    def run_login(position, identify, ready):
        positions.append(position)
        sign_in(position, identify, ready)

    login = UscLogin(run_login=run_login, find_account=lambda whoami: ACCOUNT)
    login.positions = positions
    monkeypatch.setattr(auth_routes, "usc_login", login)

    class StubService:
        def start_session(self, user_id, old_token):
            assert user_id == ACCOUNT.id
            return "session-token"

    app.dependency_overrides[get_auth_service] = StubService
    yield login
    app.dependency_overrides.pop(get_auth_service, None)


def test_usc_login_sets_session_once(usc_routes):
    with TestClient(app) as client:
        attempt_id = client.post("/auth/usc/start").json()["attempt_id"]
        wait_until_finished(usc_routes, attempt_id)
        response = client.post("/auth/usc/status", json={"attempt_id": attempt_id})
        assert response.status_code == 200
        assert response.json()["status"] == "done"
        assert response.json()["account"]["username"] == "ttrojan"
        assert response.cookies.get(COOKIE_NAME) == "session-token"
        # A finished attempt cannot be used again.
        assert client.post("/auth/usc/status", json={"attempt_id": attempt_id}).status_code == 404


def test_popup_position_reaches_the_login(usc_routes):
    with TestClient(app) as client:
        first = client.post("/auth/usc/start", json={"left": 300, "top": 120}).json()["attempt_id"]
        wait_until_finished(usc_routes, first)
        client.post("/auth/usc/status", json={"attempt_id": first})
        second = client.post("/auth/usc/start").json()["attempt_id"]  # no position: the browser decides
        wait_until_finished(usc_routes, second)
    assert usc_routes.positions == [(300, 120), None]


def test_setup_failure_after_sign_in_keeps_the_sign_in():
    def sign_in_then_fail_setup(position, identify, ready):
        sign_in(position, identify, ready)
        raise RuntimeError("Handshake setup failed")

    login = UscLogin(run_login=sign_in_then_fail_setup, find_account=lambda whoami: ACCOUNT)
    attempt_id = login.start()
    wait_until_finished(login, attempt_id)
    time.sleep(0.05)  # let the setup failure happen
    attempt = login.take(attempt_id)
    assert attempt.status == "done" and attempt.account == ACCOUNT


def test_account_lookup_failure_fails_the_sign_in():
    def no_account(whoami):
        raise HTTPException(502, "Brightspace did not say who logged in.")

    login = UscLogin(run_login=sign_in, find_account=no_account)
    attempt_id = login.start()
    wait_until_finished(login, attempt_id)
    assert login.take(attempt_id).status == "failed"


def test_handshake_origin_accepts_only_handshake_pages():
    assert handshake_origin("https://usc.joinhandshake.com/stu/postings?x=1") == "https://usc.joinhandshake.com"
    assert handshake_origin("https://shibboleth.usc.edu/idp/profile") is None
    assert handshake_origin("http://usc.joinhandshake.com/") is None
    assert handshake_origin("https://joinhandshake.com.evil.example/") is None


def test_failed_usc_login_reports_failure(monkeypatch):
    def fail(position, identify, ready):
        raise TimeoutError("closed")

    login = UscLogin(run_login=fail, find_account=lambda whoami: ACCOUNT)
    monkeypatch.setattr(auth_routes, "usc_login", login)
    with TestClient(app) as client:
        attempt_id = client.post("/auth/usc/start").json()["attempt_id"]
        wait_until_finished(login, attempt_id)
        body = client.post("/auth/usc/status", json={"attempt_id": attempt_id}).json()
    assert body["status"] == "failed"
    assert COOKIE_NAME not in body


def test_usc_login_rejects_unknown_attempt_and_cross_site_requests(usc_routes):
    with TestClient(app) as client:
        assert client.post("/auth/usc/status", json={"attempt_id": "unknown"}).status_code == 404
        untrusted = {"Origin": "https://untrusted.example"}
        assert client.post("/auth/usc/start", headers=untrusted).status_code == 403
        assert client.post("/auth/usc/status", json={"attempt_id": "x"}, headers=untrusted).status_code == 403
