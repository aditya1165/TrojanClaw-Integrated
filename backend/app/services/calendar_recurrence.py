"""Bounded recurrence expansion and canonical Google Calendar fields."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil import rrule

from app.errors import AppError


def recurrence_rule(event):
    recurrence = event.recurrence
    if recurrence is None:
        return None
    parts = [f"FREQ={recurrence.frequency}", f"INTERVAL={recurrence.interval}"]
    if recurrence.weekdays:
        parts.append("BYDAY=" + ",".join(recurrence.weekdays))
    if recurrence.count:
        parts.append(f"COUNT={recurrence.count}")
    else:
        until = datetime.combine(recurrence.until, time(23, 59, 59), ZoneInfo(event.timezone)).astimezone(timezone.utc)
        parts.append("UNTIL=" + until.strftime("%Y%m%dT%H%M%SZ"))
    return "RRULE:" + ";".join(parts)


def occurrences(event):
    start = event.start.astimezone(ZoneInfo(event.timezone))
    rule = recurrence_rule(event)
    starts = list(rrule.rrulestr(rule, dtstart=start)[:53]) if rule else [start]
    if not starts or len(starts) > 52 or starts[0] != start or starts[-1] > start + timedelta(days=366):
        raise AppError("Choose a recurrence starting on the first event date, with at most 52 occurrences within one year.", "calendar_invalid_recurrence", 400)
    duration = event.end.astimezone(ZoneInfo(event.timezone)) - start
    result = [(value, value + duration) for value in starts]
    for pair in result:
        for value in pair:
            # Reject wall times skipped by a DST transition instead of silently shifting.
            if value.astimezone(timezone.utc).astimezone(ZoneInfo(event.timezone)).replace(tzinfo=None) != value.replace(tzinfo=None):
                raise AppError("An occurrence falls in a daylight-saving clock change. Choose a different time.", "calendar_invalid_recurrence", 400)
            if value.replace(fold=0).utcoffset() != value.replace(fold=1).utcoffset():
                raise AppError("An occurrence has an ambiguous time during a daylight-saving clock change. Choose a different time.", "calendar_invalid_recurrence", 400)
    return result


def event_body(event):
    body = {"summary": event.title, "description": event.description, "location": event.location,
            "start": {"dateTime": event.start.isoformat(), "timeZone": event.timezone},
            "end": {"dateTime": event.end.isoformat(), "timeZone": event.timezone},
            "reminders": {"useDefault": False}}
    if event.reminder_minutes is not None:
        body["reminders"]["overrides"] = [{"method": "popup", "minutes": event.reminder_minutes}]
    rule = recurrence_rule(event)
    if rule:
        body["recurrence"] = [rule]
    return body
