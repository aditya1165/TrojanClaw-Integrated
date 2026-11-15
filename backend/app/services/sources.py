"""Links an answer was based on, shown as chips under the reply.

A link is shown only when it came from a tool result or retrieved document AND
the answer cites it, so the model cannot invent a source link.
"""

import json

PROVIDER_NAMES = {"brightspace": "Brightspace", "handshake": "Handshake", "usc": "USC"}
TITLE_KEYS = ("title", "name", "subject", "jobTitle", "employerName", "courseName")


def provider_name(provider: str) -> str:
    return PROVIDER_NAMES.get(provider, provider.title())


def activity_for(tool_name: str) -> str:
    """'brightspace__get_upcoming_due_dates' -> 'Checking Brightspace: upcoming due dates'."""
    provider, _, tool = tool_name.partition("__")
    words = tool.removeprefix("get_").replace("_", " ").strip()
    return f"Checking {provider_name(provider)}: {words}" if words else f"Checking {provider_name(provider)}"


def _walk(value, provider: str, found: dict, depth: int = 0) -> None:
    if depth > 8 or len(found) >= 200:
        return
    if isinstance(value, dict):
        url = next((value[key] for key in ("url", "link", "href") if isinstance(value.get(key), str)), None)
        if url and url.startswith("https://") and url not in found:
            label = next((str(value[key]).strip() for key in TITLE_KEYS
                          if isinstance(value.get(key), (str, int)) and str(value[key]).strip()), provider_name(provider))
            found[url] = {"label": label[:120], "url": url, "provider": provider}
        for item in value.values():
            _walk(item, provider, found, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _walk(item, provider, found, depth + 1)
    elif isinstance(value, str) and value.lstrip()[:1] in ("[", "{"):
        try:
            _walk(json.loads(value), provider, found, depth + 1)
        except ValueError:
            pass


def collect_links(result: str, provider: str, found: dict) -> None:
    """Add links from a connector tool result (JSON content blocks) to `found`."""
    _walk(result, provider, found)


def cited_sources(answer: str, found: dict, providers_used: list[str]) -> list[dict]:
    """Links the answer cites; otherwise just the sources it checked."""
    cited = [item for url, item in found.items() if url in answer][:8]
    if cited:
        return cited
    return [{"label": provider_name(provider), "url": None, "provider": provider} for provider in dict.fromkeys(providers_used)]
