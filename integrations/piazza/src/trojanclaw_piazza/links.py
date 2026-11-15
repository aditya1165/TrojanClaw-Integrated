"""Add piazza.com links to course and post results.

Upstream results identify a post by its course ID and post number (@123) but carry no link.
Students need to open the thread, so posts get `https://piazza.com/class/<course>/post/<n>`
and courses get `https://piazza.com/class/<course>`. Nothing else in the result changes.
"""

from collections.abc import Callable
import functools
import inspect
import re
from typing import Any

SITE = "https://piazza.com/class"
_COURSE_ID = re.compile(r"^[A-Za-z0-9]{4,40}$")  # Piazza network IDs are short alphanumeric strings


def course_url(course: Any) -> str | None:
    return f"{SITE}/{course}" if isinstance(course, str) and _COURSE_ID.match(course) else None


def post_url(course: Any, number: Any) -> str | None:
    base = course_url(course)
    if base is None or isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return None
    return f"{base}/post/{number}"


def add_links(value: Any, course: str | None = None, depth: int = 0) -> Any:
    """Walk a result and add `url` to each post (course + int number) and course group (in place)."""
    if depth > 6:
        return value
    if isinstance(value, list):
        for item in value:
            add_links(item, course, depth + 1)
    elif isinstance(value, dict):
        here = value.get("course") if isinstance(value.get("course"), str) else course
        number = value.get("number")
        if isinstance(number, int) and not isinstance(number, bool):
            if url := post_url(here, number):
                value.setdefault("url", url)  # a post or thread
        elif "posts" in value and (url := course_url(here)):
            value.setdefault("url", url)  # a course group of posts
        elif "course" not in value and isinstance(value.get("id"), str) and (url := course_url(value["id"])):
            value.setdefault("url", url)  # a course in list_courses
        for key in ("posts", "courses", "children", "followups", "replies"):
            if key in value:
                add_links(value[key], here, depth + 1)
    return value


def linked(function: Callable) -> Callable:
    """Wrap an upstream tool so its result carries piazza.com links. Keeps its name and inputs.

    The upstream return types are TypedDicts, and FastMCP serializes results through them,
    which would drop the added `url` keys. So the wrapper declares no return type: the
    result is sent as returned, and the tool has no fixed output schema.
    """

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return add_links(function(*args, **kwargs))

    del wrapper.__wrapped__  # stop signature lookups from reaching the upstream return type
    wrapper.__annotations__ = {key: value for key, value in function.__annotations__.items() if key != "return"}
    wrapper.__signature__ = inspect.signature(function).replace(return_annotation=inspect.Signature.empty)
    return wrapper
