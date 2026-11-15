"""Validate and bind one request's in-memory account session. No disk fallback."""

import json
import re
from urllib.parse import urlparse

from .profiles import handshake_origin


def session_cookies(session: dict, provider: str) -> dict[str, str]:
    if provider not in {"handshake", "brightspace", "piazza"}:
        raise ValueError("Unknown provider.")
    origin = session.get("origin", "")
    valid = (handshake_origin(origin) if provider == "handshake" else
             origin == {"brightspace": "https://brightspace.usc.edu", "piazza": "https://piazza.com"}.get(provider))
    if not valid or len(json.dumps(session)) > 1_000_000:
        raise ValueError("Invalid provider session.")
    host = urlparse(origin).hostname
    result = {}
    for cookie in session.get("cookies", []):
        domain = cookie.get("domain", host).lstrip(".")
        if host != domain and not host.endswith("." + domain):
            continue
        name, value = cookie.get("name"), cookie.get("value")
        if provider == "piazza" and (name not in {"session_id", "piazza_session"} or domain != "piazza.com"):
            continue
        if (not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 or char == ";" for char in value)):
            raise ValueError("Invalid provider cookies.")
        result[name] = value
    if not result:
        raise ValueError("The provider session has no cookies.")
    return result


def bind_handshake_session(session: dict) -> None:
    import handshake_mcp.client as client

    cookies = session_cookies(session, "handshake")
    client.load_cookies = lambda: dict(cookies)
    client.BASE_URL = handshake_origin(session["origin"])
    client.API_BASE = client.BASE_URL + "/stu"
    client.GQL_URL = client.BASE_URL + "/hs/graphql"
    client._csrf_token = None
    from .transport import bind_transport
    bind_transport(client)
