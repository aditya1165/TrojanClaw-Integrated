"""Add Handshake page links to job, employer and application results.

The pinned upstream tools return IDs but no links. Students need to open the posting, so
each job and employer gets a `url` on the signed-in Handshake site, built the same way as
the event links in events.py. Nothing else in the result changes.
"""

from collections.abc import Callable
import functools
from typing import Any
from urllib.parse import quote

import handshake_mcp.client as client

# Tools whose results contain jobs or employers.
LINKED_TOOLS = frozenset({
    "hs_search_jobs", "hs_get_job", "hs_search_employers", "hs_get_employer", "hs_get_applications",
})


def page(kind: str, identity: Any) -> str | None:
    """`{site}/stu/{kind}/{id}`, or None for a missing or unsafe ID."""
    value = str(identity or "").strip()
    if not value or value in {".", ".."}:
        return None
    return f"{client.BASE_URL.rstrip('/')}/stu/{kind}/{quote(value, safe='')}"


def _employer(employer: Any) -> None:
    if isinstance(employer, dict) and (url := page("employers", employer.get("id"))):
        employer["url"] = url


def _job(job: Any) -> None:
    if not isinstance(job, dict):
        return
    if url := page("jobs", job.get("id")):
        job["url"] = url
    _employer(job.get("employer"))


def add_links(tool: str, result: Any) -> Any:
    """Add `url` fields to the jobs and employers in one tool's result (in place)."""
    if not isinstance(result, dict):
        return result
    if tool == "hs_search_jobs":
        for job in result.get("results") or []:
            _job(job)
    elif tool == "hs_get_job":
        _job(result)
    elif tool == "hs_search_employers":
        for employer in result.get("employers") or []:
            _employer(employer)
    elif tool == "hs_get_employer":
        _employer(result)
    elif tool == "hs_get_applications":
        for application in result.get("nodes") or result.get("results") or []:
            if isinstance(application, dict):
                _job(application.get("job"))
    return result


def linked(function: Callable) -> Callable:
    """Wrap an upstream tool so its result carries page links. Keeps its name and schema."""
    if function.__name__ not in LINKED_TOOLS:
        return function

    @functools.wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return add_links(function.__name__, await function(*args, **kwargs))

    return wrapper
