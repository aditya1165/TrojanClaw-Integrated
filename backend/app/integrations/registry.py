"""Registry for MCP-backed learning-platform integrations."""

from app.config import Settings
from app.integrations.models import (
    IntegrationProvider,
    IntegrationState,
    IntegrationStatus,
)


class IntegrationRegistry:
    def __init__(self, settings: Settings):
        self._settings = settings

    def statuses(self) -> list[IntegrationStatus]:
        endpoints = {
            IntegrationProvider.piazza: self._settings.piazza_mcp_url,
            IntegrationProvider.brightspace: self._settings.brightspace_mcp_url,
        }
        return [
            IntegrationStatus(
                provider=provider,
                state=(
                    IntegrationState.configured
                    if endpoint
                    else IntegrationState.not_configured
                ),
            )
            for provider, endpoint in endpoints.items()
        ]
