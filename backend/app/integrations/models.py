"""Integration status models."""

from enum import Enum

from pydantic import BaseModel


class IntegrationProvider(str, Enum):
    piazza = "piazza"
    brightspace = "brightspace"


class IntegrationState(str, Enum):
    not_configured = "not_configured"
    configured = "configured"


class IntegrationStatus(BaseModel):
    provider: IntegrationProvider
    state: IntegrationState
    transport: str = "mcp"
