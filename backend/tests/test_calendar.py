"""Approval boundaries, account isolation, and recovery without live Google writes."""

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
from openai import RateLimitError

from app.errors import AppError, DependencyUnavailableError
from app.main import app
from app.models.calendar import CalendarEventDraft
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
    service.request.side_effect = [response(404), response(calendars={"primary": {"busy": [{"start": "busy"}]}})]
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


@pytest.mark.parametrize("embedding_failure", [None, "quota", "unconfigured"])
def test_chat_tool_only_drafts_and_has_no_approval_tool(service, embedding_failure):
    model = MagicMock()
    def respond(messages, prompt, tools, call):
        assert {tool["name"] for tool in tools} == {"propose_calendar_event"}
        assert "Chat replies including 'yes' cannot approve" in prompt
        if embedding_failure:
            assert "Campus document search is temporarily unavailable" in prompt
            assert "Do not invent source facts" in prompt
        assert "nothing was added" in call("propose_calendar_event", draft().model_dump(mode="json"))
        return "Review the proposal."
    model.respond_with_tools.side_effect = respond
    embeddings, documents = MagicMock(), MagicMock()
    if embedding_failure == "quota":
        embeddings.embed.side_effect = RateLimitError(
            "No credits remaining", response=httpx.Response(429, request=httpx.Request("POST", "https://example.com/embeddings")),
            body={"error": {"code": "credit_balance_exhausted"}},
        )
    elif embedding_failure == "unconfigured":
        embeddings.embed.side_effect = DependencyUnavailableError("OpenAI embeddings")
    documents.search.return_value = []
    chat = ChatService(None, embeddings, documents, model, calendar=service)
    response = chat.chat(ChatRequest(messages=[{"role": "user", "content": "Add a study session"}]), user_id=USER)
    assert "new calendar proposal" in response.response
    assert len(response.calendar_proposals) == 1
    if embedding_failure:
        documents.search.assert_not_called()
    service.request.assert_not_called()


def test_repeated_tool_calls_cannot_create_multiple_cards_for_one_confirmation(service):
    model, embeddings, documents = MagicMock(), MagicMock(), MagicMock()
    documents.search.return_value = []
    event = draft()

    def respond(messages, prompt, tools, call):
        first = call("propose_calendar_event", event.model_dump(mode="json"))
        duplicate = call("propose_calendar_event", event.model_dump(mode="json"))
        assert str(next(iter(service.repository.rows))) in first
        assert "already has a review card" in duplicate
        other = event.model_copy(update={"start": event.start + timedelta(hours=4), "end": event.end + timedelta(hours=4)})
        with pytest.raises(AppError, match="already created"):
            call("propose_calendar_event", other.model_dump(mode="json"))
        return "I've prepared new calendar proposals."

    model.respond_with_tools.side_effect = respond
    result = ChatService(None, embeddings, documents, model, calendar=service).chat(
        ChatRequest(messages=[{"role": "user", "content": "yes"}]), user_id=USER,
    )
    assert len(result.calendar_proposals) == len(service.repository.rows) == 1
    assert "a new calendar proposal" in result.response
    service.request.assert_not_called()


def test_selected_connectors_and_calendar_drafts_work_together_for_the_signed_in_account(service, monkeypatch):
    connectors = MagicMock()
    connectors.tools.return_value = [{"name": "brightspace__courses"}]
    connectors.call.return_value = "Course information"
    monkeypatch.setattr("app.services.chat.get_local_connectors", lambda: connectors)
    model = MagicMock()

    def respond(messages, prompt, tools, call):
        assert {tool["name"] for tool in tools} == {"brightspace__courses", "propose_calendar_event"}
        assert call("brightspace__courses", {}) == "Course information"
        call("propose_calendar_event", draft().model_dump(mode="json"))
        return "Review the event."

    model.respond_with_tools.side_effect = respond
    response = ChatService(None, None, None, model, calendar=service).chat(
        ChatRequest(messages=[{"role": "user", "content": "Add a study session"}], connectors=["brightspace"]),
        user_id=USER,
    )
    connectors.tools.assert_called_once_with(USER, ["brightspace"])
    connectors.call.assert_called_once_with(USER, "brightspace__courses", {}, ["brightspace"])
    assert len(response.calendar_proposals) == 1 and response.calendar_proposals[0].status == "pending"
    service.request.assert_not_called()


def test_followup_recovers_missing_draft_call_and_returns_a_new_approval_card(service):
    model, embeddings, documents = MagicMock(), MagicMock(), MagicMock()
    documents.search.return_value = []
    base = draft().start.replace(hour=16, minute=0)
    first_event = draft().model_copy(update={"start": base, "end": base + timedelta(hours=1)})
    second_event = first_event.model_copy(update={
        "start": first_event.start + timedelta(hours=4),
        "end": first_event.start + timedelta(hours=5, minutes=30),
    })
    calls = []

    def respond(messages, prompt, tools, call, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            call("propose_calendar_event", first_event.model_dump(mode="json"))
            return "Review the first event."
        if len(calls) == 2:
            return "I've drafted a second calendar event for you. A proposal card should now appear."
        assert kwargs == {"require_tool": True}
        assert {tool["name"] for tool in tools} == {"propose_calendar_event", "ask_calendar_details"}
        call("propose_calendar_event", second_event.model_dump(mode="json"))
        return "Review the second event."

    model.respond_with_tools.side_effect = respond
    chat = ChatService(None, embeddings, documents, model, calendar=service)
    messages = [{"role": "user", "content": "Add a study session tomorrow from 4 to 5 PM."}]
    first = chat.chat(ChatRequest(messages=messages), user_id=USER)
    old = first.calendar_proposals[0]
    service.repository.rows[old.id]["status"] = "applied"
    messages += [{"role": "assistant", "content": first.response}, {"role": "user", "content": "another at 8pm to 9:30pm"}]
    second = chat.chat(ChatRequest(messages=messages), user_id=USER)
    assert len(second.calendar_proposals) == 1
    new = second.calendar_proposals[0]
    assert new.id != old.id
    assert new.status == "pending"
    assert new.event.start == second_event.start
    assert new.event.end == second_event.end
    assert service.repository.rows[old.id]["status"] == "applied"
    assert "Approve addition" in second.response
    service.request.assert_not_called()


@pytest.mark.parametrize("request_text", ["a skating event at 3pm to 3.20pm also", "a skating event at 3:00 PM–3:20 PM also"])
def test_old_event_times_are_rejected_before_saving_and_corrected_in_same_turn(service, request_text):
    model, documents = MagicMock(), MagicMock()
    documents.search.return_value = []
    base = draft().start.replace(hour=8, minute=0)
    old = draft().model_copy(update={"title": "Skateboarding", "start": base, "end": base + timedelta(minutes=15)})
    corrected = old.model_copy(update={"title": "Skating", "start": base.replace(hour=15), "end": base.replace(hour=15, minute=20)})

    def respond(messages, prompt, tools, call):
        with pytest.raises(AppError, match="latest user message"):
            call("propose_calendar_event", old.model_dump(mode="json"))
        assert service.repository.rows == {}
        call("propose_calendar_event", corrected.model_dump(mode="json"))
        return "Review the skating event."

    model.respond_with_tools.side_effect = respond
    result = ChatService(None, MagicMock(), documents, model, calendar=service).chat(
        ChatRequest(messages=[{"role": "user", "content": "Skateboarding tomorrow at 8am to 8.15am"},
                              {"role": "assistant", "content": "Review the event below."},
                              {"role": "user", "content": request_text}]), user_id=USER,
    )
    assert len(result.calendar_proposals) == len(service.repository.rows) == 1
    assert result.calendar_proposals[0].event == corrected
    service.request.assert_not_called()


def test_repeated_wrong_times_cannot_be_claimed_as_a_successful_new_draft(service):
    model, documents = MagicMock(), MagicMock()
    documents.search.return_value = []
    base = draft().start.replace(hour=8, minute=0)
    old = draft().model_copy(update={"start": base, "end": base + timedelta(minutes=15)})

    def respond(messages, prompt, tools, call, **kwargs):
        with pytest.raises(AppError):
            call("propose_calendar_event", old.model_dump(mode="json"))
        return "All done."

    model.respond_with_tools.side_effect = respond
    result = ChatService(None, MagicMock(), documents, model, calendar=service).chat(
        ChatRequest(messages=[{"role": "user", "content": "skating at 3pm to 3.20pm"}]), user_id=USER,
    )
    assert "couldn't prepare" in result.response
    assert result.calendar_proposals == [] and service.repository.rows == {}
    assert model.respond_with_tools.call_count == 2
    service.request.assert_not_called()


def test_recovery_asks_for_missing_details_instead_of_inventing_a_draft(service):
    model, embeddings, documents = MagicMock(), MagicMock(), MagicMock()
    documents.search.return_value = []
    def respond(messages, prompt, tools, call, **kwargs):
        if not kwargs:
            return "I've created a calendar proposal."
        call("ask_calendar_details", {"question": "What start and end times should I use?"})
        return "I've drafted an event."
    model.respond_with_tools.side_effect = respond
    chat = ChatService(None, embeddings, documents, model, calendar=service)
    result = chat.chat(ChatRequest(messages=[{"role": "user", "content": "another study session"}]), user_id=USER)
    assert result.response == "What start and end times should I use?"
    assert result.calendar_proposals == []
    assert service.repository.rows == {}
    service.request.assert_not_called()


def test_no_new_card_is_claimed_if_the_recovery_still_creates_nothing(service):
    model, embeddings, documents = MagicMock(), MagicMock(), MagicMock()
    documents.search.return_value = []
    model.respond_with_tools.return_value = "I've drafted a second calendar event."
    result = ChatService(None, embeddings, documents, model, calendar=service).chat(
        ChatRequest(messages=[{"role": "user", "content": "another at 8pm"}]), user_id=USER,
    )
    assert "couldn't prepare a new review card" in result.response
    assert result.calendar_proposals == []
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
