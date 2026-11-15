"""Liveness and dependency-readiness endpoints."""

from fastapi import APIRouter
from psycopg2 import Error as PsycopgError
from psycopg2.pool import PoolError

from app.config import ConfigurationError, get_settings
from app.database import get_database
from app.errors import DependencyUnavailableError


router = APIRouter(tags=["system"])


@router.get("/")
def root() -> dict[str, str]:
    settings = get_settings()
    return {"name": settings.app_name, "version": settings.app_version}


@router.get("/healthcheck")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def readiness() -> dict[str, str]:
    try:
        get_database().ping()
    except (ConfigurationError, PsycopgError, PoolError) as exc:
        raise DependencyUnavailableError("database") from exc
    return {"status": "ready", "database": "connected"}
