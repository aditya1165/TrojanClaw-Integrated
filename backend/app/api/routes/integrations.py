"""Connector status and sign-in endpoints. Every route acts on the signed-in account's own connectors."""

from fastapi import APIRouter, Depends, Request
from urllib.parse import urlsplit

from app.api.routes.auth import get_current_user
from app.config import get_settings
from app.errors import AppError
from app.integrations.local import LocalConnectors, get_local_connectors
from app.integrations.models import IntegrationProvider, IntegrationStatus
from app.models.auth import AccountProfile


router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("", response_model=list[IntegrationStatus])
def list_integrations(user: AccountProfile = Depends(get_current_user),
                      service: LocalConnectors = Depends(get_local_connectors)) -> list[IntegrationStatus]:
    return service.statuses(user.id)


def require_local_request(request: Request) -> None:
    # A configured public frontend may reach this same backend through a tunnel.
    # Its forwarded visitor IP is not loopback. Windows still open on the server.
    settings = get_settings()
    origin = request.headers.get("origin")
    if (origin and origin.rstrip("/") not in settings.cors_origins) or (
        not origin and request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise AppError("This page cannot access local connectors.", "origin_forbidden", 403)
    if request.client and request.client.host in {"127.0.0.1", "::1", "testclient"}:
        return
    public = (settings.mcp_public_url or "").rstrip("/")
    configured = urlsplit(public)
    if (configured.scheme == "https" and configured.netloc and not configured.path
            and origin == public and origin in settings.cors_origins
            and request.url.netloc == configured.netloc):
        return
    raise AppError("Connector sign-in is available on the local website or its configured public address.", "local_only", 403)


@router.post("/{provider}/connect", status_code=202, dependencies=[Depends(require_local_request)])
def connect(provider: IntegrationProvider, user: AccountProfile = Depends(get_current_user),
            service: LocalConnectors = Depends(get_local_connectors)):
    service.connect(user.id, provider.value)
    return {"state": "connecting"}


@router.post("/{provider}/verify", dependencies=[Depends(require_local_request)])
def verify(provider: IntegrationProvider, user: AccountProfile = Depends(get_current_user),
           service: LocalConnectors = Depends(get_local_connectors)):
    service.verify(user.id, provider.value)
    return {"state": "connected"}
