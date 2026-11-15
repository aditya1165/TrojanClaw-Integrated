import asyncio
from types import SimpleNamespace
from uuid import UUID
import json
import os

from fastapi.testclient import TestClient
import pytest

from app.errors import AppError
from app.integrations.local import user_root
from app.integrations.local import LocalConnectors, get_local_connectors
from app.main import app
from app.models.chat import ChatRequest
from anthropic.lib.tools import ToolError

from app.agent.config import load_agent_config
from app.agent.runtime import AgentResult, AgentRuntime, _connector_tools, _connectors_line
from app.config import get_settings
from app.errors import DependencyUnavailableError
from app.services.chat import ChatService

USER = UUID(int=2)


@pytest.fixture
def connectors(monkeypatch, tmp_path):
    monkeypatch.setenv("TROJANCLAW_SESSION_DIR", str(tmp_path))
    service = LocalConnectors()
    yield service
    service._jobs.shutdown(wait=True)


def test_saved_sessions_survive_backend_restart_and_remain_separate(connectors):
    path = user_root(USER) / "brightspace" / "cookies.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]")
    statuses = {item.provider: item.state for item in connectors.statuses(USER)}
    assert statuses == {"brightspace": "saved", "handshake": "not_configured", "piazza": "not_configured"}


def test_connection_job_marks_verified_login_connected(connectors, monkeypatch):
    calls = []
    monkeypatch.setattr(connectors, "_run", lambda *args, **kwargs: calls.append(args) or {"ok": True})
    monkeypatch.setattr(connectors._jobs, "submit", lambda function, *args: function(*args))
    connectors.connect(USER, "brightspace")
    assert calls == [(USER, "brightspace", "connect")]
    assert next(item for item in connectors.statuses(USER) if item.provider == "brightspace").state == "connected"


def test_failed_login_is_not_reported_as_connected(connectors, monkeypatch):
    def fail(*args, **kwargs):
        raise AppError("Reconnect.", "connector_failed", 502)
    monkeypatch.setattr(connectors, "_run", fail)
    monkeypatch.setattr(connectors._jobs, "submit", lambda function, *args: function(*args))
    connectors.connect(USER, "handshake")
    assert next(item for item in connectors.statuses(USER) if item.provider == "handshake").state == "needs_login"


def test_login_timeout_reports_specific_safe_error(connectors, monkeypatch, tmp_path):
    connectors.project = tmp_path
    executable = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr("app.integrations.local.subprocess.run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout=json.dumps({"ok": False, "code": "login_timeout", "error": "private-test-value"}),
    ))
    with pytest.raises(AppError) as result:
        connectors._run(USER, "handshake", "connect")
    assert result.value.code == "login_timeout"
    assert "five minutes" in result.value.message
    assert "private-test-value" not in result.value.message


def test_unknown_and_unselected_connector_are_blocked(connectors):
    with pytest.raises(AppError):
        connectors.connect(USER, "unknown")
    with pytest.raises(AppError):
        connectors.call(USER, "handshake__hs_get_profile", {}, ["brightspace"])


def test_browser_cannot_trigger_connect_from_unapproved_origin(connectors):
    app.dependency_overrides[get_local_connectors] = lambda: connectors
    try:
        response = TestClient(app).post("/integrations/handshake/connect", headers={"Origin": "https://untrusted.example"})
        assert response.status_code == 403
        assert not connectors._states
    finally:
        app.dependency_overrides.clear()


@pytest.mark.usefixtures("signed_in_account")
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


OTHER = UUID(int=3)


def test_chat_forwards_selected_connectors_to_the_agent():
    class Runtime:
        async def run(self, goal, messages, **kwargs):
            self.args = (goal, messages, kwargs["user_id"], list(kwargs["connectors"]),
                         kwargs["use_saved_connectors"], kwargs["timezone"])
            return AgentResult(text="Your profile", sources=[dict(title="Handshake", url=None, kind="connector")])

    runtime = Runtime()
    request = ChatRequest(messages=[{"role": "user", "content": "My profile?"}], connectors=["handshake"],
                          timezone="America/New_York")
    response = asyncio.run(ChatService(runtime).chat(request, user_id=USER, use_saved_connectors=True))
    assert response.response == "Your profile"
    assert response.sources[0].title == "Handshake"
    assert runtime.args == ("chat", [{"role": "user", "content": "My profile?"}], USER, ["handshake"], True,
                            "America/New_York")


def test_chat_goal_allows_connectors_and_keeps_campus_event_tool():
    goal = load_agent_config(get_settings()).goal("chat")
    assert set(goal.connectors) == {"brightspace", "handshake", "piazza"}
    assert "list_campus_events" in goal.tools


def test_connector_tools_call_the_signed_in_account_and_record_calls():
    calls = []

    class Connectors:
        def call(self, user_id, name, arguments, providers):
            calls.append((user_id, name, arguments, providers))
            if arguments.get("fail"):
                raise AppError("Please reconnect.", "connector_failed", 502)
            return "profile data"

    definitions = [{"name": "handshake__hs_get_profile", "description": "Profile",
                    "input_schema": {"type": "object", "properties": {"fail": {"type": "boolean"}}}}]
    tool_calls, sources = [], []
    [tool] = _connector_tools(Connectors(), USER, definitions, ["handshake"], tool_calls, sources)
    assert tool.name == "handshake__hs_get_profile"

    assert asyncio.run(tool.call({})) == "profile data"
    assert calls == [(USER, "handshake__hs_get_profile", {}, ["handshake"])]
    assert sources[0]["title"] == "Handshake"

    with pytest.raises(ToolError):
        asyncio.run(tool.call({"fail": True}))
    assert [call.is_error for call in tool_calls] == [False, True]


def test_selected_connector_without_helper_is_a_dependency_error():
    runtime = AgentRuntime(load_agent_config(get_settings()), lambda _: None, "key", client=object())
    with pytest.raises(DependencyUnavailableError):
        asyncio.run(runtime.run("chat", [{"role": "user", "content": "Hi"}],
                                user_id=USER, connectors=["brightspace"]))


def test_connector_line_names_selected_sources():
    assert _connectors_line([]) == "Live connectors available in this chat: none."
    assert "Brightspace" in _connectors_line(["brightspace"])
    assert "`brightspace__`" in _connectors_line(["brightspace"])


def test_all_three_connectors_are_selectable_in_chat():
    request = ChatRequest(messages=[{"role":"user", "content":"Hello"}], connectors=['piazza','brightspace','handshake'])
    assert request.connectors == ['piazza','brightspace','handshake']


class SavedConnectors:
    """Fake LocalConnectors. `states` maps account -> provider -> state (default: USER has Brightspace saved)."""

    def __init__(self, states=None, failing=()):
        self.states = states or {USER: {"brightspace": "saved", "handshake": "not_configured", "piazza": "not_configured"}}
        self.failing = set(failing)
        self.listed = []

    def statuses(self, user_id):
        return [SimpleNamespace(provider=SimpleNamespace(value=p), state=SimpleNamespace(value=s))
                for p, s in self.states.get(user_id, {}).items()]

    def tools(self, user_id, providers):
        self.listed.append((user_id, list(providers)))
        if set(providers) & self.failing:
            raise AppError("Please reconnect.", "connector_failed", 502)
        return [{"name": f"{p}__tool", "description": p, "input_schema": {"type": "object"}} for p in providers]


def _runtime(connectors):
    return AgentRuntime(load_agent_config(get_settings()), lambda _: None, "key", client=object(),
                        connectors=connectors)


ALLOWED = ("brightspace", "handshake")


def test_piazza_saved_session_is_available_to_only_its_owner():
    fake = SavedConnectors({USER:{'piazza':'saved'}, OTHER:{'piazza':'not_configured'}})
    runtime = _runtime(fake)
    allowed = ('brightspace','handshake','piazza')
    providers, definitions = asyncio.run(runtime._connector_definitions_for(USER, allowed, [], True))
    assert providers == ['piazza'] and definitions[0]['name'] == 'piazza__tool'
    assert asyncio.run(runtime._connector_definitions_for(OTHER, allowed, [], True))[0] == []


def test_piazza_uses_separate_runtime_and_only_account_scoped_environment(connectors, monkeypatch, tmp_path):
    connectors.project = tmp_path / 'handshake'
    executable = tmp_path / 'piazza' / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    executable.parent.mkdir(parents=True)
    executable.touch()
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"ok":true,"connected":true}')
    monkeypatch.setenv('ANTHROPIC_API_KEY','must-not-inherit')
    monkeypatch.setenv('PIAZZA_COOKIE_FILE','must-not-inherit')
    monkeypatch.setattr('app.integrations.local.subprocess.run', run)
    connectors._run(USER, 'piazza', 'verify')
    args, options = calls[0]
    assert args == [str(executable), '-m', 'trojanclaw_piazza.bridge']
    assert options['env']['TROJANCLAW_SESSION_DIR'] == str(user_root(USER))
    assert 'ANTHROPIC_API_KEY' not in options['env'] and 'PIAZZA_COOKIE_FILE' not in options['env']


def test_saved_sessions_are_used_without_ticking_and_tools_are_listed_once():
    fake = SavedConnectors()
    runtime = _runtime(fake)
    for _ in range(3):
        providers, definitions = asyncio.run(runtime._connector_definitions_for(USER, ALLOWED, [], True))
        assert providers == ["brightspace"]
        assert [d["name"] for d in definitions] == ["brightspace__tool"]
    assert fake.listed == [(USER, ["brightspace"])]  # cached after the first chat


def test_one_account_never_gets_another_accounts_saved_sessions():
    fake = SavedConnectors({USER: {"brightspace": "saved"}, OTHER: {"brightspace": "not_configured"}})
    runtime = _runtime(fake)
    assert asyncio.run(runtime._connector_definitions_for(USER, ALLOWED, [], True))[0] == ["brightspace"]
    assert asyncio.run(runtime._connector_definitions_for(OTHER, ALLOWED, [], True))[0] == []
    # Once OTHER saves a session, its tools are listed with OTHER's own folder, not USER's cache.
    fake.states[OTHER]["brightspace"] = "saved"
    assert asyncio.run(runtime._connector_definitions_for(OTHER, ALLOWED, [], True))[0] == ["brightspace"]
    assert fake.listed == [(USER, ["brightspace"]), (OTHER, ["brightspace"])]


def test_saved_sessions_are_not_used_for_remote_requests():
    fake = SavedConnectors()
    providers, _ = asyncio.run(_runtime(fake)._connector_definitions_for(USER, ALLOWED, [], False))
    assert providers == []
    assert fake.listed == []


def test_broken_saved_session_is_skipped_but_ticked_one_reports_the_error():
    fake = SavedConnectors({USER: {"brightspace": "saved", "handshake": "connected"}}, failing={"handshake"})
    providers, _ = asyncio.run(_runtime(fake)._connector_definitions_for(USER, ALLOWED, [], True))
    assert providers == ["brightspace"]
    with pytest.raises(AppError):
        asyncio.run(_runtime(fake)._connector_definitions_for(USER, ALLOWED, ["handshake"], True))


def test_sessions_needing_login_or_not_allowed_by_the_goal_are_left_out():
    fake = SavedConnectors({USER: {"brightspace": "needs_login", "handshake": "saved"}})
    providers, _ = asyncio.run(_runtime(fake)._connector_definitions_for(USER, ("brightspace",), [], True))
    assert providers == []


def test_sources_turned_off_in_chat_are_not_used_even_with_a_saved_session():
    runtime = _runtime(SavedConnectors())
    seen = []

    async def capture(user_id, allowed, selected, use_saved):
        seen.append(list(allowed))
        raise RuntimeError("stop here")

    runtime._connector_definitions_for = capture
    for allowed_connectors in (["handshake"], None):
        with pytest.raises(RuntimeError):
            asyncio.run(runtime.run("chat", [{"role": "user", "content": "hi"}], user_id=USER,
                                    use_saved_connectors=True, allowed_connectors=allowed_connectors))
    assert seen == [["handshake"], ["brightspace", "handshake", "piazza"]]
