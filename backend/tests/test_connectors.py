from types import SimpleNamespace
import json
import os

from fastapi.testclient import TestClient
import pytest

from app.errors import AppError
from app.integrations.local import LocalConnectors, get_local_connectors
from app.main import app
from app.models.chat import ChatRequest
from app.services.chat import ChatService
from app.services.llm import LanguageModelService


@pytest.fixture
def connectors(monkeypatch, tmp_path):
    monkeypatch.setenv("TROJANCLAW_SESSION_DIR", str(tmp_path))
    service = LocalConnectors()
    yield service
    service._jobs.shutdown(wait=True)


def test_saved_sessions_survive_backend_restart_and_remain_separate(connectors):
    path = connectors.root / "brightspace" / "cookies.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]")
    statuses = {item.provider: item.state for item in connectors.statuses()}
    assert statuses == {"brightspace": "saved", "handshake": "not_configured", "piazza": "unavailable"}


def test_connection_job_marks_verified_login_connected(connectors, monkeypatch):
    calls = []
    monkeypatch.setattr(connectors, "_run", lambda *args, **kwargs: calls.append(args) or {"ok": True})
    monkeypatch.setattr(connectors._jobs, "submit", lambda function, *args: function(*args))
    connectors.connect("brightspace")
    assert calls == [("brightspace", "connect")]
    assert next(item for item in connectors.statuses() if item.provider == "brightspace").state == "connected"


def test_failed_login_is_not_reported_as_connected(connectors, monkeypatch):
    def fail(*args, **kwargs):
        raise AppError("Reconnect.", "connector_failed", 502)
    monkeypatch.setattr(connectors, "_run", fail)
    monkeypatch.setattr(connectors._jobs, "submit", lambda function, *args: function(*args))
    connectors.connect("handshake")
    assert next(item for item in connectors.statuses() if item.provider == "handshake").state == "needs_login"


def test_login_timeout_reports_specific_safe_error(connectors, monkeypatch, tmp_path):
    connectors.project = tmp_path
    executable = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr("app.integrations.local.subprocess.run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout=json.dumps({"ok": False, "code": "login_timeout", "error": "private-test-value"}),
    ))
    with pytest.raises(AppError) as result:
        connectors._run("handshake", "connect")
    assert result.value.code == "login_timeout"
    assert "five minutes" in result.value.message
    assert "private-test-value" not in result.value.message


def test_piazza_and_unselected_connector_are_blocked(connectors):
    with pytest.raises(AppError):
        connectors.connect("piazza")
    with pytest.raises(AppError):
        connectors.call("handshake__hs_get_profile", {}, ["brightspace"])


def test_browser_cannot_trigger_connect_from_unapproved_origin(connectors):
    app.dependency_overrides[get_local_connectors] = lambda: connectors
    try:
        response = TestClient(app).post("/integrations/handshake/connect", headers={"Origin": "https://untrusted.example"})
        assert response.status_code == 403
        assert not connectors._states
    finally:
        app.dependency_overrides.clear()


def test_allowed_loopback_origin_can_connect_across_localhost_names(connectors, monkeypatch):
    monkeypatch.setattr(connectors, "_run", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(connectors._jobs, "submit", lambda function, *args: function(*args))
    app.dependency_overrides[get_local_connectors] = lambda: connectors
    try:
        response = TestClient(app).post("/integrations/handshake/connect", headers={
            "Origin": "http://127.0.0.1:5173", "Sec-Fetch-Site": "cross-site",
        })
        assert response.status_code == 202
    finally:
        app.dependency_overrides.clear()


def test_chat_connector_path_does_not_require_campus_database(monkeypatch):
    tools = [{"name": "handshake__hs_get_profile", "description": "Profile", "input_schema": {"type": "object"}}]
    fake = SimpleNamespace(tools=lambda providers: tools, call=lambda *args: "profile data")
    monkeypatch.setattr("app.services.chat.get_local_connectors", lambda: fake)

    class Model:
        def respond_with_tools(self, messages, prompt, actual_tools, callback):
            assert actual_tools == tools
            assert callback("handshake__hs_get_profile", {}) == "profile data"
            return "Your profile"

    service = ChatService(None, None, None, Model())
    request = ChatRequest(messages=[{"role": "user", "content": "My profile?"}], connectors=["handshake"])
    assert service.chat(request).response == "Your profile"


def test_model_tool_loop_dispatches_only_selected_tools(monkeypatch):
    class Block:
        type = "tool_use"
        id = "call1"
        name = "handshake__hs_get_profile"
        input = {}

        def model_dump(self, **kwargs):
            return {"type": self.type, "id": self.id, "name": self.name, "input": self.input}

    responses = iter([
        SimpleNamespace(content=[Block()]),
        SimpleNamespace(content=[SimpleNamespace(type="text", text="Your profile")]),
    ])
    requests = []

    class Client:
        def __init__(self, **kwargs):
            self.messages = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            requests.append(kwargs)
            return next(responses)

    monkeypatch.setattr("app.services.llm.Anthropic", Client)
    model = LanguageModelService(SimpleNamespace(anthropic_api_key="test", anthropic_model="test"))
    calls = []
    request = ChatRequest(messages=[{"role": "user", "content": "My profile?"}])
    answer = model.respond_with_tools(request.messages, "system", [{"name": Block.name}],
                                     lambda *args: calls.append(args) or "profile data")
    assert answer == "Your profile"
    assert calls == [(Block.name, {})]
    assert requests[-1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_piazza_is_not_selectable_in_chat():
    response = TestClient(app).post("/chat", json={
        "messages": [{"role": "user", "content": "Hello"}], "connectors": ["piazza"],
    })
    assert response.status_code == 422
