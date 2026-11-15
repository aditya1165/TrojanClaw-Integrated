"""Read-only Handshake student event catalog; scoped to the supplied account."""

from typing import Annotated, Literal
from urllib.parse import quote, urlparse

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

import handshake_mcp.client as client


DateWindow = Literal["ALL", "TODAY", "NEXT_5", "NEXT_7", "NEXT_10", "NEXT_30", "PAST_YEAR"]
PageSize = Annotated[int, Field(ge=1, le=50)]
Cursor = Annotated[str | None, Field(max_length=2048)]
Keyword = Annotated[str, Field(max_length=300)]
EventID = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]

EVENT_TOOLS = frozenset({"hs_search_events", "hs_search_career_fairs", "hs_get_event", "hs_get_career_fair"})

CATALOG_QUERY = """query TrojanClawEventCatalog(
  $params: EventAbstractionSearchInput, $first: Int, $after: String
) {
  eventAbstractions(params: $params, first: $first, after: $after) {
    pageInfo { endCursor hasNextPage }
    edges {
      node {
        __typename id name startDate endDate medium registered favorited
        employers(limit: 3) { id name }
        categories { id name behaviorIdentifier }
        ... on Event {
          host { name }
          studentRegistrationStart studentRegistrationEnd
        }
        ... on CareerFair {
          host { name }
          studentRegistrationStart studentRegistrationEnd
        }
      }
    }
  }
}"""


class EmployerSummary(BaseModel):
    id: str
    name: str


class CategorySummary(BaseModel):
    id: str
    name: str
    behavior_identifier: str | None = None


class EventSummary(BaseModel):
    id: str
    name: str
    kind: Literal["event", "career_fair"]
    url: str
    start_date: str | None = None
    end_date: str | None = None
    medium: str | None = None
    registered: bool | None = None
    saved: bool | None = None
    host: str | None = None
    registration_start: str | None = None
    registration_end: str | None = None
    employers: list[EmployerSummary] = Field(default_factory=list, description="Employer previews, not a complete employer directory.")
    categories: list[CategorySummary] = Field(default_factory=list)


class EventPage(BaseModel):
    results: list[EventSummary]
    count: int
    has_more: bool
    next_cursor: str | None
    date_window: DateWindow


class EventDetail(EventSummary):
    description: str | None = None
    time_zone: str | None = None
    external_url: str | None = None
    total_employers: int | None = None
    employer_preview_limit: int
    employers_truncated: bool | None


DETAIL_FIELDS = """
  __typename id name startDate endDate medium registered favorited totalEmployersCount
  employers(limit: $employersLimit) { id name }
  categories { id name behaviorIdentifier }
  host { name }
  studentRegistrationStart studentRegistrationEnd
"""
EVENT_QUERY = "query TrojanClawEvent($id: ID!, $employersLimit: Int) { event(id: $id) { " + DETAIL_FIELDS + " description timeZone externalLink } }"
FAIR_QUERY = "query TrojanClawCareerFair($id: ID!, $employersLimit: Int) { careerFair(id: $id) { " + DETAIL_FIELDS + " } }"


async def student_query(query: str, variables: dict) -> dict:
    """Use the pinned client's cookie/CSRF transport, with the student API path."""
    try:
        response = await client._request("POST", client.BASE_URL + "/stu/graphql",
                                         json={"query": query, "variables": variables})
        client._raise_for_status(response, "student event catalog")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise ValueError
        return payload["data"]
    except Exception:
        # Never forward provider bodies, credentials, or arbitrary GraphQL errors.
        raise ToolError("Handshake events could not be read. Try again; reconnect Handshake if your session expired.") from None


def event_summary(node: dict) -> EventSummary:
    kind = {"Event": "event", "CareerFair": "career_fair"}.get(node.get("__typename"))
    if kind is None or not node.get("id") or str(node["id"]) in {".", ".."} or not isinstance(node.get("name"), str):
        raise ValueError("Unexpected event catalog record")
    identity = str(node["id"])
    path = "events" if kind == "event" else "career_fairs"
    return EventSummary(
        id=identity, name=node["name"], kind=kind,
        url=f"{client.BASE_URL}/stu/{path}/{quote(identity, safe='')}",
        start_date=node.get("startDate"), end_date=node.get("endDate"),
        medium=node.get("medium"), registered=node.get("registered"), saved=node.get("favorited"),
        host=(node.get("host") or {}).get("name"),
        registration_start=node.get("studentRegistrationStart"), registration_end=node.get("studentRegistrationEnd"),
        employers=[EmployerSummary(id=str(item["id"]), name=item["name"]) for item in node.get("employers") or []],
        categories=[CategorySummary(id=str(item["id"]), name=item["name"], behavior_identifier=item.get("behaviorIdentifier"))
                    for item in node.get("categories") or []],
    )


async def search_catalog(kind: Literal["Event", "CareerFair"], query: str, per_page: int, after: str | None,
                         date_window: DateWindow, registered_only: bool, saved_only: bool,
                         posted_by_school: bool) -> EventPage:
    params = {"keyword": query.strip(), "sort": "DATE", "date": date_window, "searchModels": [kind]}
    for key, enabled in (("registeredOnly", registered_only), ("favoritedOnly", saved_only), ("postedBySchool", posted_by_school)):
        if enabled:
            params[key] = True
    data = await student_query(CATALOG_QUERY, {"params": params, "first": per_page, "after": after})
    try:
        catalog = data["eventAbstractions"]
        page = catalog["pageInfo"]
        has_more, cursor = page["hasNextPage"], page.get("endCursor")
        if not isinstance(has_more, bool) or has_more and (not isinstance(cursor, str) or not cursor or cursor == after):
            raise ValueError("Invalid catalog pagination")
        nodes = [edge["node"] for edge in catalog["edges"] if edge and edge.get("node")]
        if len(nodes) > per_page or any(node.get("__typename") != kind for node in nodes):
            raise ValueError("Unexpected catalog result scope")
        results = [event_summary(node) for node in nodes]
        return EventPage(results=results, count=len(results), has_more=has_more,
                         next_cursor=cursor if has_more else None, date_window=date_window)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ToolError("Handshake returned an unsupported event catalog response. Try again later.") from None


async def get_detail(kind: str, identity: str, max_employers: int) -> EventDetail:
    root, query = ("event", EVENT_QUERY) if kind == "Event" else ("careerFair", FAIR_QUERY)
    data = await student_query(query, {"id": identity, "employersLimit": max_employers})
    node = data.get(root)
    if node is None:
        raise ToolError("This event or career fair is unavailable to your account. Search again for an accessible ID.")
    try:
        if node.get("__typename") != kind:
            raise ValueError
        summary = event_summary(node)
        total = node.get("totalEmployersCount")
        if total is not None and (type(total) is not int or total < len(summary.employers)):
            raise ValueError
        if len(summary.employers) > max_employers:
            raise ValueError
        external = node.get("externalLink")
        if external:
            try:
                parsed = urlparse(external) if isinstance(external, str) else None
                if (parsed is None or parsed.scheme not in {"https", "http"} or not parsed.hostname
                        or parsed.username or parsed.password or len(external) > 2048
                        or any(ord(char) < 32 for char in external)):
                    external = None
            except ValueError:
                external = None
        return EventDetail(**summary.model_dump(), description=node.get("description"),
                           time_zone=node.get("timeZone"), external_url=external,
                           total_employers=total, employer_preview_limit=max_employers,
                           employers_truncated=total > len(summary.employers) if total is not None else None)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ToolError("Handshake returned unsupported event details. Open the source link or try again later.") from None


def register(mcp):
    @mcp.tool()
    async def hs_get_event(event_id: EventID, max_employers: PageSize = 20) -> EventDetail:
        """Read one Handshake event by an ID from hs_search_events.

        Returns its description, dates/time zone, host, registration window,
        your saved/registered state, source link and a bounded employer preview.
        This does not register, save or follow the event.
        """
        return await get_detail("Event", event_id, max_employers)

    @mcp.tool()
    async def hs_get_career_fair(career_fair_id: EventID, max_employers: PageSize = 20) -> EventDetail:
        """Read one Handshake career fair by an ID from hs_search_career_fairs.

        Returns dates, host, registration window, your saved/registered state,
        source link and a bounded employer preview. Check employers_truncated;
        this preview is not the complete fair directory. Fair descriptions are
        not exposed by this read API. Never registers or changes the account.
        """
        return await get_detail("CareerFair", career_fair_id, max_employers)

    @mcp.tool()
    async def hs_search_events(query: Keyword = "", per_page: PageSize = 20, after: Cursor = None,
                               date_window: DateWindow = "ALL", registered_only: bool = False,
                               saved_only: bool = False, posted_by_school: bool = False) -> EventPage:
        """Find Handshake events visible to the signed-in student, with IDs, dates, hosts and source links.

        Empty query browses the catalog. Dates use Handshake's presets: TODAY,
        NEXT_5/7/10/30 days, PAST_YEAR, or ALL (the site's default date window).
        Follow next_cursor with the same filters until has_more is false before
        claiming a complete list. For fairs use hs_search_career_fairs instead.
        Employer previews contain at most three employers; details give a larger preview.
        Read-only: never registers, follows, or saves anything.
        """
        return await search_catalog("Event", query, per_page, after, date_window, registered_only, saved_only, posted_by_school)

    @mcp.tool()
    async def hs_search_career_fairs(query: Keyword = "", per_page: PageSize = 20, after: Cursor = None,
                                    date_window: DateWindow = "ALL", registered_only: bool = False,
                                    saved_only: bool = False, posted_by_school: bool = False) -> EventPage:
        """Find Handshake career fairs visible to the signed-in student, with IDs, dates and source links.

        Empty query browses fairs. Date presets are TODAY, NEXT_5/7/10/30,
        PAST_YEAR, or ALL (Handshake's default window). Use next_cursor with
        unchanged filters until has_more is false for all matches. Employer
        previews contain up to three employers, not the entire fair directory.
        Read-only: never registers the student for a fair.
        """
        return await search_catalog("CareerFair", query, per_page, after, date_window, registered_only, saved_only, posted_by_school)
