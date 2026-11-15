from uuid import UUID

import pytest

from app.api.routes.auth import get_current_user
from app.main import app
from app.models.auth import AccountProfile


@pytest.fixture
def signed_in_account():
    app.dependency_overrides[get_current_user] = lambda: AccountProfile(
        id=UUID("00000000-0000-0000-0000-000000000002"),
        name="Test User", username="test_user",
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)
