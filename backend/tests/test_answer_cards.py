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


def test_calendar_event_links_from_the_calendar_tool_can_be_used_in_cards():
    import asyncio
    from types import SimpleNamespace
    from uuid import UUID
    from mcp.server.mcpserver import MCPServer
    from app.agent.config import load_agent_config
    from app.agent.runtime import AgentRuntime
    from app.config import get_settings

    event = "https://calendar.google.com/calendar/event?eid=abc"
    reply = "```cards\n" + json.dumps({"items": [{"kind": "event", "title": "CSCI 571 lecture", "url": event}]}) + "\n```"

    class Runner:
        async def until_done(self):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)], stop_reason="end_turn", model="m")

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(tool_runner=lambda **_: Runner())))
    runtime = AgentRuntime(load_agent_config(get_settings()), lambda _: MCPServer("test"), "key", client=client)
    run = lambda links: asyncio.run(runtime.run("chat", [{"role": "user", "content": "my week"}],
                                               user_id=UUID(int=2), extra_links=links))
    assert cards(run({event}).text)["items"][0]["url"] == event  # returned by list_calendar_events
    assert "url" not in cards(run(set()).text)["items"][0]  # not returned by any tool


def test_draft_replies_are_short_and_read_naturally():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from types import SimpleNamespace
    from app.models.calendar import CalendarEventDraft
    from app.services.chat import draft_reply

    pt = ZoneInfo("America/Los_Angeles")
    same_day = CalendarEventDraft(title="Study block", start=datetime(2030, 11, 18, 15, 0, tzinfo=pt),
                                  end=datetime(2030, 11, 18, 15, 20, tzinfo=pt), timezone="America/Los_Angeles")
    text = draft_reply(SimpleNamespace(event=same_day, operation="create"))
    assert "**Study block** on Mon, Nov 18, 3:00 – 3:20 PM PST." in text and "**Approve addition**" in text
    across = same_day.model_copy(update={"start": datetime(2030, 11, 18, 11, 30, tzinfo=pt), "end": datetime(2030, 11, 18, 13, 0, tzinfo=pt)})
    assert "11:30 AM – 1:00 PM PST" in draft_reply(SimpleNamespace(event=across, operation="create"))
    assert "**Approve changes**" in draft_reply(SimpleNamespace(event=same_day, operation="update"))
