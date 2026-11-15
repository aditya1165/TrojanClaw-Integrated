"""Google Calendar connection, drafts, and explicitly approved event creation."""

import base64
from datetime import datetime, timezone
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
from app.models.calendar import CalendarEventDraft, CalendarProposal, CalendarStatus
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
    "description": "Draft one event ONLY when the user requests a calendar addition. This never creates an event. Ask for missing title, date, start, end or duration, and timezone before calling. Never invent times. The user must review and click Approve in the app.",
    "input_schema": CalendarEventDraft.model_json_schema(),
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
        return CalendarProposal(id=row["id"], version=row["version"], event=row["event"],
                                status=row["status"], expires_at=row["expires_at"],
                                calendar=connection["calendar_label"], event_url=safe_event_url(row.get("event_url")))

    @staticmethod
    def future(event):
        if event.start <= datetime.now(timezone.utc):
            raise AppError("Choose a start time in the future.", "calendar_past_event", 400)

    def propose(self, user_id, event, conversation_id=None):
        self.future(event)
        connection = self.connection(user_id)
        row = self.repository.create(user_id, connection["id"], event.model_dump(mode="json"), conversation_id)
        return self.view(row, connection)

    def proposals(self, user_id, conversation_id=None):
        if not self.configured:
            return []
        connection = self.repository.connection(user_id)
        return [self.view(row, connection) for row in self.repository.list(user_id, conversation_id)] if connection else []

    def edit(self, user_id, proposal_id, version, event):
        self.future(event)
        connection = self.connection(user_id)
        return self.view(self.repository.edit(user_id, proposal_id, version, event.model_dump(mode="json")), connection)

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
                busy = self.check(self.request(connection, "POST", "/freeBusy", json={
                    "timeMin": event.start.isoformat(), "timeMax": event.end.isoformat(),
                    "items": [{"id": connection["calendar_id"]}],
                }))
                availability = busy.get("calendars", {}).get(connection["calendar_id"])
                if availability is None or availability.get("errors"):
                    raise AppError("Could not verify calendar availability. Please try again.", "calendar_unavailable", 502)
                if availability.get("busy"):
                    raise AppError("This time overlaps an existing event. Edit the proposal and review it again.", "calendar_busy", 409)
                body = {
                    "id": event_id, "summary": event.title, "description": event.description,
                    "location": event.location,
                    "start": {"dateTime": event.start.isoformat(), "timeZone": event.timezone},
                    "end": {"dateTime": event.end.isoformat(), "timeZone": event.timezone},
                    "reminders": {"useDefault": False},
                    "extendedProperties": {"private": {"trojanclaw_proposal": str(proposal_id)}},
                }
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


@lru_cache(maxsize=1)
def get_calendar_service():
    return GoogleCalendar(get_settings(), CalendarRepository(get_database()))
