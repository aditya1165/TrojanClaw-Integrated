"""Account-scoped encrypted connector records; database locks coordinate workers."""

from contextlib import contextmanager
from uuid import UUID, uuid4

from app.database import Database
from app.errors import AppError


COLUMNS = "provider, state, encrypted_session, encrypted_login, operation_id, login_expires_at"


def record(row):
    if not row:
        return None
    return dict(zip(("provider", "state", "session", "login", "operation_id", "expires_at"), row))


class ConnectorRepository:
    def __init__(self, database: Database):
        self.database = database

    @contextmanager
    def cursor(self, user_id: UUID):
        with self.database.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.connector_user_id', %s, true)", (str(user_id),))
            yield cursor

    def list(self, user_id: UUID) -> list[dict]:
        with self.cursor(user_id) as cursor:
            cursor.execute("""UPDATE public.user_connector_sessions
                SET state = 'needs_login', encrypted_login = NULL, operation_id = NULL,
                    login_expires_at = NULL, updated_at = now()
                WHERE user_id = %s AND state = 'connecting' AND login_expires_at <= now()""", (str(user_id),))
            cursor.execute(f"SELECT {COLUMNS} FROM public.user_connector_sessions WHERE user_id = %s", (str(user_id),))
            return [record(row) for row in cursor.fetchall()]

    def get(self, user_id: UUID, provider: str) -> dict | None:
        with self.cursor(user_id) as cursor:
            cursor.execute(f"SELECT {COLUMNS} FROM public.user_connector_sessions WHERE user_id = %s AND provider = %s", (str(user_id), provider))
            return record(cursor.fetchone())

    def reserve(self, user_id: UUID, provider: str) -> dict:
        with self.cursor(user_id) as cursor:
            cursor.execute("INSERT INTO public.user_connector_sessions (user_id, provider) VALUES (%s, %s) ON CONFLICT DO NOTHING", (str(user_id), provider))
            cursor.execute(f"""UPDATE public.user_connector_sessions
                SET state = 'connecting', operation_id = %s, encrypted_login = NULL,
                    login_expires_at = now() + interval '10 minutes', updated_at = now()
                WHERE user_id = %s AND provider = %s AND state <> 'revoking'
                  AND (state <> 'connecting' OR login_expires_at <= now())
                RETURNING {COLUMNS}""", (str(uuid4()), str(user_id), provider))
            result = record(cursor.fetchone())
            if not result:
                raise AppError("A sign-in or disconnect is already in progress.", "connector_busy", 409)
            return result

    def attach(self, user_id: UUID, provider: str, operation_id: UUID, session: bytes, login: bytes) -> None:
        self._change(user_id, provider, operation_id,
                     "encrypted_session = %s, encrypted_login = %s", (session, login), active=True)

    def complete(self, user_id: UUID, provider: str, operation_id: UUID, session: bytes) -> None:
        self._change(user_id, provider, operation_id,
                     "state = 'connected', encrypted_session = %s, encrypted_login = NULL, operation_id = NULL, login_expires_at = NULL",
                     (session,), active=True)

    def cancel(self, user_id: UUID, provider: str, operation_id: UUID) -> None:
        self._change(user_id, provider, operation_id,
                     "state = 'needs_login', encrypted_login = NULL, operation_id = NULL, login_expires_at = NULL", ())

    def mark_needs_login(self, user_id: UUID, provider: str, session: bytes) -> None:
        with self.cursor(user_id) as cursor:
            cursor.execute("""UPDATE public.user_connector_sessions SET state = 'needs_login', updated_at = now()
                WHERE user_id = %s AND provider = %s AND encrypted_session = %s AND state = 'connected'""",
                (str(user_id), provider, session))

    def revoke(self, user_id: UUID, provider: str) -> dict | None:
        with self.cursor(user_id) as cursor:
            cursor.execute(f"""UPDATE public.user_connector_sessions
                SET state = 'revoking', operation_id = %s, updated_at = now()
                WHERE user_id = %s AND provider = %s RETURNING {COLUMNS}""",
                (str(uuid4()), str(user_id), provider))
            return record(cursor.fetchone())

    def delete(self, user_id: UUID, provider: str, operation_id: UUID) -> None:
        with self.cursor(user_id) as cursor:
            cursor.execute("DELETE FROM public.user_connector_sessions WHERE user_id = %s AND provider = %s AND operation_id = %s AND state = 'revoking'",
                           (str(user_id), provider, str(operation_id)))

    def _change(self, user_id, provider, operation_id, assignments, values, active=False):
        # assignments is always an internal literal, never supplied by the caller.
        condition = " AND state = 'connecting' AND login_expires_at > now()" if active else " AND state = 'connecting'"
        with self.cursor(user_id) as cursor:
            cursor.execute(f"UPDATE public.user_connector_sessions SET {assignments}, updated_at = now() WHERE user_id = %s AND provider = %s AND operation_id = %s{condition}",
                           (*values, str(user_id), provider, str(operation_id)))
            if cursor.rowcount != 1:
                raise AppError("This sign-in has expired or was replaced. Start again.", "login_expired", 409)
