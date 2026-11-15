from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from app.dependencies import get_chat_service
from app.main import app
from app.models.chat import ChatResponse


client = TestClient(app)
pytestmark = pytest.mark.usefixtures("signed_in_account")


def test_healthcheck() -> None:
    response = client.get("/healthcheck")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_identifies_service() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"] == "TrojanClaw API"


def test_integrations_are_visible_without_being_configured() -> None:
    response = client.get("/integrations")
    assert response.status_code == 200
    providers = {item["provider"] for item in response.json()}
    assert providers == {"piazza", "brightspace", "handshake"}


def test_handshake_status_uses_local_transport() -> None:
    from app.integrations.local import LocalConnectors

    service = LocalConnectors()
    try:
        handshake = next(item for item in service.statuses(UUID(int=2)) if item.provider == "handshake")
        assert handshake.transport == "stdio"
        assert handshake.state in {"saved", "not_configured"}
    finally:
        service._jobs.shutdown(wait=True)


def test_chat_rejects_empty_messages() -> None:
    response = client.post("/chat", json={"messages": []})
    assert response.status_code == 422


def test_chat_requires_latest_message_to_be_user() -> None:
    response = client.post(
        "/chat",
        json={"messages": [{"role": "assistant", "content": "hello"}]},
    )
    assert response.status_code == 422


def test_chat_preserves_frontend_response_contract() -> None:
    class FakeChatService:
        def chat(self, _request, *, user_id) -> ChatResponse:
            assert str(user_id) == "00000000-0000-0000-0000-000000000002"
            return ChatResponse(response="Hello from TrojanClaw")

    app.dependency_overrides[get_chat_service] = lambda: FakeChatService()
    try:
        response = client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"response": "Hello from TrojanClaw", "calendar_proposals": []}
