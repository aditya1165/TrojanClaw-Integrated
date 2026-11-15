"""Approval boundaries, account isolation, and recovery without live Google writes."""

import asyncio
from copy import deepcopy
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest
from anthropic.lib.tools import ToolError

from app.agent.runtime import AgentResult
from app.errors import AppError, DependencyUnavailableError
from app.main import app
from app.models.calendar import CalendarEventDraft, CalendarEventQuery
from app.models.chat import ChatRequest
from app.services.calendar import GoogleCalendar, SCOPES, get_calendar_service
from app.services.chat import ChatService


USER = UUID(int=2)


def draft():
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(second=0, microsecond=0)
    return CalendarEventDraft(title="Study session", start=start, end=start + timedelta(hours=1), timezone="UTC")


class MemoryRepository:
    def __init__(self):
        self.owner = USER
        self.link = {"id": uuid4(), "calendar_id": "primary", "calendar_label": "Student calendar", "encrypted_tokens": "secret"}
        self.rows = {}
        self.states = {}

    def connection(self, user_id):
        return self.link if user_id == self.owner else None

    def create(self, user_id, connection_id, event):
        row = dict(id=uuid4(), user_id=user_id, connection_id=connection_id, event=deepcopy(event), version=1,
                   status="pending", expires_at=datetime.now(timezone.utc) + timedelta(days=1), event_url=None, locked=False)
        self.rows[row["id"]] = row
        return deepcopy(row)

    def get(self, user_id, proposal_id):
        row = self.rows.get(proposal_id)
        if not row or row["user_id"] != user_id:
            raise AppError("Not found", "proposal_not_found", 404)
        return deepcopy(row)

    def list(self, user_id):
        return [deepcopy(row) for row in self.rows.values() if row["user_id"] == user_id]

    def claim(self, user_id, proposal_id, version):
        row = self.get(user_id, proposal_id)
        if (row["version"] != version or row["locked"] or row["status"] not in {"pending", "approved"}
                or (row["status"] == "pending" and row["expires_at"] <= datetime.now(timezone.utc))):
            raise AppError("Changed", "proposal_conflict", 409)
        self.rows[proposal_id].update(status="approved", locked=True)
        return self.get(user_id, proposal_id)

    def release(self, user_id, proposal_id, *, conflict=False):
        row = self.rows[proposal_id]
        row["locked"] = False
        if conflict:
            row["status"] = "pending"

    def applied(self, user_id, proposal_id, event_url):
        self.rows[proposal_id].update(status="applied", event_url=event_url, locked=False)
        return self.get(user_id, proposal_id)

    def edit(self, user_id, proposal_id, version, event):
        row = self.get(user_id, proposal_id)
        if row["version"] != version or row["status"] != "pending":
            raise AppError("Changed", "proposal_conflict", 409)
        self.rows[proposal_id].update(version=version + 1, event=deepcopy(event))
        return self.get(user_id, proposal_id)

    def cancel(self, user_id, proposal_id, version):
        row = self.get(user_id, proposal_id)
        if row["version"] != version or row["status"] != "pending":
            raise AppError("Changed", "proposal_conflict", 409)
        self.rows[proposal_id]["status"] = "cancelled"
        return self.get(user_id, proposal_id)

    def save_state(self, state_hash, user_id, session_hash, verifier):
        self.states[(state_hash, user_id, session_hash)] = {"verifier": verifier}

    def consume_state(self, state_hash, user_id, session_hash):
        return self.states.pop((state_hash, user_id, session_hash), None)

    def save_connection(self, *args):
        self.saved_connection = args


@pytest.fixture
def service():
    settings = SimpleNamespace(google_calendar_client_id="client", google_calendar_client_secret="secret",
                               google_calendar_encryption_key=Fernet.generate_key().decode(),
                               google_calendar_redirect_uri="http://localhost:8000/calendar/callback")
    service = GoogleCalendar(settings, MemoryRepository())
    service.mcp = MagicMock()
    service.mcp.verify.return_value = {"id": "primary", "summary": "student@example.com"}
    service.request = MagicMock(side_effect=AssertionError("Unexpected Google request"))
    return service


def response(status=200, **body):
    return httpx.Response(status, json=body)


def success(proposal_id):
    return response(htmlLink="https://calendar.google.com/calendar/event?eid=test",
                    extendedProperties={"private": {"trojanclaw_proposal": str(proposal_id)}})


def test_listing_events_is_scoped_read_only_and_preserves_all_day_dates(service):
    service.request.return_value = response(events=[
        {"id": "external", "summary": "Outside chat", "start": {"dateTime": "2030-11-17T15:00:00-08:00"}, "end": {"dateTime": "2030-11-17T15:20:00-08:00"}, "description": "unneeded private text"},
        {"id": "all-day", "summary": "Holiday", "start": {"date": "2030-11-18"}, "end": {"date": "2030-11-19"}},
    ], limited=False)
    service.request.side_effect = None
    result = service.list_events(USER, CalendarEventQuery(), "America/Los_Angeles")
    assert len(result["events"]) == 2
    assert "description" not in result["events"][0]
    assert result["events"][1]["start"] == {"date": "2030-11-18"}
    args, kwargs = service.request.call_args
    assert args[1:] == ("GET", "/calendars/primary/events")
    assert datetime.fromisoformat(kwargs["params"]["timeMax"]) - datetime.fromisoformat(kwargs["params"]["timeMin"]) == timedelta(days=7)
    with pytest.raises(AppError, match="Connect Google Calendar"):
        service.list_events(UUID(int=3), CalendarEventQuery(), "America/Los_Angeles")
    assert service.request.call_count == 1
    assert service.repository.rows == {}


@pytest.mark.parametrize("query", [
    {"start": "2030-11-17T00:00:00-08:00"},
    {"start": "2030-11-17T00:00:00", "end": "2030-11-18T00:00:00"},
    {"start": "2030-11-17T00:00:00Z", "end": "2031-01-18T00:00:00Z"},
    {"start": "2030-11-18T00:00:00Z", "end": "2030-11-17T00:00:00Z"},
    {"calendar_id": "another-users-calendar"},
])
def test_calendar_read_ranges_are_bounded_and_cannot_select_another_calendar(query):
    with pytest.raises(ValidationError):
        CalendarEventQuery.model_validate(query)


def test_chat_reads_actual_events_without_creating_a_proposal(service):
    from app.errors import DependencyUnavailableError
    import json

    service.request.side_effect = None
    service.request.return_value = response(events=[{"summary": "Skating", "start": {"dateTime": "2030-11-17T15:00:00-08:00"}, "end": {"dateTime": "2030-11-17T15:20:00-08:00"}}])

    async def script(number, kwargs, tools):
        assert "list_calendar_events" in tools
        assert "Reading never needs approval" in kwargs["extra_instructions"]
        events = json.loads(await tools["list_calendar_events"].call({}))
        assert events["events"][0]["summary"] == "Skating"
        return "Your upcoming event is Skating at 3 PM."

    result = chat(FakeRuntime(script), service, "what are my events?")
    assert "Skating" in result.response and result.calendar_proposals == []
    assert service.repository.rows == {}


def test_proposing_editing_and_cancelling_never_write_google(service):
    proposal = service.propose(USER, draft())
    changed = draft().model_copy(update={"title": "Revised study session"})
    edited = service.edit(USER, proposal.id, 1, changed)
    assert edited.version == 2
    assert edited.status == "pending"
    assert service.cancel(USER, proposal.id, 2).status == "cancelled"
    service.request.assert_not_called()


def test_only_explicit_approval_creates_reviewed_event_and_repeat_is_safe(service):
    proposal = service.propose(USER, draft())
    service.request.side_effect = [response(404), response(calendars={"primary": {"busy": []}}), success(proposal.id)]
    result = service.approve(USER, proposal.id, 1)
    assert result.status == "applied"
    assert service.approve(USER, proposal.id, 1).status == "applied"
    assert service.request.call_count == 3
    args, kwargs = service.request.call_args
    assert args[1:] == ("POST", "/calendars/primary/events")
    assert kwargs["json"]["summary"] == proposal.event.title
    assert kwargs["json"]["id"] == "tc" + proposal.id.hex
    assert kwargs["json"]["reminders"] == {"useDefault": False}
    assert "attendees" not in kwargs["json"]


def test_lost_response_retries_same_id_and_recovers_existing_event(service):
    proposal = service.propose(USER, draft())
    service.request.side_effect = [response(404), response(calendars={"primary": {"busy": []}}),
                                   AppError("Timeout", "calendar_unavailable", 502)]
    with pytest.raises(AppError):
        service.approve(USER, proposal.id, 1)
    assert service.repository.rows[proposal.id]["status"] == "approved"
    with pytest.raises(AppError):
        service.edit(USER, proposal.id, 1, draft())
    service.request.reset_mock(side_effect=True)
    service.request.return_value = success(proposal.id)
    assert service.approve(USER, proposal.id, 1).status == "applied"
    assert service.request.call_count == 1
    assert service.request.call_args.args[1] == "GET"


def test_busy_slot_requires_edit_and_new_approval_without_writing(service):
    proposal = service.propose(USER, draft())
    service.request.side_effect = [response(404), response(calendars={"primary": {"busy": [{"start": proposal.event.start.isoformat(), "end": proposal.event.end.isoformat()}]}})]
    with pytest.raises(AppError, match="overlaps"):
        service.approve(USER, proposal.id, 1)
    assert service.repository.rows[proposal.id]["status"] == "pending"
    assert not any(call.args[2].endswith("/events") and call.args[1] == "POST" for call in service.request.call_args_list)


def test_mcp_schema_rejection_before_create_returns_to_review(service):
    proposal = service.propose(USER, draft())
    service.request.side_effect = [response(404), response(calendars={"primary": {"busy": []}}),
                                   AppError("Unsupported create schema", "calendar_mcp_schema", 409)]
    with pytest.raises(AppError):
        service.approve(USER, proposal.id, 1)
    assert service.repository.rows[proposal.id]["status"] == "pending"
    assert service.edit(USER, proposal.id, 1, draft()).version == 2


@pytest.mark.parametrize("change", ["version", "expired", "cancelled", "connection", "locked"])
def test_unreviewed_or_invalid_proposal_cannot_be_applied(service, change):
    proposal = service.propose(USER, draft())
    row = service.repository.rows[proposal.id]
    if change == "version": row["version"] = 2
    if change == "expired": row["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    if change == "cancelled": row["status"] = "cancelled"
    if change == "connection": row["connection_id"] = uuid4()
    if change == "locked": row["locked"] = True
    with pytest.raises(AppError):
        service.approve(USER, proposal.id, 1)
    service.request.assert_not_called()


def test_another_user_cannot_view_or_approve_proposals(service):
    proposal = service.propose(USER, draft())
    assert service.proposals(UUID(int=3)) == []
    with pytest.raises(AppError):
        service.approve(UUID(int=3), proposal.id, 1)
    service.request.assert_not_called()


@pytest.mark.parametrize("update", [
    {"start": "2030-07-17T16:00:00"},
    {"end": "2000-01-01T00:00:00Z"},
    {"timezone": "not/a-zone"},
    {"start": "2030-07-17T16:00:00-08:00", "end": "2030-07-17T17:00:00-08:00", "timezone": "America/Los_Angeles"},
    {"attendees": ["someone@example.com"]},
])
def test_invalid_or_hidden_event_fields_are_rejected(update):
    with pytest.raises(ValidationError):
        CalendarEventDraft.model_validate(draft().model_dump(mode="json") | update)


def test_oauth_state_is_bound_to_user_session_and_single_use(service):
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(service.start(USER, "session-a")).query)["state"][0]
    service._http = MagicMock()
    with pytest.raises(AppError, match="expired"):
        service.callback(USER, "session-b", state, "code")
    with pytest.raises(AppError, match="not connected"):
        service.callback(USER, "session-a", state, None, "access_denied")
    with pytest.raises(AppError, match="expired"):
        service.callback(USER, "session-a", state, "code")
    service._http.assert_not_called()


def test_oauth_tokens_are_encrypted_and_permissions_required(service):
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(service.start(USER, "session")).query)["state"][0]
    service._http = MagicMock(return_value=response(access_token="access", refresh_token="refresh", scope=" ".join(SCOPES)))
    service.callback(USER, "session", state, "code")
    encrypted = service.repository.saved_connection[-1]
    assert "refresh" not in encrypted
    assert service.decrypt(encrypted)["refresh_token"] == "refresh"
    assert service._http.call_args_list[0].kwargs["data"]["code_verifier"]
    service.mcp.verify.assert_called_once_with("access")


class FakeRuntime:
    """Stands in for the agent. `script(call_number, kwargs, tools)` plays Claude and returns the reply."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    async def run(self, goal, messages, **kwargs):
        self.calls.append(kwargs)
        tools = {tool.name: tool for tool in kwargs.get("extra_tools", [])}
        return AgentResult(text=await self.script(len(self.calls), kwargs, tools))


def chat(runtime, service, content="Add a study session", **request):
    messages = content if isinstance(content, list) else [{"role": "user", "content": content}]
    return asyncio.run(ChatService(runtime, calendar=service).chat(
        ChatRequest(messages=messages, **request), user_id=USER))


def test_chat_tool_only_drafts_and_has_no_approval_tool(service):
    async def script(number, kwargs, tools):
        assert set(tools) == {"propose_calendar_event", "list_calendar_events", "propose_calendar_update"}
        assert "Chat replies including 'yes' cannot approve" in kwargs["extra_instructions"]
        assert "nothing was added" in await tools["propose_calendar_event"].call(draft().model_dump(mode="json"))
        return "Review the proposal."

    response = chat(FakeRuntime(script), service)
    assert "new calendar proposal" in response.response
    assert len(response.calendar_proposals) == 1
    service.request.assert_not_called()


def test_without_google_calendar_the_agent_gets_no_draft_tool_and_is_told_so(service):
    service.settings.google_calendar_client_id = None

    async def script(number, kwargs, tools):
        assert tools == {}
        assert "Google Calendar is not configured" in kwargs["extra_instructions"]
        return "I can't add events yet."

    assert chat(FakeRuntime(script), service).response == "I can't add events yet."


def test_invalid_draft_is_a_tool_error_and_creates_nothing(service):
    async def script(number, kwargs, tools):
        bad = {**draft().model_dump(mode="json"), "timezone": "Mars/Base"}
        with pytest.raises(ToolError):
            await tools["propose_calendar_event"].call(bad)
        return "Which time zone should I use?"

    response = chat(FakeRuntime(script), service)
    assert response.response == "Which time zone should I use?"
    assert response.calendar_proposals == [] and service.repository.rows == {}


def test_selected_connectors_and_calendar_drafts_work_together_for_the_signed_in_account(service):
    async def script(number, kwargs, tools):
        assert kwargs["user_id"] == USER and list(kwargs["connectors"]) == ["brightspace"]
        assert kwargs["timezone"] == "America/Chicago"
        await tools["propose_calendar_event"].call(draft().model_dump(mode="json"))
        return "Review the event."

    response = chat(FakeRuntime(script), service, connectors=["brightspace"], timezone="America/Chicago")
    assert len(response.calendar_proposals) == 1 and response.calendar_proposals[0].status == "pending"
    service.request.assert_not_called()


def test_followup_recovers_missing_draft_call_and_returns_a_new_approval_card(service):
    base = draft().start.replace(hour=16, minute=0)
    first_event = draft().model_copy(update={"start": base, "end": base + timedelta(hours=1)})
    second_event = first_event.model_copy(update={
        "start": first_event.start + timedelta(hours=4),
        "end": first_event.start + timedelta(hours=5, minutes=30),
    })

    async def script(number, kwargs, tools):
        if number == 1:
            await tools["propose_calendar_event"].call(first_event.model_dump(mode="json"))
            return "Review the first event."
        if number == 2:
            return "I've drafted a second calendar event for you. A proposal card should now appear."
        # The retry gets only the calendar tools, no MCP or connector tools.
        assert kwargs["include_goal_tools"] is False
        assert set(tools) == {"propose_calendar_event", "list_calendar_events", "propose_calendar_update", "ask_calendar_details"}
        assert "No valid draft has been saved" in kwargs["extra_instructions"]
        await tools["propose_calendar_event"].call(second_event.model_dump(mode="json"))
        return "Review the second event."

    runtime = FakeRuntime(script)
    messages = [{"role": "user", "content": "Add a study session tomorrow from 4 to 5 PM."}]
    first = chat(runtime, service, messages)
    old = first.calendar_proposals[0]
    service.repository.rows[old.id]["status"] = "applied"
    messages += [{"role": "assistant", "content": first.response}, {"role": "user", "content": "another at 8pm to 9:30pm"}]
    second = chat(runtime, service, messages)
    assert len(second.calendar_proposals) == 1
    new = second.calendar_proposals[0]
    assert new.id != old.id
    assert new.status == "pending"
    assert new.event.start == second_event.start
    assert new.event.end == second_event.end
    assert service.repository.rows[old.id]["status"] == "applied"
    assert "Approve addition" in second.response
    service.request.assert_not_called()


def test_repeated_tool_calls_cannot_create_multiple_cards_for_one_confirmation(service):
    event = draft()

    async def script(number, kwargs, tools):
        propose = tools["propose_calendar_event"]
        first = await propose.call(event.model_dump(mode="json"))
        duplicate = await propose.call(event.model_dump(mode="json"))
        assert str(next(iter(service.repository.rows))) in first
        assert "already has a review card" in duplicate
        other = event.model_copy(update={"start": event.start + timedelta(hours=4), "end": event.end + timedelta(hours=4)})
        with pytest.raises(ToolError, match="already created"):
            await propose.call(other.model_dump(mode="json"))
        return "I've prepared new calendar proposals."

    result = chat(FakeRuntime(script), service, "yes")
    assert len(result.calendar_proposals) == len(service.repository.rows) == 1
    assert "a new calendar proposal" in result.response
    service.request.assert_not_called()


def test_parallel_draft_calls_still_create_only_one_card(service):
    event = draft()

    async def script(number, kwargs, tools):
        propose = tools["propose_calendar_event"]
        other = event.model_copy(update={"start": event.start + timedelta(hours=4), "end": event.end + timedelta(hours=4)})
        results = await asyncio.gather(propose.call(event.model_dump(mode="json")),
                                       propose.call(other.model_dump(mode="json")), return_exceptions=True)
        assert sum(isinstance(result, ToolError) for result in results) == 1
        return "Review the event."

    result = chat(FakeRuntime(script), service)
    assert len(result.calendar_proposals) == len(service.repository.rows) == 1


@pytest.mark.parametrize("request_text", ["a skating event at 3pm to 3.20pm also", "a skating event at 3:00 PM–3:20 PM also"])
def test_old_event_times_are_rejected_before_saving_and_corrected_in_same_turn(service, request_text):
    base = draft().start.replace(hour=8, minute=0)
    old = draft().model_copy(update={"title": "Skateboarding", "start": base, "end": base + timedelta(minutes=15)})
    corrected = old.model_copy(update={"title": "Skating", "start": base.replace(hour=15), "end": base.replace(hour=15, minute=20)})

    async def script(number, kwargs, tools):
        with pytest.raises(ToolError, match="latest user message"):
            await tools["propose_calendar_event"].call(old.model_dump(mode="json"))
        assert service.repository.rows == {}
        await tools["propose_calendar_event"].call(corrected.model_dump(mode="json"))
        return "Review the skating event."

    runtime = FakeRuntime(script)
    result = chat(runtime, service, [{"role": "user", "content": "Skateboarding tomorrow at 8am to 8.15am"},
                                     {"role": "assistant", "content": "Review the event below."},
                                     {"role": "user", "content": request_text}])
    assert len(result.calendar_proposals) == len(service.repository.rows) == 1
    assert result.calendar_proposals[0].event == corrected
    assert "Skating" in result.response and "03:20 PM" in result.response
    assert len(runtime.calls) == 1  # corrected in the same turn, no retry
    service.request.assert_not_called()


def test_repeated_wrong_times_cannot_be_claimed_as_a_successful_new_draft(service):
    base = draft().start.replace(hour=8, minute=0)
    old = draft().model_copy(update={"start": base, "end": base + timedelta(minutes=15)})

    async def script(number, kwargs, tools):
        with pytest.raises(ToolError):
            await tools["propose_calendar_event"].call(old.model_dump(mode="json"))
        return "All done."

    runtime = FakeRuntime(script)
    result = chat(runtime, service, "skating at 3pm to 3.20pm")
    assert "couldn't prepare" in result.response
    assert result.calendar_proposals == [] and service.repository.rows == {}
    assert len(runtime.calls) == 2  # the rejection triggers the one retry
    service.request.assert_not_called()


def test_recovery_asks_for_missing_details_instead_of_inventing_a_draft(service):
    async def script(number, kwargs, tools):
        if number == 1:
            return "I've created a calendar proposal."
        await tools["ask_calendar_details"].call({"question": "What start and end times should I use?"})
        return "I've drafted an event."

    result = chat(FakeRuntime(script), service, "another study session")
    assert result.response == "What start and end times should I use?"
    assert result.calendar_proposals == []
    assert service.repository.rows == {}
    service.request.assert_not_called()


def test_no_new_card_is_claimed_if_the_recovery_still_creates_nothing(service):
    async def script(number, kwargs, tools):
        return "I've drafted a second calendar event."

    runtime = FakeRuntime(script)
    result = chat(runtime, service, "another at 8pm")
    assert "couldn't prepare a new review card" in result.response
    assert result.calendar_proposals == []
    assert len(runtime.calls) == 2  # one retry only
    service.request.assert_not_called()


def test_http_approval_rejects_changed_payload_and_untrusted_origin(service, signed_in_account):
    proposal = service.propose(USER, draft())
    app.dependency_overrides[get_calendar_service] = lambda: service
    try:
        client = TestClient(app)
        url = f"/calendar/proposals/{proposal.id}/approve"
        assert client.post(url, json={"version": 1, "event": draft().model_dump(mode="json")}).status_code == 422
        assert client.post(url, json={"version": 1}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/calendar/proposals").headers["cache-control"] == "no-store"
        assert client.get(url).status_code == 405
    finally:
        app.dependency_overrides.pop(get_calendar_service, None)
    service.request.assert_not_called()


def test_http_calendar_requires_sign_in(service):
    from app.api.routes.auth import get_current_user
    from fastapi import HTTPException
    def anonymous():
        raise HTTPException(401, "Please sign in.")
    app.dependency_overrides[get_calendar_service] = lambda: service
    app.dependency_overrides[get_current_user] = anonymous
    try:
        assert TestClient(app).post(f"/calendar/proposals/{uuid4()}/approve", json={"version": 1}).status_code == 401
    finally:
        app.dependency_overrides.pop(get_calendar_service, None)
        app.dependency_overrides.pop(get_current_user, None)
    service.request.assert_not_called()


@pytest.mark.skipif(os.getenv("RUN_CALENDAR_DB_TESTS") != "1", reason="Requires migrated database; creates and removes temporary accounts")
def test_postgres_approval_isolation_and_private_tables():
    from app.database import get_database
    from app.repositories.calendar import CalendarRepository
    database = get_database()
    repository = CalendarRepository(database)
    users = [uuid4(), uuid4()]
    try:
        with database.connection() as connection, connection.cursor() as cursor:
            for user_id in users:
                cursor.execute("INSERT INTO public.app_users (id, display_name, username) VALUES (%s, %s, %s)",
                               (str(user_id), "Calendar Test", "cal_test_" + user_id.hex[:16]))
            for table in ("google_calendar_connections", "google_calendar_oauth_states", "calendar_proposals"):
                cursor.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", ("public." + table,))
                assert cursor.fetchone() == (True,)
                cursor.execute("SELECT has_table_privilege('anon', %s, 'SELECT,INSERT,UPDATE,DELETE'), has_table_privilege('authenticated', %s, 'SELECT,INSERT,UPDATE,DELETE')", ("public." + table, "public." + table))
                assert cursor.fetchone() == (False, False)
        for user_id in users:
            repository.save_connection(user_id, "primary", "Test calendar", "encrypted-test-token")
        link = repository.connection(users[0])
        row = repository.create(users[0], link["id"], draft().model_dump(mode="json"))
        assert repository.list(users[1]) == []
        with pytest.raises(AppError):
            repository.get(users[1], row["id"])
        with pytest.raises(AppError):
            repository.claim(users[1], row["id"], 1)
        edited = repository.edit(users[0], row["id"], 1, draft().model_dump(mode="json"))
        assert edited["version"] == 2
        with pytest.raises(AppError):
            repository.claim(users[0], row["id"], 1)
        assert repository.claim(users[0], row["id"], 2)["approved_at"]
        with pytest.raises(AppError):
            repository.claim(users[0], row["id"], 2)
        with pytest.raises(AppError):
            repository.edit(users[0], row["id"], 2, draft().model_dump(mode="json"))
        repository.release(users[0], row["id"])
        assert repository.claim(users[0], row["id"], 2)["status"] == "approved"
        repository.applied(users[0], row["id"], "https://calendar.google.com/calendar/event?eid=test")
        repository.save_connection(users[0], "primary", "Reconnected", "new-token")
        assert repository.list(users[0]) == []
    finally:
        with database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM public.app_users WHERE id = ANY(%s::uuid[])", ([str(user_id) for user_id in users],))
