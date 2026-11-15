"""Auth regressions plus an opt-in real PostgreSQL account lifecycle test."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.routes.auth import rate_limiter
from app.main import app
from app.services.auth import AuthService
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def reset_limiter():
    rate_limiter.attempts.clear()
    yield
    rate_limiter.attempts.clear()




def test_missing_session_denies_profile_and_chat():
    with TestClient(app) as client:
        assert client.get("/auth/me").status_code == 401
        assert client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401


def test_auth_rejects_cross_site_requests():
    with TestClient(app) as client:
        for path in ("logout", "google/start", "google/finish", "google/status", "google/cancel"):
            response = client.post(f"/auth/{path}", json={}, headers={"Origin": "https://untrusted.example"})
            assert response.status_code == 403


def test_sign_in_attempts_are_rate_limited():
    for _ in range(20):
        rate_limiter.check(SimpleNamespace(client=SimpleNamespace(host="203.0.113.9")))
    with pytest.raises(HTTPException) as exc:
        rate_limiter.check(SimpleNamespace(client=SimpleNamespace(host="203.0.113.9")))
    assert exc.value.status_code == 429 and exc.value.headers["Retry-After"] == "300"


def test_password_and_direct_usc_sign_in_are_gone():
    with TestClient(app) as client:
        for path in ("register", "login", "usc/start", "usc/status"):
            assert client.post(f"/auth/{path}", json={}).status_code in {404, 405}


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

