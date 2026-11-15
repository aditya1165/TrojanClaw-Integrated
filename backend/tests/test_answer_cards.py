"""Result cards in replies: cleaned, and linked only to pages a tool returned."""

import json

from app.agent.runtime import _cited_first
from app.services.answer_cards import card_urls, verify_cards

JOB = "https://app.joinhandshake.com/stu/jobs/11467469"


def cards(text):
    start = text.index("```cards\n") + len("```cards\n")
    return json.loads(text[start:text.index("\n```", start)])


def test_known_links_are_kept_and_invented_links_removed():
    reply = (
        "Here are two roles.\n\n```cards\n"
        + json.dumps({"title": "ML roles", "items": [
            {"kind": "job", "title": "MLE Graduate", "subtitle": "TikTok · Culver City", "url": JOB, "tags": ["Full-time"]},
            {"kind": "job", "title": "Made up", "url": "https://app.joinhandshake.com/stu/jobs/999"},
        ]})
        + "\n```\n\nApply early."
    )
    cleaned = verify_cards(reply, {JOB})
    data = cards(cleaned)
    assert data["title"] == "ML roles"
    assert data["items"][0]["url"] == JOB
    assert "url" not in data["items"][1]  # never returned by a tool
    assert cleaned.startswith("Here are two roles.") and cleaned.endswith("Apply early.")
    assert card_urls(cleaned) == {JOB}


def test_fields_are_limited_and_unknown_ones_dropped():
    reply = "```cards\n" + json.dumps({"items": [
        {"kind": "rocket", "title": "  Career   fair ", "tags": ["a", "b", "c", "d", "e", 5], "note": "x" * 900,
         "onclick": "alert(1)", "url": "javascript:alert(1)"},
        {"subtitle": "no title"},
    ]}) + "\n```"
    [item] = cards(verify_cards(reply, set()))["items"]
    assert item["kind"] == "link" and item["title"] == "Career fair"
    assert item["tags"] == ["a", "b", "c", "d"]
    assert len(item["note"]) == 300
    assert "onclick" not in item and "url" not in item


def test_a_bare_list_and_broken_json_still_render():
    assert cards(verify_cards('```cards\n[{"title": "HW4"}]\n```', set()))["items"][0]["title"] == "HW4"
    broken = verify_cards('Due soon:\n```cards\n{"items": [{"title": "HW4", "when": "Fri"},]}\n```', set())
    assert "```" not in broken and "- HW4" in broken


def test_replies_without_cards_are_unchanged():
    text = "Plain answer with a [link](https://example.com)."
    assert verify_cards(text, set()) == text


def test_sources_show_only_what_the_reply_links_to():
    events = [{"title": f"Event {n}", "url": f"https://engage.usc.edu/rsvp?id={n}", "kind": "campus_event"} for n in range(30)]
    connector = {"title": "Handshake", "url": "https://app.joinhandshake.com", "kind": "connector"}
    reply = "See [Event 3](https://engage.usc.edu/rsvp?id=3) and https://engage.usc.edu/rsvp?id=7."
    shown = _cited_first(events + [connector], reply)
    assert [s["title"] for s in shown] == ["Event 3", "Event 7", "Handshake"]
    # A reply that links to nothing shows only a few.
    assert len(_cited_first(events, "No links here.")) == 5


def test_a_link_is_cited_only_when_the_whole_link_appears():
    sources = [{"title": "One", "url": "https://engage.usc.edu/rsvp?id=1", "kind": "campus_event"},
               {"title": "Thirteen", "url": "https://engage.usc.edu/rsvp?id=13", "kind": "campus_event"}]
    assert [s["title"] for s in _cited_first(sources, "Go to https://engage.usc.edu/rsvp?id=13.")] == ["Thirteen"]


def test_links_survive_a_connector_result_cut_at_the_size_limit():
    from app.services.sources import collect_links
    jobs = [{"id": str(n), "title": f"Job {n}", "url": f"https://app.joinhandshake.com/stu/jobs/{n}"} for n in range(400)]
    full = json.dumps([{"type": "text", "text": json.dumps({"results": jobs})}])
    cut = full[:20_000] + "\n[Result shortened to 20,000 characters.]"
    found = {}
    collect_links(cut, "handshake", found)
    assert "https://app.joinhandshake.com/stu/jobs/0" in found
    assert len(found) > 50
    assert all(url.startswith("https://app.joinhandshake.com/stu/jobs/") and "\\" not in url for url in found)
    # An uncut result still gets real titles.
    whole = {}
    collect_links(json.dumps([{"type": "text", "text": json.dumps({"results": jobs[:2]})}]), "handshake", whole)
    assert whole["https://app.joinhandshake.com/stu/jobs/1"]["title"] == "Job 1"
