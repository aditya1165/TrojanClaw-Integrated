"""Piazza courses and posts carry links to piazza.com."""

import asyncio
import json
from unittest.mock import patch

import requests

from trojanclaw_piazza.bridge import execute
from trojanclaw_piazza.links import add_links, post_url
from trojanclaw_piazza.server import save_cookies

from test_connector import account, cookies  # noqa: F401  (fixture and helper)


def test_post_and_course_shapes_get_links():
    grouped = add_links({"since": "2026-11-08", "courses": [
        {"course": "abc123xyz", "number": "CSCI 571", "name": "Web", "posts": [{"number": 42, "subject": "HW3"}]},
    ]})
    group = grouped["courses"][0]
    assert group["url"] == "https://piazza.com/class/abc123xyz"
    assert group["posts"][0]["url"] == "https://piazza.com/class/abc123xyz/post/42"
    thread = add_links({"course": "abc123xyz", "number": 7, "followups": [{"number": None}]})
    assert thread["url"] == "https://piazza.com/class/abc123xyz/post/7"
    flat = add_links({"course": "abc123xyz", "posts": [{"number": 3}]})
    assert flat["posts"][0]["url"].endswith("/post/3")


def test_unsafe_or_missing_values_get_no_link():
    assert post_url("../evil", 1) is None
    assert post_url("abc123xyz", True) is None
    assert post_url("abc123xyz", 0) is None
    assert add_links({"course": "abc123xyz", "number": "CSCI 571"}) == {"course": "abc123xyz", "number": "CSCI 571"}


def test_links_survive_real_mcp_serialization_through_the_bridge():
    save_cookies(cookies())
    status = {"id": "user-a", "networks": [{"id": "abc123xyz", "name": "Web Technologies", "course_number": "CSCI 571",
                                             "term": "Fall 2026", "status": "active"}]}

    def respond(self, method, url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"result": status}).encode()
        return response

    with patch.object(requests.Session, "request", respond):
        result = asyncio.run(execute({"provider": "piazza", "action": "call", "tool": "list_courses", "arguments": {}}))
    text = "".join(block["text"] for block in result["content"])
    assert "https://piazza.com/class/abc123xyz" in text
