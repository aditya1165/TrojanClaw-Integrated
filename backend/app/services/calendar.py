"""Google Calendar connection, drafts, and explicitly approved event creation."""

import base64
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from functools import lru_cache
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, quote, urlparse

from cryptography.fernet import Fernet, InvalidToken
import httpx

from app.config import ConfigurationError, get_settings
from app.database import get_database
from app.errors import AppError
from app.models.calendar import CalendarEventDraft, CalendarEventQuery, CalendarProposal, CalendarStatus, CalendarUpdateRequest
from app.services.calendar_recurrence import occurrences, event_body
from app.repositories.calendar import CalendarRepository
from app.services.auth import token_hash
from app.integrations.google_calendar import CalendarMCP


SCOPES = {
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
    "https://www.googleapis.com/auth/calendar.events.owned",
}
TOKEN_URL = "https://oauth2.googleapis.com/token"
PROPOSE_TOOL = {
    "name": "propose_calendar_event",
    "description": "Draft an event or bounded recurring series ONLY when requested. Include recurrence for repeated events and reminder_minutes for a popup notification (0 = at the event time). This never saves to Google. The user must review and click Approve.",
    "input_schema": CalendarEventDraft.model_json_schema(),
}
LIST_EVENTS_TOOL = {
    "name": "list_calendar_events",
    "description": "Read actual events from the user's connected primary Google Calendar, including events created outside chat. "
                   "Use whenever the user asks about their events, schedule, or calendar. No approval is needed to read. "
                   "Omit start/end for the next seven days. For today, tomorrow, or a specific week, supply explicit timezone-aware "
                   "start and exclusive end boundaries. Maximum 31 days per request. Never invent events or infer them from chat history.",
    "input_schema": CalendarEventQuery.model_json_schema(),
}
UPDATE_EVENT_TOOL = {
    "name": "propose_calendar_update",
    "description": "Draft changes to an existing timed Google Calendar event. First use list_calendar_events to find the exact event ID; never guess IDs. "
                   "Supply only changed fields. For recurring events, ask whether to edit one occurrence (event) or the entire series (series). "
                   "This reads the current event and creates a before/after review card; it does NOT change Google Calendar. Approval is required.",
    "input_schema": CalendarUpdateRequest.model_json_schema(),
}


def safe_event_url(value):
    parsed = urlparse(value or "")
    return value if parsed.scheme == "https" and parsed.hostname in {"calendar.google.com", "www.google.com"} else None


class GoogleCalendar:
    def __init__(self, settings, repository):
        self.settings = settings
        self.repository = repository
        self.mcp = CalendarMCP()

    @property
    def configured(self):
        return bool(self.settings.google_calendar_client_id and self.settings.google_calendar_client_secret
                    and self.settings.google_calendar_encryption_key)

    def cipher(self):
        if not self.configured:
            raise ConfigurationError("Google Calendar is not configured.")
        try:
            return Fernet(self.settings.google_calendar_encryption_key.encode())
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("GOOGLE_CALENDAR_ENCRYPTION_KEY must be a Fernet key.") from exc

    def encrypt(self, data):
        return self.cipher().encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, encrypted):
        try:
            return json.loads(self.cipher().decrypt(encrypted.encode()))
        except InvalidToken as exc:
            raise ConfigurationError("Cannot decrypt Google Calendar credentials. Restore the encryption key or reconnect.") from exc

    def _http(self, method, url, **kwargs):
        try:
            with httpx.Client(timeout=15) as client:
                return client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise AppError("Google Calendar could not be reached. Please try again.", "calendar_unavailable", 502) from exc

    @staticmethod
    def check(response):
        if response.status_code in {401, 403}:
            raise AppError("Google Calendar access failed. Reconnect and grant the requested permissions.", "calendar_reconnect", 409)
        if not response.is_success:
            raise AppError("Google Calendar could not finish the request. Please try again.", "calendar_unavailable", 502)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Expected a JSON object")
            return result
        except ValueError as exc:
            raise AppError("Google Calendar returned an unreadable response. Please try again.", "calendar_unavailable", 502) from exc

    def status(self, user_id):
        if not self.configured:
            return CalendarStatus(configured=False, connected=False)
        row = self.repository.connection(user_id)
        return CalendarStatus(configured=True, connected=bool(row), calendar=row["calendar_label"] if row else None)

    def start(self, user_id, session):
        self.cipher()
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        self.repository.save_state(token_hash(state), user_id, token_hash(session), verifier)
        return self.authorization_url(state, verifier)

    def authorization_url(self, state, verifier, *, redirect_uri=None, signin=False):
        self.cipher()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {
            "client_id": self.settings.google_calendar_client_id,
            "redirect_uri": redirect_uri or self.settings.google_calendar_redirect_uri,
            "response_type": "code", "scope": " ".join(sorted(SCOPES | ({"openid", "email"} if signin else set()))),
            "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }
        if signin:
            params["hd"] = "usc.edu"
            params["prompt"] = "select_account consent"
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    def callback(self, user_id, session, state, code, error=None):
        self.cipher()
        saved = self.repository.consume_state(token_hash(state), user_id, token_hash(session))
        if not saved:
            raise AppError("The calendar connection expired. Start connecting again.", "calendar_state_invalid", 400)
        if error or not code:
            raise AppError("Google Calendar was not connected. You can try again from Sources.", "calendar_consent_denied", 400)
        tokens = self.exchange(code, saved["verifier"])
        self.complete_connection(user_id, tokens)

    def exchange(self, code, verifier, *, redirect_uri=None):
        tokens = self.check(self._http("POST", TOKEN_URL, data={
            "client_id": self.settings.google_calendar_client_id,
            "client_secret": self.settings.google_calendar_client_secret,
            "redirect_uri": redirect_uri or self.settings.google_calendar_redirect_uri,
            "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
        }))
        if not SCOPES.issubset(set(tokens.get("scope", "").split())) or not tokens.get("refresh_token"):
            raise AppError("Reconnect and allow the requested calendar permissions.", "calendar_scopes_missing", 400)
        tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600)
        return tokens

    def complete_connection(self, user_id, tokens):
        info = self.mcp.verify(tokens["access_token"])
        label = info.get("summary", "Primary Google calendar")
        self.repository.save_connection(user_id, info["id"], label, self.encrypt(tokens))

    def google_identity(self, tokens):
        identity = self.check(self._http("GET", "https://openidconnect.googleapis.com/v1/userinfo",
                                         headers={"Authorization": "Bearer " + tokens["access_token"]}))
        email = str(identity.get("email", "")).lower()
        if (identity.get("email_verified") is not True or identity.get("hd") != "usc.edu"
                or not email.endswith("@usc.edu") or not identity.get("sub")):
            raise AppError("Use your USC-managed Google account ending in @usc.edu.", "google_usc_account_required", 403)
        return email

    def connection(self, user_id):
        row = self.repository.connection(user_id)
        if not row:
            raise AppError("Connect Google Calendar from Sources first.", "calendar_not_connected", 409)
        return row

    def request(self, connection, method, path, **kwargs):
        tokens = self.decrypt(connection["encrypted_tokens"])
        if tokens.get("expires_at", 0) < time.time() + 60:
            refreshed = self._http("POST", TOKEN_URL, data={
                "client_id": self.settings.google_calendar_client_id,
                "client_secret": self.settings.google_calendar_client_secret,
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            })
            if refreshed.status_code == 400:
                raise AppError("Google Calendar access expired. Reconnect to continue.", "calendar_reconnect", 409)
            fresh = self.check(refreshed)
            tokens.update(fresh)
            tokens["expires_at"] = time.time() + fresh.get("expires_in", 3600)
            connection["encrypted_tokens"] = self.encrypt(tokens)
            self.repository.update_tokens(connection["id"], connection["encrypted_tokens"])
        return self.mcp.request(tokens["access_token"], method, path, **kwargs)

    def view(self, row, connection):
        stored = row["event"]
        target = stored.get("target", {})
        draft = CalendarEventDraft.model_validate(stored.get("draft", stored))
        return CalendarProposal(id=row["id"], version=row["version"], event=draft,
                                status=row["status"], expires_at=row["expires_at"],
                                calendar=connection["calendar_label"], event_url=safe_event_url(row.get("event_url")),
                                operation=stored.get("operation", "create"), before=target.get("before"), scope=target.get("scope", "event"),
                                recurrence_changed=target.get("recurrence_changed", False), reminder_changed=target.get("reminder_changed", False),
                                occurrences=[{"start": start.isoformat(), "end": end.isoformat()} for start, end in occurrences(draft)] if draft.recurrence else [])

    @staticmethod
    def future(event):
        if event.start <= datetime.now(timezone.utc):
            raise AppError("Choose a start time in the future.", "calendar_past_event", 400)

    def propose(self, user_id, event, conversation_id=None):
        self.future(event)
        occurrences(event)
        connection = self.connection(user_id)
        row = self.repository.create(user_id, connection["id"], event.model_dump(mode="json"), conversation_id)
        return self.view(row, connection)

    def proposals(self, user_id, conversation_id=None):
        if not self.configured:
            return []
        connection = self.repository.connection(user_id)
        return [self.view(row, connection) for row in self.repository.list(user_id, conversation_id)] if connection else []

    def list_events(self, user_id, query: CalendarEventQuery, timezone_name: str):
        connection = self.connection(user_id)
        start = query.start or datetime.now(ZoneInfo(timezone_name)).replace(microsecond=0)
        end = query.end or start + timedelta(days=7)
        path = "/calendars/" + quote(connection["calendar_id"], safe="") + "/events"
        data = self.check(self.request(connection, "GET", path, params={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "timeZone": timezone_name,
        }))
        if not isinstance(data.get("events"), list):
            raise AppError("Google Calendar returned an incomplete event list. Please retry.", "calendar_mcp_response", 502)
        # Calendar text is untrusted source content. Return only schedule fields.
        events = [{key: item[key] for key in ("id", "summary", "start", "end", "location", "recurringEventId") if key in item}
                  for item in data["events"] if item.get("status") != "cancelled"]
        return {"calendar": connection["calendar_label"], "start": start.isoformat(), "end": end.isoformat(),
                "timezone": timezone_name, "events": events, "limited": bool(data.get("limited")),
                "note": "Only the connected primary calendar was checked. All-day event end dates are exclusive."}

    def propose_update(self, user_id, request: CalendarUpdateRequest, timezone_name, latest_message="", conversation_id=None):
        connection = self.connection(user_id)
        path = "/calendars/" + quote(connection["calendar_id"], safe="") + "/events/"
        event_id = request.event_id
        original = self.check(self.request(connection, "GET", path + event_id))
        if request.scope == "series" and original.get("recurringEventId"):
            event_id = original["recurringEventId"]
            original = self.check(self.request(connection, "GET", path + quote(event_id, safe="")))
        if request.scope == "series" and not original.get("recurrence"):
            raise AppError("This is a single event. Choose event scope instead of an entire series.", "calendar_choose_occurrence", 400)
        if original.get("recurringEventId") and "recurrence" in request.changes.model_fields_set:
            raise AppError("Choose the entire series to change its repeat rule.", "calendar_choose_occurrence", 400)
        if original.get("recurrence") and request.scope != "series":
            raise AppError("Select a dated occurrence from the event list to change just that occurrence, or choose the entire series.", "calendar_choose_occurrence", 400)
        if original.get("status") == "cancelled" or not original.get("etag"):
            raise AppError("This event cannot be safely edited. Refresh its details and try again.", "calendar_event_changed", 409)
        if not original.get("start", {}).get("dateTime") or not original.get("end", {}).get("dateTime"):
            raise AppError("Editing all-day events is not supported yet. Choose a timed event.", "calendar_all_day_edit", 400)
        base = {"title": original.get("summary") or "Untitled event", "start": original["start"]["dateTime"],
                "end": original["end"]["dateTime"], "timezone": original["start"].get("timeZone") or timezone_name,
                "description": original.get("description") or "", "location": original.get("location") or ""}
        changes = request.changes.model_dump(exclude_unset=True)
        event = CalendarEventDraft.model_validate({**base, **changes})
        from app.services.calendar_intent import validate_latest_times
        validate_latest_times(event, latest_message)
        if "start" in changes or "end" in changes:
            self.future(event)
        occurrences(event)
        before = {key: original[key] for key in ("summary", "start", "end", "description", "location", "recurrence", "reminders") if key in original}
        stored = {"operation": "update", "draft": event.model_dump(mode="json"), "target": {
            "id": event_id, "etag": original["etag"], "before": before, "scope": request.scope,
            "recurrence_changed": "recurrence" in changes, "reminder_changed": "reminder_minutes" in changes}}
        return self.view(self.repository.create(user_id, connection["id"], stored, conversation_id), connection)

    def edit(self, user_id, proposal_id, version, event):
        connection = self.connection(user_id)
        row = self.repository.get(user_id, proposal_id)
        stored = row["event"]
        if stored.get("operation") == "update":
            previous = CalendarEventDraft.model_validate(stored["draft"])
            if event.start != previous.start or event.end != previous.end:
                self.future(event)
            stored["target"]["recurrence_changed"] |= event.recurrence != previous.recurrence
            stored["target"]["reminder_changed"] |= event.reminder_minutes != previous.reminder_minutes
            stored["draft"] = event.model_dump(mode="json")
        else:
            self.future(event)
            stored = event.model_dump(mode="json")
        occurrences(event)
        return self.view(self.repository.edit(user_id, proposal_id, version, stored), connection)

    def cancel(self, user_id, proposal_id, version):
        connection = self.connection(user_id)
        return self.view(self.repository.cancel(user_id, proposal_id, version), connection)

    def approve(self, user_id, proposal_id, version):
        connection = self.connection(user_id)
        row = self.repository.get(user_id, proposal_id)
        if row["version"] != version or str(row["connection_id"]) != str(connection["id"]):
            raise AppError("The proposal changed. Review it again.", "proposal_conflict", 409)
        if row["status"] == "applied":
            return self.view(row, connection)
        if row["event"].get("operation") == "update":
            return self._approve_update(user_id, proposal_id, version, row, connection)
        if row["status"] == "pending":
            self.future(CalendarEventDraft.model_validate(row["event"]))
        row = self.repository.claim(user_id, proposal_id, version)
        event_id = "tc" + str(proposal_id).replace("-", "")
        path = "/calendars/" + quote(connection["calendar_id"], safe="") + "/events"
        confirmed_absent = False
        checked_absent = False
        try:
            # Recover a successful write whose response/DB update was lost.
            existing = self.request(connection, "GET", path + "/" + event_id)
            if existing.status_code == 404:
                confirmed_absent = True
                checked_absent = True
                event = CalendarEventDraft.model_validate(row["event"])
                self.future(event)
                slots = occurrences(event)
                busy = self.check(self.request(connection, "POST", "/freeBusy", json={
                    "timeMin": slots[0][0].isoformat(), "timeMax": slots[-1][1].isoformat(),
                    "items": [{"id": connection["calendar_id"]}],
                }))
                availability = busy.get("calendars", {}).get(connection["calendar_id"])
                if availability is None or availability.get("errors") or not isinstance(availability.get("busy"), list):
                    raise AppError("Could not verify calendar availability. Please try again.", "calendar_unavailable", 502)
                try:
                    overlaps = any(datetime.fromisoformat(block["start"].replace("Z", "+00:00")) < end and datetime.fromisoformat(block["end"].replace("Z", "+00:00")) > start
                                   for block in availability.get("busy", []) for start, end in slots)
                except (KeyError, ValueError, TypeError) as exc:
                    raise AppError("Could not verify every occurrence's availability. Please retry.", "calendar_unavailable", 502) from exc
                if overlaps:
                    raise AppError("This time overlaps an existing event. Edit the proposal and review it again.", "calendar_busy", 409)
                body = {**event_body(event), "id": event_id,
                        "extendedProperties": {"private": {"trojanclaw_proposal": str(proposal_id)}}}
                confirmed_absent = False
                existing = self.request(connection, "POST", path, json=body)
                if existing.status_code == 409:
                    existing = self.request(connection, "GET", path + "/" + event_id)
            result = self.check(existing)
            if result.get("status") == "cancelled" or result.get("extendedProperties", {}).get("private", {}).get("trojanclaw_proposal") != str(proposal_id):
                raise AppError("The calendar event was changed outside TrojanClaw. Check Google Calendar before retrying.", "calendar_event_changed", 409)
            saved = self.repository.applied(user_id, proposal_id, safe_event_url(result.get("htmlLink")))
            if not saved:
                raise AppError("Check Google Calendar to confirm the save before reconnecting.", "proposal_conflict", 409)
            return self.view(saved, connection)
        except Exception as exc:
            # Preserve approved content after uncertain network outcomes. Retries
            # use the same event ID; editing/cancelling cannot hide a possible write.
            rejected_before_write = checked_absent and isinstance(exc, AppError) and exc.code == "calendar_mcp_schema"
            self.repository.release(user_id, proposal_id, conflict=confirmed_absent or rejected_before_write)
            raise

    def _approve_update(self, user_id, proposal_id, version, row, connection):
        row = self.repository.claim(user_id, proposal_id, version)
        target = row["event"]["target"]
        event = CalendarEventDraft.model_validate(row["event"]["draft"])
        path = "/calendars/" + quote(connection["calendar_id"], safe="") + "/events/" + quote(target["id"], safe="")
        before_write = True
        try:
            current = self.check(self.request(connection, "GET", path))
            marker = current.get("extendedProperties", {}).get("private", {}).get("trojanclaw_proposal")
            if marker == str(proposal_id):
                before_write = False  # A previous attempt already changed Google.
            if marker != str(proposal_id):
                if current.get("etag") != target["etag"] or current.get("status") == "cancelled":
                    raise AppError("This event changed since you reviewed it. Ask for a fresh edit proposal.", "calendar_event_changed", 409)
                body = event_body(event)
                if not target["recurrence_changed"]:
                    body.pop("recurrence", None)
                elif event.recurrence is None:
                    body["recurrence"] = []
                if not target["reminder_changed"]:
                    body.pop("reminders", None)
                properties = current.get("extendedProperties") or {}
                body["extendedProperties"] = {**properties, "private": {**properties.get("private", {}), "trojanclaw_proposal": str(proposal_id)}}
                before_write = False
                current = self.check(self.request(connection, "PATCH", path, json=body,
                                                   expected_etag=target["etag"], approved_proposal=str(proposal_id)))
                if current.get("extendedProperties", {}).get("private", {}).get("trojanclaw_proposal") != str(proposal_id):
                    raise AppError("Could not confirm the calendar update. Retry to check its result.", "calendar_update_uncertain", 502)
            saved = self.repository.applied(user_id, proposal_id, safe_event_url(current.get("htmlLink")))
            return self.view(saved, connection)
        except Exception as exc:
            self.repository.release(user_id, proposal_id, conflict=before_write or (isinstance(exc, AppError) and exc.code == "calendar_event_changed"))
            raise


@lru_cache(maxsize=1)
def get_calendar_service():
    return GoogleCalendar(get_settings(), CalendarRepository(get_database()))
