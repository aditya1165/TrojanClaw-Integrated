"""Tests for Gemini embeddings, agent config, the TrojanClaw MCP server and retrieval filters."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from mcp import Client

from app.agent.config import RetrievalConfig, load_agent_config
from app.agent.runtime import _dedupe, _RecordingClient
from app.config import ConfigurationError, get_settings
from app.errors import DependencyUnavailableError
from app.mcp_server.server import build_mcp_server
from app.models.document import RetrievedDocument
from app.repositories.documents import DocumentRepository
from app.repositories.student_data import _course_pattern
from app.services.embeddings import EmbeddingService

USER = UUID("00000000-0000-0000-0000-000000000001")


# --- Embeddings --------------------------------------------------------------


class FakeModels:
    def __init__(self):
        self.calls = []

    def embed_content(self, *, model, contents, config):
        self.calls.append((model, list(contents), config.task_type, config.output_dimensionality))
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0]) for _ in contents])


def _embedding_service():
    models = FakeModels()
    return EmbeddingService(get_settings(), client=SimpleNamespace(models=models)), models


def test_query_embedding_is_normalized_and_uses_query_task_type() -> None:
    service, models = _embedding_service()
    assert service.embed_query("when is hw4 due") == pytest.approx([0.6, 0.8])
    model, contents, task_type, dims = models.calls[0]
    assert task_type == "RETRIEVAL_QUERY"
    assert dims == get_settings().embedding_dimensions
    assert contents == ["when is hw4 due"]


def test_document_embeddings_are_batched_by_100() -> None:
    service, models = _embedding_service()
    vectors = service.embed_documents([f"chunk {i}" for i in range(250)])
    assert len(vectors) == 250
    assert [len(call[1]) for call in models.calls] == [100, 100, 50]
    assert {call[2] for call in models.calls} == {"RETRIEVAL_DOCUMENT"}


def test_missing_gemini_key_is_a_dependency_error() -> None:
    settings = replace(get_settings(), gemini_api_key=None)
    with pytest.raises(DependencyUnavailableError):
        EmbeddingService(settings).embed_query("hi")


# --- Agent config ------------------------------------------------------------


def test_agent_config_loads_prompt_and_chat_goal() -> None:
    config = load_agent_config(get_settings())
    assert config.system_prompt.startswith("You are TrojanClaw")
    chat = config.goal("chat")
    assert "list_deadlines" in chat.tools
    assert chat.model == config.model
    with pytest.raises(ConfigurationError):
        config.goal("does_not_exist")


# --- MCP server ----------------------------------------------------------------


class FakeStudentData:
    def __init__(self):
        self.search_args = None

    def list_deadlines(self, user_id, days_ahead, course):
        return [{"title": "Homework 4", "course": "CSCI 570", "due_at": "2026-11-20T23:59:00-08:00",
                 "url": "https://brightspace.usc.edu/hw4"}]

    def list_announcements(self, user_id, days_back, course, limit):
        return []

    def search_content(self, user_id, embedding, limit, min_similarity, course):
        self.search_args = (user_id, embedding, limit, min_similarity, course)
        return [{"kind": "piazza_posts", "course": "CSCI 570", "title": "HW4 extended", "url": None,
                 "text": "Extended by 48 hours", "similarity": 0.81}]

    def data_status(self, user_id):
        return {"connections": [{"provider": "piazza", "status": "connected"}], "counts": {"piazza_posts": 3}}


class FakeEmbeddings:
    def embed_query(self, text):
        return [0.1, 0.2]


def _server(student_data=None):
    return build_mcp_server(
        user_id=USER,
        embeddings=FakeEmbeddings(),
        documents=SimpleNamespace(search=lambda **kwargs: []),
        student_data=student_data or FakeStudentData(),
        retrieval=RetrievalConfig(min_similarity=0.55, per_source_limit=2),
    )


async def _call(server, name, arguments=None):
    async with Client(server) as client:
        result = await client.call_tool(name=name, arguments=arguments or {})
        return result, json.loads(result.content[0].text)


def test_mcp_server_exposes_the_agent_tools() -> None:
    async def names():
        async with Client(_server()) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    assert asyncio.run(names()) == {
        "list_deadlines", "list_announcements", "search_student_context",
        "search_campus_knowledge", "list_campus_events", "get_data_status",
    }


def test_list_deadlines_returns_items_and_sources() -> None:
    _, payload = asyncio.run(_call(_server(), "list_deadlines", {"course": "CSCI 570"}))
    assert payload["deadlines"][0]["title"] == "Homework 4"
    assert payload["sources"][0] == {
        "title": "Homework 4", "url": "https://brightspace.usc.edu/hw4", "course": "CSCI 570", "kind": "deadline",
    }


def test_search_student_context_uses_configured_similarity_floor() -> None:
    student_data = FakeStudentData()
    _, payload = asyncio.run(_call(_server(student_data), "search_student_context", {"query": "hw4", "limit": 50}))
    assert student_data.search_args == (USER, [0.1, 0.2], 12, 0.55, None)  # limit clamped to 12
    assert payload["results"][0]["title"] == "HW4 extended"


def test_each_server_only_sees_its_own_student() -> None:
    class RecordingStudentData(FakeStudentData):
        def __init__(self):
            super().__init__()
            self.users = []

        def list_deadlines(self, user_id, days_ahead, course):
            self.users.append(user_id)
            return []

    other = UUID("00000000-0000-0000-0000-000000000099")
    student_data = RecordingStudentData()

    async def run():
        for user in (USER, other):
            server = build_mcp_server(
                user_id=user, embeddings=FakeEmbeddings(), documents=SimpleNamespace(search=lambda **kwargs: []),
                student_data=student_data, retrieval=RetrievalConfig(min_similarity=0.55, per_source_limit=2),
            )
            async with Client(server) as client:
                # The model cannot pick a user: extra arguments are not part of the tool schema.
                tool = next(t for t in (await client.list_tools()).tools if t.name == "list_deadlines")
                assert "user_id" not in tool.input_schema["properties"]
                await client.call_tool(name="list_deadlines", arguments={})

    asyncio.run(run())
    assert student_data.users == [USER, other]


def test_empty_results_point_the_agent_to_data_status() -> None:
    _, payload = asyncio.run(_call(_server(), "list_announcements"))
    assert payload["announcements"] == []
    assert "get_data_status" in payload["note"]


# --- Runtime helpers -----------------------------------------------------------


def test_recording_client_collects_tool_calls_and_sources() -> None:
    async def run():
        async with Client(_server()) as client:
            recorder = _RecordingClient(client)
            await recorder.call_tool("list_deadlines", {"days_ahead": 7})
            await recorder.call_tool("list_deadlines", {"days_ahead": 7})
            return recorder

    recorder = asyncio.run(run())
    assert [call.tool for call in recorder.tool_calls] == ["list_deadlines", "list_deadlines"]
    assert len(_dedupe(recorder.sources)) == 1


# --- Retrieval filters ------------------------------------------------------


class RowsDatabase:
    def __init__(self, rows):
        self.rows = rows
        self.parameters = None

    @contextmanager
    def connection(self):
        database = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, _query, parameters):
                database.parameters = parameters

            def fetchall(self):
                return database.rows

        yield SimpleNamespace(cursor=lambda: Cursor())


def test_campus_search_drops_weak_matches_and_caps_chunks_per_page() -> None:
    rows = [
        ("a1", "https://usc.edu/a", "A", "general", None, 0.10),
        ("a2", "https://usc.edu/a", "A", "general", None, 0.12),
        ("a3", "https://usc.edu/a", "A", "general", None, 0.14),  # third chunk of page A: capped
        ("b1", "https://usc.edu/b", "B", "general", None, 0.20),
        ("c1", "https://usc.edu/c", "C", "general", None, 0.70),  # similarity 0.30: too weak
    ]
    database = RowsDatabase(rows)
    results = DocumentRepository(database).search(
        embedding=[0.1], limit=5, min_similarity=0.5, per_source_limit=2
    )
    assert [r.chunk_text for r in results] == ["a1", "a2", "b1"]
    assert all(isinstance(r, RetrievedDocument) for r in results)
    assert database.parameters[-1] == 20  # over-fetched 4x to survive filtering


def test_course_filter_ignores_spaces_and_case() -> None:
    assert _course_pattern("CSCI 570") == "%csci570%"
    assert _course_pattern(None) is None
