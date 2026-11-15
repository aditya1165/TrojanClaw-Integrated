"""Token use: prompt caching is on, and repeated content stays bounded."""

import asyncio
from types import SimpleNamespace
from uuid import UUID

from mcp.server.mcpserver import MCPServer

from app.agent.config import load_agent_config
from app.agent.runtime import AgentRuntime
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
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}  # tools + fixed instructions
    assert "cache_control" not in seen["system"][1]  # date and connectors change, so they come after
    assert seen["max_iterations"] <= 5


def test_connector_results_are_capped_and_say_so(monkeypatch):
    connectors = LocalConnectors()
    monkeypatch.setattr(connectors, "_run", lambda *a, **k: {"content": [{"type": "text", "text": "x" * 100_000}]})
    result = connectors.call(USER, "brightspace__get_assignments", {}, ["brightspace"])
    assert len(result) < RESULT_CHARS + 200 and "Result shortened" in result
    connectors._jobs.shutdown(wait=False)
