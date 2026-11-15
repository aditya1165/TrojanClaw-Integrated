# Chat connectors

The main chat supports Brightspace, Handshake and Piazza with separate, account-scoped persistent Chromium profiles. Piazza has its own sign-in; see [Piazza setup](docs/piazza.md).

Once a connector has a saved session, chat uses it automatically: the student signs in once (Sign in with USC or **Connect**) and then just asks. See [How chat uses connectors](#how-chat-uses-connectors).

## Local setup

Requirements: Git, uv, Python 3.11 for FastAPI, Python 3.12–3.14 for the connector helper, Node.js 22 or newer for Brightspace and the frontend, and Google Chrome (Sign in with USC opens its popup in the installed Chrome). Run the backend on your own computer, in a single process, bound to `127.0.0.1`. The persistent browser profiles belong to that computer's owner; this prototype is not a hosted multi-user authentication service. Saved sessions are per TrojanClaw account (see [Persistent storage](#persistent-storage) below).

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

## Sign in with USC (one login for everything)

The sign-in dialog also offers **Continue with USC Google** when Calendar OAuth
is configured. It completes Google authorization in the normal browser, then
automatically starts the USC flow below, validates both sign-in identities, and
connects the primary calendar through Google's official MCP server. The current
USC helper uses a separate browser session and may require another USC/Duo step.
Calendar uses the local open-source nspady MCP server with the standard Calendar API.
Install its dependencies with `npm ci --prefix integrations/google-calendar`.
Register both callback URIs; see [Google Calendar setup](docs/google-calendar.md).

**Sign in with USC** signs the student in to TrojanClaw and connects Brightspace and Handshake with a single USC login (password + Duo).

1. Click **Sign in**, then **Sign in with USC**. A small Chrome popup opens over the app on Brightspace. Its address bar stays visible so the student can check it is the real USC login page.
2. The student logs in to USC. The backend asks Brightspace who logged in (`/d2l/api/lp/<version>/users/whoami`) and finds or creates the TrojanClaw account. USC accounts are matched only by Brightspace user ID, never by username, so a USC login cannot take over a username/password account. Brightspace's own session is saved for its connector.
3. The app shows **Setting things up for you…** while the backend minimizes the popup and opens Handshake's direct USC sign-in link in the same browser. USC's login session is still alive there, so Handshake signs in without a password. Its session is saved and verified with a read-only MCP call.
4. If Handshake has not signed in by itself after 20 seconds (for example USC asks for Duo again), the popup comes back to the front so the student can finish. After five minutes the card shows **Sign-in needed** and **Connect** works as below.
5. The browser closes and the app opens the chat with the connected sources selected.

Why it works this way (checked against the live sites on 2026-11-14):

- Brightspace and Handshake use the same USC login: Brightspace goes through Microsoft to `shibboleth.usc.edu`, Handshake goes straight to `shibboleth.usc.edu`, and both end at `login.usc.edu`.
- Direct sign-in links skip the site's own login page: `https://brightspace.usc.edu/d2l/login` and `https://usc.joinhandshake.com/auth/saml/70/session/new?redirect_to_idp=true` (skips Handshake's "choose your school").
- USC's login cookies are session cookies, deleted when the browser closes. So every site is connected in the same browser right after the login, and nothing of the USC login itself is stored.
- Piazza's school SSO does not include USC, so Piazza cannot use the USC login.

Username/password accounts still work, and **Connect** below still works for any source.

The backend must run on the student's computer, because the popup opens there.

## Connect and ask

1. Click **Connect** on Brightspace or Handshake.
2. A Chromium window opens with that connector's saved profile. Complete your usual school login and Duo/MFA yourself.
3. The helper saves the browser session automatically, closes the window, and verifies it through a read-only MCP call. The card changes from **Waiting for sign-in** to **Connected** only after that succeeds.
4. Ask in the main chat. Every connector with a saved session is available automatically; you don't need to reconnect or tick anything for each question. Ticking **Use in chat** forces a connector into the chat and shows an error right away if its session no longer works.

Examples: "What's due this week?", "Show my latest course announcements", "Find software engineering internships on Handshake".

After an app restart, cards with stored session data show **Session saved**, and chat keeps using them. Click **Check session** to verify one. If a school expires the session, a connector tool call fails, the card changes to **Sign in again**, and chat stops using that connector until you sign in again. Persisting a browser profile does not prevent session expiry or future MFA prompts.

## How chat uses connectors

Chat runs one Claude agent (`backend/app/agent/runtime.py`, configured in `config/agent.yaml` and `prompts/ai_instructions.md`). Each run gets:

- **TrojanClaw MCP tools** (`backend/app/mcp_server/server.py`), bound to the signed-in account: `list_deadlines`, `list_announcements`, `search_student_context`, `search_campus_knowledge`, `list_campus_events`, `get_data_status`.
- **Connector tools** for each live connector, named `brightspace__<tool>`, `handshake__<tool>` and `piazza__<tool>`. A connector is included when:
  - the student ticked **Use in chat** for it (the request's `connectors` list), or
  - its card state is **Session saved** or **Connected** for the signed-in account, and the request comes from this computer (the same loopback and origin check as the connector endpoints).

  Connectors are also limited by `goals.chat.connectors` in `config/agent.yaml`.

- **Google Calendar draft tool** (`propose_calendar_event`), when Google Calendar is configured. It only creates a proposal card; the event is saved only when the student clicks Approve (see [Google Calendar setup](docs/google-calendar.md)). Each reply can create at most one card, and a draft whose times don't match an explicit time range in the latest message (such as "3pm to 3.20pm") is rejected. If Claude says it drafted an event without calling the tool, or its draft was rejected, chat retries once with only the calendar tools, and otherwise asks the student for the missing details.

Claude decides which tools a question needs. The prompt tells it to prefer the live connector tools for the student's own Brightspace and Handshake data, to look up IDs (courses, jobs) before details, and to send the student to the connector card when a session has expired.

To avoid reconnecting on every question:

- **Tool definitions are cached.** The backend lists a connector's tools once per account (one helper run, about 1–2 seconds) and keeps them in memory until the backend restarts.
- **Helpers start only when needed.** A helper process starts only when Claude actually calls a connector tool. Questions that don't need Brightspace or Handshake never start one.
- **Each call uses the signed-in account's saved session.** It runs `trojanclaw_handshake.bridge` or the isolated `trojanclaw_piazza.bridge` with that account's session files only. Reads never open a login window; logins happen through **Connect** or Sign in with USC (Brightspace/Handshake only).
- **Expired sessions are left out.** If listing a saved connector's tools fails, the chat carries on without that connector. If a tool call fails, the card switches to **Sign in again**.

Connector answers list **Brightspace** or **Handshake** in the response's `sources`.

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

The backend passes the helper only that account's folders (`TROJANCLAW_SESSION_DIR` and `HANDSHAKE_DIR`), so the helper itself needs no account logic. Sign in with USC writes the same files into the signing-in account's folder. It uses an in-memory browser context, so it creates no profile folder and keeps no USC login cookies.

There is no shared cookie file and no manual cookie import. Brightspace receives only its own two session cookies through the child process environment (`D2L_SESSION_COOKIE`); Handshake reads only its own session directory. Cookies and browser profiles never pass through the frontend or HTTP API. Tool results are sent to Anthropic to answer selected-source questions; they are not written to the campus knowledge database.

Brightspace uses USC's `https://brightspace.usc.edu` endpoint. Its allowed read tools cover courses, assignments, deadlines, grades, announcements, course content, discussions, groups, rubrics, feedback, and transcripts. Downloads, session-management tools, and class lists are blocked. Handshake's allowlist is documented in [handshake.md](handshake.md). Tool names and parameters are checked again before dispatch; selecting one connector does not grant access to another.

## Endpoints and verification

- `POST /auth/usc/start`: opens the Sign in with USC popup; optional body `{"left", "top"}` places it. Returns a one-time `attempt_id`.
- `POST /auth/usc/status` with `{"attempt_id"}`: `waiting`, `failed`, or `done` (sets the session cookie). A finished attempt can be read once.
- `GET /integrations`: status of the signed-in account's three cards. All `/integrations` routes require a signed-in account.
- `POST /integrations/{provider}/connect`: starts a login job, returns 202, then the UI polls status.
- `POST /integrations/{provider}/verify`: checks a saved session.
- `POST /chat`: runs the agent for the signed-in account. Saved connector sessions are used automatically for requests from this computer. Optional `connectors: ["brightspace", "handshake"]` forces those connectors (only from this computer). Optional `timezone` (IANA name, default `America/Los_Angeles`) sets the dates the agent uses. Returns `{response, sources, calendar_proposals}`. The number of tool rounds is capped by `max_tool_rounds` in `config/agent.yaml`.

Login, verification, and connector chat requests (including automatic use of saved sessions) require a loopback client and an allowed frontend origin. Keep `CORS_ORIGINS` limited to the local frontend. Only one sign-in job per connector can run in a backend process at a time.

```sh
uv run --locked --project integrations/handshake --group dev pytest integrations/handshake/tests -q
cd backend
uv run --no-project --python 3.11 --with-requirements requirements-dev.txt python -m pytest tests -q
```

Automated tests use fake account responses and browser contexts. A real USC/Handshake sign-in still needs the student present to verify institutional login compatibility.

Developer tools for testing the USC login outside the app (run from `backend/`):

```sh
python -m app.connectors.usc_login   # one USC login on Brightspace in a Chrome window; prints who logged in
uv run --no-project --with "mcp>=1.10,<2" --with keyring python app/connectors/check.py   # calls the Brightspace MCP server once
python -m app.connectors.ip_check    # does the saved Brightspace session work from this network?
```

`ip_check` answers a hosting question: a server can only use students' sessions if USC does not tie a session to the network it was created on. Save a session on your usual network, then run `ip_check` again from another network (for example a phone hotspot).

Sign-in progress is logged in the backend terminal as `USC login: ...` and `USC setup: ...` lines.
