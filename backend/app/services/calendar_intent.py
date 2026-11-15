"""Checks explicit clock times before a model-generated draft can be persisted."""

import re
from zoneinfo import ZoneInfo

from app.errors import AppError
from app.models.calendar import CalendarEventDraft


def explicit_time_range(text: str) -> tuple[int, int] | None:
    # Only unambiguous ranges with AM/PM on both ends are enforced here.
    # Other wording remains the model's responsibility and still needs review.
    clock = r"(1[0-2]|0?[1-9])(?:[:.]([0-5]\d))?\s*([ap])\.?m\.?"
    matches = list(re.finditer(r"(?<![\w:.])" + clock + r"\s*(?:to|until|through|[-–—])\s*" + clock + r"(?!\w)", text, re.I))
    if len(matches) != 1:
        return None
    groups = matches[0].groups()

    def minutes(hour, minute, period):
        return (int(hour) % 12 + (12 if period.lower() == "p" else 0)) * 60 + int(minute or 0)

    return minutes(*groups[:3]), minutes(*groups[3:])


def validate_latest_times(event: CalendarEventDraft, latest_message: str) -> None:
    expected = explicit_time_range(latest_message)
    if expected is None:
        return
    zone = ZoneInfo(event.timezone)
    actual = tuple(value.astimezone(zone).hour * 60 + value.astimezone(zone).minute for value in (event.start, event.end))
    if actual != expected:
        raise AppError(
            "The proposed times do not match the latest user message. Read its start and end times again, "
            "including AM/PM and minutes (for example 3.20pm means 15:20). Do not reuse an earlier event's times. "
            "Call propose_calendar_event with the corrected event, or ask for clarification. No draft was saved.",
            "calendar_latest_times_mismatch", 400,
        )
