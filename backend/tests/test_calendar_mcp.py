"""The backend sends only approved writes through the private MCP bridge."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.errors import AppError
from app.integrations.google_calendar import CalendarMCP


def test_mcp_token_is_passed_on_stdin_and_never_in_arguments(monkeypatch):
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr("app.integrations.google_calendar.shutil.which", lambda _: "node")
    run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "status": 200, "data": {"id": "primary"}})))
    monkeypatch.setattr("app.integrations.google_calendar.subprocess.run", run)
    assert CalendarMCP().verify("private-token") == {"id": "primary"}
    args, kwargs = run.call_args
    assert "private-token" not in repr(args)
    assert json.loads(kwargs["input"])["access_token"] == "private-token"


def test_raw_provider_failure_does_not_expose_credentials(monkeypatch):
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr("app.integrations.google_calendar.shutil.which", lambda _: "node")
    monkeypatch.setattr("app.integrations.google_calendar.subprocess.run", MagicMock(return_value=SimpleNamespace(
        returncode=1, stdout=json.dumps({"ok": False, "error": "private-token"}))))
    with pytest.raises(AppError) as error:
        CalendarMCP().verify("private-token")
    assert "private-token" not in error.value.message


def test_mcp_create_requires_stored_approval_marker():
    mcp = CalendarMCP()
    mcp.run = MagicMock()
    with pytest.raises(AppError, match="approval"):
        mcp.request("token", "POST", "/calendars/primary/events", json={"summary": "Injected event"})
    mcp.run.assert_not_called()


@pytest.mark.parametrize("method,path", [("DELETE", "/calendars/primary/events/event"),
                                          ("PATCH", "/calendars/primary/events/event"),
                                          ("POST", "/calendars/primary/events/event/move")])
def test_mcp_does_not_forward_other_calendar_changes(method, path):
    mcp = CalendarMCP()
    mcp.run = MagicMock()
    with pytest.raises(AppError):
        mcp.request("token", method, path)
    mcp.run.assert_not_called()
