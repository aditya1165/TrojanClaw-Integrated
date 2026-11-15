# Chat connectors

TrojanClaw uses Browserbase managed Chromium for Brightspace, Handshake and Piazza authentication. The current chat, Sources menu, settings, light/dark mode, Google-first onboarding, calendar approval cards, Gemini and Siri integrations remain in place.

See [the remote-browser implementation guide](docs/remote-browser.md) for the lifecycle, trust boundaries and troubleshooting, and [deployment](DEPLOYMENT.md) for the server configuration.

## Setup

Install from the repository root on Windows or macOS:

```sh
uv sync --locked --project integrations/handshake
uv sync --locked --project integrations/piazza --python 3.12
npm ci --prefix integrations/brightspace
npm ci --prefix integrations/google-calendar
npm ci --prefix frontend
```

Install backend dependencies in its virtual environment using `backend/requirements-dev.txt`. Copy `backend/.env.example` to `backend/.env` and fill in the database, model and Google OAuth settings. Managed browsers additionally require:

```dotenv
CONNECTOR_MODE=browserbase
BROWSERBASE_API_KEY=
BROWSERBASE_PROJECT_ID=
CONNECTOR_ENCRYPTION_KEYS=
```

Generate a durable Fernet key for `CONNECTOR_ENCRYPTION_KEYS`. Keep it private and stable across restarts. Comma-separated keys support rotation: first key encrypts new writes, older keys decrypt existing rows. Browserbase credentials and encryption keys are backend secrets, never frontend `VITE_` settings.

Apply the current schema migrations in order, including:

- `20261124000000_hosted_connector_sessions.sql`: private connector sessions and shared production authentication rate limits.
- `20261125000000_remote_usc_login.sql`: cookie-bound remote USC attempts and unique Brightspace identity index.
- `20261126000000_piazza_connector.sql`: Piazza provider constraint.

These timestamps follow the newer calendar and conversation migrations. If an earlier Browserbase branch already applied equivalent tables under older migration versions, inspect migration history before applying; do not recreate tables or remove stored sessions.

Run the usual backend and frontend development commands. Vite forwards `/api` to the backend and serves the existing allowed ngrok host. Keep requests on the same origin so browser cookies work. Google callbacks are `/auth/google/callback` and `/calendar/callback`, without `/api`.

## Signing in

1. Choose **Continue with Google** and authenticate with the USC Google account in the ordinary browser.
2. On return, the existing dialog opens a large private Browserbase view for USC sign-in and Duo.
3. The backend captures Brightspace, verifies its identity against Google, and switches the view to connection progress.
4. Handshake opens its USC SAML URL inside the same Chromium context, reusing USC authentication. If Handshake requests another verification or Cloudflare interaction, the live browser becomes visible again. Complete the check or choose **Skip Handshake**.
5. WebReg is read opportunistically in that context. Its course-bin snapshot is stored with the encrypted Brightspace payload. An unavailable WebReg page does not block sign-in.
6. Verified source sessions are saved to the account, Google Calendar is linked, and the app creates its normal signed-in session.

Piazza uses a separate login. In chat, open **Sources**, choose **Connect** next to Piazza, complete sign-in in the remote view and choose **Finish sign-in**. The same flow reconnects Brightspace or Handshake. Full screen is available for a larger login view.

## Persistent storage

`public.user_connector_sessions` holds one record per account and provider. Session cookies, context IDs and temporary CDP connection details are encrypted. Browserbase retains a distinct persistent context for each account/provider. Handshake retains the verified USC context to reuse Shibboleth authentication.

`public.remote_usc_attempts` holds encrypted, ten-minute onboarding attempts. A hashed secret from the Google attempt cookie binds each attempt to its browser; knowing an attempt ID does not authorize access. Every repository operation has an explicit owner predicate and transaction-local owner for row security.

The web client receives only the short-lived Browserbase live-view URL, attempt ID and progress. Treat live-view URLs as private bearer links. No provider cookies, encryption keys, Browserbase project API key or CDP URL are returned to the client. Recording, browser logs and automatic CAPTCHA solving are disabled.

Signing out ends the app session and keeps saved connector sessions. Disconnecting a connector blocks tool reads immediately, releases any active session, deletes its Browserbase context, and removes its encrypted record. Vendor outages may require retrying cleanup.

## How chat and assistants use sources

The current Claude agent and per-account TrojanClaw MCP tools remain unchanged. Selected Brightspace, Handshake and Piazza tools are named `brightspace__<tool>`, `handshake__<tool>` and `piazza__<tool>`. Gemini and Siri use the same connector factory as website chat, so hosted mode applies there too.

Each helper runs in a separate subprocess. Only the selected account/provider session travels over stdin; server database, Browserbase and encryption secrets are removed from its environment. Temporary directories isolate runtime state. Hosted helpers do not fall back to desktop cookie files. The pinned Brightspace Node MCP package is installed ahead of time.

Handshake retains the read-only events and career-fair tools in addition to jobs, employers, applications and profile reads. Piazza retains its newer source links and read-only allowlist. Provider content is untrusted source data. Tool results retain the newer bounded response size.

Calendar reads and proposal cards remain part of the current implementation. Every calendar addition or edit still requires the user's explicit approval. See [Google Calendar setup](docs/google-calendar.md).

## Desktop development mode

`CONNECTOR_MODE=local` explicitly selects the existing desktop development flow. This opens browsers on the computer running the backend and uses per-account files under `TROJANCLAW_SESSION_DIR` (default `~/.trojanclaw/connectors`). It requires Chrome/local Chromium and the helper browser installation. It is not accepted in production or staging.

Browserbase mode is the default and does not require installing a local browser. Production requires an HTTPS `PUBLIC_APP_URL`, explicit HTTPS `CORS_ORIGINS`, and `APP_DEBUG=false`.

## Verification and limits

Automated tests use mocked Browserbase and school responses, covering owner isolation, encryption, cookie binding, identity matching, session capture, helper dispatch and connection cleanup. A real school MFA/Cloudflare login must still be checked interactively.

Use one API worker/instance: completed sessions are in PostgreSQL, but the newer Google OAuth attempt registry is process-local. A backend restart during onboarding requires a new sign-in. Browserbase persistence does not prevent school sessions from expiring or guarantee Cloudflare acceptance.