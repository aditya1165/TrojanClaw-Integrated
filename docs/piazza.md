# Piazza connector

Managed Browserbase authentication is the default. The current Google-first flow and Sources UI use encrypted per-account PostgreSQL sessions. See [the connector guide](../CONNECTORS.md) and [remote-browser implementation](remote-browser.md). Desktop commands and cookie-file examples below apply only to explicit `CONNECTOR_MODE=local` development.


Piazza is available in Extensions and the Claude chat agent. It uses
[mcp-piazza](https://github.com/jadenpxrk/mcp-piazza), pinned to 0.1.0, in its own
Python environment. Piazza requires FastMCP 4; Handshake stays on FastMCP 3.

## Setup

From the project root:

```sh
uv sync --locked --project integrations/piazza --python 3.12
uv run --locked --project integrations/piazza patchright install chromium
```

Restart the backend, sign into the app and choose **Connect** on Piazza. Sign into
the visible Chromium window and reach a class dashboard. The connector verifies
the account, saves its session and closes the window automatically. Leave it open
until then; sign-in times out after five minutes. **Check session** reads courses
through MCP without opening a browser. **Sign in again** reuses the same profile.

Piazza has a separate sign-in. USC bootstrap still connects only Brightspace and
Handshake. Use whichever login method Piazza offers; never paste credentials into chat.

## Tools

| Tools | Access |
|---|---|
| `list_courses`, `get_course_info`, `get_course_stats` | Enrolled courses, syllabus/staff details, available statistics |
| `list_posts`, `search_posts` | Summaries, folders, unread filters, keyword search |
| `get_post`, `get_posts`, `get_post_history` | Full threads, answers, follow-ups, revisions |
| `recent_activity`, `get_unread_counts`, `list_instructor_notes` | Catch-up, unread counts, instructor announcements |

Tools are named `piazza__<tool>` in the agent. The chat goal in `config/agent.yaml`
allows Piazza, with the same selection and saved-session behavior as the other
connectors. The agent includes Piazza source attribution. Attachment downloads,
arbitrary URL fetching, posting, editing, replying and deleting are excluded.

Reading threads can increment views and mark posts read. The adapter enables
upstream `keep_unread=True` and waits for restoration threads before exiting.
Restoration is best effort; provider errors or process interruption can leave
a thread read. Course/post content is fenced as untrusted source material.
Upstream search, pagination and batch limits apply.

## Storage and isolation

```text
~/.trojanclaw/connectors/users/<app-account-UUID>/piazza/
  profile/       # Persistent Chromium login state
  cookies.json   # Only session_id and piazza_session for piazza.com
```

Both cookies are required: `session_id` alone did not authenticate the live API.
The existing local connector service supplies the per-account root, honoring
`TROJANCLAW_SESSION_DIR`. Snapshots use atomic writes and private file permissions
where supported by the OS. These files are credentials; keep the folder private
and out of Git. HTTP routes use the authenticated account's ID and do not return
cookie values. Windows open on the computer running the backend.

This follows the current checkout's local storage model; sessions are not stored
in PostgreSQL or Browserbase. Every request uses a fresh isolated helper. It does
not scan browser cookies, read upstream's global cookie file, inherit upstream
credentials or server API keys, or start the upstream login web server. Cookie
save/load login fallbacks are disabled; only the explicit account snapshot is read.
Tool calls never open Chromium. Missing or expired sessions require **Connect**.

The transport permits HTTPS POSTs only to Piazza's fixed `/logic/api` and
`/main/api` endpoints, with an RPC method allowlist. Redirects and downloads are
disabled. Request/subprocess timeouts apply, and upstream errors are sanitized.

## Tests

```sh
uv run --locked --project integrations/piazza --group dev python -m pytest integrations/piazza/tests -q
```

Tests cover actual MCP discovery, fake-transport course verification, cookie and
account isolation, visible persistent-browser launch, forbidden tools/destinations,
and disabled global login fallback. Backend tests cover helper routing and agent
tool availability. A live enrollment check needs the account holder's Piazza login.
