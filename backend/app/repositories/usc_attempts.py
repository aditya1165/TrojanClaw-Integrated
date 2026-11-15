"""Cross-worker, cookie-bound reservations for anonymous managed USC sign-in."""

from contextlib import contextmanager
from hashlib import sha256

from fastapi import HTTPException
from psycopg2.errors import UniqueViolation


class UscAttemptRepository:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def cursor(self, binding):
        digest = sha256(binding.encode()).hexdigest()
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.usc_attempt_owner', %s, true)", (digest,))
            yield cursor, digest

    def create(self, attempt_id, binding, payload):
        try:
            self._create(attempt_id, binding, payload)
        except UniqueViolation:
            raise HTTPException(409, "A USC sign-in is already open in this browser.") from None

    def current(self, binding):
        with self.cursor(binding) as (cursor, digest):
            cursor.execute("""SELECT id, encrypted_payload FROM public.remote_usc_attempts
                WHERE owner_hash = %s AND expires_at > now()""", (digest,))
            return cursor.fetchone()

    def _create(self, attempt_id, binding, payload):
        with self.cursor(binding) as (cursor, digest):
            # Only this browser's expired metadata is cleaned here. Browserbase's
            # hard timeout ends abandoned browsers; contexts require explicit cleanup.
            cursor.execute("DELETE FROM public.remote_usc_attempts WHERE owner_hash = %s AND expires_at <= now()", (digest,))
            cursor.execute("SELECT 1 FROM public.remote_usc_attempts WHERE owner_hash = %s AND expires_at > now()", (digest,))
            if cursor.fetchone():
                raise HTTPException(409, "A USC sign-in is already open in this browser. Finish or cancel it first.")
            cursor.execute("INSERT INTO public.remote_usc_attempts (id, owner_hash, encrypted_payload) VALUES (%s, %s, %s)",
                           (str(attempt_id), digest, payload))

    def claim(self, attempt_id, binding):
        with self.cursor(binding) as (cursor, digest):
            cursor.execute("""UPDATE public.remote_usc_attempts SET busy = true
                WHERE id = %s AND owner_hash = %s AND expires_at > now() AND busy = false
                RETURNING encrypted_payload""", (str(attempt_id), digest))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(409, "This USC sign-in expired, is busy, or belongs to another browser.")
            return row[0]

    def save(self, attempt_id, binding, payload):
        with self.cursor(binding) as (cursor, digest):
            cursor.execute("""UPDATE public.remote_usc_attempts SET encrypted_payload = %s, busy = false
                WHERE id = %s AND owner_hash = %s AND busy = true AND expires_at > now()""",
                           (payload, str(attempt_id), digest))
            if cursor.rowcount != 1:
                raise HTTPException(409, "This USC sign-in expired. Start again.")

    def unlock(self, attempt_id, binding):
        with self.cursor(binding) as (cursor, digest):
            cursor.execute("UPDATE public.remote_usc_attempts SET busy = false WHERE id = %s AND owner_hash = %s",
                           (str(attempt_id), digest))

    def checkpoint(self, attempt_id, binding, payload):
        with self.cursor(binding) as (cursor, digest):
            cursor.execute("""UPDATE public.remote_usc_attempts SET encrypted_payload = %s
                WHERE id = %s AND owner_hash = %s AND busy = true AND expires_at > now()""",
                           (payload, str(attempt_id), digest))
            if cursor.rowcount != 1:
                raise HTTPException(409, "This USC sign-in expired. Start again.")

    def delete(self, attempt_id, binding):
        with self.cursor(binding) as (cursor, digest):
            cursor.execute("DELETE FROM public.remote_usc_attempts WHERE id = %s AND owner_hash = %s", (str(attempt_id), digest))
