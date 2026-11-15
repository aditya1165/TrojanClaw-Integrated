"""Exercise catalog pagination, student API details, scoping and write boundaries."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

import handshake_mcp.client as upstream
from trojanclaw_handshake import create_server
from trojanclaw_handshake.events import CATALOG_QUERY, EVENT_QUERY, FAIR_QUERY


SESSION = {"origin": "https://school.joinhandshake.com", "cookies": [
    {"domain": "school.joinhandshake.com", "name": "session", "value": "account-cookie"},
]}


def node(kind="Event", identity="123"):
    return {"__typename": kind, "id": identity, "name": "Engineering opportunities",
            "startDate": "2026-12-01T18:00:00Z", "endDate": "2026-12-01T20:00:00Z",
            "medium": "VIRTUAL", "registered": False, "favorited": True,
            "host": {"name": "Career Center"}, "employers": [{"id": "9", "name": "Example Employer"}],
            "categories": [{"id": "5", "name": "Hiring", "behaviorIdentifier": "hiring"}],
            "studentRegistrationStart": "2026-11-01T00:00:00Z", "studentRegistrationEnd": None}


def page(nodes, more=False, cursor=None):
    return {"data": {"eventAbstractions": {"edges": [{"node": item} for item in nodes],
            "pageInfo": {"hasNextPage": more, "endCursor": cursor}}}}


def unpack(result):
    return json.loads(result.content[0].text)


@pytest.fixture
def catalog(monkeypatch):
    # create_server binds account credentials in process; restore every modified
    # upstream global so no following desktop test inherits this fake account.
    for key in ("load_cookies", "BASE_URL", "API_BASE", "GQL_URL", "_csrf_token", "_request", "_fetch_csrf_token"):
        monkeypatch.setattr(upstream, key, getattr(upstream, key))
    requests, responses = [], []

    async def request(method, url, **kwargs):
        assert upstream.load_cookies() == {"session": "account-cookie"}
        requests.append((method, url, deepcopy(kwargs["json"])))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(status_code=200, json=lambda: response)

    server = create_server(session=SESSION)
    monkeypatch.setattr(upstream, "_request", request)
    return server, requests, responses


def test_event_pagination_preserves_opaque_cursor_and_filters(catalog):
    server, requests, responses = catalog
    responses.extend([page([node()], True, "Mg==\n"), page([node(identity="124")])])

    async def check():
        async with Client(server) as client:
            arguments = {"query": "  software  ", "per_page": 1, "date_window": "NEXT_7",
                         "registered_only": True, "saved_only": True, "posted_by_school": True}
            first = unpack(await client.call_tool("hs_search_events", arguments))
            second = unpack(await client.call_tool("hs_search_events", {**arguments, "after": first["next_cursor"]}))
            assert first["count"] == 1 and first["has_more"] is True
            assert first["next_cursor"] == "Mg==\n"
            assert second["has_more"] is False and second["next_cursor"] is None
            assert first["results"][0]["id"] != second["results"][0]["id"]
            assert first["results"][0]["url"] == "https://school.joinhandshake.com/stu/events/123"
    asyncio.run(check())
    assert all(method == "POST" and url == SESSION["origin"] + "/stu/graphql" for method, url, _ in requests)
    assert requests[0][2]["query"] == CATALOG_QUERY
    assert requests[0][2]["variables"]["params"] == {
        "keyword": "software", "sort": "DATE", "date": "NEXT_7", "searchModels": ["Event"],
        "registeredOnly": True, "favoritedOnly": True, "postedBySchool": True,
    }
    assert requests[1][2]["variables"]["after"] == "Mg==\n"
    assert requests[0][2]["variables"]["params"] == requests[1][2]["variables"]["params"]


def test_fairs_have_a_distinct_catalog_scope_and_links(catalog):
    server, requests, responses = catalog
    responses.append(page([node("CareerFair")]))
    async def check():
        async with Client(server) as client:
            result = unpack(await client.call_tool("hs_search_career_fairs", {}))
            assert result["results"][0]["kind"] == "career_fair"
            assert result["results"][0]["url"] == "https://school.joinhandshake.com/stu/career_fairs/123"
    asyncio.run(check())
    assert requests[0][2]["variables"]["params"]["searchModels"] == ["CareerFair"]


def test_empty_catalog_is_an_explicit_empty_page(catalog):
    server, _, responses = catalog
    responses.append(page([]))
    async def check():
        async with Client(server) as client:
            result = unpack(await client.call_tool("hs_search_events", {}))
            assert result == {"results": [], "count": 0, "has_more": False, "next_cursor": None, "date_window": "ALL"}
    asyncio.run(check())


@pytest.mark.parametrize("tool,arguments", [
    ("hs_search_events", {"per_page": 0}), ("hs_search_events", {"per_page": 51}),
    ("hs_search_career_fairs", {"date_window": "THIS_WEEK"}),
    ("hs_search_events", {"query": "a" * 301}), ("hs_search_events", {"after": "a" * 2049}),
    ("hs_get_event", {"event_id": "../123"}), ("hs_get_career_fair", {"career_fair_id": ""}),
    ("hs_get_career_fair", {"career_fair_id": "123", "max_employers": 500}),
])
def test_invalid_arguments_are_rejected_before_network(catalog, tool, arguments):
    server, requests, _ = catalog
    async def check():
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool(tool, arguments)
    asyncio.run(check())
    assert requests == []


@pytest.mark.parametrize("broken", [
    {"data": {"eventAbstractions": {}}},
    page([node("CareerFair")]),
    page([node()], True, None),
    page([node()], True, "same-cursor"),
    {"errors": [{"message": "private-provider-body"}]},
    RuntimeError("private-provider-body"),
])
def test_schema_and_transport_errors_are_not_reported_as_complete_results(catalog, broken):
    server, _, responses = catalog
    responses.append(broken)
    async def check():
        async with Client(server) as client:
            with pytest.raises(ToolError) as error:
                await client.call_tool("hs_search_events", {"after": "same-cursor"})
            assert "private-provider-body" not in str(error.value)
    asyncio.run(check())


@pytest.mark.parametrize("kind,tool,argument,query", [
    ("Event", "hs_get_event", "event_id", EVENT_QUERY),
    ("CareerFair", "hs_get_career_fair", "career_fair_id", FAIR_QUERY),
])
def test_details_use_student_fields_and_mark_employer_previews(catalog, kind, tool, argument, query):
    server, requests, responses = catalog
    item = node(kind)
    item["totalEmployersCount"] = 5
    if kind == "Event":
        item.update(description="<p>Meet employers.</p>", timeZone="America/Los_Angeles", externalLink="https://example.edu/event")
    responses.append({"data": {"event" if kind == "Event" else "careerFair": item}})
    async def check():
        async with Client(server) as client:
            result = unpack(await client.call_tool(tool, {argument: "123", "max_employers": 1}))
            assert result["employers_truncated"] is True and result["total_employers"] == 5
            assert result["registration_start"] == item["studentRegistrationStart"]
            assert result["description"] == ("<p>Meet employers.</p>" if kind == "Event" else None)
    asyncio.run(check())
    assert requests[0][2] == {"query": query, "variables": {"id": "123", "employersLimit": 1}}
    assert "registrationStatus" not in query


def test_missing_detail_has_an_actionable_account_scoped_error(catalog):
    server, _, responses = catalog
    responses.append({"data": {"event": None}})
    async def check():
        async with Client(server) as client:
            with pytest.raises(ToolError, match="unavailable to your account"):
                await client.call_tool("hs_get_event", {"event_id": "123"})
    asyncio.run(check())


def test_search_text_cannot_change_the_graphql_operation(catalog):
    server, requests, responses = catalog
    responses.append(page([]))
    text = '"} mutation { registerAttendee }'
    async def check():
        async with Client(server) as client:
            await client.call_tool("hs_search_events", {"query": text})
    asyncio.run(check())
    assert requests[0][2]["query"] == CATALOG_QUERY
    assert requests[0][2]["variables"]["params"]["keyword"] == text
