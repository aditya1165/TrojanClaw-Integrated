"""Jobs, employers and applications carry links to their Handshake pages."""

import asyncio

from fastmcp import Client

from trojanclaw_handshake import create_server
from trojanclaw_handshake.links import add_links, page
import handshake_mcp.client as handshake_client


def test_search_results_link_each_job_and_employer_through_mcp(monkeypatch):
    async def fake_gql(query, variables):
        return {"jobSearch": {"totalCount": 1, "pageInfo": {}, "nodes": [
            {"job": {"id": "11467469", "title": "ML Engineer", "employer": {"id": "42", "name": "TikTok"}}},
        ]}}

    monkeypatch.setattr("handshake_mcp.tools.jobs.gql", fake_gql)
    monkeypatch.setattr(handshake_client, "BASE_URL", "https://usc.joinhandshake.com")

    async def check():
        async with Client(create_server()) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            # Wrapping keeps the upstream input schema.
            assert {"query", "per_page", "after"} <= tools["hs_search_jobs"].inputSchema["properties"].keys()
            result = await client.call_tool("hs_search_jobs", {"query": "ml"})
            job = result.data["results"][0]
            assert job["url"] == "https://usc.joinhandshake.com/stu/jobs/11467469"
            assert job["employer"]["url"] == "https://usc.joinhandshake.com/stu/employers/42"
    asyncio.run(check())


def test_job_details_employers_and_applications_get_links(monkeypatch):
    monkeypatch.setattr(handshake_client, "BASE_URL", "https://app.joinhandshake.com/")
    job = add_links("hs_get_job", {"id": "7", "employer": {"id": "9"}})
    assert job["url"] == "https://app.joinhandshake.com/stu/jobs/7"
    assert job["employer"]["url"] == "https://app.joinhandshake.com/stu/employers/9"
    found = add_links("hs_search_employers", {"employers": [{"id": "3", "name": "Acme"}]})
    assert found["employers"][0]["url"].endswith("/stu/employers/3")
    applications = add_links("hs_get_applications", {"nodes": [{"id": "a1", "job": {"id": "5", "employer": {"id": "6"}}}]})
    assert applications["nodes"][0]["job"]["url"].endswith("/stu/jobs/5")
    assert "url" not in applications["nodes"][0]  # the application itself has no public page


def test_missing_or_unsafe_ids_get_no_link():
    assert page("jobs", None) is None
    assert page("jobs", "..") is None
    assert page("jobs", "a/b").endswith("/stu/jobs/a%2Fb")
    assert add_links("hs_get_profile", {"id": "1"}) == {"id": "1"}  # other tools are unchanged
