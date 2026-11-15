"""Private JSON subprocess bridge. Never start a browser for a read request."""

import asyncio
from contextlib import redirect_stdout
import json
import sys

from fastmcp import Client

from .browser import LoginError, login
from .server import READ_TOOLS, RECONNECT, create_server, saved_cookies


async def execute(request):
    action = request.get("action")
    if request.get("provider") != "piazza" or action not in {"connect", "tools", "verify", "call"}:
        raise ValueError("Invalid Piazza action.")
    if action == "call" and request.get("tool") not in READ_TOOLS:
        raise ValueError("Piazza tool is not allowed.")
    if action == "connect":
        await login()
        action = "verify"
    server, account = create_server(saved_cookies())
    try:
        async with Client(server, timeout=65) as client:
            if action == "tools":
                return {"tools": [{"name": tool.name, "description": tool.description or "", "input_schema": tool.input_schema}
                                  for tool in await client.list_tools() if tool.name in READ_TOOLS]}
            result = await client.call_tool("list_courses" if action == "verify" else request["tool"],
                                            {} if action == "verify" else request.get("arguments", {}))
            if result.is_error:
                raise ValueError(RECONNECT)
            if action == "verify":
                return {"connected": True}
            return {"content": [block.model_dump(mode="json") for block in result.content if block.type == "text"]}
    finally:
        await asyncio.to_thread(account.close)


def main():
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            result = asyncio.run(asyncio.wait_for(execute(request), 350 if request.get("action") == "connect" else 85))
        print(json.dumps({"ok": True, **result}))
    except LoginError as error:
        print(json.dumps({"ok": False, "code": error.code, "error": RECONNECT}))
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({"ok": False, "error": RECONNECT}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
