"""Activity text while the agent works, and links a reply cites from live connectors.

A connector link is shown under a reply only when a connector tool returned it AND the
reply cites it, so the model cannot invent a source link.
"""

import json

PROVIDER_NAMES = {"brightspace": "Brightspace", "handshake": "Handshake", "piazza": "Piazza"}
TITLE_KEYS = ("title", "name", "subject", "jobTitle", "employerName", "courseName")

# What the status line says while a TrojanClaw MCP tool runs.
TOOL_ACTIVITY = {
    "list_deadlines": "Checking your deadlines",
    "list_announcements": "Checking announcements",
    "search_student_context": "Searching your course materials",
    "search_campus_knowledge": "Searching USC information",
    "list_campus_events": "Checking campus events",
    "get_data_status": "Checking your synced data",
    "propose_calendar_event": "Drafting a calendar event",
    "ask_calendar_details": "Checking the event details",
}


def provider_name(provider: str) -> str:
    return PROVIDER_NAMES.get(provider, provider.title())


def activity_for(tool_name: str) -> str:
    """'brightspace__get_upcoming_due_dates' -> 'Checking Brightspace: upcoming due dates'."""
    if tool_name in TOOL_ACTIVITY:
        return TOOL_ACTIVITY[tool_name]
    provider, separator, tool = tool_name.partition("__")
    if not separator:
        return "Looking that up"
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
            found[url] = {"title": label[:120], "url": url, "provider": provider}
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


def cited_links(answer: str, found: dict) -> list[dict]:
    """Connector links the answer cites, as sources (title, url, course, kind, provider)."""
    return [{"title": item["title"], "url": url, "course": None, "kind": "connector", "provider": item["provider"]}
            for url, item in found.items() if url in answer][:8]
