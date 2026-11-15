"""Developer test: does the Brightspace MCP server work with the session saved by usc_login.py?

Run from backend/ (uses its own temporary environment, so the backend's packages are untouched):

  uv run --no-project --with "mcp>=1.10,<2" --with keyring python app/connectors/check.py

Starts brightspace-mcp-server, calls get_my_courses, and prints how many courses it returned.
"""

import asyncio
import json
import os
import sys

import keyring
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

KEYCHAIN_SERVICE = "trojanclaw"
KEYCHAIN_BRIGHTSPACE = "brightspace_cookie"
TIMEOUT_SECONDS = 120


async def call_tool(params: StdioServerParameters, tool: str) -> list:
    """Start one MCP server, call one tool with no arguments, return the JSON payloads."""
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, {})
            if result.isError:
                text = " ".join(b.text for b in result.content if b.type == "text")
                raise RuntimeError(text[:300])
            if result.structuredContent:
                return [result.structuredContent]
            payloads = []
            for block in result.content:
                if block.type == "text":
                    try:
                        payloads.append(json.loads(block.text))
                    except json.JSONDecodeError:
                        pass  # e.g. "update available" notices
            return payloads


def count_courses(payloads: list) -> int:
    for payload in payloads:
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, list):
                    return len(value)
    return 0


async def check(name: str, params: StdioServerParameters, tool: str) -> bool:
    try:
        payloads = await asyncio.wait_for(call_tool(params, tool), TIMEOUT_SECONDS)
    except Exception as exc:
        print(f"{name}: FAILED ({type(exc).__name__}: {exc})")
        return False
    print(f"{name}: OK, {tool} returned {count_courses(payloads)} courses")
    return True


async def main() -> int:
    cookie = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_BRIGHTSPACE)
    if not cookie:
        print("Brightspace: no saved session. Run: python -m app.connectors.usc_login")
        brightspace_ok = False
    else:
        brightspace_ok = await check(
            "Brightspace",
            StdioServerParameters(
                command="npx",
                args=["-y", "brightspace-mcp-server@5.2.3"],
                env={**os.environ, "D2L_NO_UPDATE_CHECK": "1", "D2L_SESSION_COOKIE": cookie},
            ),
            "get_my_courses",
        )
    return 0 if brightspace_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
