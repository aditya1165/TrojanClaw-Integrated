"""Integration discovery endpoints."""

from fastapi import APIRouter

from app.config import get_settings
from app.integrations.models import IntegrationStatus
from app.integrations.registry import IntegrationRegistry


router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("", response_model=list[IntegrationStatus])
def list_integrations() -> list[IntegrationStatus]:
    return IntegrationRegistry(get_settings()).statuses()
