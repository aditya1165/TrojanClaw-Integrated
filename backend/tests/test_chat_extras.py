"""Activity text while the agent works, and connector links a reply cites."""

import json

from app.services.sources import activity_for, cited_links, collect_links


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
    assert cited_links(answer, found) == [
        {"title": "ASN-04", "url": "https://brightspace.usc.edu/a", "course": None, "kind": "connector", "provider": "brightspace"}]
    assert cited_links("Nothing cited.", found) == []


def test_activity_text_is_readable():
    assert activity_for("brightspace__get_upcoming_due_dates") == "Checking Brightspace: upcoming due dates"
    assert activity_for("piazza__search_posts") == "Checking Piazza: search posts"
    assert activity_for("list_deadlines") == "Checking your deadlines"
    assert activity_for("something_else") == "Looking that up"
