"""Dependency wiring for the HTTP layer."""

from functools import lru_cache
from uuid import UUID

from app.agent.config import load_agent_config
from app.agent.runtime import AgentRuntime
from app.config import get_settings
from app.database import get_database
from app.integrations.local import get_local_connectors
from app.mcp_server.server import build_mcp_server
from app.repositories.documents import DocumentRepository
from app.repositories.student_data import StudentDataRepository
from app.services.chat import ChatService
from app.services.embeddings import EmbeddingService
from app.services.engagesc import get_engagesc_service
from app.services.calendar import get_calendar_service


@lru_cache(maxsize=1)
def get_agent_runtime() -> AgentRuntime:
    settings = get_settings()
    agent_config = load_agent_config(settings)
    database = get_database()
    embeddings = EmbeddingService(settings)
    documents = DocumentRepository(database)
    student_data = StudentDataRepository(database)
    campus_events = get_engagesc_service()

    def server_for_user(user_id: UUID):
        # A fresh MCP server per run, bound to the signed-in student.
        return build_mcp_server(
            user_id=user_id,
            embeddings=embeddings,
            documents=documents,
            student_data=student_data,
            retrieval=agent_config.retrieval,
            campus_events=campus_events,
        )

    return AgentRuntime(
        agent_config,
        server_for_user,
        settings.anthropic_api_key,
        connectors=get_local_connectors(),
    )


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    return ChatService(get_agent_runtime(), calendar=get_calendar_service())
