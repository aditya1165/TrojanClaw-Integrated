"""Integration discovery endpoints."""

from fastapi import APIRouter, Depends, Request

from app.config import get_settings
from app.integrations.models import IntegrationProvider, IntegrationStatus
from app.integrations.local import LocalConnectors, get_local_connectors
from app.errors import AppError


router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("", response_model=list[IntegrationStatus])
def list_integrations(service: LocalConnectors = Depends(get_local_connectors)) -> list[IntegrationStatus]:
    return service.statuses()


def require_local_request(request: Request) -> None:
    # These profiles belong to the desktop owner, not remote website visitors.
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise AppError("Connectors are available only on this computer.", "local_only", 403)
    origin = request.headers.get("origin")
    if (origin and origin.rstrip("/") not in get_settings().cors_origins) or (
        not origin and request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise AppError("This page cannot access local connectors.", "origin_forbidden", 403)


@router.post("/{provider}/connect", status_code=202, dependencies=[Depends(require_local_request)])
def connect(provider: IntegrationProvider, service: LocalConnectors = Depends(get_local_connectors)):
    service.connect(provider.value)
    return {"state": "connecting"}


@router.post("/{provider}/verify", dependencies=[Depends(require_local_request)])
def verify(provider: IntegrationProvider, service: LocalConnectors = Depends(get_local_connectors)):
    service.verify(provider.value)
    return {"state": "connected"}
