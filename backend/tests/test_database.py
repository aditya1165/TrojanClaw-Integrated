"""Request bursts wait for bounded DB slots rather than failing immediately."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

from psycopg2.pool import PoolError
import pytest

from app.database import Database


def database(maximum=2):
    return Database(SimpleNamespace(database_url="postgresql://test", database_pool_min=1, database_pool_max=maximum))


def test_concurrent_requests_wait_and_reuse_the_bounded_pool():
    db = database()
    lock, release, full, waiter_started = Lock(), Event(), Event(), Event()
    active = 0
    maximum = 0
    checkouts = 0

    def getconn():
        nonlocal active, maximum, checkouts
        with lock:
            if active == 2:
                raise PoolError("connection pool exhausted")
            active += 1
            checkouts += 1
            maximum = max(maximum, active)
            if active == 2:
                full.set()
        return MagicMock()

    def putconn(connection):
        nonlocal active
        with lock:
            active -= 1

    db._pool = SimpleNamespace(getconn=getconn, putconn=putconn)

    def holder():
        with db.connection():
            assert release.wait(3)

    def waiter():
        waiter_started.set()
        with db.connection():
            return "finished"

    with ThreadPoolExecutor(max_workers=3) as executor:
        first, second = executor.submit(holder), executor.submit(holder)
        try:
            assert full.wait(2)
            third = executor.submit(waiter)
            assert waiter_started.wait(2)
            assert not third.done()
            with lock:
                assert checkouts == 2
        finally:
            release.set()
        assert third.result(timeout=3) == "finished"
        first.result(timeout=3)
        second.result(timeout=3)
    assert maximum == 2
    assert active == 0


def test_checkout_failure_does_not_leak_a_waiting_slot():
    db = database(1)
    connection = MagicMock()
    db._pool = MagicMock()
    db._pool.getconn.side_effect = [PoolError("temporarily unavailable"), connection]
    with pytest.raises(PoolError):
        with db.connection():
            pass
    with db.connection() as borrowed:
        assert borrowed is connection
    connection.commit.assert_called_once()
    db._pool.putconn.assert_called_once_with(connection)


def test_query_failure_rolls_back_and_releases_both_connection_and_slot():
    db = database(1)
    connection = MagicMock()
    db._pool = MagicMock()
    db._pool.getconn.return_value = connection
    with pytest.raises(ValueError):
        with db.connection():
            raise ValueError("query failed")
    with db.connection():
        pass
    connection.rollback.assert_called_once()
    assert db._pool.putconn.call_count == 2


def test_pool_wait_has_a_bounded_timeout(monkeypatch):
    monkeypatch.setattr("app.database.POOL_WAIT_SECONDS", 0.01)
    db = database(1)
    db._pool = MagicMock()
    assert db._slots.acquire(timeout=0)
    try:
        with pytest.raises(PoolError, match="Timed out waiting"):
            with db.connection():
                pass
        db._pool.getconn.assert_not_called()
    finally:
        db._slots.release()
    with db.connection():
        pass
