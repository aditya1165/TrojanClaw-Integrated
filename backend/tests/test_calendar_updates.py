"""Recurrence and existing-event changes use the same explicit approval boundary."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.errors import AppError
from app.models.calendar import CalendarEventDraft, CalendarUpdateRequest
from app.services.calendar_recurrence import occurrences, recurrence_rule
from tests.test_calendar import service, USER, response


def repeating(**changes):
    start = datetime(2030, 10, 28, 9, tzinfo=ZoneInfo("America/Los_Angeles"))
    return CalendarEventDraft.model_validate({"title": "Do Splitwise", "start": start, "end": start + timedelta(minutes=15),
        "timezone": "America/Los_Angeles", "recurrence": {"frequency": "WEEKLY", "weekdays": ["MO"], "until": "2030-11-27"},
        "reminder_minutes": 0, **changes})


def original(**changes):
    return {"id": "existing123", "etag": '"version1"', "summary": "Study", "start": {"dateTime": "2030-11-17T16:00:00-08:00", "timeZone": "America/Los_Angeles"},
            "end": {"dateTime": "2030-11-17T17:00:00-08:00", "timeZone": "America/Los_Angeles"},
            "description": "Notes", "location": "Library", "reminders": {"useDefault": True},
            "extendedProperties": {"private": {"other_app": "keep"}}, **changes}


def update_request(**changes):
    return CalendarUpdateRequest(event_id="existing123", changes={"title": "Study updated", **changes})


def test_weekly_series_stays_at_nine_across_dst_and_calendar_month_has_five_dates():
    event = repeating()
    slots = occurrences(event)
    assert len(slots) == 5
    assert [start.hour for start, _ in slots] == [9] * 5
    assert slots[0][0].utcoffset() != slots[1][0].utcoffset()
    assert slots[-1][0].date().isoformat() == "2030-11-25"
    assert "UNTIL=20301128T075959Z" in recurrence_rule(event)


def test_recurring_creation_and_reminder_are_saved_only_on_approval(service):
    proposal = service.propose(USER, repeating())
    service.request.assert_not_called()
    assert len(proposal.occurrences) == 5
    saved = {"extendedProperties": {"private": {"trojanclaw_proposal": str(proposal.id)}}}
    service.request.side_effect = [response(404), response(calendars={"primary": {"busy": []}}), response(**saved)]
    service.approve(USER, proposal.id, proposal.version)
    body = service.request.call_args_list[-1].kwargs["json"]
    assert body["recurrence"] == [recurrence_rule(repeating())]
    assert body["reminders"] == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}]}


def test_conflict_on_later_recurrence_blocks_the_whole_creation(service):
    proposal = service.propose(USER, repeating())
    service.request.side_effect = [response(404), response(calendars={"primary": {"busy": [{"start": "2030-11-25T09:00:00-08:00", "end": "2030-11-25T09:15:00-08:00"}]}})]
    with pytest.raises(AppError, match="overlaps"):
        service.approve(USER, proposal.id, 1)
    assert service.request.call_count == 2
    assert service.repository.rows[proposal.id]["status"] == "pending"


@pytest.mark.parametrize("recurrence", [
    {"frequency": "DAILY", "count": 52, "interval": 12},
    {"frequency": "DAILY", "until": "2031-12-31"},
    {"frequency": "WEEKLY", "weekdays": ["TU"], "count": 4},
    {"frequency": "WEEKLY", "until": "2030-10-01"},
])
def test_unbounded_or_inconsistent_schedules_never_become_proposals(service, recurrence):
    with pytest.raises(AppError):
        service.propose(USER, repeating(recurrence=recurrence))
    assert service.repository.rows == {}
    service.request.assert_not_called()


def test_existing_event_edit_uses_before_after_and_conditional_patch_after_approval(service):
    old = original()
    service.request.side_effect = [response(**old)]
    proposal = service.propose_update(USER, update_request(), "America/Los_Angeles")
    assert proposal.operation == "update"
    assert proposal.before["summary"] == "Study"
    assert proposal.event.title == "Study updated"
    assert service.request.call_args.args[1] == "GET"
    updated = {**old, "summary": "Study updated", "extendedProperties": {"private": {"trojanclaw_proposal": str(proposal.id)}}}
    service.request.side_effect = [response(**old), response(**updated)]
    result = service.approve(USER, proposal.id, proposal.version)
    assert result.status == "applied"
    args, kwargs = service.request.call_args
    assert args[1:] == ("PATCH", "/calendars/primary/events/existing123")
    assert kwargs["expected_etag"] == old["etag"]
    assert kwargs["json"]["extendedProperties"]["private"]["other_app"] == "keep"
    assert "reminders" not in kwargs["json"] and "recurrence" not in kwargs["json"]
    calls = service.request.call_count
    assert service.approve(USER, proposal.id, 1).status == "applied"
    assert service.request.call_count == calls


def test_stale_existing_event_cannot_be_overwritten(service):
    service.request.side_effect = [response(**original())]
    proposal = service.propose_update(USER, update_request(), "America/Los_Angeles")
    service.request.side_effect = [response(**original(etag='"version2"'))]
    with pytest.raises(AppError, match="changed since"):
        service.approve(USER, proposal.id, 1)
    assert service.request.call_args.args[1] == "GET"
    assert service.repository.rows[proposal.id]["status"] == "pending"


def test_lost_update_response_recovers_using_proposal_marker_without_second_patch(service):
    service.request.side_effect = [response(**original())]
    proposal = service.propose_update(USER, update_request(), "America/Los_Angeles")
    service.request.side_effect = [response(**original()), TimeoutError("response lost")]
    with pytest.raises(TimeoutError):
        service.approve(USER, proposal.id, 1)
    assert service.repository.rows[proposal.id]["status"] == "approved"
    service.request.side_effect = [response(**original(etag='"version2"', extendedProperties={"private": {"trojanclaw_proposal": str(proposal.id)}}))]
    assert service.approve(USER, proposal.id, 1).status == "applied"
    assert service.request.call_args.args[1] == "GET"


def test_entire_series_edit_resolves_master_and_preserves_original_recurrence(service):
    rule = ["RRULE:FREQ=WEEKLY;COUNT=4"]
    service.request.side_effect = [response(**original(recurringEventId="series123")), response(**original(id="series123", recurrence=rule))]
    proposal = service.propose_update(USER, CalendarUpdateRequest(event_id="existing123", scope="series", changes={"title": "New series title"}), "America/Los_Angeles")
    assert proposal.scope == "series" and proposal.before["recurrence"] == rule
    assert service.repository.rows[proposal.id]["event"]["target"]["id"] == "series123"
    assert not proposal.recurrence_changed


def test_updates_are_account_scoped_before_any_google_request(service):
    with pytest.raises(AppError, match="Connect Google Calendar"):
        service.propose_update(UUID(int=3), update_request(), "America/Los_Angeles")
    service.request.assert_not_called()


def test_recovered_update_stays_frozen_if_database_confirmation_fails(service):
    service.request.side_effect = [response(**original())]
    proposal = service.propose_update(USER, update_request(), "America/Los_Angeles")
    service.request.side_effect = [response(**original(extendedProperties={"private": {"trojanclaw_proposal": str(proposal.id)}}))]
    service.repository.applied = MagicMock(side_effect=RuntimeError("database unavailable"))
    with pytest.raises(RuntimeError):
        service.approve(USER, proposal.id, 1)
    assert service.repository.rows[proposal.id]["status"] == "approved"


def test_chat_can_propose_existing_event_changes_but_cannot_apply_them(service):
    from tests.test_calendar import FakeRuntime, chat
    service.request.side_effect = [response(**original())]

    async def script(number, kwargs, tools):
        assert "propose_calendar_update" in tools
        assert "entire series" in kwargs["extra_instructions"]
        await tools["propose_calendar_update"].call({"event_id": "existing123", "changes": {"title": "Exam prep"}})
        return "Review the proposed change."

    result = chat(FakeRuntime(script), service, "Rename my study event to Exam prep")
    assert "Approve changes" in result.response
    assert result.calendar_proposals[0].operation == "update"
    assert result.calendar_proposals[0].status == "pending"
    assert service.request.call_count == 1 and service.request.call_args.args[1] == "GET"


def test_recurring_calendar_draft_and_runtime_sources_are_preserved_together(service):
    from tests.test_calendar import FakeRuntime, chat

    class SourcedRuntime(FakeRuntime):
        async def run(self, *args, **kwargs):
            result = await super().run(*args, **kwargs)
            result.sources = [{"title": "Course announcement", "url": "https://brightspace.usc.edu/test", "kind": "announcement"}]
            return result

    async def script(number, kwargs, tools):
        assert kwargs["connectors"] == ["piazza"]
        await tools["propose_calendar_event"].call(repeating().model_dump(mode="json"))
        return "Review this series."

    result = chat(SourcedRuntime(script), service, "Remind me to do Splitwise every Monday for a month", connectors=["piazza"])
    assert result.sources[0].title == "Course announcement"
    assert result.calendar_proposals[0].event.reminder_minutes == 0
    assert len(result.calendar_proposals[0].occurrences) == 5
    assert "Repeats weekly" in result.response
    service.request.assert_not_called()


@pytest.mark.parametrize("update_first", [False, True])
def test_async_creates_and_updates_share_one_review_card_limit(service, update_first):
    import asyncio
    from anthropic.lib.tools import ToolError
    from tests.test_calendar import FakeRuntime, chat

    service.request.side_effect = [response(**original())]

    async def script(number, kwargs, tools):
        create = tools["propose_calendar_event"].call(repeating().model_dump(mode="json"))
        update = tools["propose_calendar_update"].call({"event_id": "existing123", "changes": {"title": "New title"}})
        results = await asyncio.gather(*( [update, create] if update_first else [create, update]), return_exceptions=True)
        assert sum(isinstance(result, ToolError) for result in results) == 1
        return "Review this proposal."

    result = chat(FakeRuntime(script), service)
    assert len(result.calendar_proposals) == len(service.repository.rows) == 1
    assert result.calendar_proposals[0].operation == ("update" if update_first else "create")
    assert all(call.args[1] == "GET" for call in service.request.call_args_list)


def test_async_recovery_can_edit_an_existing_event_without_creating_a_replacement(service):
    from tests.test_calendar import FakeRuntime, chat

    service.request.side_effect = [response(**original())]

    async def script(number, kwargs, tools):
        if number == 1:
            return "I've prepared a calendar proposal."
        assert kwargs["include_goal_tools"] is False
        await tools["propose_calendar_update"].call({"event_id": "existing123", "changes": {"title": "Exam prep"}})
        return "Review the changes."

    result = chat(FakeRuntime(script), service, "Rename the existing study event to Exam prep")
    assert "Approve changes" in result.response
    assert len(result.calendar_proposals) == 1 and result.calendar_proposals[0].operation == "update"
    assert service.request.call_count == 1 and service.request.call_args.args[1] == "GET"
