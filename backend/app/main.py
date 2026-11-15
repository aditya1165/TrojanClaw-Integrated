"""TrojanClaw FastAPI application factory."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from starlette.routing import Route
import anthropic
from google.genai import errors as genai_errors
import psycopg2

from app.api.routes import (
    auth, calendar, chat, conversations, engagesc, google_auth, health,
    integrations, mcp_connection, mobile, uploads, webreg,
)
from app.config import ConfigurationError, get_settings
from app.database import get_database
from app.errors import AppError


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    try:
        factory = getattr(application.state, "remote_mcp_factory", None)
        remote = factory() if factory else None
        if remote:
            application.state.remote_mcp = remote
            async with remote.router.lifespan_context(remote):
                yield
        else:
            yield
    finally:
        get_database().close()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="TrojanClaw academic copilot API.",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        if request.url.path.startswith(("/auth/", "/assistant/")) or request.url.path == "/mobile/pairing/redeem":
            # Pydantic's default error input can contain submitted passwords.
            return JSONResponse(status_code=422, content={"detail": [
                {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
                for error in exc.errors()
            ]})
        return await request_validation_exception_handler(request, exc)

    @application.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "code": exc.code},
        )

    def _error(status_code: int, code: str, detail: str) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": detail, "code": code})

    @application.exception_handler(ConfigurationError)
    async def configuration_error_handler(request: Request, exc: ConfigurationError) -> JSONResponse:
        logger.error("Configuration error on %s: %s", request.url.path, exc)
        return _error(503, "dependency_unavailable", "A required service is not configured.")

    @application.exception_handler(psycopg2.Error)
    async def database_error_handler(request: Request, exc: psycopg2.Error) -> JSONResponse:
        logger.exception("Database error on %s", request.url.path)
        return _error(503, "dependency_unavailable", "The database is temporarily unavailable.")

    @application.exception_handler(genai_errors.APIError)
    async def embedding_error_handler(request: Request, exc: genai_errors.APIError) -> JSONResponse:
        logger.exception("Gemini embedding error on %s", request.url.path)
        return _error(502, "upstream_error", "The embedding provider failed. Please try again.")

    @application.exception_handler(anthropic.AnthropicError)
    async def anthropic_error_handler(request: Request, exc: anthropic.AnthropicError) -> JSONResponse:
        logger.exception("Anthropic error on %s", request.url.path)
        return _error(502, "upstream_error", "The language model failed. Please try again.")

    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(google_auth.router)
    application.include_router(chat.router)
    application.include_router(conversations.router)
    application.include_router(uploads.router)
    application.include_router(calendar.router)
    # Register fixed EngageSC paths before /integrations/{provider}/... routes.
    application.include_router(engagesc.router)
    application.include_router(webreg.router)
    application.include_router(integrations.router)
    application.include_router(mobile.router)
    application.include_router(mcp_connection.router)
    if settings.mcp_public_url:
        from app.remote_mcp import create_remote_mcp
        from app.services.mcp_auth import get_mcp_oauth
        def remote_factory():
            return create_remote_mcp(get_mcp_oauth())
        remote = remote_factory()
        application.state.remote_mcp = remote
        application.state.remote_mcp_factory = remote_factory
        # Keep OAuth discovery at the origin root, as required by remote MCP clients.
        # Dispatch only the SDK's exact paths; a catch-all mount masks API 405s.
        class RemoteDispatcher:
            async def __call__(self, scope, receive, send):
                await application.state.remote_mcp(scope, receive, send)
        dispatcher = RemoteDispatcher()
        application.router.routes.extend(Route(route.path, endpoint=dispatcher,
            methods=route.methods) for route in remote.routes)
    return application


app = create_app()
