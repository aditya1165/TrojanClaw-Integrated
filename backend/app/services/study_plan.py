"""Study planning: free slots from the student's calendar, and checked multi-block plans.

Claude never computes times. `get_free_slots` reads the connected Google Calendar and returns
numbered free slots inside the study hours from config/scheduling.yaml (with a buffer around
existing events). `propose_study_plan` takes Claude's picks (slot ID, minutes, title, deadline),
places the blocks inside those slots and checks every one. If any block breaks a rule, nothing
is saved and the reasons go back to Claude. Each placed block becomes a normal calendar draft
(app/services/calendar.py), so approval keeps its free/busy re-check and idempotent save.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
import yaml

from app.config import ConfigurationError, get_settings
from app.models.calendar import CalendarEventDraft


@dataclass(frozen=True, slots=True)
class SchedulingConfig:
    day_start: time = time(8, 0)
    day_end: time = time(22, 0)
    buffer_minutes: int = 15
    min_block_minutes: int = 30
    max_block_minutes: int = 180
    max_study_minutes_per_day: int = 360
    max_blocks_per_plan: int = 12
    horizon_days: int = 14
    round_to_minutes: int = 15


def _clock(value: Any, name: str) -> time:
    try:
        hours, minutes = str(value).split(":")
        return time(int(hours), int(minutes))
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(f"config/scheduling.yaml: {name} must look like 08:00.") from exc


def parse_scheduling(raw: dict[str, Any]) -> SchedulingConfig:
    hours = raw.get("study_hours") or {}
    config = SchedulingConfig(
        day_start=_clock(hours.get("start", "08:00"), "study_hours.start"),
        day_end=_clock(hours.get("end", "22:00"), "study_hours.end"),
        **{key: int(raw[key]) for key in (
            "buffer_minutes", "min_block_minutes", "max_block_minutes", "max_study_minutes_per_day",
            "max_blocks_per_plan", "horizon_days", "round_to_minutes",
        ) if key in raw},
    )
    if not (config.day_start < config.day_end and 0 < config.min_block_minutes <= config.max_block_minutes
            and config.round_to_minutes > 0 and config.horizon_days > 0 and config.max_blocks_per_plan > 0):
        raise ConfigurationError("config/scheduling.yaml has an invalid combination of values.")
    return config


@lru_cache(maxsize=1)
def get_scheduling_config() -> SchedulingConfig:
    path = Path(get_settings().config_dir) / "config" / "scheduling.yaml"
    if not path.exists():
        return SchedulingConfig()
    return parse_scheduling(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


# ---------------------------------------------------------------- free slots

@dataclass(frozen=True, slots=True)
class Slot:
    id: str
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def _event_time(value: Any) -> datetime | None:
    """A timed event's start or end; None for all-day events (they don't block study time)."""
    if isinstance(value, dict):
        value = value.get("dateTime")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def busy_intervals(events: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    busy = []
    for event in events:
        start, end = _event_time(event.get("start")), _event_time(event.get("end"))
        if start and end and end > start:
            busy.append((start, end))
    return sorted(busy)


def _ceil(value: datetime, step: int) -> datetime:
    value = value.replace(second=0, microsecond=0)
    extra = value.minute % step
    return value + timedelta(minutes=step - extra) if extra else value


def _floor(value: datetime, step: int) -> datetime:
    value = value.replace(second=0, microsecond=0)
    return value - timedelta(minutes=value.minute % step)


def free_slots(
    busy: list[tuple[datetime, datetime]], window_start: datetime, window_end: datetime, zone: ZoneInfo,
    config: SchedulingConfig, *, now: datetime, earliest: time | None = None, latest: time | None = None,
    min_minutes: int | None = None,
) -> list[Slot]:
    """Free time inside the study hours of each day, outside busy times plus a buffer."""
    day_start = max(config.day_start, earliest) if earliest else config.day_start
    day_end = min(config.day_end, latest) if latest else config.day_end
    shortest = max(min_minutes or config.min_block_minutes, config.min_block_minutes)
    buffer = timedelta(minutes=config.buffer_minutes)
    blocked = [(start - buffer, end + buffer) for start, end in busy]
    slots: list[Slot] = []
    first, last = window_start.astimezone(zone).date(), window_end.astimezone(zone).date()
    for offset in range((last - first).days + 1):
        day: date = first + timedelta(days=offset)
        opens = max(datetime.combine(day, day_start, zone), window_start, now)
        closes = min(datetime.combine(day, day_end, zone), window_end)
        pieces = [(opens, closes)]
        for busy_start, busy_end in blocked:
            pieces = [part for start, end in pieces for part in (
                ((start, end),) if busy_end <= start or busy_start >= end else
                ((start, busy_start), (busy_end, end))
            ) if part[1] > part[0]]
        for start, end in pieces:
            start, end = _ceil(start, config.round_to_minutes), _floor(end, config.round_to_minutes)
            if (end - start) >= timedelta(minutes=shortest):
                slots.append(Slot(f"S{len(slots) + 1}", start, end))
    return slots


def describe_slots(slots: list[Slot], zone: ZoneInfo) -> list[dict[str, Any]]:
    def label(value: datetime) -> str:
        local = value.astimezone(zone)
        return f"{local.hour % 12 or 12}:{local:%M %p}"

    return [{
        "id": slot.id,
        "day": f"{slot.start.astimezone(zone):%a %b} {slot.start.astimezone(zone).day}",
        "time": f"{label(slot.start)} – {label(slot.end)}",
        "minutes": slot.minutes,
        "start": slot.start.isoformat(), "end": slot.end.isoformat(),
    } for slot in slots]


# ---------------------------------------------------------------- plans

class PlanBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    slot_id: str = Field(pattern=r"^S\d{1,4}$", description="A slot ID from get_free_slots, like S3.")
    minutes: int = Field(ge=5, le=600, description="Length of this study block in minutes.")
    title: str = Field(min_length=1, max_length=120, description='Short calendar title, like "Study: CSCI 571 HW4".')
    due: AwareDatetime | None = Field(default=None, description="The deadline this block works toward, if any (from the tools).")
    notes: str = Field(default="", max_length=600, description="One or two lines on what to do in this block.")


class StudyPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[PlanBlock] = Field(min_length=1, max_length=30)


@dataclass(slots=True)
class PlanResult:
    events: list[CalendarEventDraft] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def place_blocks(request: StudyPlanRequest, slots: dict[str, Slot], timezone: str, config: SchedulingConfig) -> PlanResult:
    """Place each block in its slot, in order, and check every rule. All or nothing."""
    zone = ZoneInfo(timezone)
    result = PlanResult()
    if len(request.blocks) > config.max_blocks_per_plan:
        result.problems.append(f"A plan can have at most {config.max_blocks_per_plan} blocks.")
    used: dict[str, datetime] = {}  # where the next block in a slot may start
    per_day: dict[date, int] = {}
    buffer = timedelta(minutes=config.buffer_minutes)
    for number, block in enumerate(request.blocks, 1):
        slot = slots.get(block.slot_id)
        if slot is None:
            result.problems.append(f"Block {number}: {block.slot_id} is not a free slot from get_free_slots in this turn.")
            continue
        if not config.min_block_minutes <= block.minutes <= config.max_block_minutes:
            result.problems.append(
                f"Block {number}: use {config.min_block_minutes}-{config.max_block_minutes} minutes, not {block.minutes}.")
            continue
        start = used[slot.id] + buffer if slot.id in used else slot.start
        end = start + timedelta(minutes=block.minutes)
        if end > slot.end:
            left = max(0, int((slot.end - start).total_seconds() // 60))
            result.problems.append(f"Block {number}: {block.minutes} min does not fit in {slot.id} ({left} min left there).")
            continue
        if block.due is not None and end > block.due:
            result.problems.append(f"Block {number}: it would end after its deadline ({block.due.astimezone(zone):%a %b %d %I:%M %p}).")
            continue
        day = start.astimezone(zone).date()
        if per_day.get(day, 0) + block.minutes > config.max_study_minutes_per_day:
            result.problems.append(
                f"Block {number}: more than {config.max_study_minutes_per_day} min of study on {day:%a %b %d}.")
            continue
        used[slot.id] = end
        per_day[day] = per_day.get(day, 0) + block.minutes
        description = block.notes
        if block.due is not None:
            due = block.due.astimezone(zone)
            description = (description + "\n\n" if description else "") + f"Due {due:%a %b} {due.day}, {due.hour % 12 or 12}:{due:%M %p}."
        result.events.append(CalendarEventDraft(
            title=block.title, start=start.astimezone(zone), end=end.astimezone(zone), timezone=timezone,
            description=(description + "\n\nPlanned with TrojanClaw.").strip(), location="",
        ))
    if result.problems:
        result.events = []
    return result


def parse_window(arguments: dict[str, Any], timezone: str, config: SchedulingConfig, now: datetime) -> tuple[datetime, datetime]:
    """The period to search: given start/end, or the next 7 days. At most `horizon_days`."""
    zone = ZoneInfo(timezone)
    try:
        start = datetime.fromisoformat(arguments["start"]) if arguments.get("start") else now
        end = datetime.fromisoformat(arguments["end"]) if arguments.get("end") else start + timedelta(days=7)
    except (TypeError, ValueError) as exc:
        raise ValueError("Use ISO date-times with a time zone for start and end.") from exc
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end need a time zone offset.")
    start, end = max(start, now).astimezone(zone), end.astimezone(zone)
    if end <= start:
        raise ValueError("end must be after start (and after now).")
    if end - start > timedelta(days=config.horizon_days):
        raise ValueError(f"Look at most {config.horizon_days} days ahead.")
    return start, end


FREE_SLOTS_TOOL = {
    "name": "get_free_slots",
    "description": (
        "Find free time for studying in the student's Google Calendar. Returns numbered free slots (S1, S2, ...) inside "
        "the student's study hours, with a buffer around existing events. Use it before propose_study_plan. Omit start/end "
        "for the next 7 days. earliest/latest narrow the hours of each day (HH:MM, 24-hour), for example when the student "
        "says 'only evenings'. Never compute free times yourself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "start": {"type": "string", "format": "date-time", "description": "Start of the period (ISO with offset)."},
            "end": {"type": "string", "format": "date-time", "description": "End of the period (ISO with offset)."},
            "earliest": {"type": "string", "pattern": "^\\d{2}:\\d{2}$", "description": "Earliest start each day, HH:MM."},
            "latest": {"type": "string", "pattern": "^\\d{2}:\\d{2}$", "description": "Latest end each day, HH:MM."},
            "min_minutes": {"type": "integer", "minimum": 15, "maximum": 600, "description": "Shortest slot to return."},
        },
        "additionalProperties": False,
    },
}

PLAN_TOOL = {
    "name": "propose_study_plan",
    "description": (
        "Draft a study plan of several calendar blocks in ONE call, after get_free_slots in this turn. Each block picks a "
        "slot ID and a length; blocks in the same slot are placed one after another with a short buffer. Give each block "
        "a short title (\"Study: CSCI 571 HW4\") and, when it works toward a deadline, that deadline as `due`, so the "
        "block is checked to end before it. If a block breaks a rule, nothing is drafted and you get the reasons: fix "
        "them and call again. This never saves to Google: the student reviews the plan and approves it."
    ),
    "input_schema": StudyPlanRequest.model_json_schema(),
}


def validation_message(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()[:5])
