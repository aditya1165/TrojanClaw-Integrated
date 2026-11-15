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
from app.services.auth import AuthService, COOKIE_NAME, get_auth_service, usc_usernames
from app.services.usc_auth import UscLogin, handshake_origin
from tests.test_auth import StubDatabase

WHOAMI = {"Identifier": "12345", "FirstName": "Tommy", "LastName": "Trojan", "UniqueName": "ttrojan@usc.edu"}


def wait_until_finished(login: UscLogin, attempt_id: str):
    for _ in range(100):
        attempt = login._attempts.get(attempt_id)
        if attempt is None or attempt.status != "waiting":
            return
        time.sleep(0.01)


def test_usc_usernames_prefer_usc_username_then_brightspace_id():
    assert usc_usernames("ttrojan@usc.edu", "12345") == ["ttrojan", "usc_12345"]
    assert usc_usernames("T.Trojan", "12345") == ["t_trojan", "usc_12345"]
    assert usc_usernames("ab", "12345") == ["usc_12345"]


def test_existing_usc_account_signs_in_without_creating_a_user():
    user_id = str(uuid4())
    database = StubDatabase((user_id, "Tommy Trojan", "ttrojan"))
    profile, token = AuthService(database).usc_sign_in(WHOAMI, None)
    assert profile.username == "ttrojan"
    sql = [call.args[0] for call in database.cursor.execute.call_args_list]
    assert "brightspace_user_id" in sql[0]
    assert database.cursor.execute.call_args_list[0].args[1] == ("12345",)
    assert not any("INSERT INTO public.app_users" in statement for statement in sql)
    assert any("INSERT INTO public.account_sessions" in statement for statement in sql)
    assert token


def test_new_usc_account_is_created_with_brightspace_id():
    user_id = str(uuid4())
    database = StubDatabase()
    database.cursor.fetchone.side_effect = [None, (user_id, "Tommy Trojan", "ttrojan")]
    profile, _ = AuthService(database).usc_sign_in(WHOAMI, None)
    assert profile.name == "Tommy Trojan"
    insert = next(c.args[1] for c in database.cursor.execute.call_args_list if "INSERT INTO public.app_users" in c.args[0])
    assert insert[1:3] == ("Tommy Trojan", "ttrojan")
    assert '"brightspace_user_id": "12345"' in insert[3]


def test_taken_username_falls_back_to_brightspace_id():
    user_id = str(uuid4())
    database = StubDatabase()
    database.cursor.fetchone.side_effect = [None, (user_id, "Tommy Trojan", "usc_12345")]
    inserts = []

    def execute(sql, params=None):
        if "INSERT INTO public.app_users" in sql:
            inserts.append(params[2])
            if len(inserts) == 1:
                raise UniqueViolation()

    database.cursor.execute.side_effect = execute
    profile, _ = AuthService(database).usc_sign_in(WHOAMI, None)
    assert inserts == ["ttrojan", "usc_12345"]
    assert profile.username == "usc_12345"


def test_whoami_without_identifier_is_rejected():
    with pytest.raises(HTTPException) as exc:
        AuthService(StubDatabase()).usc_sign_in({"UniqueName": "ttrojan"}, None)
    assert exc.value.status_code == 502


def test_only_one_login_window_at_a_time():
    release = threading.Event()
    login = UscLogin(run_login=lambda position, signed_in: release.wait(1) and signed_in(WHOAMI))
    login.start()
    with pytest.raises(HTTPException) as exc:
        login.start()
    assert exc.value.status_code == 409
    release.set()


@pytest.fixture
def usc_routes(monkeypatch):
    positions = []

    def run_login(position, signed_in):
        positions.append(position)
        signed_in(WHOAMI)

    login = UscLogin(run_login=run_login)
    login.positions = positions
    monkeypatch.setattr(auth_routes, "usc_login", login)
    profile = AccountProfile(id=uuid4(), name="Tommy Trojan", username="ttrojan")

    class StubService:
        def usc_sign_in(self, whoami, old_token):
            assert whoami == WHOAMI
            return profile, "session-token"

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
    def sign_in_then_fail_setup(position, signed_in):
        signed_in(WHOAMI)
        raise RuntimeError("Handshake setup failed")

    login = UscLogin(run_login=sign_in_then_fail_setup)
    attempt_id = login.start()
    wait_until_finished(login, attempt_id)
    time.sleep(0.05)  # let the setup failure happen
    assert login.take(attempt_id).status == "done"


def test_handshake_origin_accepts_only_handshake_pages():
    assert handshake_origin("https://usc.joinhandshake.com/stu/postings?x=1") == "https://usc.joinhandshake.com"
    assert handshake_origin("https://shibboleth.usc.edu/idp/profile") is None
    assert handshake_origin("http://usc.joinhandshake.com/") is None
    assert handshake_origin("https://joinhandshake.com.evil.example/") is None


def test_failed_usc_login_reports_failure(monkeypatch):
    def fail(position, signed_in):
        raise TimeoutError("closed")

    login = UscLogin(run_login=fail)
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
