"""Study plans: free slots computed in code, blocks checked before any draft is saved. No Claude or Google calls."""

from datetime import datetime, time, timedelta
import json
from zoneinfo import ZoneInfo

from anthropic.lib.tools import ToolError
import pytest

from app.services.study_plan import (
    SchedulingConfig, StudyPlanRequest, busy_intervals, free_slots, parse_scheduling, place_blocks,
)
from tests.test_calendar import FakeRuntime, chat, draft, service  # noqa: F401  (fixture)

PT = ZoneInfo("America/Los_Angeles")
CONFIG = SchedulingConfig()  # 08:00-22:00, 15 min buffer, 30-180 min blocks, 360 min/day
MON = datetime(2030, 11, 18, tzinfo=PT)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return MON.replace(day=18 + day, hour=hour, minute=minute)


def event(start: datetime, end: datetime) -> dict:
    return {"summary": "Busy", "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}


def slots_for(events, days=1, **options):
    return free_slots(busy_intervals(events), at(0, 0), at(days, 0), PT, CONFIG, now=at(0, 0) - timedelta(days=1), **options)


def test_free_time_skips_busy_events_with_a_buffer_and_stays_in_study_hours():
    found = slots_for([event(at(0, 10), at(0, 12)), event(at(0, 13), at(0, 14, 30))])
    assert [(s.start.hour, s.start.minute, s.end.hour, s.end.minute) for s in found] == [
        (8, 0, 9, 45),     # until 15 min before the 10:00 class
        (12, 15, 12, 45),  # between the two events, 15 min buffer each side
        (14, 45, 22, 0),   # after the 14:30 event, until study hours end
    ]
    assert [s.id for s in found] == ["S1", "S2", "S3"]


def test_all_day_events_do_not_block_and_short_gaps_are_dropped():
    all_day = {"summary": "Holiday", "start": {"date": "2030-11-18"}, "end": {"date": "2030-11-19"}}
    assert [s.minutes for s in slots_for([all_day])] == [14 * 60]
    tight = [event(at(0, 8), at(0, 9)), event(at(0, 9, 40), at(0, 22))]  # 40 min gap minus buffers = 10 min
    assert slots_for(tight) == []


def test_now_and_narrowed_hours_limit_the_slots():
    found = free_slots([], at(0, 0), at(1, 0), PT, CONFIG, now=at(0, 17, 7), earliest=time(18, 0), latest=time(21, 0))
    assert [(s.start.hour, s.start.minute, s.end.hour) for s in found] == [(18, 0, 21)]
    late = free_slots([], at(0, 0), at(1, 0), PT, CONFIG, now=at(0, 19, 7))
    assert (late[0].start.hour, late[0].start.minute) == (19, 15)  # rounded up to the next 15 minutes


def plan(*blocks):
    return StudyPlanRequest.model_validate({"blocks": [dict(zip(("slot_id", "minutes", "title"), b[:3]), **(b[3] if len(b) > 3 else {}))
                                                        for b in blocks]})


def test_blocks_are_placed_one_after_another_with_a_buffer():
    slots = {s.id: s for s in slots_for([event(at(0, 10), at(0, 12))])}  # S1 08:00-09:45, S2 12:15-22:00
    result = place_blocks(plan(("S2", 60, "HW4 part 1"), ("S2", 90, "HW4 part 2")), slots, "America/Los_Angeles", CONFIG)
    assert result.problems == []
    assert [(e.start.hour, e.start.minute, e.end.hour, e.end.minute) for e in result.events] == [(12, 15, 13, 15), (13, 30, 15, 0)]
    assert result.events[0].timezone == "America/Los_Angeles" and "Planned with TrojanClaw" in result.events[0].description


def test_any_broken_rule_drafts_nothing_and_explains_why():
    slots = {s.id: s for s in slots_for([event(at(0, 10), at(0, 12))])}
    due = (at(0, 9)).isoformat()
    result = place_blocks(plan(
        ("S1", 120, "Too long for S1"),                     # S1 has 105 min
        ("S9", 60, "Unknown slot"),
        ("S2", 20, "Too short"),
        ("S1", 90, "After the deadline", {"due": due}),     # would end 09:30, due 09:00
        ("S2", 60, "Fine"),
    ), slots, "America/Los_Angeles", CONFIG)
    assert result.events == []  # all or nothing
    text = " ".join(result.problems)
    assert "Block 1" in text and "Block 2" in text and "Block 3" in text and "Block 4" in text and "Block 5" not in text


def test_daily_study_limit_and_block_count_are_enforced():
    slots = {s.id: s for s in slots_for([])}  # one 14 hr slot
    result = place_blocks(plan(*[("S1", 180, f"Block {n}") for n in range(3)]), slots, "America/Los_Angeles", CONFIG)
    assert any("more than 360 min" in problem for problem in result.problems)
    many = place_blocks(plan(*[("S1", 30, f"B{n}") for n in range(13)]), slots, "America/Los_Angeles", CONFIG)
    assert any("at most 12 blocks" in problem for problem in many.problems)


def test_settings_file_is_validated():
    assert parse_scheduling({"study_hours": {"start": "09:30", "end": "20:00"}, "buffer_minutes": 10}).buffer_minutes == 10
    with pytest.raises(Exception):
        parse_scheduling({"study_hours": {"start": "22:00", "end": "08:00"}})


# ---- the tools inside chat (fake runtime, fake calendar)

def calendar_with(service, events):
    service.list_events = lambda user_id, query, timezone: {"calendar": "Primary calendar", "events": events, "limited": False}
    return service


def test_plan_my_week_drafts_every_block_for_one_approval(service):
    calendar_with(service, [event(at(0, 10), at(0, 12))])
    due = at(2, 23, 59).isoformat()

    async def script(number, kwargs, tools):
        found = json.loads(await tools["get_free_slots"].call({"start": at(0, 0).isoformat(), "end": at(1, 0).isoformat()}))
        assert [s["id"] for s in found["slots"]] == ["S1", "S2"] and found["slots"][1]["time"] == "12:15 PM – 10:00 PM"
        result = json.loads(await tools["propose_study_plan"].call({"blocks": [
            {"slot_id": "S1", "minutes": 90, "title": "Study: CSCI 571 HW4", "due": due},
            {"slot_id": "S2", "minutes": 120, "title": "Study: CSCI 599 reading", "due": due},
        ]}))
        assert result["drafted"] == 2 and result["total_minutes"] == 210
        return "HW4 first since it's due soonest, then the 599 reading."

    response = chat(FakeRuntime(script), service, "Plan my week around my deadlines", timezone="America/Los_Angeles")
    assert len(response.calendar_proposals) == 2 and all(p.status == "pending" for p in response.calendar_proposals)
    assert response.response.startswith("HW4 first since it's due soonest")
    assert "**2 study blocks** (3 hr 30 min in all)" in response.response and "Add all to Google Calendar" in response.response
    assert response.calendar_proposals[0].event.start == at(0, 8)
    service.request.assert_not_called()  # nothing was sent to Google


def test_plan_needs_free_slots_first_and_rejects_bad_blocks_without_saving(service):
    calendar_with(service, [])

    async def script(number, kwargs, tools):
        with pytest.raises(ToolError, match="get_free_slots first"):
            await tools["propose_study_plan"].call({"blocks": [{"slot_id": "S1", "minutes": 60, "title": "X"}]})
        await tools["get_free_slots"].call({"start": at(0, 0).isoformat(), "end": at(1, 0).isoformat()})
        with pytest.raises(ToolError, match="No blocks were drafted"):
            await tools["propose_study_plan"].call({"blocks": [{"slot_id": "S1", "minutes": 600, "title": "Too long"}]})
        assert service.repository.rows == {}
        return "Let me know your preferred hours."

    response = chat(FakeRuntime(script), service, "plan my week")
    assert response.calendar_proposals == [] and service.repository.rows == {}


def test_a_plan_is_the_only_card_in_its_reply(service):
    calendar_with(service, [])

    async def script(number, kwargs, tools):
        await tools["propose_calendar_event"].call(draft().model_dump(mode="json"))
        await tools["get_free_slots"].call({"start": at(0, 0).isoformat(), "end": at(1, 0).isoformat()})
        with pytest.raises(ToolError, match="already created"):
            await tools["propose_study_plan"].call({"blocks": [{"slot_id": "S1", "minutes": 60, "title": "Extra"}]})
        return "Review the event."

    assert len(chat(FakeRuntime(script), service, "Add a study session").calendar_proposals) == 1
