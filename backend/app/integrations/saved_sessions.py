"""Write connector sessions where the connector helper reads them.

Layout (same as integrations/handshake): <root>/<provider>/cookies.json, plus
<root>/handshake/origin.json. Root is TROJANCLAW_SESSION_DIR, else ~/.trojanclaw/connectors.
"""

import json
import os
from pathlib import Path


def session_root() -> Path:
    configured = os.environ.get("TROJANCLAW_SESSION_DIR", "").strip()
    return Path(configured or Path.home() / ".trojanclaw" / "connectors").expanduser().resolve()


def save_json(provider: str, name: str, data) -> None:
    """Atomically write a private (0600) JSON file under <root>/<provider>/."""
    directory = session_root() / provider
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = directory / f".{name}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream)
    os.replace(temporary, directory / name)
