"""Private gateway to the pinned local nspady/google-calendar-mcp server."""

import json
import os
import subprocess
import shutil
from urllib.parse import unquote

import httpx

from app.config import BACKEND_DIR
from app.errors import AppError


class CalendarMCP:
    def run(self, access_token, action, **payload):
        root = BACKEND_DIR.parent
        project = root / "integrations" / "google-calendar"
        portable = root / ".runtime"  # optional portable Node (Windows); otherwise Node from PATH
        try:
            candidates = sorted(portable.glob("node-*-win-x64/node.exe")) if portable.is_dir() else []
        except OSError:
            candidates = []
        node = str(candidates[-1]) if candidates else shutil.which("node")
        if not node or not (project / "node_modules" / "@cocal" / "google-calendar-mcp").is_dir():
            raise AppError("Install the Google Calendar connector dependencies first.", "calendar_mcp_not_installed", 503)
        try:
            process = subprocess.run(
                [node, str(project / "bridge.mjs")],
                input=json.dumps({"access_token": access_token, "action": action, **payload}),
                capture_output=True, text=True, encoding="utf-8", timeout=70, cwd=project,
                env={**os.environ},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            result = json.loads(process.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise AppError("Google Calendar MCP could not be reached. Please retry.", "calendar_mcp_unavailable", 502) from None
        if process.returncode or not result.get("ok"):
            code = result.get("code", "calendar_mcp_access")
            if code == "calendar_mcp_schema":
                raise AppError("This Google MCP tool schema cannot safely save the reviewed event. No calendar change was requested.", code, 409)
            messages = {
                "calendar_api_disabled": "Enable Google Calendar API in your OAuth client's Google Cloud project, then reconnect.",
                "calendar_scope_missing": "Reconnect Google Calendar and grant the requested calendar permissions.",
                "calendar_reconnect": "Google Calendar access expired. Reconnect to continue.",
                "calendar_primary_missing": "Google did not return a primary calendar for this account. Check Google Calendar, then reconnect.",
                "calendar_availability_failed": "Could not check calendar availability. Please try again.",
                "calendar_event_changed": "This event changed since review. Ask for a fresh edit proposal.",
                "calendar_timezone_required": "Google requires a named time zone for this recurring event. Retry after updating the calendar connector.",
                "calendar_invalid_draft": "The event time and time zone do not agree. Ask for a fresh proposal before saving.",
            }
            raise AppError(messages.get(code, "The local Google Calendar MCP server could not access your calendar. Check that Google Calendar API is enabled and reconnect."), code if code in messages else "calendar_mcp_access", 409 if code in messages else 502)
        return result

    def verify(self, access_token):
        return self.run(access_token, "verify")["data"]

    def request(self, access_token, method, path, **kwargs):
        # Only these operations are reachable from the approval service.
        body = kwargs.get("json", {})
        segments = path.split("/")
        if method == "POST" and path == "/freeBusy":
            calendar_id = body["items"][0]["id"]
            result = self.run(access_token, "freebusy", calendar_id=calendar_id, start=body["timeMin"], end=body["timeMax"])
            return httpx.Response(result["status"], json=result["data"])
        if len(segments) in {4, 5} and segments[1] == "calendars" and segments[3] == "events":
            calendar_id = unquote(segments[2])
            if method == "GET" and len(segments) == 4:
                params = kwargs["params"]
                result = self.run(access_token, "list", calendar_id=calendar_id,
                                  start=params["timeMin"], end=params["timeMax"], timezone=params["timeZone"])
                return httpx.Response(result["status"], json=result["data"])
            if method == "GET" and len(segments) == 5:
                result = self.run(access_token, "get", calendar_id=calendar_id, event_id=segments[4])
                return httpx.Response(result["status"], json=result["data"])
            if method == "PATCH" and len(segments) == 5:
                proposal = kwargs.get("approved_proposal")
                etag = kwargs.get("expected_etag")
                if not proposal or not etag or body.get("extendedProperties", {}).get("private", {}).get("trojanclaw_proposal") != proposal:
                    raise AppError("Explicit event approval is required.", "calendar_approval_required", 403)
                result = self.run(access_token, "update", calendar_id=calendar_id, event_id=unquote(segments[4]),
                                  event=body, approved_proposal=proposal, expected_etag=etag)
                return httpx.Response(result["status"], json=result["data"])
            if method == "POST" and len(segments) == 4:
                proposal = body.get("extendedProperties", {}).get("private", {}).get("trojanclaw_proposal")
                if not proposal:
                    raise AppError("Explicit event approval is required.", "calendar_approval_required", 403)
                result = self.run(access_token, "create", calendar_id=calendar_id, event=body, approved_proposal=proposal)
                return httpx.Response(result["status"], json=result["data"])
        raise AppError("This calendar operation is not available.", "calendar_operation_forbidden", 403)
