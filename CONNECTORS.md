# Chat connectors

The main chat supports Brightspace and Handshake with separate persistent Chromium profiles. Piazza is shown as **Coming soon** and cannot be selected or called.

## Local setup

Requirements: Git, uv, Python 3.11 for FastAPI, Python 3.12–3.14 for the connector helper, Node.js 22 or newer for Brightspace and the frontend, and Google Chrome (Sign in with USC opens its popup in the installed Chrome). Run the backend on your own computer, in a single process, bound to `127.0.0.1`. The persistent browser profiles belong to that computer's owner; this prototype is not a hosted multi-user authentication service.

From the repository root:

```sh
uv sync --locked --project integrations/handshake
uv run --locked --project integrations/handshake patchright install chromium
```

Configure `backend/.env` using `backend/.env.example`. `ANTHROPIC_API_KEY` is required for connected chat. Database/OpenAI configuration is only needed for the original campus-knowledge chat when no connectors are selected. No USC password goes into this file.

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
4. Ask in the main chat. Newly connected sources are enabled automatically. Use the **Use in chat** checkboxes to choose which sources the model can read.

Examples: "What's due this week?", "Show my latest course announcements", "Find software engineering internships on Handshake".

After an app restart, cards with stored session data show **Session saved**. Click **Check session** to verify it, or select **Use in chat** to try it directly. If a school expires the session, click **Sign in again**. Persisting a browser profile does not prevent session expiry or future MFA prompts.

## Persistent storage

Default base directory: `~/.trojanclaw/connectors` (Windows: `%USERPROFILE%\.trojanclaw\connectors`). Set `TROJANCLAW_SESSION_DIR` to change it; a blank value uses the default. Use an absolute path and the same value when launching the backend or a standalone connector.

```text
connectors/
  brightspace/
    profile/          # Persistent Chromium state
    cookies.json      # Automatically captured after reaching the USC dashboard
  handshake/
    profile/          # Upstream persistent Chromium state
    cookies.json      # Upstream automatically captured session
    origin.json       # Authenticated Handshake school address used by MCP
```

Sign in with USC writes the same files: `brightspace/cookies.json`, and `handshake/cookies.json` plus `handshake/origin.json`. It uses an in-memory browser context, so it creates no profile folder and keeps no USC login cookies.

There is no shared cookie file and no manual cookie import. Brightspace receives only its own two session cookies through the child process environment (`D2L_SESSION_COOKIE`); Handshake reads only its own session directory. Cookies and browser profiles never pass through the frontend or HTTP API. Tool results are sent to Anthropic to answer selected-source questions; they are not written to the campus knowledge database.

Brightspace uses USC's `https://brightspace.usc.edu` endpoint. Its allowed read tools cover courses, assignments, deadlines, grades, announcements, course content, discussions, groups, rubrics, feedback, and transcripts. Downloads, session-management tools, and class lists are blocked. Handshake's allowlist is documented in [handshake.md](handshake.md). Tool names and parameters are checked again before dispatch; selecting one connector does not grant access to another.

## Endpoints and verification

- `POST /auth/usc/start`: opens the Sign in with USC popup; optional body `{"left", "top"}` places it. Returns a one-time `attempt_id`.
- `POST /auth/usc/status` with `{"attempt_id"}`: `waiting`, `failed`, or `done` (sets the session cookie). A finished attempt can be read once.
- `GET /integrations`: status of the three cards.
- `POST /integrations/{provider}/connect`: starts a login job, returns 202, then the UI polls status.
- `POST /integrations/{provider}/verify`: checks a saved session.
- `POST /chat`: accepts optional `connectors: ["brightspace", "handshake"]` and runs a bounded read-only tool loop.

Login, verification, and connector chat requests require a loopback client and an allowed frontend origin. Keep `CORS_ORIGINS` limited to the local frontend. Only one sign-in job per connector can run in a backend process at a time.

```sh
uv run --locked --project integrations/handshake --group dev pytest integrations/handshake/tests -q
cd backend
uv run --no-project --python 3.11 --with-requirements requirements-dev.txt python -m pytest tests -q
```

Automated tests use fake account responses and browser contexts. A real USC/Handshake sign-in still needs the student present to verify institutional login compatibility.

Developer tools for testing the USC login outside the app (run from `backend/`):

```sh
python -m app.connectors.usc_login   # one USC login in a Chrome window; prints who logged in
uv run --no-project --with "mcp>=1.10,<2" --with keyring python app/connectors/check.py   # calls each MCP server once
```

Sign-in progress is logged in the backend terminal as `USC login: ...` and `USC setup: ...` lines.
