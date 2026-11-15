"""Integration status models."""

from enum import Enum

from pydantic import BaseModel


class IntegrationProvider(str, Enum):
    piazza = "piazza"
    brightspace = "brightspace"
    handshake = "handshake"


class IntegrationState(str, Enum):
    not_configured = "not_configured"
    configured = "configured"
    saved = "saved"
    connecting = "connecting"
    connected = "connected"
    needs_login = "needs_login"
    unavailable = "unavailable"
    revoking = "revoking"


class IntegrationStatus(BaseModel):
    provider: IntegrationProvider
    state: IntegrationState
    transport: str = "mcp"
    message: str | None = None
