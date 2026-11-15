# Chat connectors

Recurring reminders and edits: chat can draft a bounded daily/weekly/monthly series with optional popup reminders, or propose changes to an existing timed event. Existing-event edits require a read first to identify the event. The card shows the before/after details and whether the edit affects one occurrence or the whole series. **Approve changes** is the only way to apply an edit. New series are limited to 52 occurrences within a year, show every occurrence for review, and keep their local time across daylight saving changes. Deletions, guest changes and all-day event edits are not supported.

Calendar chat can read events from the connected primary Google Calendar using the local MCP server's `list-events` tool. “What are my events?” defaults to the next seven days; explicit ranges can cover up to 31 days. Results include timed and all-day events, with up to 100 events per response; a limited response asks the user to narrow the range. Reading requires no approval. Every calendar addition still requires the user to review its card and click **Approve addition**. Existing Google OAuth scopes already include event reading; reconnect only if Google reports missing permissions or expired access.

The main chat supports Brightspace, Handshake and Piazza with separate, account-scoped persistent Chromium profiles. Piazza has its own sign-in; see [Piazza setup](docs/piazza.md).

Once a connector has a saved session, chat uses it automatically: the student signs in once (Continue with Google, or **Connect** in Sources) and then just asks. See [How chat uses connectors](#how-chat-uses-connectors).

## Local setup

Requirements: Git, uv, Python 3.11 for FastAPI, Python 3.12–3.14 for the connector helper, Node.js 22 or newer for Brightspace and the frontend, and Google Chrome (the USC login popup opens in the installed Chrome). Run the backend on your own computer, in a single process, bound to `127.0.0.1`. The persistent browser profiles belong to that computer's owner; this prototype is not a hosted multi-user authentication service. Saved sessions are per TrojanClaw account (see [Persistent storage](#persistent-storage) below).

From the repository root:

```sh
uv sync --locked --project integrations/handshake
uv sync --locked --project integrations/piazza --python 3.12
uv run --locked --project integrations/piazza patchright install chromium
uv run --locked --project integrations/handshake patchright install chromium
```

Configure `backend/.env` using `backend/.env.example`. Chat needs `ANTHROPIC_API_KEY`, plus `DATABASE_URL` and `GEMINI_API_KEY` for the TrojanClaw tools (campus search, EngageSC events, synced course data). The Claude model and agent tools are set in `config/agent.yaml`, not in `.env`. No USC password goes into this file.

Start the backend in one terminal:

```sh
cd backend
uv run --no-project --python 3.11 --with-requirements requirements.txt uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`. The first Brightspace tool call may download the pinned `brightspace-mcp-server@5.2.3` through npx. Internet access is needed. The backend also recognizes a project-local Node installation under `.runtime/node-*-win-x64` if one is present.

The frontend uses `/api`; Vite proxies it to `127.0.0.1:8000`. Keep
`VITE_API_BASE_URL=/api` if you create a frontend `.env`. Mixing a frontend at
`127.0.0.1` with direct API calls to `localhost` can make the browser reject or
omit the HttpOnly sign-in cookie, causing 401 responses immediately after login.
Restart Vite after changing environment settings. Google OAuth additionally
requires its configured frontend/callback URLs to use matching hostnames.

## Signing in: Continue with Google, then one USC login

**Continue with Google** is the only way to sign in. It needs Google Calendar OAuth configured (see [Google Calendar setup](docs/google-calendar.md)) and the Calendar helper installed (`npm ci --prefix integrations/google-calendar`).

1. The student clicks **Get started**, then **Continue with Google**, and signs in with their @usc.edu Google account **in their own browser** (a normal page redirect). Only verified `usc.edu` accounts are accepted.
2. Back in the app, a small Chrome window opens on Brightspace and is brought to the front. The student logs in to USC (NetID + Duo). Its address bar stays visible so the student can check it is the real USC login page.
3. The backend asks Brightspace who logged in (`whoami`), checks it matches the Google account, and finds or creates the TrojanClaw account by Brightspace user ID. Brightspace's session is saved and Google Calendar is connected.
4. The app shows **Setting things up for you…** while the window (now minimized) signs in to Handshake and reads the WebReg course bin, both reusing the USC login from step 2. WebReg is skipped if it doesn't open by itself within 30 seconds.
5. The window closes by itself and the app opens the chat with the connected sources turned on.

Google sign-in deliberately stays in the student's own browser: Google (and USC's hand-off to Google) does not complete inside an automated browser window.

Why it works this way (checked against the live sites on 2026-11-14):

- Brightspace and Handshake use the same USC login: Brightspace goes through Microsoft to `shibboleth.usc.edu`, Handshake goes straight to `shibboleth.usc.edu`, and both end at `login.usc.edu`.
- Direct sign-in links skip the site's own login page: `https://brightspace.usc.edu/d2l/login` and `https://usc.joinhandshake.com/auth/saml/70/session/new?redirect_to_idp=true` (skips Handshake's "choose your school").
- USC's login cookies are session cookies, deleted when the browser closes. So every site is connected in the same browser right after the login, and nothing of the USC login itself is stored.
- Piazza's school SSO does not include USC, so Piazza cannot use the USC login.

**Connect** in Sources still works for any source, for example after a session expires.

The backend must run on the student's computer, because the popup opens there.

## Connect and ask

1. In the chat, open **Sources** (sidebar, or **Manage connections** in the chat box's Sources menu) and click **Connect** on Brightspace or Handshake.
2. A Chromium window opens with that connector's saved profile. Complete your usual school login and Duo/MFA yourself.
3. The helper saves the browser session automatically, closes the window, and verifies it through a read-only MCP call. The card changes from **Signing in…** to **Connected** only after that succeeds.
4. Ask in the main chat. Every source that is turned on (the **Sources** button in the chat box) and has a saved session is used automatically; you don't need to reconnect for each question. Turning a source off leaves it out of chat even if its session is saved. New sessions are turned on when they connect (see **Settings → Default sources**).

Examples: "What's due this week?", "Show my latest course announcements", "Find software engineering internships on Handshake".

After an app restart, cards with stored session data show **Session saved**, and chat keeps using them. Click **Check session** in the Sources window to verify one. If a school expires the session, a connector tool call fails, the source changes to **Sign-in needed**, a banner at the top of the chat offers **Reconnect**, and chat stops using that connector until you sign in again. Persisting a browser profile does not prevent session expiry or future MFA prompts.

## WebReg (read-only course bin)

The USC login step also opens USC Web Registration (`webreg.usc.edu`) in the same window, after Handshake, reusing the USC login (no extra Duo expected; if USC asks, the popup comes back). For every term WebReg lists (for example Spring 2027, Fall 2026, Summer 2026) it reads the **course bin**: each course, each section's columns as WebReg labels them, and whether the section is scheduled and/or registered. Nothing is clicked except term links and page loads, so it never registers or drops.

- WebReg has no data API and is behind Cloudflare, so it is read in the real Chrome window, not with plain HTTP requests (`backend/app/integrations/webreg.py`).
- The result is a snapshot in the account's own folder (`users/<id>/webreg/coursebin.json`), refreshed at every sign-in. No WebReg cookies are kept.
- Chat gets `list_webreg_courses` when a snapshot exists; the Sources window shows when it was taken (`GET /integrations/webreg`).

## How chat uses connectors

Chat runs one Claude agent (`backend/app/agent/runtime.py`, configured in `config/agent.yaml` and `prompts/ai_instructions.md`). Each run gets:

- **TrojanClaw MCP tools** (`backend/app/mcp_server/server.py`), bound to the signed-in account: `list_deadlines`, `list_announcements`, `search_student_context`, `search_campus_knowledge`, `list_campus_events`, `get_data_status`.
- **Connector tools** for each live connector, named `brightspace__<tool>`, `handshake__<tool>` and `piazza__<tool>`. A connector is included when:
  - it is turned on in chat (the request's `connectors` list), and
  - its state is **Session saved** or **Connected** for the signed-in account, and the request comes from this computer (the same loopback and origin check as the connector endpoints).

  Connectors are also limited by `goals.chat.connectors` in `config/agent.yaml`. A turned-on source with an expired session is skipped rather than failing the reply.

- **Google Calendar draft tool** (`propose_calendar_event`), when Google Calendar is configured. It only creates a proposal card; the event is saved only when the student clicks Approve (see [Google Calendar setup](docs/google-calendar.md)). Each reply can create at most one card, and a draft whose times don't match an explicit time range in the latest message (such as "3pm to 3.20pm") is rejected. If Claude says it drafted an event without calling the tool, or its draft was rejected, chat retries once with only the calendar tools, and otherwise asks the student for the missing details.

Claude decides which tools a question needs. The prompt tells it to prefer the live connector tools for the student's own Brightspace and Handshake data, to look up IDs (courses, jobs) before details, and to send the student to Sources (or the Reconnect banner) when a session has expired.

To avoid reconnecting on every question:

- **Tool definitions are cached.** The backend lists a connector's tools once per account (one helper run, about 1–2 seconds) and keeps them in memory until the backend restarts.
- **Helpers start only when needed.** A helper process starts only when Claude actually calls a connector tool. Questions that don't need Brightspace or Handshake never start one.
- **Each call uses the signed-in account's saved session.** It runs `trojanclaw_handshake.bridge` or the isolated `trojanclaw_piazza.bridge` with that account's session files only. Reads never open a login window; logins happen through **Connect** or the USC step of sign-in (Brightspace, Handshake and WebReg).
- **Expired sessions are left out.** If listing a saved connector's tools fails, the chat carries on without that connector. If a tool call fails, the card switches to **Sign in again**.

While a tool runs, chat shows what it is checking (for example "Checking Brightspace: upcoming due dates"). Connector answers list **Brightspace**, **Handshake** or **Piazza** in the response's `sources`; when the reply cites a specific link a connector returned (an assignment, a job), that link is listed instead.

## Persistent storage

Default base directory: `~/.trojanclaw/connectors` (Windows: `%USERPROFILE%\.trojanclaw\connectors`). Set `TROJANCLAW_SESSION_DIR` to change it; a blank value uses the default. Use an absolute path and the same value when launching the backend or a standalone connector.

Each TrojanClaw account has its own folder, named by its account ID. Connector routes and chat only ever use the signed-in account's folder, so one account can never use another account's sessions. Signing out keeps the account's sessions; signing in again reuses them.

```text
connectors/
  users/
    <account id>/
      brightspace/
        profile/          # Persistent Chromium state (Connect button)
        cookies.json      # Captured after reaching the USC dashboard
      handshake/
        profile/          # Upstream persistent Chromium state (Connect button)
        cookies.json      # Captured Handshake session
        origin.json       # Authenticated Handshake school address used by MCP
      piazza/
        profile/          # Account-specific persistent Chromium sign-in
        cookies.json      # Only Piazza's session_id and piazza_session
```

The backend passes the helper only that account's folders (`TROJANCLAW_SESSION_DIR` and `HANDSHAKE_DIR`), so the helper itself needs no account logic. The USC step of sign-in writes the same files into the signing-in account's folder. It uses an in-memory browser context, so it creates no profile folder and keeps no USC login cookies.

There is no shared cookie file and no manual cookie import. Brightspace receives only its own two session cookies through the child process environment (`D2L_SESSION_COOKIE`); Handshake reads only its own session directory. Cookies and browser profiles never pass through the frontend or HTTP API. Tool results are sent to Anthropic to answer selected-source questions; they are not written to the campus knowledge database.

Brightspace uses USC's `https://brightspace.usc.edu` endpoint. Its allowed read tools cover courses, assignments, deadlines, grades, announcements, course content, discussions, groups, rubrics, feedback, and transcripts. Downloads, session-management tools, and class lists are blocked. Handshake's allowlist is documented in [handshake.md](docs/handshake.md). Tool names and parameters are checked again before dispatch; selecting one connector does not grant access to another.

## Endpoints and verification

- `POST /auth/google/start` (this computer only): returns the Google sign-in URL and sets an HttpOnly attempt cookie. Google redirects back to `/auth/google/callback`, which checks the state and returns to the app.
- `POST /auth/google/finish`: opens the USC login window; optional body `{"left", "top"}` places it. Returns a one-time `attempt_id`.
- `POST /auth/google/status` with `{"attempt_id"}`: `waiting`, `failed`, or `done` (sets the session cookie). `POST /auth/google/cancel` stops it.
- `GET /integrations`: status of the signed-in account's three cards. All `/integrations` routes require a signed-in account.
- `POST /integrations/{provider}/connect`: starts a login job, returns 202, then the UI polls status.
- `POST /integrations/{provider}/verify`: checks a saved session.
- `POST /chat`: runs the agent for the signed-in account and streams the reply. Takes `{conversation_id, message, connectors, timezone}`: leave out `conversation_id` to start a new saved chat; `connectors` lists the sources turned on in chat (from another computer, any connector is refused); `timezone` (IANA name, default `America/Los_Angeles`) sets the dates the agent uses. The reply is newline-delimited JSON ending in `done` with `{response, sources, calendar_proposals, conversation_id, title}`. Tool rounds are capped by `max_tool_rounds` in `config/agent.yaml`. The full chat API is in [backend/README.md](backend/README.md#chats).

Login, verification, and connector chat requests (including automatic use of saved sessions) require a loopback client and an allowed frontend origin. Keep `CORS_ORIGINS` limited to the local frontend. Only one sign-in job per connector can run in a backend process at a time.

```sh
uv run --locked --project integrations/handshake --group dev pytest integrations/handshake/tests -q
cd backend
uv run --no-project --python 3.11 --with-requirements requirements-dev.txt python -m pytest tests -q
```

Automated tests use fake account responses and browser contexts. A real USC/Handshake sign-in still needs the student present to verify institutional login compatibility.

Sign-in progress is logged in the backend terminal as `USC login: ...` and `USC setup: ...` lines.
