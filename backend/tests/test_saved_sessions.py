"""Connector sessions are written privately, where the connector helper reads them."""

import json
import stat

from app.integrations.local import LocalConnectors
from app.integrations.models import IntegrationState
from app.integrations.saved_sessions import save_json, session_root


def test_sessions_are_private_and_where_the_helper_reads_them():
    cookies = [{"name": "d2lSessionVal", "value": "a"}, {"name": "d2lSecureSessionVal", "value": "b"}]
    save_json("brightspace", "cookies.json", cookies)
    path = session_root() / "brightspace" / "cookies.json"
    assert json.loads(path.read_text()) == cookies
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.parent == LocalConnectors().root / "brightspace"  # same folder the helper uses


def test_setup_progress_shows_on_the_cards():
    connectors = LocalConnectors()
    connectors.set_state("brightspace", IntegrationState.connected)
    connectors.set_state("handshake", IntegrationState.connecting, "Setting up…")
    states = {item.provider.value: (item.state, item.message) for item in connectors.statuses()}
    assert states["brightspace"] == (IntegrationState.connected, None)
    assert states["handshake"] == (IntegrationState.connecting, "Setting up…")
