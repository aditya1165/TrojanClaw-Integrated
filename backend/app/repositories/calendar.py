"""Private backend-only calendar storage. All account queries are user-scoped."""

import json
from uuid import uuid4

from psycopg2.extras import RealDictCursor

from app.errors import AppError


class CalendarRepository:
    def __init__(self, database):
        self.database = database

    def query(self, sql, params=(), *, many=False):
        with self.database.connection() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql, params)
                if cursor.description:
                    return cursor.fetchall() if many else cursor.fetchone()

    def connection(self, user_id):
        return self.query("SELECT * FROM public.google_calendar_connections WHERE user_id = %s", (str(user_id),))

    def save_state(self, state_hash, user_id, session_hash, verifier):
        self.query("DELETE FROM public.google_calendar_oauth_states WHERE expires_at <= now() OR user_id = %s", (str(user_id),))
        self.query("INSERT INTO public.google_calendar_oauth_states VALUES (%s, %s, %s, %s, now() + interval '10 minutes')",
                   (state_hash, str(user_id), session_hash, verifier))

    def consume_state(self, state_hash, user_id, session_hash):
        return self.query("DELETE FROM public.google_calendar_oauth_states WHERE state_hash = %s AND user_id = %s AND session_hash = %s AND expires_at > now() RETURNING verifier",
                          (state_hash, str(user_id), session_hash))

    def disconnect(self, user_id):
        self.query("DELETE FROM public.google_calendar_connections WHERE user_id = %s", (str(user_id),))
        self.query("DELETE FROM public.google_calendar_oauth_states WHERE user_id = %s", (str(user_id),))

    def save_connection(self, user_id, calendar_id, label, encrypted_tokens):
        # Reconnection invalidates drafts tied to the previous Google account.
        with self.database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM public.google_calendar_connections WHERE user_id = %s", (str(user_id),))
                cursor.execute("INSERT INTO public.google_calendar_connections VALUES (%s, %s, %s, %s, %s)",
                               (str(user_id), str(uuid4()), calendar_id, label, encrypted_tokens))

    def update_tokens(self, connection_id, encrypted_tokens):
        self.query("UPDATE public.google_calendar_connections SET encrypted_tokens = %s WHERE id = %s", (encrypted_tokens, str(connection_id)))

    def create(self, user_id, connection_id, event):
        return self.query("INSERT INTO public.calendar_proposals (id, user_id, connection_id, event) VALUES (%s, %s, %s, %s::jsonb) RETURNING *",
                          (str(uuid4()), str(user_id), str(connection_id), json.dumps(event)))

    def list(self, user_id):
        return self.query("SELECT * FROM public.calendar_proposals WHERE user_id = %s AND (status = 'approved' OR expires_at > now()) ORDER BY created_at DESC LIMIT 30", (str(user_id),), many=True)

    def get(self, user_id, proposal_id):
        row = self.query("SELECT * FROM public.calendar_proposals WHERE user_id = %s AND id = %s", (str(user_id), str(proposal_id)))
        if not row:
            raise AppError("Calendar proposal not found.", "proposal_not_found", 404)
        return row

    def edit(self, user_id, proposal_id, version, event):
        row = self.query("UPDATE public.calendar_proposals SET event = %s::jsonb, version = version + 1 WHERE user_id = %s AND id = %s AND version = %s AND status = 'pending' AND expires_at > now() RETURNING *",
                         (json.dumps(event), str(user_id), str(proposal_id), version))
        return self._changed(row)

    def cancel(self, user_id, proposal_id, version):
        row = self.query("UPDATE public.calendar_proposals SET status = 'cancelled' WHERE user_id = %s AND id = %s AND version = %s AND status = 'pending' RETURNING *",
                         (str(user_id), str(proposal_id), version))
        return self._changed(row)

    def claim(self, user_id, proposal_id, version):
        # Commit explicit approval before making any Google write. A short lease
        # serializes attempts without keeping a DB transaction open over HTTP.
        row = self.query("""UPDATE public.calendar_proposals SET status = 'approved',
            approved_at = coalesce(approved_at, now()), processing_until = now() + interval '5 minutes'
            WHERE user_id = %s AND id = %s AND version = %s
            AND ((status = 'pending' AND expires_at > now()) OR status = 'approved')
            AND (processing_until IS NULL OR processing_until < now()) RETURNING *""",
                         (str(user_id), str(proposal_id), version))
        return self._changed(row)

    def release(self, user_id, proposal_id, *, conflict=False):
        self.query("""UPDATE public.calendar_proposals SET processing_until = NULL,
            status = CASE WHEN %s THEN 'pending' ELSE status END,
            approved_at = CASE WHEN %s THEN NULL ELSE approved_at END
            WHERE user_id = %s AND id = %s AND status = 'approved'""",
                   (conflict, conflict, str(user_id), str(proposal_id)))

    def applied(self, user_id, proposal_id, event_url):
        return self.query("UPDATE public.calendar_proposals SET status = 'applied', event_url = %s, processing_until = NULL WHERE user_id = %s AND id = %s AND status = 'approved' RETURNING *",
                          (event_url, str(user_id), str(proposal_id)))

    @staticmethod
    def _changed(row):
        if row is None:
            raise AppError("This proposal changed, expired, or is being saved. Refresh before trying again.", "proposal_conflict", 409)
        return row
