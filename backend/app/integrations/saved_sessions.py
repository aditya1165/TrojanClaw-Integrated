"""Write one account's connector sessions where the connector helper reads them.

Layout (same as integrations/handshake, inside the account's folder from user_root):
<user folder>/<provider>/cookies.json, plus <user folder>/handshake/origin.json.
"""

import json
import os
from uuid import UUID

from app.integrations.local import user_root


def save_json(user_id: UUID | str, provider: str, name: str, data) -> None:
    """Atomically write a private (0600) JSON file under <user folder>/<provider>/."""
    directory = user_root(user_id) / provider
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = directory / f".{name}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream)
    os.replace(temporary, directory / name)
