# Handshake integration

TrojanClaw uses [shahparam11/handshake-mcp-server](https://github.com/shahparam11/handshake-mcp-server), with a local **read-only wrapper** in `integrations/handshake`. This follows Brightspace's local stdio and read-only approach. The upstream server also supports writes; always launch the TrojanClaw wrapper instead of the upstream server directly.

## Setup

Requires Git, uv, and Python 3.12–3.14. Run these commands from the repository root on Windows, macOS, or Linux:

```sh
uv sync --locked --project integrations/handshake
uv run --locked --project integrations/handshake patchright install chromium
uv run --locked --project integrations/handshake trojanclaw-handshake login
uv run --locked --project integrations/handshake trojanclaw-handshake status
```

The login command launches Chromium through Patchright with a persistent browser profile. Select your school, sign in yourself, and complete any SSO/MFA prompts. The connector checks all Handshake tabs and confirms the current account through a read-only browser request before automatically saving the session and closing the window. It does not rely on a fixed dashboard URL. Later login runs reuse the same profile. There is no manual cookie-copy step. Leave Chromium open until it closes automatically.

The profile lives in `~/.trojanclaw/connectors/handshake/profile/` and the upstream server automatically saves a cookie snapshot beside it for MCP requests. `TROJANCLAW_SESSION_DIR` changes the common base directory used by both the chat and connector processes. These session files stay outside the project by default. Old sessions under `~/.handshake-mcp` are not imported automatically; reconnect once from the chat. The shared connector cookie file and `TROJANCLAW_COOKIE_FILE` are no longer used.

School-specific Handshake addresses are supported: for example, USC can use
`usc.joinhandshake.com` rather than `app.joinhandshake.com`. The connector saves
the authenticated address in `origin.json` beside the session and configures the
MCP client to use that same address. Only HTTPS subdomains of `joinhandshake.com`
are accepted. Sign-in completion is checked in every open Handshake tab, including
tabs opened by SSO, rather than relying on the original tab or a fixed URL path.

Persistence lets you reuse the session across restarts, but Handshake can still expire or revoke it. Run `login` again when needed. `status` checks the saved session with a live read-only request and returns a nonzero exit code if authentication fails.

## Running

```sh
uv run --locked --project integrations/handshake trojanclaw-handshake serve
```

This starts a stdio MCP server; it waits for an MCP client rather than opening a web page. Configure a local MCP client with this entry, replacing the project path with your absolute path:

```json
{
  "mcpServers": {
    "handshake": {
      "command": "uv",
      "args": [
        "run", "--locked", "--project",
        "E:/Projects/TrojanClaw_v2/integrations/handshake",
        "trojanclaw-handshake", "serve"
      ]
    }
  }
}
```

The source is pinned to commit `c61800020a4ddcf9e41b1526d0a29154c3781c9a`, FastMCP to 3.0.2, and transitive dependencies to the checked-in `uv.lock`. This environment is separate from FastAPI's older dependency pins.

## What can be pulled

| Tool | Inputs / result |
|---|---|
| `hs_search_jobs` | Optional `query`, `per_page` (default 20, capped at 50), `after`; returns personalized jobs, total count, and `pageInfo` cursor metadata |
| `hs_get_job` | `job_id`; description, employer, requirements for applying, existing application status |
| `hs_search_employers` | Optional `query`, `limit` (default 25, capped at 100); matching employers |
| `hs_get_employer` | `employer_id`; company description, industry, location, website |
| `hs_get_applications` | Your applications and statuses; upstream does not expose pagination here |
| `hs_get_profile` | Your profile, education, skills, experience, projects |
| `hs_get_documents` | Document metadata (IDs, names, types, dates), not file contents |
| `hs_get_interest_options` | Available job types, employment types, industries |
| `hs_get_event` | `event_id`; event details and registration status |
| `hs_get_career_fair` | `career_fair_id`; fair details and registration status |

Use `pageInfo.endCursor` as `after` while `pageInfo.hasNextPage` is true for job search. Event and fair tools require IDs obtained elsewhere; this version has no event search. There are no dedicated location or job-type filters on the pinned job-search tool.

Only the ten tools above are registered. Applying, withdrawing, saving jobs, uploading/deleting documents, changing a profile, following employers, and event registration are unavailable through this wrapper. Unknown tools are excluded by default. Results are structured MCP data; the original source text should be treated as untrusted content.

## Session refresh

```sh
uv run --locked --project integrations/handshake trojanclaw-handshake login
```

Re-run login if calls report missing credentials, expired sessions, or inability to obtain a CSRF token, then restart the MCP client. Session lifetime is unknown. To remove the saved Chromium profile and cookie snapshot:

```sh
uv run --locked --project integrations/handshake trojanclaw-handshake logout
```

Logout removes Handshake's saved profile and cookie snapshot. It does not affect your normal browser or the Brightspace/Piazza connectors. Handshake's internal API is unofficial and can change independently of this connector.

## TrojanClaw backend status

Sign in with USC connects Handshake automatically. To connect it again, open **Sources** in the chat sidebar, click **Connect**, sign in in Chromium, and wait for **Connected**. Connected sources are turned on for chat automatically (see **Settings → Default sources**); the **Sources** button in the chat box turns each one on or off. Chat calls the allowed tools live and sends their results to the configured Anthropic model to answer your question. This does not synchronize account data into the database. See [CONNECTORS.md](../CONNECTORS.md) for the complete local workflow.

## Verification

```sh
uv run --locked --project integrations/handshake --group dev pytest integrations/handshake/tests -q
```

Tests exercise MCP discovery and stdio startup, keyword/cursor dispatch with a mocked Handshake response, required input validation, missing-login errors, rejection of write tools, persistent-profile login delegation, session reuse, and authentication status checks. They do not use a real account.

Example questions for a connected MCP client:

- "Find software engineering internships on Handshake."
- "Which applications have an interview status?"
- "Tell me about the employer for this posting."

Status (2026-11-14): local connector implemented; real USC login and live tool responses still need verification.


## Event and career fair catalog

The local `trojanclaw_handshake/events.py` module replaces the upstream event/fair
read tools with queries against the authenticated school origin's `/stu/graphql`
student API. The older `/stu/events` suggested feed repeated records across pages
and is not used as a full event catalog.

- `hs_search_events` and `hs_search_career_fairs` accept keyword, date-window,
  saved/registered/school filters, `per_page` (1-50), and an opaque `after` cursor.
- Results contain `results`, `count`, `has_more`, and `next_cursor`. Keep the same
  filters and pass each cursor unchanged until `has_more` is false.
- `hs_get_event` and `hs_get_career_fair` use returned IDs and current student-API
  fields. Employer lists are bounded previews, with completeness metadata.
- Requests reuse the existing cookie, school-origin and CSRF transport. This
  feature does not change login or Cloudflare handling and adds no write tools.

The wrapper excludes upstream event/fair implementations before registering the
four local tools, preventing duplicate names and excluding registration/following.
A single page is not the complete catalog; access follows the student's account.

```sh
uv run --locked --project integrations/handshake --group dev python -m pytest integrations/handshake/tests/test_events.py integrations/handshake/tests/test_server.py -q
```
