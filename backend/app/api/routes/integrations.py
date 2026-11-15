"""Authenticated account-owned managed-browser connections."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from app.api.routes.auth import get_current_user, require_trusted_origin
from app.integrations.hosted import HostedConnectors
from app.integrations.local import get_local_connectors as get_connectors
from app.integrations.models import IntegrationProvider, IntegrationStatus
from app.models.auth import AccountProfile
from app.config import get_settings
from app.errors import AppError


def require_local_request(request: Request):
    """Compatibility for Google routes; hosted sign-in has no loopback dependency."""
    settings = get_settings()
    if getattr(settings, "connector_mode", "local") == "browserbase":
        require_trusted_origin(request)
        return
    origin = request.headers.get("origin")
    if (origin and origin.rstrip("/") not in settings.cors_origins) or (
        not origin and request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise AppError("This page cannot access local connectors.", "origin_forbidden", 403)
    if request.client and request.client.host in {"127.0.0.1", "::1", "testclient"}:
        return
    from urllib.parse import urlsplit
    public = (get_settings().mcp_public_url or "").rstrip("/")
    if public and request.headers.get("origin") == public and request.url.netloc == urlsplit(public).netloc:
        return
    raise AppError("Use the configured public frontend or enable Browserbase.", "local_only", 403)


router = APIRouter(prefix="/integrations", tags=["integrations"], dependencies=[Depends(require_trusted_origin)])


class LoginAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_id: UUID


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.get("", response_model=list[IntegrationStatus])
def list_integrations(response: Response, user: AccountProfile = Depends(get_current_user), service: HostedConnectors = Depends(get_connectors)):
    private_response(response)
    return service.statuses(user.id)


@router.post("/{provider}/connect", status_code=201)
def connect(provider: IntegrationProvider, response: Response, user: AccountProfile = Depends(get_current_user), service: HostedConnectors = Depends(get_connectors)):
    private_response(response)
    result = service.connect(user.id, provider.value)
    if result is None:
        response.status_code = 202
        return {"state": "connecting"}
    return result


@router.get("/{provider}/login")
def resume(provider: IntegrationProvider, response: Response, user: AccountProfile = Depends(get_current_user), service: HostedConnectors = Depends(get_connectors)):
    private_response(response)
    if not hasattr(service, "resume"):
        return {"state": "connecting"}
    return service.resume(user.id, provider.value)


@router.post("/{provider}/finish")
def finish(provider: IntegrationProvider, payload: LoginAttempt, response: Response, user: AccountProfile = Depends(get_current_user), service: HostedConnectors = Depends(get_connectors)):
    private_response(response)
    service.finish(user.id, provider.value, payload.login_id)
    return {"state": "connected"}


@router.post("/{provider}/cancel")
def cancel(provider: IntegrationProvider, payload: LoginAttempt, response: Response, user: AccountProfile = Depends(get_current_user), service: HostedConnectors = Depends(get_connectors)):
    private_response(response)
    service.cancel(user.id, provider.value, payload.login_id)
    return {"state": "needs_login"}


@router.post("/{provider}/verify")
def verify(provider: IntegrationProvider, response: Response, user: AccountProfile = Depends(get_current_user), service: HostedConnectors = Depends(get_connectors)):
    private_response(response)
    service.verify(user.id, provider.value)
    return {"state": "connected"}


@router.post("/{provider}/disconnect")
def disconnect(provider: IntegrationProvider, response: Response, user: AccountProfile = Depends(get_current_user), service: HostedConnectors = Depends(get_connectors)):
    private_response(response)
    service.disconnect(user.id, provider.value)
    return {"state": "not_configured"}
