from uuid import UUID
import os

# Tests must never create billable remote browsers or use real connector state.
os.environ.setdefault("CONNECTOR_MODE", "local")

import pytest

from app.api.routes.auth import get_current_user
from app.main import app
from app.models.auth import AccountProfile


@pytest.fixture(autouse=True)
def isolated_session_dir(monkeypatch, tmp_path):
    """Tests never read or delete the real ~/.trojanclaw sessions (sign-out deletes the USC session)."""
    monkeypatch.setenv("TROJANCLAW_SESSION_DIR", str(tmp_path / "sessions"))


@pytest.fixture
def signed_in_account():
    app.dependency_overrides[get_current_user] = lambda: AccountProfile(
        id=UUID("00000000-0000-0000-0000-000000000002"),
        name="Test User", username="test_user",
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)
