"""Auth regressions plus an opt-in real PostgreSQL account lifecycle test."""

import os
from contextlib import contextmanager
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.auth import rate_limiter
from app.database import get_database
from app.main import app
from app.services.auth import AuthService, COOKIE_NAME, hash_password, token_hash, verify_password
from app.models.auth import RegisterRequest
from fastapi import HTTPException
from psycopg2.errors import UniqueViolation


@pytest.fixture(autouse=True)
def reset_limiter():
    rate_limiter.attempts.clear()
    yield
    rate_limiter.attempts.clear()


def test_password_hashes_are_salted_and_verify():
    first = hash_password("test passphrase")
    second = hash_password("test passphrase")
    assert first != second
    assert "test passphrase" not in first
    assert verify_password("test passphrase", first)
    assert not verify_password("wrong password", first)
    assert not verify_password("password", "invalid")


def test_missing_session_denies_profile_and_chat():
    with TestClient(app) as client:
        assert client.get("/auth/me").status_code == 401
        assert client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401


@pytest.mark.parametrize("changes", [
    {"name": "   "}, {"username": "bad user"}, {"username": "ab"},
    {"password": "tiny123"}, {"confirm_password": "different-password"},
])
def test_registration_rejects_invalid_fields_without_echoing_passwords(changes):
    payload = {"name": "Test", "username": "new_user", "password": "test-password", "confirm_password": "test-password"}
    payload.update(changes)
    with TestClient(app) as client:
        response = client.post("/auth/register", json=payload)
    assert response.status_code == 422
    assert payload["password"] not in response.text
    assert "input" not in response.json()["detail"][0]


def test_auth_rejects_cross_site_requests():
    with TestClient(app) as client:
        for path in ("login", "register", "logout"):
            response = client.post(f"/auth/{path}", json={}, headers={"Origin": "https://untrusted.example"})
            assert response.status_code == 403


def test_auth_limits_repeated_attempts():
    with TestClient(app) as client:
        for _ in range(20):
            assert client.post("/auth/login", json={}).status_code == 422
        response = client.post("/auth/login", json={})
        assert response.status_code == 429
        assert response.headers["retry-after"] == "300"


class StubDatabase:
    def __init__(self, row=None):
        self.cursor = MagicMock()
        self.cursor.fetchone.return_value = row
        self.cursor.__enter__.return_value = self.cursor
        self.connection_mock = MagicMock()
        self.connection_mock.cursor.return_value = self.cursor

    @contextmanager
    def connection(self):
        yield self.connection_mock


def test_register_hashes_password_and_session_before_database_write():
    user_id = str(uuid4())
    database = StubDatabase((user_id, "Test User", "test_user"))
    payload = RegisterRequest(name=" Test User ", username="TEST_USER", password="test-password", confirm_password="test-password")
    profile, token = AuthService(database).register(payload, None)
    assert profile.username == "test_user"
    calls = database.cursor.execute.call_args_list
    credential_parameters = next(call.args[1] for call in calls if "INSERT INTO public.account_credentials" in call.args[0])
    assert verify_password("test-password", credential_parameters[1])
    session_parameters = next(call.args[1] for call in calls if "INSERT INTO public.account_sessions" in call.args[0])
    assert session_parameters[0] == token_hash(token)
    assert token not in repr(calls)
    assert "test-password" not in repr(calls)


def test_duplicate_username_is_reported_as_conflict():
    database = StubDatabase()
    database.cursor.execute.side_effect = UniqueViolation()
    payload = RegisterRequest(name="Test", username="test_user", password="test-password", confirm_password="test-password")
    with pytest.raises(HTTPException) as exc:
        AuthService(database).register(payload, None)
    assert exc.value.status_code == 409


def test_incorrect_login_never_creates_session():
    database = StubDatabase((str(uuid4()), "Test", "test_user", hash_password("correct-password")))
    with pytest.raises(HTTPException) as exc:
        AuthService(database).login("test_user", "wrong-password", None)
    assert exc.value.status_code == 401
    assert all("INSERT" not in call.args[0] for call in database.cursor.execute.call_args_list)


def test_unrecognized_session_is_unauthorized():
    with pytest.raises(HTTPException) as exc:
        AuthService(StubDatabase()).current_user("invalid-session-token")
    assert exc.value.status_code == 401


def test_cookie_flags_in_production(monkeypatch):
    from dataclasses import replace
    from fastapi import Response
    from app.api.routes import auth
    from app.config import get_settings

    production = replace(get_settings(), app_env="production")
    monkeypatch.setattr(auth, "get_settings", lambda: production)
    response = Response()
    auth._set_cookie(response, "opaque-test-token")
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


@pytest.mark.skipif(os.getenv("RUN_AUTH_DB_TESTS") != "1", reason="Requires migrated application database; creates and removes a temporary account")
def test_postgres_account_lifecycle():
    username = "auth_test_" + uuid4().hex[:16]
    password = "temporary-test-password-123"
    database = get_database()
    try:
        with TestClient(app) as client:
            response = client.post("/auth/register", json={
                "name": "Auth Test", "username": username.upper(),
                "password": password, "confirm_password": password,
            })
            assert response.status_code == 201, response.text
            assert response.json()["username"] == username
            assert set(response.json()) == {"id", "name", "username"}
            user_id = response.json()["id"]
            assert "httponly" in response.headers["set-cookie"].lower()
            assert "samesite=lax" in response.headers["set-cookie"].lower()
            assert client.get("/auth/me").json()["id"] == user_id
            first_token = client.cookies.get(COOKIE_NAME)

            duplicate = client.post("/auth/register", json={
                "name": "Duplicate", "username": username, "password": password, "confirm_password": password,
            })
            assert duplicate.status_code == 409
            assert client.post("/auth/login", json={"username": username, "password": "wrong"}).status_code == 401
            unknown = client.post("/auth/login", json={"username": "missing_" + uuid4().hex[:20], "password": "wrong"})
            assert unknown.status_code == 401
            assert client.post("/auth/login", json={"username": username, "password": password}).status_code == 200
            assert client.cookies.get(COOKIE_NAME) != first_token
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, first_token)
                assert other.get("/auth/me").status_code == 401

            with database.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT password_hash FROM public.account_credentials WHERE user_id = %s", (user_id,))
                    stored_hash = cursor.fetchone()[0]
                    assert password not in stored_hash
                    assert verify_password(password, stored_hash)
                    cursor.execute("SELECT token_hash FROM public.account_sessions WHERE user_id = %s", (user_id,))
                    assert all(row[0] != client.cookies.get(COOKIE_NAME) for row in cursor.fetchall())
            assert client.post("/auth/logout").status_code == 204
            assert client.get("/auth/me").status_code == 401
            assert client.post("/auth/login", json={"username": username, "password": password}).status_code == 200
            with database.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE public.account_sessions SET expires_at = now() - interval '1 second', created_at = now() - interval '1 day' WHERE user_id = %s", (user_id,))
            assert client.get("/auth/me").status_code == 401
    finally:
        with database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM public.app_users WHERE username = %s", (username,))
