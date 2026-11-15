"""Connector sessions belong to one account and are written where the connector helper reads them."""

import json
import stat
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes.auth import get_current_user
from app.integrations.local import LocalConnectors, get_local_connectors, user_root
from app.integrations.models import IntegrationState
from app.integrations.saved_sessions import save_json
from app.main import app
from app.models.auth import AccountProfile

ALICE = AccountProfile(id=uuid4(), name="Alice", username="alice")
BOB = AccountProfile(id=uuid4(), name="Bob", username="bob")


def test_sessions_are_private_and_in_the_accounts_own_folder():
    cookies = [{"name": "d2lSessionVal", "value": "a"}, {"name": "d2lSecureSessionVal", "value": "b"}]
    save_json(ALICE.id, "brightspace", "cookies.json", cookies)
    path = user_root(ALICE.id) / "brightspace" / "cookies.json"
    assert json.loads(path.read_text()) == cookies
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_user_folder_cannot_escape_the_base_folder():
    with pytest.raises(ValueError):
        user_root("../../etc")


def test_one_account_never_sees_another_accounts_sessions():
    save_json(ALICE.id, "brightspace", "cookies.json", [])
    connectors = LocalConnectors()
    connectors.set_state(ALICE.id, "handshake", IntegrationState.connected)
    alice = {s.provider.value: s.state for s in connectors.statuses(ALICE.id)}
    bob = {s.provider.value: s.state for s in connectors.statuses(BOB.id)}
    assert alice == {"brightspace": "saved", "handshake": "connected", "piazza": "unavailable"}
    assert bob == {"brightspace": "not_configured", "handshake": "not_configured", "piazza": "unavailable"}


def test_helper_gets_only_the_accounts_own_folders(monkeypatch, tmp_path):
    connectors = LocalConnectors()
    connectors.project = tmp_path
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    seen = {}

    def run(args, **kwargs):
        seen.update(kwargs["env"])
        raise OSError("stop here")

    monkeypatch.setattr("app.integrations.local.subprocess.run", run)
    with pytest.raises(Exception):
        connectors._run(BOB.id, "handshake", "tools")
    assert seen["TROJANCLAW_SESSION_DIR"] == str(user_root(BOB.id))
    assert seen["HANDSHAKE_DIR"] == str(user_root(BOB.id) / "handshake")


def test_connector_routes_require_sign_in_and_use_the_signed_in_account():
    connectors = LocalConnectors()
    connectors.set_state(ALICE.id, "handshake", IntegrationState.connected)
    app.dependency_overrides[get_local_connectors] = lambda: connectors
    try:
        with TestClient(app) as client:
            assert client.get("/integrations").status_code == 401
            app.dependency_overrides[get_current_user] = lambda: BOB
            states = {item["provider"]: item["state"] for item in client.get("/integrations").json()}
            assert states["handshake"] == "not_configured"  # Alice's connection is not Bob's
    finally:
        app.dependency_overrides.clear()
