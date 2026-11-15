"""Result cards inside replies: the ```cards blocks the chat shows as rich lists.

When a reply lists concrete items (jobs, events, deadlines, courses, posts), Claude writes
them as a fenced ```cards block of JSON (format in prompts/ai_instructions.md), and the
frontend renders the block as cards with an Open button. Before a reply is saved or sent,
`verify_cards` cleans every block:

- only known fields of the expected types are kept, with length limits;
- a card's `url` is kept only if a tool returned that exact link in this run, so a reply
  cannot show an invented or mistyped link;
- a block that is not valid JSON is turned into a plain markdown list, so nothing breaks.
"""

import json
import re
from typing import Any

FENCE = re.compile(r"```cards[^\S\n]*\n(.*?)\n?```", re.DOTALL)
KINDS = {"job", "event", "deadline", "course", "post", "announcement", "employer", "grade", "link"}
TEXT_FIELDS = {"title": 200, "subtitle": 200, "when": 120, "location": 120, "note": 300, "status": 40}
MAX_ITEMS = 12
MAX_TAGS = 4


def _text(value: Any, limit: int) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:limit] if value else None


def _item(raw: Any, known_urls: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item: dict[str, Any] = {}
    for key, limit in TEXT_FIELDS.items():
        if (value := _text(raw.get(key), limit)) is not None:
            item[key] = value
    if "title" not in item:
        return None
    kind = raw.get("kind")
    item["kind"] = kind if kind in KINDS else "link"
    raw_tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    tags = [tag for tag in (_text(value, 40) for value in raw_tags) if tag]
    if tags:
        item["tags"] = tags[:MAX_TAGS]
    url = raw.get("url")
    if isinstance(url, str) and url.strip() in known_urls:
        item["url"] = url.strip()
    return item


def _clean(block: str, known_urls: set[str]) -> str:
    try:
        data = json.loads(block)
    except ValueError:
        return _fallback(block)
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return _fallback(block)
    items = [item for item in (_item(raw, known_urls) for raw in data["items"][:MAX_ITEMS]) if item]
    if not items:
        return ""
    cleaned: dict[str, Any] = {"items": items}
    if title := _text(data.get("title"), 120):
        cleaned = {"title": title, **cleaned}
    return "```cards\n" + json.dumps(cleaned, ensure_ascii=False) + "\n```"


def _fallback(block: str) -> str:
    """Unreadable JSON: show the titles as a plain list instead of a broken card block."""
    titles = re.findall(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', block)
    return "\n".join("- " + json.loads('"' + title + '"') for title in titles[:MAX_ITEMS])


def verify_cards(text: str, known_urls: set[str]) -> str:
    """Clean every ```cards block in a reply (see the module docstring)."""
    if "```cards" not in text:
        return text
    cleaned = FENCE.sub(lambda match: _clean(match.group(1), known_urls), text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def card_urls(text: str) -> set[str]:
    """Links used by the cards in a (verified) reply."""
    urls: set[str] = set()
    for match in FENCE.finditer(text):
        try:
            for item in json.loads(match.group(1)).get("items", []):
                if isinstance(item, dict) and isinstance(item.get("url"), str):
                    urls.add(item["url"])
        except (ValueError, AttributeError):
            continue
    return urls
