"""Private JSON subprocess bridge used by the local TrojanClaw backend."""

import asyncio
from contextlib import redirect_stdout
import json
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from . import READ_TOOLS, create_server, run_login
from .profiles import LoginError, brightspace_cookie_header, brightspace_directory, login_brightspace


BRIGHTSPACE_TOOLS = frozenset({
    "get_my_courses", "get_upcoming_due_dates", "get_calendar_events",
    "get_assignments", "get_my_grades", "get_announcements", "get_syllabus",
    "get_course_content", "search_course", "get_discussions", "get_my_groups",
    "get_assignment_rubric", "get_dropbox_feedback", "get_assignment_files",
    "get_announcement_files", "get_video_transcript",
})


def allowed_tools(provider: str) -> frozenset[str]:
    if provider == "handshake":
        return READ_TOOLS
    if provider == "brightspace":
        return BRIGHTSPACE_TOOLS
    raise ValueError("Connector is not available.")


def validate_call(provider: str, tool: str, arguments: dict) -> None:
    if tool not in allowed_tools(provider):
        raise ValueError("This tool is not allowed.")
    if provider == "brightspace" and any(
        any(fragment in key.lower() for fragment in ("path", "directory", "destination", "saveto", "download", "dest"))
        for key in arguments
    ):
        raise ValueError("Saving course files is not enabled.")


def client_for(provider: str) -> Client:
    if provider == "handshake":
        return Client(create_server(), timeout=60)
    allowed_tools(provider)
    env = {
        "D2L_BASE_URL": "https://brightspace.usc.edu",
        "D2L_SESSION_COOKIE": brightspace_cookie_header(),
        "D2L_NO_UPDATE_CHECK": "1",
        "D2L_SESSION_DIR": str(brightspace_directory() / "mcp-session"),
        "D2L_ACCESS_TOKEN": "",
    }
    args = ["-y", "brightspace-mcp-server@5.2.3"]
    command = "npx"
    if os.name == "nt":
        command, args = "cmd.exe", ["/d", "/c", "npx", *args]
    return Client(StdioTransport(command, args, env=env, keep_alive=False), timeout=60)


async def execute(request: dict) -> dict:
    provider = request["provider"]
    allowed = allowed_tools(provider)
    action = request["action"]
    if action == "connect":
        if provider == "handshake":
            await run_login()
        else:
            await login_brightspace()
        action = "verify"
    if action == "call":
        validate_call(provider, request["tool"], request.get("arguments", {}))
    async with client_for(provider) as client:
        if action == "tools":
            tools = await client.list_tools()
            return {"tools": [
                {"name": tool.name, "description": tool.description or "",
                 "input_schema": tool.inputSchema}
                for tool in tools if tool.name in allowed
            ]}
        if action == "verify":
            tool = "hs_get_profile" if provider == "handshake" else "get_my_courses"
            result = await client.call_tool(tool, {})
            # Some Brightspace releases encode errors in JSON text instead of isError.
            validate_result(result)
            return {"connected": True}
        if action == "call":
            result = await client.call_tool(request["tool"], request.get("arguments", {}))
            return {"content": validate_result(result)}
        raise ValueError("Unknown connector action.")


def validate_result(result) -> list[dict]:
    content = [block.model_dump(mode="json") for block in result.content if block.type == "text"]
    for block in content:
        try:
            data = json.loads(block["text"])
        except ValueError:
            continue
        if isinstance(data, dict) and (data.get("error") or data.get("success") is False):
            raise RuntimeError("The connector could not read the account. Reconnect and try again.")
    return content


def main() -> None:
    try:
        request = json.load(sys.stdin)
        timeout = 350 if request.get("action") == "connect" else 85
        with redirect_stdout(sys.stderr):
            response = asyncio.run(asyncio.wait_for(execute(request), timeout))
        print(json.dumps({"ok": True, **response}))
    except LoginError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)}))
        raise SystemExit(1) from None
    except Exception:
        # Do not expose upstream responses, cookie values, or profile paths to HTTP clients.
        print(json.dumps({"ok": False, "error": "Connection failed. Sign in again; check that Chromium and Node.js are installed."}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
