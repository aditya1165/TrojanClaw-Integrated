"""Exercise real MCP discovery, validation, dispatch, and the write boundary."""

import asyncio
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
import pytest

from trojanclaw_handshake import READ_TOOLS, create_server


def test_discovery_exposes_only_reviewed_read_tools():
    async def check():
        async with Client(create_server()) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == READ_TOOLS
            assert all(tool.annotations.readOnlyHint for tool in tools)
            search = next(tool for tool in tools if tool.name == "hs_search_jobs")
            assert {"query", "per_page", "after"} <= search.inputSchema["properties"].keys()
    asyncio.run(check())


def test_search_dispatch_preserves_query_and_cursor(monkeypatch):
    calls = []

    async def fake_gql(query, variables):
        calls.append(variables)
        return {"jobSearch": {
            "totalCount": 2, "nodes": [{"job": {"id": "123", "title": "Intern"}}],
            "pageInfo": {"endCursor": "next", "hasNextPage": True},
        }}

    monkeypatch.setattr("handshake_mcp.tools.jobs.gql", fake_gql)

    async def check():
        async with Client(create_server()) as client:
            result = await client.call_tool("hs_search_jobs", {
                "query": "software intern", "per_page": 100, "after": "previous",
            })
            assert result.data["results"][0]["id"] == "123"
            assert result.data["pageInfo"]["endCursor"] == "next"
    asyncio.run(check())
    assert calls == [{"query": "software intern", "first": 50, "after": "previous"}]


@pytest.mark.parametrize("tool", [
    "hs_apply", "hs_withdraw_application", "hs_save_job", "hs_unsave_job",
    "hs_upload_document", "hs_delete_document", "hs_update_bio",
    "hs_register_event", "hs_register_career_fair", "hs_follow_employer",
    "unknown_tool",
])
def test_write_and_unknown_tools_cannot_be_called(tool):
    async def check():
        async with Client(create_server()) as client:
            with pytest.raises(ToolError, match="[Uu]nknown tool|[Nn]ot found"):
                await client.call_tool(tool, {})
    asyncio.run(check())


def test_missing_required_id_is_rejected_before_network(monkeypatch):
    async def unexpected_call(*args, **kwargs):
        pytest.fail("Invalid input must not reach Handshake")
    monkeypatch.setattr("handshake_mcp.tools.jobs.gql", unexpected_call)

    async def check():
        async with Client(create_server()) as client:
            with pytest.raises(ToolError, match="required|validation"):
                await client.call_tool("hs_get_job", {})
    asyncio.run(check())


def test_stdio_server_starts_and_discovers_tools():
    async def check():
        transport = StdioTransport(
            command=sys.executable,
            args=["-c", "from trojanclaw_handshake import main; main()", "serve"],
            keep_alive=False,
        )
        async with Client(transport, timeout=30) as client:
            assert {tool.name for tool in await client.list_tools()} == READ_TOOLS
    asyncio.run(check())


def test_missing_login_returns_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setattr("handshake_mcp.auth.COOKIES_FILE", tmp_path / "absent.json")

    async def check():
        async with Client(create_server()) as client:
            with pytest.raises(ToolError, match="Not logged in to Handshake"):
                await client.call_tool("hs_get_profile", {})
    asyncio.run(check())
