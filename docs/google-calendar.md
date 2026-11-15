# Google Calendar MCP and Google-first USC sign-in

Chat can read the student's events (`list_calendar_events`, up to 31 days per
request), draft a new event or a bounded repeating series with an optional popup
reminder (`propose_calendar_event`), and draft a change to an existing timed
event (`propose_calendar_update`). It does not choose study times automatically.
Each draft becomes a review card below the conversation, in the chat that made
it, and can be edited or cancelled without writing to Google. Only **Approve
addition** or **Approve changes** calls the backend approval endpoint and writes
the reviewed event through the local open-source [nspady/google-calendar-mcp](https://github.com/nspady/google-calendar-mcp)
server, pinned to `@cocal/google-calendar-mcp@2.8.1`.

## Setup

1. Install `backend/requirements.txt` into the backend Python environment and
   install the local Calendar MCP server with Node 22.12 or newer:

   ```sh
   npm ci --prefix integrations/google-calendar
   ```

   The backend starts its private Node gateway with a short-lived access token
   on stdin. The gateway runs upstream tools over the MCP SDK's in-memory
   transport. Client secrets and refresh tokens remain in the backend; no
   additional credential/token files or public MCP HTTP port are needed.
   See [connector internals](../integrations/google-calendar/README.md).
2. Apply the repository's migrations, including
   `supabase/migrations/20261120000000_calendar_approval.sql`, using the existing
   migration workflow. The backend uses the private PostgreSQL connection;
   browser Supabase roles cannot access the calendar tables.
3. In Google Cloud, enable the standard **Google Calendar API**. Configure the
   OAuth consent screen and a **Web application** OAuth client. In testing mode,
   add the USC Google accounts you will use as test users. Workspace administrators
   may need to allow the app. The local open-source MCP server does not require
   Google-hosted MCP API activation, Developer Preview access, or an agent-client flag.
4. Register both exact redirect URIs for local development:

   - `http://127.0.0.1:8000/calendar/callback` for connecting from an existing account
   - `http://127.0.0.1:8000/auth/google/callback` for Google-first sign-in

   The sign-in URI is derived from `GOOGLE_CALENDAR_REDIRECT_URI` on the same
   backend host. Use the same host consistently throughout the app; switching
   between `localhost` and `127.0.0.1` loses the cookie binding. Use HTTPS when
   deploying the web backend. The current USC connector remains a local prototype.
5. Set these values in `backend/.env`:

   ```dotenv
   GOOGLE_CALENDAR_CLIENT_ID=your-web-client-id
   GOOGLE_CALENDAR_CLIENT_SECRET=your-web-client-secret
   GOOGLE_CALENDAR_REDIRECT_URI=http://127.0.0.1:8000/calendar/callback
   GOOGLE_CALENDAR_FRONTEND_URL=http://127.0.0.1:5173
   GOOGLE_CALENDAR_ENCRYPTION_KEY=your-fernet-key
   ```

   Generate the key once in the backend environment:

   ```sh
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Keep it private and stable across backend instances and restarts. Google
   tokens are encrypted with this key in PostgreSQL. Replacing the key requires
   users to reconnect. The frontend URL must also be in `CORS_ORIGINS`.
6. Restart the backend and use **Continue with USC Google** in the sign-in
   dialog. Existing users can open **Sources** in the chat sidebar and select
   **Connect** next to Google Calendar. Connecting uses a full-page redirect;
   chats are saved, so nothing is lost.

Google consent grants API access, not approval of individual events. Requested
scopes are `calendar.calendarlist.readonly`, `calendar.events.readonly`,
`calendar.events.freebusy`, and `calendar.events.owned`. Google-first sign-in
also requests `openid` and `email` to verify the USC Google account. There is no
request for permission to manage sharing or delete calendars.
Google OAuth test-mode refresh tokens may expire; use **Reconnect** when needed.

## Combined sign-in flow

1. Google authorization opens in the user's ordinary browser. Selecting a
   USC-managed Google account may redirect through USC Shibboleth and Duo.
2. The callback validates single-use OAuth state, the browser-bound HttpOnly
   attempt cookie, and PKCE. Google's userinfo endpoint must report a verified
   `@usc.edu` email, a Google user ID, and hosted domain `usc.edu`. The `hd` hint
   in the authorization URL is not treated as identity verification.
3. The frontend returns to the sign-in dialog and automatically starts the
   existing local USC helper. This opens Brightspace and completes its own
   sign-in. Google OAuth does not itself create a Brightspace session.
4. Before saving Brightspace cookies, the helper requires Brightspace's signed-in
   `Identifier` and `UniqueName`. If `UniqueName` is an email address, a conflicting
   address is rejected. USC also returns numeric student identifiers in this field;
   opaque IDs and bare usernames are never converted into guessed email addresses.
   Both providers authenticate within the same browser-bound onboarding attempt.
   The final account-linking step repeats the identity validation.
5. The app signs in using its existing Brightspace-ID account mapping; it never
   takes over a username/password account merely because an email matches.
   Calendar MCP verification lists the primary calendar, stores encrypted Google
   tokens for this app account, and the existing helper continues Handshake setup.
6. Setup displays Brightspace, Handshake, and Google Calendar. If Calendar MCP
   is unavailable, the USC sign-in remains valid and the app shows a message to
   finish Calendar setup later.

Google requires authorization to run in a normal browser rather than a
developer-controlled browser that can inspect cookies or inject scripts.
Consequently, Google's browser and the current USC automation helper do **not**
share browser cookies. This is one onboarding flow, with possible additional
USC authentication/Duo in the helper. It does not promise a single credential
prompt or automatic reuse of Google's Shibboleth session.

Pending sign-ins are held in memory, encrypted after the OAuth exchange, expire
after ten minutes per stage, and are bound to the initiating browser cookie.
Run one local backend worker; restarting it requires starting sign-in again.
The existing **Sign in with USC** and username/password options remain available.

## Try it

Ask chat: “Add a study session on [a future date] from 4 to 5 PM.” The time zone
from **Settings** (the browser's, unless changed) is supplied with the chat
request; an explicitly requested zone takes precedence. Each draft belongs to
the chat that created it and shows only in that chat; deleting the chat deletes
its drafts, not events already added to Google Calendar. If a date, title, time or duration is missing, chat should ask for it.
Verify the actual draft before approving.

- Review the title, start, end, time zone, location, description, and destination.
- **Edit** saves a new draft version and returns to review. It never approves.
- **Cancel** discards a pending proposal without touching Google Calendar.
- **Approve addition** checks availability on the connected primary calendar,
  then saves the exact approved draft. A conflict returns the proposal to review.
- Successful saves show an **Open in Google Calendar** link.

Supported on the connected primary calendar: reading events; new single events
and daily, weekly or monthly series (up to 52 occurrences within a year, every
occurrence listed on the card) with an optional popup reminder; and changes to
an existing timed event or a whole recurring series, shown as before/after.
Deleting events, guests, and editing all-day events are not supported.
Availability checking covers only that primary calendar and is a
snapshot; Google cannot reserve a time atomically between the check and save.
Availability uses the upstream `get-freebusy` tool, bounded to the exact proposed
interval. Missing or errored calendar availability fails instead of assuming
the time is free.

## Approval and retry guarantees

The model can read events and draft proposals (`list_calendar_events`,
`propose_calendar_event`, `propose_calendar_update`); none of these writes to
Google. Approval is an authenticated
POST from the review UI; typing “yes” in chat cannot execute a calendar write.
The approval body contains only the proposal version, never replacement event
details. The backend uses the stored, validated draft and rejects stale versions.
Pending proposals expire after 24 hours, and past start times are rejected.

Approval is persisted before calling Google. A database lease serializes save
attempts across backend instances. Every proposal has a deterministic Google
event ID; if a response is lost, **Retry save** checks that ID before creating
anything. An uncertain save keeps the approved details frozen until recovery;
it cannot be edited or cancelled as though nothing happened. An interrupted
attempt may need up to five minutes before its lease permits a retry.

Tests exercise the pinned server's actual tool schemas and handlers through MCP.
Creates preserve the reviewed fields, caller-supplied event ID, explicit reminder
suppression, and private proposal marker. The gateway verifies that the approved
proposal ID, event ID and marker agree before enabling a create tool.

The approval service alone can call MCP `create-event`, and `update-event` only
for an approved change proposal (with the original ETag as `If-Match`). Read
invocations register only `list-calendars`, `list-events`, `get-event`, and
`get-freebusy`. Delete, account management, invitation-response, and arbitrary
tool execution are unavailable.

Calendar MCP tool results and credentials are not exposed as model tools.
The model receives only its own validated proposal output. Connector/source
content is untrusted; it cannot authorize calendar changes. These controls
reduce the indirect prompt-injection risk described in Google's MCP guidance.

Connecting again invalidates proposals tied to the previous connection, so an
old approval cannot be used for a different Google account. Disconnecting removes
stored access and proposals without deleting existing Google events. It cannot
undo a save that was already approved and is in progress. To revoke the Google
grant itself, remove the app from your Google account's connections settings.

OAuth state is single-use, expires after ten minutes, and is tied to the signed-in
TrojanClaw account and browser session. The authorization exchange uses PKCE.
Tokens never go to the browser or the model. Calendar responses use `no-store`.

## Verification

Run from `backend`:

```sh
python -m pytest tests/test_calendar.py tests/test_calendar_mcp.py tests/test_google_auth.py -q
```

The default tests mock Google and storage; they never create live events.
To additionally verify the migrated database's account isolation, approval
transitions and browser-role access restrictions, set `RUN_CALENDAR_DB_TESTS=1`.
That test creates and removes temporary application accounts and makes no Google
requests. Real OAuth and event creation require completing setup and manually
approving an event with a test Google account.

Run the MCP adapter tests from the repository root:

```sh
npm test --prefix integrations/google-calendar
```

The frontend requires a Vite-compatible Node runtime (22.12 or newer).

Provider references: [selected open-source Calendar MCP server](https://github.com/nspady/google-calendar-mcp),
[Google OAuth secure-browser requirements](https://developers.google.com/identity/protocols/oauth2/policies#browsers),
[OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server),
[calendar scopes](https://developers.google.com/workspace/calendar/api/auth),
[event insertion and caller-supplied IDs](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert),
[availability checks](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query).
