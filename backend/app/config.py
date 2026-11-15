"""Typed, centralized environment configuration."""

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = BACKEND_DIR.parent
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
    supabase_secret_key: str | None  # backend-only; chat file uploads (private Storage bucket)
    gemini_api_key: str | None
    embedding_model: str
    embedding_dimensions: int
    anthropic_api_key: str | None
    config_dir: Path
    google_calendar_client_id: str | None = None
    google_calendar_client_secret: str | None = None
    google_calendar_redirect_uri: str = "http://localhost:8000/calendar/callback"
    google_calendar_frontend_url: str = "http://localhost:5173"
    google_calendar_encryption_key: str | None = None
    mcp_public_url: str | None = None
    mcp_encryption_key: str | None = None

    @property
    def google_signin_redirect_uri(self) -> str:
        return self.google_calendar_redirect_uri.removesuffix("/calendar/callback") + "/auth/google/callback"

    @classmethod
    def from_environment(cls) -> "Settings":
        raw_origins = os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        )
        origins = [item.strip().rstrip("/") for item in raw_origins.split(",")]

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
            supabase_secret_key=_optional("SUPABASE_SECRET_KEY"),
            gemini_api_key=_optional("GEMINI_API_KEY"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001").strip(),
            embedding_dimensions=_integer("EMBEDDING_DIMENSIONS", 1536),
            anthropic_api_key=_optional("ANTHROPIC_API_KEY"),
            google_calendar_client_id=_optional("GOOGLE_CALENDAR_CLIENT_ID"),
            google_calendar_client_secret=_optional("GOOGLE_CALENDAR_CLIENT_SECRET"),
            google_calendar_redirect_uri=os.getenv("GOOGLE_CALENDAR_REDIRECT_URI", "http://localhost:8000/calendar/callback").strip(),
            google_calendar_frontend_url=os.getenv("GOOGLE_CALENDAR_FRONTEND_URL", "http://localhost:5173").strip().rstrip("/"),
            google_calendar_encryption_key=_optional("GOOGLE_CALENDAR_ENCRYPTION_KEY"),
            mcp_public_url=_optional("MCP_PUBLIC_URL"),
            mcp_encryption_key=_optional("MCP_ENCRYPTION_KEY"),
            config_dir=Path(os.getenv("TROJANCLAW_CONFIG_DIR", "").strip() or REPOSITORY_DIR),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.google_calendar_client_id:
            if self.google_calendar_frontend_url not in self.cors_origins:
                raise ConfigurationError("GOOGLE_CALENDAR_FRONTEND_URL must be a trusted CORS origin.")
            redirect = urlparse(self.google_calendar_redirect_uri)
            if (redirect.scheme != "https" and not (
                self.app_env in {"development", "test"} and redirect.scheme == "http"
                and redirect.hostname in {"localhost", "127.0.0.1"}
            )) or redirect.path != "/calendar/callback" or redirect.query or redirect.fragment:
                raise ConfigurationError("Use an HTTPS GOOGLE_CALENDAR_REDIRECT_URI ending in /calendar/callback (HTTP localhost is allowed in development).")
        if self.database_pool_min < 1:
            raise ConfigurationError("DATABASE_POOL_MIN must be at least 1.")
        if self.database_pool_max < self.database_pool_min:
            raise ConfigurationError(
                "DATABASE_POOL_MAX must be greater than or equal to DATABASE_POOL_MIN."
            )
        database_scheme = (self.database_url or "").split(":", 1)[0].lower()
        if self.database_url and database_scheme not in {"postgres", "postgresql"}:
            raise ConfigurationError("DATABASE_URL must be a PostgreSQL DSN.")
        # Must equal the vector(N) size of documents.embedding and content_chunks.embedding.
        # 3072 is excluded because pgvector cannot index more than 2,000 dimensions.
        if self.embedding_dimensions not in {768, 1536}:
            raise ConfigurationError("EMBEDDING_DIMENSIONS must be 768 or 1536.")
        if self.supabase_url:
            parsed = urlparse(self.supabase_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ConfigurationError("SUPABASE_URL must be an HTTPS URL.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_environment()
