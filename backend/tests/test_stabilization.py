"""Regression tests for repository parameter adaptation and error mapping."""

from contextlib import contextmanager

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extensions import adapt

from app.config import ConfigurationError
from app.dependencies import get_conversation_repository
from app.main import app
from app.repositories.documents import DocumentRepository

pytestmark = pytest.mark.usefixtures("signed_in_account")


class AdaptingCursor:
    """Fake cursor that adapts every parameter exactly like psycopg2 would."""

    def __init__(self, log: list, per_user=True):
        self._log = log
        self.per_user = per_user

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, parameters=None):
        for value in parameters or ():
            adapt(value)  # raises ProgrammingError for unadaptable types such as UUID
        self._log.append((query, parameters))

    def fetchall(self):
        return [("chunk", "https://usc.edu", "Title", "general", None, 0.1)]

    def fetchone(self):
        return (self.per_user,)


class FakeDatabase:
    def __init__(self, per_user=True):
        self.log: list = []
        self.per_user = per_user

    @contextmanager
    def connection(self):
        class Connection:
            def cursor(inner):
                return AdaptingCursor(self.log, self.per_user)

        yield Connection()


def test_campus_search_parameters_adapt_and_are_shared_across_users() -> None:
    database = FakeDatabase()
    results = DocumentRepository(database).search(embedding=[0.1, 0.2])
    assert len(results) == 1
    query, parameters = database.log[0]
    assert "user_id" not in query  # campus pages are shared by every student
    assert parameters == ["[0.1,0.2]", "[0.1,0.2]", 8]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ConfigurationError("DATABASE_URL is not configured."), 503, "dependency_unavailable"),
        (psycopg2.OperationalError("connection refused"), 503, "dependency_unavailable"),
    ],
)
def test_chat_maps_dependency_errors_without_leaking_details(error, status, code) -> None:
    class FailingConversations:
        def create(self, user_id, title):
            raise error

    app.dependency_overrides[get_conversation_repository] = lambda: FailingConversations()
    try:
        response = TestClient(app).post("/chat", json={"message": "Hi"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status
    assert response.json()["code"] == code
    assert str(error) not in response.text
