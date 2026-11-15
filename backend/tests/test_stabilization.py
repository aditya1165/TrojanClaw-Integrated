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
from app.scraper import piazza_ingest


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
        def chat(self, _request):
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


def test_dedupe_collapses_same_post_from_different_sources() -> None:
    posts = [
        {"post_id": "42", "source_url": "https://piazza.com/class/x?cid=42", "title": "HW3"},
        {"post_id": "42", "source_url": "piazza://post/42", "title": "HW3 (edited)"},
        {"post_id": None, "source_url": "u", "title": "a"},
        {"post_id": None, "source_url": "u", "title": "a"},
    ]
    assert len(piazza_ingest._dedupe_posts(posts)) == 2


class RecordingConnection:
    def __init__(self):
        self.executed: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        connection = self

        class Cursor:
            def execute(self, query, parameters=None):
                connection.executed.append(" ".join(query.split()))

            def close(self):
                pass

        return Cursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _patch_sync(monkeypatch, connection, embed):
    monkeypatch.setattr(piazza_ingest, "connect_database", lambda: connection)
    monkeypatch.setattr(piazza_ingest, "validate_documents_schema", lambda *a, **k: None)
    monkeypatch.setattr(piazza_ingest, "_openai_client", lambda: object())
    monkeypatch.setattr(piazza_ingest, "_embed_text", embed)
    monkeypatch.setattr(
        piazza_ingest,
        "_fetch_piazza_documents",
        lambda _course: [
            {
                "post_id": "1",
                "title": "HW3",
                "source_url": "https://piazza.com/class/x?cid=1",
                "content": "Clarification about homework three.",
            }
        ],
    )


def test_piazza_sync_keeps_existing_corpus_when_embedding_fails(monkeypatch) -> None:
    connection = RecordingConnection()

    def failing_embed(_client, _text):
        raise RuntimeError("embedding provider down")

    _patch_sync(monkeypatch, connection, failing_embed)

    with pytest.raises(RuntimeError):
        piazza_ingest.sync_piazza_course_documents("CSCI544")

    assert not any(q.startswith("DELETE") for q in connection.executed)
    assert connection.commits == 0


def test_piazza_sync_publishes_in_single_commit(monkeypatch) -> None:
    connection = RecordingConnection()
    _patch_sync(monkeypatch, connection, lambda _client, _text: [0.0, 1.0])

    inserted = piazza_ingest.sync_piazza_course_documents("CSCI544")

    assert inserted >= 1
    assert connection.commits == 1
    deletes = [i for i, q in enumerate(connection.executed) if q.startswith("DELETE FROM documents")]
    inserts = [i for i, q in enumerate(connection.executed) if q.startswith("INSERT INTO documents")]
    assert deletes and inserts and deletes[0] < inserts[0]
