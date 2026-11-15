"""Source links under replies and activity text."""

import json

from app.services.sources import activity_for, cited_sources, collect_links


def test_only_links_from_tools_that_the_answer_cites_become_sources():
    result = json.dumps([{"type": "text", "text": json.dumps([
        {"title": "ASN-04", "url": "https://brightspace.usc.edu/a"},
        {"title": "Quiz 3", "url": "https://brightspace.usc.edu/q"},
        {"title": "Unsafe", "url": "http://evil.example/"},
    ])}])
    found = {}
    collect_links(result, "brightspace", found)
    assert set(found) == {"https://brightspace.usc.edu/a", "https://brightspace.usc.edu/q"}
    answer = "ASN-04 is due tonight: [submit](https://brightspace.usc.edu/a). See https://made-up.example/"
    assert cited_sources(answer, found, ["brightspace"]) == [
        {"label": "ASN-04", "url": "https://brightspace.usc.edu/a", "provider": "brightspace"}]


def test_without_cited_links_the_checked_sources_are_named():
    assert cited_sources("Nothing due.", {}, ["brightspace", "handshake", "brightspace"]) == [
        {"label": "Brightspace", "url": None, "provider": "brightspace"},
        {"label": "Handshake", "url": None, "provider": "handshake"}]
    assert cited_sources("Hi", {}, []) == []


def test_activity_text_is_readable():
    assert activity_for("brightspace__get_upcoming_due_dates") == "Checking Brightspace: upcoming due dates"
    assert activity_for("handshake__search_jobs") == "Checking Handshake: search jobs"

