"""Regression tests for repository parameter adaptation, error mapping and Piazza sync safety."""

from contextlib import contextmanager
from uuid import UUID

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extensions import adapt

from app.config import ConfigurationError
from app.dependencies import get_chat_service
from app.main import app
from app.repositories.documents import DocumentRepository

pytestmark = pytest.mark.usefixtures("signed_in_account")


class AdaptingCursor:
    """Fake cursor that adapts every parameter exactly like psycopg2 would."""

    def __init__(self, log: list):
        self._log = log

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


class FakeDatabase:
    def __init__(self):
        self.log: list = []

    @contextmanager
    def connection(self):
        class Connection:
            def cursor(inner):
                return AdaptingCursor(self.log)

        yield Connection()


def test_document_search_adapts_uuid_user_id() -> None:
    database = FakeDatabase()
    results = DocumentRepository(database).search(
        embedding=[0.1, 0.2],
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
    )
    assert len(results) == 1
    assert database.log[0][1][1] == "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ConfigurationError("DATABASE_URL is not configured."), 503, "dependency_unavailable"),
        (psycopg2.OperationalError("connection refused"), 503, "dependency_unavailable"),
    ],
)
def test_chat_maps_dependency_errors_without_leaking_details(error, status, code) -> None:
    class FailingChatService:
        def chat(self, _request, *, user_id):
            raise error

    app.dependency_overrides[get_chat_service] = lambda: FailingChatService()
    try:
        response = TestClient(app).post(
            "/chat", json={"messages": [{"role": "user", "content": "Hi"}]}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status
    assert response.json()["code"] == code
    assert str(error) not in response.text
