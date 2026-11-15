"""Lazy, bounded PostgreSQL connection pool."""

from contextlib import contextmanager
from functools import lru_cache
from threading import BoundedSemaphore, Lock
from typing import Iterator

from psycopg2.extensions import connection
from psycopg2.pool import PoolError, ThreadedConnectionPool
import psycopg2

from app.config import ConfigurationError, Settings, get_settings


POOL_WAIT_SECONDS = 10


class Database:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._pool: ThreadedConnectionPool | None = None
        self._lock = Lock()
        # psycopg2's pool raises immediately when all slots are borrowed.
        # Queue short request bursts without opening more DB connections.
        self._slots = BoundedSemaphore(settings.database_pool_max)

    def _get_pool(self) -> ThreadedConnectionPool:
        if not self._settings.database_url:
            raise ConfigurationError("DATABASE_URL is not configured.")
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = ThreadedConnectionPool(
                        self._settings.database_pool_min,
                        self._settings.database_pool_max,
                        dsn=self._settings.database_url,
                        connect_timeout=10,
                        application_name="trojanclaw-api",
                    )
        return self._pool

    @contextmanager
    def connection(self) -> Iterator[connection]:
        pool = self._get_pool()
        if not self._slots.acquire(timeout=POOL_WAIT_SECONDS):
            raise PoolError("Timed out waiting for an available database connection.")
        try:
            database_connection = pool.getconn()
            try:
                yield database_connection
                database_connection.commit()
            except Exception:
                database_connection.rollback()
                raise
            finally:
                pool.putconn(database_connection)
        finally:
            self._slots.release()

    def ping(self) -> None:
        with self.connection() as database_connection:
            with database_connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()

    def close(self) -> None:
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None


@lru_cache(maxsize=1)
def get_database() -> Database:
    return Database(get_settings())


def connect_database() -> connection:
    """Open a standalone connection for legacy batch-ingestion commands."""
    database_url = get_settings().database_url
    if not database_url:
        raise ConfigurationError("DATABASE_URL is not configured.")
    return psycopg2.connect(
        database_url,
        connect_timeout=10,
        application_name="trojanclaw-ingestion",
    )
