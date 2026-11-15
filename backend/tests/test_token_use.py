"""Token use: prompt caching is on, and repeated content stays bounded."""

import asyncio
from types import SimpleNamespace
from uuid import UUID

from mcp.server.mcpserver import MCPServer

from app.agent.config import load_agent_config
from app.agent.runtime import AgentRuntime, _with_context
from app.config import get_settings
from app.integrations import local as local_module
from app.integrations.local import RESULT_CHARS, LocalConnectors

USER = UUID("00000000-0000-0000-0000-000000000002")


class FakeRunner:
    def __init__(self, params):
        self.params = params

    async def until_done(self):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Done.")], stop_reason="end_turn", model="m")


def test_requests_cache_the_fixed_prompt_and_the_conversation():
    seen = {}

    def tool_runner(**params):
        seen.update(params)
        return FakeRunner(params)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(tool_runner=tool_runner)))
    runtime = AgentRuntime(load_agent_config(get_settings()), lambda _: MCPServer("test"), "key", client=client)
    result = asyncio.run(runtime.run("chat", [{"role": "user", "content": "hi"}], user_id=USER))
    assert result.text == "Done."
    assert seen["cache_control"] == {"type": "ephemeral"}  # the growing conversation
    assert seen["system"] == [{"type": "text", "text": seen["system"][0]["text"], "cache_control": {"type": "ephemeral"}}]
    # The date and connectors change every request, so they come after the conversation.
    assert seen["messages"][0] == {"role": "user", "content": "hi"}
    assert seen["messages"][-1]["role"] == "system" and "Current date and time" in seen["messages"][-1]["content"]
    assert seen["max_iterations"] <= 5


def test_the_cached_part_is_identical_on_the_next_message(monkeypatch):
    """Only the context after the conversation differs, so the earlier turns are read from the cache."""
    requests = []

    def tool_runner(**params):
        requests.append(params)
        return FakeRunner(params)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(tool_runner=tool_runner)))
    runtime = AgentRuntime(load_agent_config(get_settings()), lambda _: MCPServer("test"), "key", client=client)
    first = [{"role": "user", "content": "What is due?"}]
    asyncio.run(runtime.run("chat", first, user_id=USER))
    monkeypatch.setattr("app.agent.runtime._now_line", lambda timezone: "Current date and time: one minute later.")
    second = first + [{"role": "assistant", "content": "HW4 on Friday."}, {"role": "user", "content": "And next week?"}]
    asyncio.run(runtime.run("chat", second, user_id=USER))
    one, two = requests
    assert one["system"] == two["system"]
    assert two["messages"][:1] == one["messages"][:1]  # the shared history matches byte for byte
    assert one["messages"][-1]["content"] != two["messages"][-1]["content"]
    assert first == [{"role": "user", "content": "What is due?"}]  # the caller's list is not changed


def test_models_without_system_messages_keep_the_context_in_system():
    system, messages = _with_context("Fixed.", [{"role": "user", "content": "hi"}], "Date.", "claude-sonnet-5")
    assert [block["text"] for block in system] == ["Fixed.", "Date."] and messages == [{"role": "user", "content": "hi"}]
    for model in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"):
        system, messages = _with_context("Fixed.", [{"role": "user", "content": "hi"}], "Date.", model)
        assert len(system) == 1 and messages[-1] == {"role": "system", "content": "Date."}


def test_connector_results_are_capped_and_say_so(monkeypatch):
    connectors = LocalConnectors()
    monkeypatch.setattr(connectors, "_run", lambda *a, **k: {"content": [{"type": "text", "text": "x" * 100_000}]})
    result = connectors.call(USER, "brightspace__get_assignments", {}, ["brightspace"])
    assert len(result) < RESULT_CHARS + 200 and "Result shortened" in result
    connectors._jobs.shutdown(wait=False)
