"""Exercise real SDK registration, PKCE, token exchange and authenticated MCP HTTP."""
import asyncio
import base64
from contextlib import asynccontextmanager, contextmanager
import hashlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.routes.auth import get_current_user
from app.api.routes.mcp_connection import router
from app.models.auth import AccountProfile
from app.models.chat import ChatResponse
from app.remote_mcp import create_remote_mcp
from app.services.mcp_auth import CALENDAR_SCOPE, McpOAuth, SCOPE, get_mcp_oauth

ORIGIN = "https://antelope-barricade-bluish.ngrok-free.dev"
OWNER = UUID(int=42)


class MemoryDatabase:
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
        if sql.startswith("SELECT count(*)"):
            count = sum(key.startswith("mcp:g:") and owner == params[0] for key, owner in self.rows.items())
            self.result = (count, None)
        elif sql.startswith("INSERT"):
            assert len(params[0]) == 64, "Match the real account_sessions length constraint"
            assert params[0] not in self.rows
            self.rows[params[0]] = params[1]
        elif "token_hash LIKE" in sql:
            self.rows = {key: owner for key, owner in self.rows.items() if owner != params[0]}
        else:
            owner = self.rows.pop(params[0], None) if sql.startswith("DELETE") else self.rows.get(params[0])
            self.result = (owner,) if owner else None

    def fetchone(self):
        return self.result


@pytest.fixture
def remote(monkeypatch):
    oauth = McpOAuth(SimpleNamespace(mcp_public_url=ORIGIN, mcp_encryption_key=Fernet.generate_key().decode()), MemoryDatabase())
    transport = create_remote_mcp(oauth)

    @asynccontextmanager
    async def lifespan(app):
        async with transport.router.lifespan_context(transport):
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app.dependency_overrides[get_mcp_oauth] = lambda: oauth
    app.dependency_overrides[get_current_user] = lambda: AccountProfile(id=OWNER, name="Student", username="student")
    app.mount("/", transport)
    service = SimpleNamespace(chat=AsyncMock(return_value=ChatResponse(response="Your courses are CSCI 570 and CSCI 561.")))
    monkeypatch.setattr("app.remote_mcp.get_chat_service", lambda: service)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, oauth, service


def registration(client):
    response = client.post("/register", json={"client_name":"Gemini test", "redirect_uris":["https://gemini.google.com/callback"],
        "grant_types":["authorization_code", "refresh_token"], "response_types":["code"], "token_endpoint_auth_method":"client_secret_post"})
    assert response.status_code == 201, response.text
    return response.json()


def authorize(client, info, scope=SCOPE):
    verifier = "x" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    response = client.get("/authorize", params={"client_id":info["client_id"], "redirect_uri":"https://gemini.google.com/callback",
        "response_type":"code", "scope":scope, "resource":ORIGIN+"/mcp", "state":"keep-this-state",
        "code_challenge":challenge, "code_challenge_method":"S256"}, follow_redirects=False)
    assert response.status_code in (302,303,307), response.text
    pending = parse_qs(urlparse(response.headers["location"]).query)["mcp_request"][0]
    response = client.post("/assistant/approve", json={"request":pending})
    assert response.status_code == 200, response.text
    query = parse_qs(urlparse(response.json()["redirect_url"]).query)
    assert query["state"] == ["keep-this-state"]
    return query["code"][0], verifier


def token_request(client, info, code, verifier):
    return client.post("/token", data={"grant_type":"authorization_code", "client_id":info["client_id"],
        "client_secret":info["client_secret"], "code":code, "code_verifier":verifier,
        "redirect_uri":"https://gemini.google.com/callback", "resource":ORIGIN+"/mcp"})


def test_real_oauth_pkce_mcp_chat_refresh_and_revocation(remote):
    client, oauth, service = remote
    response = client.post("/mcp", json={"jsonrpc":"2.0", "id":1, "method":"tools/list"})
    assert response.status_code == 401
    assert "oauth-protected-resource" in response.headers["www-authenticate"]
    assert client.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == ORIGIN+"/mcp"
    assert CALENDAR_SCOPE in client.get("/.well-known/oauth-protected-resource/mcp").json()["scopes_supported"]
    info = registration(client)
    code, verifier = authorize(client, info)
    assert token_request(client, info, code, "wrong" * 10).status_code == 400
    result = token_request(client, info, code, verifier)
    assert result.status_code == 200, result.text
    tokens = result.json()
    assert client.get("/assistant/status").json()["connected"] is True
    assert token_request(client, info, code, verifier).status_code == 400
    headers = {"Authorization":"Bearer "+tokens["access_token"], "Accept":"application/json, text/event-stream", "MCP-Protocol-Version":"2025-03-26"}
    result = client.post("/mcp", headers=headers, json={"jsonrpc":"2.0", "id":2, "method":"tools/list"})
    assert result.status_code == 200, result.text
    assert {tool["name"] for tool in result.json()["result"]["tools"]} == {"trojanclaw_chat", "trojanclaw_draft_calendar_event"}
    result = client.post("/mcp", headers=headers, json={"jsonrpc":"2.0", "id":3, "method":"tools/call",
        "params":{"name":"trojanclaw_chat", "arguments":{"message":"What classes am I taking?"}}})
    assert result.status_code == 200, result.text
    assert "CSCI 570" in str(result.json())
    assert service.chat.call_args.kwargs == {"user_id":OWNER, "use_saved_connectors":True, "allow_calendar_drafts":False}
    refresh_data = {"grant_type":"refresh_token", "client_id":info["client_id"], "client_secret":info["client_secret"], "refresh_token":tokens["refresh_token"]}
    refreshed = client.post("/token", data=refresh_data)
    assert refreshed.status_code == 200, refreshed.text
    assert client.post("/token", data=refresh_data).status_code == 400
    assert asyncio.run(oauth.load_access_token(tokens["access_token"])) is None
    assert client.post("/assistant/disconnect").status_code == 200
    assert client.get("/assistant/status").json()["connected"] is False
    assert asyncio.run(oauth.load_access_token(refreshed.json()["access_token"])) is None


def test_cross_client_codes_resource_binding_and_token_types(remote):
    client, oauth, _ = remote
    info, other = registration(client), registration(client)
    code, verifier = authorize(client, info)
    assert token_request(client, other, code, verifier).status_code == 400
    assert asyncio.run(oauth.load_access_token(code)) is None
    response = client.get("/authorize", params={"client_id":info["client_id"], "response_type":"code",
        "redirect_uri":"https://evil.example/callback", "code_challenge":"x"*43, "code_challenge_method":"S256"}, follow_redirects=False)
    assert response.status_code == 400
    assert client.post("/assistant/approve", json={"request":"forged"}).status_code == 400
    assert client.post("/assistant/approve", json={"request":"forged"}, headers={"Origin":"https://evil.example"}).status_code == 403


def test_mcp_metadata_cannot_be_modified_or_used_with_another_key(remote):
    _, oauth, _ = remote
    value = oauth.pack("pending", {"name":"test"})
    assert oauth.unpack("access", value, 600) is None
    assert oauth.unpack("pending", value[:-5]+"aaaaa", 600) is None


def test_remote_chat_cannot_create_calendar_drafts():
    from app.agent.runtime import AgentResult
    from app.models.chat import ChatRequest, ChatMessage
    from app.services.chat import ChatService
    runtime = SimpleNamespace(run=AsyncMock(return_value=AgentResult(text="Calendar changes are unavailable here.")))
    calendar = SimpleNamespace(configured=True, propose=AsyncMock())
    service = ChatService(runtime, calendar)
    asyncio.run(service.chat(ChatRequest(messages=[ChatMessage(role="user", content="Add a calendar event")]),
        user_id=OWNER, allow_calendar_drafts=False))
    assert runtime.run.call_args.kwargs["extra_tools"] == []
    calendar.propose.assert_not_called()


def test_calendar_draft_requires_new_permission_and_returns_website_review(remote, monkeypatch):
    from app.models.calendar import CalendarEventDraft, CalendarProposal
    client, oauth, _ = remote
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    event = CalendarEventDraft(title="Study", start=start, end=start+timedelta(hours=1), timezone="UTC")
    proposal = CalendarProposal(id=UUID(int=100), version=1, event=event, calendar="primary", status="pending", expires_at=start)
    calendar = SimpleNamespace(propose=MagicMock(return_value=proposal), approve=MagicMock())
    conversations = SimpleNamespace(create=MagicMock(return_value={"id":UUID(int=101)}), add_turn=MagicMock(), delete=MagicMock())
    monkeypatch.setattr("app.remote_mcp.get_calendar_service", lambda: calendar)
    monkeypatch.setattr("app.remote_mcp.get_conversation_repository", lambda: conversations)
    info = registration(client)
    assert CALENDAR_SCOPE in info["scope"].split()
    arguments = {"event":event.model_dump(mode="json"), "user_request":f"Add Study on {start.date()} from 3pm to 4pm UTC"}

    def call(scope):
        code, verifier = authorize(client, info, scope)
        tokens = token_request(client, info, code, verifier).json()
        headers = {"Authorization":"Bearer "+tokens["access_token"], "Accept":"application/json, text/event-stream", "MCP-Protocol-Version":"2025-03-26"}
        result = client.post("/mcp", headers=headers, json={"jsonrpc":"2.0", "id":9, "method":"tools/call",
            "params":{"name":"trojanclaw_draft_calendar_event", "arguments":arguments}})
        return result.json()["result"], tokens

    result, _ = call(SCOPE)
    assert result["isError"] is True
    conversations.create.assert_not_called()
    result, tokens = call(SCOPE+" "+CALENDAR_SCOPE)
    assert not result.get("isError")
    assert ORIGIN+"/chat/"+str(UUID(int=101)) in str(result)
    calendar.propose.assert_called_once_with(OWNER, event, UUID(int=101))
    conversations.add_turn.assert_called_once()
    calendar.approve.assert_not_called()
    refreshed = client.post("/token", data={"grant_type":"refresh_token", "client_id":info["client_id"],
        "client_secret":info["client_secret"], "refresh_token":tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert CALENDAR_SCOPE in refreshed.json()["scope"].split()
