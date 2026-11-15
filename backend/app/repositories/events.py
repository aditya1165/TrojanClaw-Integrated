"""Shared public-event cache and account-scoped extension preferences."""

from datetime import datetime
from uuid import UUID

from psycopg2.extras import execute_values

from app.database import Database
from app.models.events import CampusEvent


SOURCE_ID = "engagesc_public"


class EventRepository:
    def __init__(self, database: Database):
        self.database = database

    def is_linked(self, user_id: UUID) -> bool:
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM public.integration_connections WHERE user_id = %s AND provider = 'engagesc' AND status = 'connected'", (str(user_id),))
            return cursor.fetchone() is not None

    def set_linked(self, user_id: UUID, linked: bool) -> None:
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO public.integration_connections (user_id, provider, status, last_synced_at)
                VALUES (%s, 'engagesc', %s, CASE WHEN %s THEN now() ELSE NULL END)
                ON CONFLICT (user_id, provider) DO UPDATE
                SET status = EXCLUDED.status, last_synced_at = EXCLUDED.last_synced_at, updated_at = now()
            """, (str(user_id), "connected" if linked else "not_configured", linked))

    def last_fetch(self) -> datetime | None:
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT fetched_at FROM public.campus_event_sources WHERE source_id = %s", (SOURCE_ID,))
            row = cursor.fetchone()
            return row[0] if row else None

    def replace(self, events: list[CampusEvent], fetched_at: datetime) -> None:
        # Replace only this source, atomically. Removed/cancelled events disappear.
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (SOURCE_ID,))
            cursor.execute("DELETE FROM public.campus_events WHERE source_id = %s", (SOURCE_ID,))
            if events:
                execute_values(cursor, """
                    INSERT INTO public.campus_events
                    (source_id, external_key, title, host, starts_at, ends_at, all_day, location, url, description, fetched_at)
                    VALUES %s
                """, [(
                    SOURCE_ID, event.external_key, event.title, event.host, event.starts_at,
                    event.ends_at, event.all_day, event.location, event.url, event.description, fetched_at,
                ) for event in events])
            cursor.execute("""
                INSERT INTO public.campus_event_sources (source_id, fetched_at, event_count) VALUES (%s, %s, %s)
                ON CONFLICT (source_id) DO UPDATE SET fetched_at = EXCLUDED.fetched_at, event_count = EXCLUDED.event_count
            """, (SOURCE_ID, fetched_at, len(events)))

    def list_events(self, start: datetime, end: datetime, search: str, limit: int) -> list[CampusEvent]:
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""
                SELECT external_key, title, host, starts_at, ends_at, all_day, location, url, description
                FROM public.campus_events
                WHERE source_id = %s AND COALESCE(ends_at, starts_at) >= %s AND starts_at < %s
                  AND (%s = '' OR strpos(lower(concat_ws(' ', title, host, description)), lower(%s)) > 0)
                ORDER BY starts_at, external_key LIMIT %s
            """, (SOURCE_ID, start, end, search, search, limit))
            return [CampusEvent(**dict(zip(
                ("external_key", "title", "host", "starts_at", "ends_at", "all_day", "location", "url", "description"), row,
            ))) for row in cursor.fetchall()]
