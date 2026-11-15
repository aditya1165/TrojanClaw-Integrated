"""Explicit browser consent for external assistants; no account identity from the caller."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.routes.auth import get_current_user, require_trusted_origin, limit_auth_attempts
from app.models.auth import AccountProfile
from app.services.mcp_auth import McpOAuth, get_mcp_oauth

router = APIRouter(prefix="/assistant", tags=["assistant"], dependencies=[Depends(require_trusted_origin)])


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: SecretStr = Field(min_length=1, max_length=24000)


@router.post("/request", dependencies=[Depends(limit_auth_attempts)])
def describe(payload: ConnectionRequest, response: Response, user: AccountProfile = Depends(get_current_user),
             oauth: McpOAuth = Depends(get_mcp_oauth)):
    response.headers["Cache-Control"] = "no-store"
    try:
        data = oauth.pending(payload.request.get_secret_value())
        return {"client_name": data["name"], "account": user.name}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/approve", dependencies=[Depends(limit_auth_attempts)])
def approve(payload: ConnectionRequest, response: Response, user: AccountProfile = Depends(get_current_user),
            oauth: McpOAuth = Depends(get_mcp_oauth)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"redirect_url": oauth.approve(payload.request.get_secret_value(), user.id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/disconnect")
def disconnect(response: Response, user: AccountProfile = Depends(get_current_user),
               oauth: McpOAuth = Depends(get_mcp_oauth)):
    response.headers["Cache-Control"] = "no-store"
    oauth.disconnect(user.id)
    return {"disconnected": True}
