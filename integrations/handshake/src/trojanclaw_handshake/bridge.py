"""Private hosted MCP subprocess bridge. Account sessions arrive only via stdin."""

import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import tempfile

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from . import READ_TOOLS, create_server
from .profiles import LoginError
from .session import session_cookies
from .remote_login import remote_login
from .transport import last_failure


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


def client_for(provider: str, session: dict, temporary: str) -> Client:
    if provider == "handshake":
        return Client(create_server(session=session), timeout=60)
    allowed_tools(provider)
    cookies = session_cookies(session, provider)
    required = ("d2lSessionVal", "d2lSecureSessionVal")
    if not all(cookies.get(name) for name in required):
        raise ValueError("Brightspace session is missing required cookies.")
    env = {
        "D2L_BASE_URL": "https://brightspace.usc.edu",
        "D2L_SESSION_COOKIE": "; ".join(f"{name}={cookies[name]}" for name in required),
        "D2L_NO_UPDATE_CHECK": "1",
        "D2L_SESSION_DIR": temporary,
        "D2L_ACCESS_TOKEN": "",
        "HOME": temporary,
        "USERPROFILE": temporary,
    }
    package = Path(__file__).resolve().parents[3] / "brightspace" / "node_modules" / "brightspace-mcp-server"
    metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
    binary = metadata["bin"]
    entry = binary if isinstance(binary, str) else binary["brightspace-mcp-server"]
    return Client(StdioTransport("node", [str(package / entry)], env=env, cwd=temporary, keep_alive=False), timeout=60)


async def execute(request: dict) -> dict:
    provider = request["provider"]
    action = request["action"]
    if action in {"prepare", "usc_prepare", "capture", "resize", "identity", "usc_handshake", "seed"}:
        return await remote_login(provider, action, request["connect_url"], request.get("origin"), request.get("session"))
    allowed = allowed_tools(provider)
    if action == "call":
        validate_call(provider, request["tool"], request.get("arguments", {}))
    # The hosted bridge never reads the desktop owner's profile or cookies.json.
    with tempfile.TemporaryDirectory(prefix="trojanclaw-mcp-") as temporary:
        async with client_for(provider, request["session"], temporary) as client:
            try:
                return await execute_client(client, action, provider, allowed, request)
            except Exception:
                if provider == "handshake" and last_failure() is not None:
                    raise last_failure() from None
                raise


async def execute_client(client, action, provider, allowed, request):
    if action == "tools":
        tools = await client.list_tools()
        return {"tools": [
            {"name": tool.name, "description": tool.description or "",
             "input_schema": tool.inputSchema}
            for tool in tools if tool.name in allowed
        ]}
    if action == "verify":
        if provider == "handshake":
            from . import handshake_client
            data = await handshake_client.gql("{ currentUser { id } }")
            user = data.get("currentUser") if isinstance(data, dict) else None
            if not isinstance(user, dict) or not user.get("id"):
                raise LoginError("session_expired", "Your Handshake sign-in expired. Reconnect Handshake.")
            return {"connected": True}
        result = await client.call_tool("get_my_courses", {})
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
        timeout = 85
        with redirect_stdout(sys.stderr):
            response = asyncio.run(asyncio.wait_for(execute(request), timeout))
        print(json.dumps({"ok": True, **response}))
    except LoginError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)}))
        raise SystemExit(1) from None
    except Exception:
        # Do not expose upstream responses, cookie values, or profile paths to HTTP clients.
        print(json.dumps({"ok": False, "error": "The connector request failed. Please reconnect this source."}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
