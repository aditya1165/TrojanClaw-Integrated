# Remote browser implementation

[Setup and user flow](../CONNECTORS.md) · [Server deployment](../DEPLOYMENT.md)

## Browser lifecycle

The app defaults to `CONNECTOR_MODE=browserbase`. Its compatibility dependency, `get_local_connectors`, dispatches to `HostedConnectors`; the name is retained so the newer chat agent, MCP server, Siri and mobile routes keep using the same dependency. Only explicit development mode selects `get_desktop_connectors`.

`HostedConnectors.connect` reserves the authenticated account/provider row before creating a browser. It creates or reuses that source's persistent Browserbase context, starts a ten-minute session, opens the provider URL through the private CDP endpoint, and returns a live-view URL. The frontend's existing Sources dialog renders `RemoteSignIn`, with an expanded iframe and optional full screen.

**Finish sign-in** captures only the selected provider's cookies and verifies a real read through its MCP server. A valid result is encrypted and persisted after releasing the live browser. An incomplete login keeps the browser open for the student to finish verification. **Cancel** releases the active browser. **Disconnect** first marks the source revoking so reads stop, then removes vendor context and stored credentials.

The API also supports authenticated `GET /integrations/{provider}/login` to resume a pending login. The login ID is checked against the account/provider row and expiry.

## Google-first USC onboarding

The Google sign-in routes remain the current `/auth/google/*` implementation, with the callback at `/auth/google/callback` (no `/api`). The attempt cookie uses `/` so it reaches both the direct callback and proxied `/api/auth/google/*` requests. Pending assistant authorization is still handled by the current App implementation after sign-in.

`GoogleSignIn.begin_usc` uses `services/hosted_usc.py` in managed mode. Its encrypted attempt is bound to the Google cookie secret and expected USC email. The flow progresses through:

1. `brightspace`: show USC/Duo in Browserbase; capture Brightspace cookies.
2. `verify_brightspace`: show connecting progress; verify MCP reads and Brightspace `whoami`; check the Google/USC identities match before creating accounts or saving sources.
3. `handshake`: navigate the same context to USC Handshake SAML. Wait briefly for SSO, then return `interaction_required` when manual verification is needed.
4. `verify_handshake`: verify the captured Handshake session. Skipping Handshake preserves Brightspace and retains the USC context for a later Handshake connection.
5. Read WebReg in another tab, if available. Save the course-bin snapshot inside the encrypted Brightspace payload; never save hosted course snapshots to disk.
6. Transfer source sessions into owner-bound records. Handshake retains the original USC Browserbase context, preserving IdP state; Brightspace gets a separate seeded context. The Google route links Calendar and issues the one final app session.

The frontend hides the live browser during automatic connection progress and reveals it when interaction is required. The backend serializes operations per Google attempt, allowing different users to connect concurrently. Cancellation waits for the current attempt's operation before cleanup.

## Code map

| File | Responsibility |
| --- | --- |
| `backend/app/integrations/browserbase.py` | Browserbase HTTP client, contexts, sessions and private live-view validation |
| `backend/app/integrations/hosted.py` | Owner-scoped connector lifecycle and MCP calls |
| `backend/app/integrations/vault.py` | Account/provider/purpose-bound Fernet encryption and key rotation |
| `backend/app/repositories/connectors.py` | Connector reservations, updates, explicit owner filters and transaction-local RLS owner |
| `backend/app/repositories/usc_attempts.py` | Cookie-bound encrypted attempts and atomic claims |
| `backend/app/services/hosted_usc.py` | Remote USC bootstrap and same-context Handshake SSO |
| `backend/app/services/google_auth.py` | Current Google-first flow; selects hosted or desktop USC |
| `backend/app/integrations/runtime.py` | Isolated helper dispatch with stdin credentials and stripped environment |
| `integrations/handshake/src/trojanclaw_handshake/remote_login.py` | Browserbase CDP navigation, identity and cookie capture for all providers |
| `integrations/handshake/src/trojanclaw_handshake/bridge.py` | Hosted Brightspace/Handshake MCP bridge |
| `integrations/handshake/src/trojanclaw_handshake/desktop_bridge.py` | Explicit desktop development bridge |
| `integrations/piazza/src/trojanclaw_piazza/bridge.py` | Piazza MCP bridge; supplied sessions use temporary in-memory credentials |
| `frontend/src/components/RemoteSignIn.tsx` | Shared large remote-browser modal |
| `frontend/src/AuthDialog.tsx` | Google return, USC progress, verification fallback and skip |
| `frontend/src/lib/sources.ts` | Sources connector start, finish and cancel state |

## Data boundaries

Provider cookies and CDP bearer URLs are server-only. PostgreSQL stores ciphertext tied to the account, provider and purpose, preventing ciphertext from being reused for another account or source. Browser roles cannot read the private tables. Backend repositories also filter every operation by its authenticated owner.

Subprocesses receive credentials over stdin, never command arguments. Provider validation prevents cross-site cookie forwarding. Network transports restrict destinations and read operations. The Browserbase API key, database URL and encryption keys are removed from helper environments. Browserbase recording, session logs and automatic CAPTCHA solving are disabled.

Live-view URLs are short-lived bearer links. Never add them to analytics, screenshots shared with others, logs or persistent frontend storage. Browserbase stores the actual browser context; institutional sessions can expire independently.

## Deployment limits

Run one API worker and one instance for now. Remote connector rows and USC attempts are shared in PostgreSQL, but the newer Google OAuth registry remains in process memory. Multi-instance deployment requires moving that registry to shared storage (or carefully managed affinity); a restart during onboarding requires starting again. Completed app, calendar and connector sessions persist.

The three new migrations use timestamps after the current upstream migrations to avoid version collisions. Previously applied Browserbase branches may have the equivalent tables at older versions: inspect and reconcile migration history rather than dropping tables or blindly rerunning SQL.

## Troubleshooting

- **No browser window / runtime unavailable:** install both locked Python helpers and the pinned Brightspace npm package; verify Browserbase and encryption settings on the backend.
- **Immediate 401 after Google:** use one frontend origin; verify `/api` proxying, root-path attempt cookies, HTTPS settings, and direct `/auth/google/callback` routing. Register the callback without `/api`.
- **Google attempt expired after restart:** start Google sign-in again. Incomplete Google attempts are not durable yet.
- **Handshake verification or Cloudflare:** complete the challenge in the live browser, or skip and reconnect from Sources later. A cloud browser can still be challenged; automatic CAPTCHA solving is disabled.
- **Missing connector tables:** apply migrations 20261124–20261126 after the upstream migrations. Use the migration checkers without `--apply` to inspect prerequisites first.
- **Piazza fails despite a dashboard:** finish sign-in to capture and verify both `session_id` and `piazza_session`; reconnect if either has expired.
- **WebReg unavailable:** it is a best-effort read and can require another verification. Sign-in continues without fabricating course data.
- **Saved data cannot decrypt:** restore the original encryption key or include it after the new key in the key ring. Replacing every key makes existing sessions unreadable.

Tests cover mocked connector and onboarding boundaries. They do not prove live school MFA, Browserbase availability, production proxy configuration or a successful container deployment.