"""Siri credentials stay account-scoped, revocable and separate from website/MCP auth."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.routes import siri
from app.api.routes.auth import AuthRateLimiter, get_current_user
from app.dependencies import get_chat_service
from app.main import app
from app.models.auth import AccountProfile
from app.models.chat import ChatResponse
from app.services.auth import AuthService, get_auth_service
from app.services.siri import SiriAccess, get_siri_access, spoken_answer

OWNER, OTHER = UUID(int=400), UUID(int=401)


class SessionDatabase:
    def __init__(self):
        self.rows = {}
        self.result = None

    @contextmanager
    def connection(self):
        yield self

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql, params):
        now = datetime.now(timezone.utc)
        if sql.startswith("INSERT"):
            key, owner, seconds = params
            assert len(key) == 64
            expires = now + timedelta(seconds=seconds)
            self.rows[key] = (owner, expires)
            self.result = (expires,)
        elif sql.startswith("DELETE"):
            self.rows = {key: row for key, row in self.rows.items() if not (key.startswith("siri:") and row[0] == params[0])}
        elif "max(expires_at)" in sql:
            values = [row[1] for key, row in self.rows.items() if key.startswith("siri:") and row[0] == params[0] and row[1] > now]
            self.result = (max(values) if values else None,)
        elif sql.startswith("SELECT user_id"):
            row = self.rows.get(params[0])
            self.result = (row[0],) if row and row[1] > now else None
        else:
            raise AssertionError("Unexpected SQL in isolated Siri test")

    def fetchone(self):
        return self.result


@pytest.fixture
def connection(monkeypatch):
    database = SessionDatabase()
    access = SiriAccess(SimpleNamespace(siri_public_url="https://trojan.example/api", mcp_public_url=None), database)
    service = SimpleNamespace(chat=AsyncMock(return_value=ChatResponse(response="**Your classes**\n- CSCI 570\n- [CSCI 561](https://example.com/course)")))
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = lambda: AccountProfile(id=OWNER, name="Student", username="student")
    app.dependency_overrides[get_siri_access] = lambda: access
    app.dependency_overrides[get_chat_service] = lambda: service
    app.dependency_overrides[get_auth_service] = lambda: AuthService(database)
    monkeypatch.setattr(siri, "invalid_attempts", AuthRateLimiter())
    monkeypatch.setattr(siri, "chat_usage", AuthRateLimiter())
    yield TestClient(app), access, service
    app.dependency_overrides.clear()
    app.dependency_overrides.update(overrides)


def test_key_is_hashed_rotated_expiring_and_restricted_to_its_owner(connection):
    client, access, service = connection
    first = client.post("/siri/connect")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    key = first.json()["access_key"]
    assert key not in repr(access.database.rows)
    other = access.create(OTHER)["access_key"]
    assert access.owner(key) == OWNER
    access.database.rows["a"*64] = (str(OWNER), datetime.now(timezone.utc)+timedelta(days=1))
    status = client.get("/siri/status").json()
    assert status["connected"] and status["chat_url"] == "https://trojan.example/api/siri/chat"
    assert "access_key" not in status
    response = client.post("/siri/chat", headers={"Authorization":"Bearer "+key}, json={"question":"What classes do I have?", "timezone":"UTC"})
    assert response.status_code == 200
    assert response.json()["answer"] == "Your classes\nCSCI 570\nCSCI 561"
    assert service.chat.call_args.kwargs == {"user_id":OWNER, "use_saved_connectors":True, "allow_calendar_drafts":False}
    second = client.post("/siri/connect").json()["access_key"]
    assert second != key
    with pytest.raises(HTTPException): access.owner(key)
    assert access.owner(other) == OTHER
    assert client.post("/siri/disconnect").status_code == 200
    with pytest.raises(HTTPException): access.owner(second)
    assert "a"*64 in access.database.rows
    assert access.owner(other) == OTHER
    assert client.get("/siri/status").json()["connected"] is False


def test_cookie_and_bearer_are_not_interchangeable_and_setup_requires_browser_auth(connection):
    client, access, _ = connection
    key = access.create(OWNER)["access_key"]
    assert client.post("/siri/chat", headers={"Cookie":"trojanclaw_session=browser-session"}, json={"question":"hello"}).status_code == 401
    app.dependency_overrides.pop(get_current_user)
    for path in ("/siri/connect", "/siri/disconnect"):
        assert client.post(path, headers={"Authorization":"Bearer "+key}).status_code == 401
        assert client.post(path, headers={"Cookie":"trojanclaw_session="+key}).status_code == 401
    assert client.get("/siri/status", headers={"Cookie":"trojanclaw_session="+key}).status_code == 401
    assert client.post("/siri/chat", headers={"Authorization":"Bearer "+"x"*43}, json={"question":"hello"}).status_code == 401
    access.database.rows[access.storage_key(key)] = (str(OWNER), datetime.now(timezone.utc)-timedelta(seconds=1))
    assert client.post("/siri/chat", headers={"Authorization":"Bearer "+key}, json={"question":"hello"}).status_code == 401


def test_cannot_choose_another_owner_or_inject_tools_and_rejects_untrusted_origins(connection):
    client, access, service = connection
    key = access.create(OWNER)["access_key"]
    headers = {"Authorization":"Bearer "+key}
    for extra in ({"user_id":str(OTHER)}, {"allow_calendar_drafts":True}, {"connectors":["arbitrary"]}):
        assert client.post("/siri/chat", headers=headers, json={"question":"hello", **extra}).status_code == 422
    for payload in ({"question":"   "}, {"question":"hello", "timezone":"bad/zone"}):
        assert client.post("/siri/chat", headers=headers, json=payload).status_code == 422
    assert client.post("/siri/connect", headers={"Origin":"https://untrusted.example"}).status_code == 403
    service.chat.assert_not_called()


def test_rate_limits_are_per_account_not_shared_proxy_ip(connection):
    client, access, _ = connection
    key = access.create(OWNER)["access_key"]
    for _ in range(20):
        assert client.post("/siri/chat", headers={"Authorization":"Bearer "+key}, json={"question":"hello"}).status_code == 200
    assert client.post("/siri/chat", headers={"Authorization":"Bearer "+key}, json={"question":"hello"}).status_code == 429
    other = access.create(OTHER)["access_key"]
    assert client.post("/siri/chat", headers={"Authorization":"Bearer "+other}, json={"question":"hello"}).status_code == 200


def test_timeout_is_actionable_and_long_answers_are_limited(connection, monkeypatch):
    client, access, service = connection
    key = access.create(OWNER)["access_key"]
    async def slow(*args, **kwargs):
        await asyncio.sleep(0.05)
    service.chat.side_effect = slow
    monkeypatch.setattr(siri, "CHAT_TIMEOUT", 0.001)
    result = client.post("/siri/chat", headers={"Authorization":"Bearer "+key}, json={"question":"hello"})
    assert result.status_code == 504 and "more specific" in result.json()["detail"]
    assert len(spoken_answer("A long answer. "*1000)) <= 6000


def test_public_endpoint_configuration():
    database = SessionDatabase()
    assert SiriAccess(SimpleNamespace(siri_public_url=None, mcp_public_url="https://example.com"), database).chat_url == "https://example.com/api/siri/chat"
    assert SiriAccess(SimpleNamespace(siri_public_url=None, mcp_public_url=None), database).chat_url is None
    with pytest.raises(Exception, match="HTTPS"):
        _ = SiriAccess(SimpleNamespace(siri_public_url="http://example.com", mcp_public_url=None), database).chat_url
