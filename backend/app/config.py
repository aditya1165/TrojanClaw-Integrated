"""Typed, centralized environment configuration."""

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")


class ConfigurationError(RuntimeError):
    """Raised when a required setting is absent or invalid."""


def _optional(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _integer(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip() or str(default)
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc


def _boolean(name: str, default: bool = False) -> bool:
    raw_value = (os.getenv(name, "").strip() or str(default)).lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false.")


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    app_version: str
    app_env: str
    debug: bool
    cors_origins: list[str]
    database_url: str | None
    database_pool_min: int
    database_pool_max: int
    supabase_url: str | None
    supabase_key: str | None
    openai_api_key: str | None
    openai_embedding_model: str
    anthropic_api_key: str | None
    anthropic_model: str
    single_user_id: UUID
    single_user_name: str
    piazza_mcp_url: str | None
    brightspace_mcp_url: str | None

    @classmethod
    def from_environment(cls) -> "Settings":
        raw_origins = os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        )
        origins = [item.strip().rstrip("/") for item in raw_origins.split(",")]
        try:
            user_id = UUID(
                os.getenv("SINGLE_USER_ID", "00000000-0000-0000-0000-000000000001")
            )
        except ValueError as exc:
            raise ConfigurationError("SINGLE_USER_ID must be a valid UUID.") from exc

        settings = cls(
            app_name=os.getenv("APP_NAME", "TrojanClaw API").strip(),
            app_version=os.getenv("APP_VERSION", "0.2.0").strip(),
            app_env=os.getenv("APP_ENV", "development").strip(),
            debug=_boolean("APP_DEBUG", False),
            cors_origins=[item for item in origins if item],
            database_url=_optional("DATABASE_URL"),
            database_pool_min=_integer("DATABASE_POOL_MIN", 1),
            database_pool_max=_integer("DATABASE_POOL_MAX", 5),
            supabase_url=_optional("SUPABASE_URL"),
            supabase_key=_optional("SUPABASE_KEY"),
            openai_api_key=_optional("OPENAI_API_KEY"),
            openai_embedding_model=os.getenv(
                "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
            ).strip(),
            anthropic_api_key=_optional("ANTHROPIC_API_KEY"),
            anthropic_model=os.getenv(
                "ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"
            ).strip(),
            single_user_id=user_id,
            single_user_name=os.getenv("SINGLE_USER_NAME", "TrojanClaw User").strip(),
            piazza_mcp_url=_optional("PIAZZA_MCP_URL"),
            brightspace_mcp_url=_optional("BRIGHTSPACE_MCP_URL"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.database_pool_min < 1:
            raise ConfigurationError("DATABASE_POOL_MIN must be at least 1.")
        if self.database_pool_max < self.database_pool_min:
            raise ConfigurationError(
                "DATABASE_POOL_MAX must be greater than or equal to DATABASE_POOL_MIN."
            )
        database_scheme = (self.database_url or "").split(":", 1)[0].lower()
        if self.database_url and database_scheme not in {"postgres", "postgresql"}:
            raise ConfigurationError("DATABASE_URL must be a PostgreSQL DSN.")
        if self.supabase_url:
            parsed = urlparse(self.supabase_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ConfigurationError("SUPABASE_URL must be an HTTPS URL.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_environment()


def get_supabase_credentials() -> tuple[str, str]:
    """Compatibility helper for the Supabase SDK client."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_key:
        raise ConfigurationError("SUPABASE_URL and SUPABASE_KEY are required.")
    return settings.supabase_url, settings.supabase_key
