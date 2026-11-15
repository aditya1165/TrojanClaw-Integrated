from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4
import os

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from app.main import app
from app.models.events import CampusEventList
from app.services import engagesc
from app.services.engagesc import EngageSCService, get_engagesc_service, parse_public_calendar
from app.errors import DependencyUnavailableError
from app.database import get_database


NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def calendar(*events: str) -> bytes:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(
        "BEGIN:VEVENT\r\n" + event.replace("\n", "\r\n") + "\r\nEND:VEVENT\r\n" for event in events
    ) + "END:VCALENDAR\r\n").encode()


EVENT = "UID:event-1\nSUMMARY:Public event\nDTSTART:20261115T180000Z\nDTEND:20261115T190000Z\nURL:https://engage.usc.edu/rsvp?id=123"


def test_parser_unfolds_text_and_preserves_private_location():
    feed = calendar(EVENT + '\nLOCATION:Sign in to download the location\nORGANIZER;CN="Test Club":mailto:noreply@engage.usc.edu\nDESCRIPTION:Hello\\, world\\nA long\n  description')
    events = parse_public_calendar(feed, NOW)
    assert len(events) == 1
    event = events[0]
    assert event.host == "Test Club"
    assert event.description == "Hello, world\nA long description"
    assert "after sign-in" in event.location
    assert event.starts_at.utcoffset() == timedelta(0)


def test_parser_handles_floating_time_all_day_and_ongoing_events():
    feed = calendar(
        "UID:all-day\nSUMMARY:All day\nDTSTART;VALUE=DATE:20261115\nDTEND;VALUE=DATE:20261116",
        "UID:local\nSUMMARY:Local time\nDTSTART:20261115T100000",
        "UID:ongoing\nSUMMARY:Ongoing\nDTSTART:20261113T100000Z\nDTEND:20261116T100000Z",
    )
    events = {event.external_key: event for event in parse_public_calendar(feed, NOW)}
    assert events["all-day"].all_day
    assert events["local"].starts_at.hour == 18
    assert "ongoing" in events


def test_parser_drops_cancelled_past_invalid_and_duplicate_events():
    feed = calendar(EVENT, EVENT, EVENT.replace("event-1", "cancelled") + "\nSTATUS:CANCELLED",
                    "UID:old\nSUMMARY:Past\nDTSTART:20251115T180000Z",
                    "UID:bad\nSUMMARY:Broken\nDTSTART:not-a-date")
    events = parse_public_calendar(feed, NOW)
    assert [event.external_key for event in events] == ["event-1"]


def test_parser_sanitizes_html_and_unsafe_links():
    feed = calendar(EVENT.replace("https://engage.usc.edu/rsvp?id=123", "javascript:alert(1)") + "\nDESCRIPTION:<b>Hello</b> &amp; welcome")
    event = parse_public_calendar(feed, NOW)[0]
    assert event.description == "Hello & welcome"
    assert event.url == engagesc.PUBLIC_URL


def test_parser_rejects_login_pages_and_unexpanded_recurrences():
    with pytest.raises(ValueError):
        parse_public_calendar(b"<html>Sign in</html>", NOW)
    with pytest.raises(ValueError):
        parse_public_calendar(calendar(EVENT + "\nRRULE:FREQ=DAILY"), NOW)


def test_empty_feed_is_valid():
    assert parse_public_calendar(calendar(), NOW) == []


@pytest.mark.parametrize("redirect,allowed", [
    ("https://static-prod-us-east-1.campusgroups.com:443/ical/usc/ical_usc.ics", True),
    ("http://127.0.0.1/private", False),
    ("https://engage.usc.edu/private", False),
    ("https://evil.example/ical/usc/ical_usc.ics", False),
])
def test_fetch_follows_only_the_public_calendar_redirect(monkeypatch, redirect, allowed):
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"location": redirect})
        return httpx.Response(200, content=calendar(EVENT))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(engagesc.httpx, "Client", lambda **kwargs: client)
    if allowed:
        assert engagesc.fetch_public_calendar() == calendar(EVENT)
        assert len(requests) == 2
    else:
        with pytest.raises(ValueError):
            engagesc.fetch_public_calendar()
        assert len(requests) == 1


def test_fresh_cache_avoids_external_fetch(monkeypatch):
    repository = MagicMock()
    repository.last_fetch.return_value = datetime.now(timezone.utc)
    fetch = MagicMock(side_effect=AssertionError("Unexpected external fetch"))
    monkeypatch.setattr(engagesc, "fetch_public_calendar", fetch)
    _, stale = EngageSCService(repository).refresh()
    assert not stale
    fetch.assert_not_called()


def test_fetch_failure_preserves_cache_and_backs_off(monkeypatch):
    repository = MagicMock()
    repository.last_fetch.return_value = datetime.now(timezone.utc) - timedelta(hours=2)
    fetch = MagicMock(side_effect=httpx.ConnectError("Unavailable"))
    monkeypatch.setattr(engagesc, "fetch_public_calendar", fetch)
    service = EngageSCService(repository)
    assert service.refresh()[1]
    assert service.refresh()[1]
    assert fetch.call_count == 1
    repository.replace.assert_not_called()


def test_first_fetch_failure_does_not_claim_connection(monkeypatch):
    repository = MagicMock()
    repository.last_fetch.return_value = None
    monkeypatch.setattr(engagesc, "fetch_public_calendar", MagicMock(side_effect=httpx.ConnectError("Unavailable")))
    with pytest.raises(DependencyUnavailableError):
        EngageSCService(repository).connect(UUID(int=2))
    repository.set_linked.assert_not_called()


def test_events_require_linked_account():
    repository = MagicMock()
    repository.is_linked.return_value = False
    service = EngageSCService(repository)
    with pytest.raises(HTTPException) as exc:
        service.events(UUID(int=2))
    assert exc.value.status_code == 409
    assert service.prompt_context(UUID(int=2)) == ""
    repository.list_events.assert_not_called()


def test_copilot_uses_event_context_for_the_authenticated_user():
    from app.models.chat import ChatRequest
    from app.services.chat import ChatService

    documents = MagicMock()
    documents.search.return_value = []
    language_model = MagicMock()
    language_model.respond.return_value = "Here are public events."
    campus = MagicMock()
    campus.prompt_context.return_value = "Public EngageSC event: Robotics workshop"
    service = ChatService(None, MagicMock(), documents, language_model, campus)
    service.chat(ChatRequest(messages=[{"role": "user", "content": "Any upcoming campus events?"}]), user_id=UUID(int=2))
    campus.prompt_context.assert_called_once_with(UUID(int=2))
    assert "Robotics workshop" in language_model.respond.call_args.args[1]
    assert documents.search.call_args.kwargs["user_id"] == UUID(int=2)


def test_routes_require_login():
    with TestClient(app) as client:
        assert client.get("/integrations/engagesc").status_code == 401
        assert client.get("/integrations/engagesc/events").status_code == 401
        assert client.post("/integrations/engagesc/connect", json={}).status_code == 401


def test_routes_scope_link_and_queries_to_session(signed_in_account):
    service = MagicMock()
    service.events.return_value = CampusEventList(events=[], fetched_at=NOW)
    app.dependency_overrides[get_engagesc_service] = lambda: service
    try:
        with TestClient(app) as client:
            assert client.post("/integrations/engagesc/connect", json={"user_id": str(UUID(int=99))}).json()["connected"]
            service.connect.assert_called_once_with(UUID(int=2))
            assert client.get("/integrations/engagesc/events?days=7&search=robotics&limit=10").status_code == 200
            service.events.assert_called_once_with(UUID(int=2), 7, "robotics", 10)
            assert client.get("/integrations/engagesc/events?days=999").status_code == 422
            assert client.post("/integrations/engagesc/disconnect", headers={"Origin": "https://evil.example"}).status_code == 403
            assert client.post("/integrations/engagesc/disconnect", json={}).json()["connected"] is False
            service.repository.set_linked.assert_called_once_with(UUID(int=2), False)
    finally:
        app.dependency_overrides.pop(get_engagesc_service, None)


@pytest.mark.skipif(os.getenv("RUN_ENGAGESC_DB_TESTS") != "1", reason="Requires migrated database and public EngageSC network access")
def test_live_public_feed_link_search_and_account_isolation():
    usernames = ["event_test_" + uuid4().hex[:16] for _ in range(2)]
    database = get_database()
    try:
        with TestClient(app) as client:
            for username in usernames:
                response = client.post("/auth/register", json={
                    "name": "Temporary Events Test", "username": username,
                    "password": "temporary-event-password", "confirm_password": "temporary-event-password",
                })
                assert response.status_code == 201, response.text
                assert client.get("/integrations/engagesc").json()["connected"] is False
                assert client.get("/integrations/engagesc/events").status_code == 409
                if username == usernames[0]:
                    response = client.post("/integrations/engagesc/connect", json={})
                    assert response.status_code == 200, response.text
                    response = client.get("/integrations/engagesc/events?days=90&limit=100")
                    assert response.status_code == 200, response.text
                    result = response.json()
                    assert not result["stale"]
                    assert result["events"], "Expected upcoming events in the live feed"
                    first_title = result["events"][0]["title"]
                    response = client.get("/integrations/engagesc/events", params={"days": 90, "search": first_title, "limit": 100})
                    assert response.status_code == 200
                    assert any(event["title"] == first_title for event in response.json()["events"])
                    print(f"Verified live feed: {len(result['events'])} events displayed (limit 100).")
                client.post("/auth/logout")
            assert client.post("/auth/login", json={"username": usernames[0], "password": "temporary-event-password"}).status_code == 200
            assert client.get("/integrations/engagesc").json()["connected"] is True
            assert client.post("/integrations/engagesc/disconnect", json={}).json()["connected"] is False
            assert client.get("/integrations/engagesc/events").status_code == 409
            with database.connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT has_table_privilege('anon', 'public.campus_event_sources', 'SELECT'), has_table_privilege('authenticated', 'public.campus_events', 'SELECT')")
                assert cursor.fetchone() == (False, False)
    finally:
        with database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM public.app_users WHERE username = ANY(%s)", (usernames,))
