# Single-User Piazza and Brightspace MCP Implementation Plan

_Prepared on 2026-11-06 for the current single-user TrojanClaw prototype._

## 1. Objective

Convert TrojanClaw into a secure personal academic assistant for one owner, with read-only access to:

- Piazza classes, posts, threads, attachments, and recent activity.
- Brightspace courses, assignments, deadlines, announcements, content, grades, and feedback.
- A unified local course model that combines both sources.
- Cited chat answers backed by normalized data and RAG.

This version deliberately avoids multi-user tenancy, USC-wide deployment, and write actions.

## 2. Core design decision

Model the system as:

1. One verified application owner.
2. One Piazza connector account.
3. One Brightspace connector account.
4. One local academic-data store.
5. Two read-only MCP servers running locally.
6. One connector layer that prevents Claude from calling arbitrary MCP tools.

```text
React UI
   |
   v
FastAPI
   |
   +-- Single-owner authentication
   |
   +-- MCP Connector Manager
   |      |
   |      +-- Piazza MCP process
   |      +-- Brightspace MCP process
   |
   +-- Normalization and Sync Service
   |
   +-- PostgreSQL and pgvector
   |
   +-- Claude Chat and RAG
```

## 3. Authentication model

Three authentication boundaries must remain separate.

### 3.1 TrojanClaw owner authentication

TrojanClaw itself needs one authenticated owner.

Recommended prototype approach:

- Use a magic link, passkey, or strong owner password.
- Allow exactly one configured email.
- Create an opaque server-side session.
- Store only the session-token hash.
- Send the browser a `Secure`, `HttpOnly`, `SameSite=Lax` cookie.
- Protect `/chat`, integrations, files, grades, and synchronization endpoints.

Suggested configuration:

```env
SINGLE_USER_MODE=true
OWNER_EMAIL=owner@usc.edu
SESSION_TTL_HOURS=12
```

Remove the current:

- `AUTH_STORAGE_KEY` authentication state.
- `localStorage` boolean.
- “I have authenticated” confirmation.
- Client-supplied `authenticated` field.

If the application is strictly local, bind all services to `127.0.0.1`. If it is reachable over any network, owner authentication remains mandatory.

### 3.2 Brightspace authentication

Brightspace authentication is independent from TrojanClaw login.

The selected Brightspace MCP should:

- Complete the user's institutional login in a visible browser when needed.
- Support Duo/MFA interaction.
- Save the resulting session locally.
- Store credentials in the operating-system keychain.
- Expose read-only tools.

USC is not a built-in preset in the proposed community server, so its generic visible-browser login must be tested before depending on it.

### 3.3 Piazza authentication

Piazza authentication is also independent.

Initial options:

- Piazza-native email and password.
- A delegated Piazza session if the MCP supports it.
- User-provided JSON exports if live login is unavailable.

Do not share credentials with model prompts or store them in PostgreSQL.

## 4. Single-user data model

All records implicitly belong to the one owner. Avoid adding `user_id` to every domain table for now, but isolate ownership inside the service layer so multi-user support can be added later.

### `app_owner`

- `id`
- `email`
- `display_name`
- `created_at`
- `last_login_at`

Only one row is permitted.

### `app_sessions`

- `token_hash`
- `created_at`
- `expires_at`
- `last_seen_at`
- `revoked_at`

### `connector_accounts`

- `id`
- `connector_type`: `piazza` or `brightspace`
- `status`: `disconnected`, `connecting`, `connected`, `expired`, or `error`
- `auth_method`
- `secret_reference`
- `connected_at`
- `last_verified_at`
- `last_error`

`secret_reference` points to an operating-system keychain or secrets manager. It must not contain the secret itself.

### `courses`

- `id`
- `course_code`
- `course_name`
- `term`
- `status`
- `created_at`
- `updated_at`

### `source_courses`

- `course_id`
- `source`: `piazza` or `brightspace`
- `external_course_id`
- `external_course_name`
- `source_url`

This maps both systems into one course:

```text
Brightspace CSCI-544-2026 -> CSCI 544
Piazza network abc123     -> CSCI 544
```

### Normalized academic records

Add structured storage for:

- `assignments`
- `announcements`
- `grades`
- `course_materials`
- `piazza_posts`
- `piazza_threads`
- `attachments`
- `sync_runs`

Generate RAG chunks from these records. Do not treat vector chunks as the system of record.

## 5. MCP integration design

### 5.1 Transport

Use local `stdio` MCP processes for the first version.

Advantages:

- No public MCP network endpoint.
- Secrets stay on the same machine.
- FastAPI controls process lifetime.
- No extra bearer-token service is required.
- Lower exposure for grades and course content.

### 5.2 Candidate servers

#### Brightspace

Start with [`RohanMuppa/brightspace-mcp-server`](https://github.com/RohanMuppa/brightspace-mcp-server).

Reasons:

- Read-only behavior.
- Active project history and tests.
- Generic visible-browser login.
- Operating-system keychain integration.
- Assignments, deadlines, announcements, grades, content, files, and feedback.

Pin an exact tested version. Do not run `@latest` in the deployed application.

#### Piazza

Fork or vendor [`RohanMuppa/Piazza-MCP`](https://github.com/RohanMuppa/Piazza-MCP).

Reasons for forking:

- It is a very new project.
- It relies on Piazza's unofficial internal endpoints.
- TrojanClaw needs control over credentials, logging, tool scope, and output formats.
- Existing TrojanClaw ingestion logic may need to be preserved as a fallback.

Keep it read-only.

### 5.3 Backend structure

```text
backend/app/
├── auth/
│   ├── dependencies.py
│   ├── models.py
│   └── sessions.py
├── integrations/
│   ├── mcp_client.py
│   ├── registry.py
│   ├── piazza.py
│   ├── brightspace.py
│   └── normalization.py
├── services/
│   ├── academic_search.py
│   ├── academic_sync.py
│   └── course_mapping.py
└── routers/
    ├── auth.py
    └── integrations.py
```

### 5.4 MCP registry

Maintain an explicit configuration registry:

```python
MCP_SERVERS = {
    "piazza": {
        "command": "uvx",
        "args": [
            "--from",
            "git+https://github.com/your-org/Piazza-MCP@PINNED_COMMIT",
            "piazza-mcp",
        ],
    },
    "brightspace": {
        "command": "npx",
        "args": [
            "-y",
            "brightspace-mcp-server@PINNED_VERSION",
        ],
    },
}
```

### 5.5 Typed connector adapters

Do not expose raw tool names to the rest of TrojanClaw.

```python
class PiazzaConnector:
    async def list_courses(self): ...
    async def recent_posts(self, course_id, since): ...
    async def get_thread(self, course_id, post_id): ...
    async def search(self, course_id, query): ...


class BrightspaceConnector:
    async def list_courses(self): ...
    async def upcoming_assignments(self, days): ...
    async def announcements(self, course_id, since): ...
    async def course_content(self, course_id): ...
```

Adapters must validate inputs and normalize outputs before returning data.

## 6. Read-only allowlist

### Brightspace tools

Initially allow:

- List courses.
- List assignments.
- Get assignment details.
- Get upcoming due dates.
- Get announcements.
- Read course content.
- Read the owner's grades and feedback.
- Download/read course files.

Make grade access optional and disabled until explicitly enabled by the owner.

### Piazza tools

Initially allow:

- List classes.
- Read recent posts.
- Search posts.
- Read a full thread.
- Read safe attachment types.
- Generate a recent-activity digest.

### Blocked operations

Do not permit:

- Assignment submission.
- Piazza posting, replying, or editing.
- Sending messages.
- Grade changes.
- Enrollment changes.
- Marking items read.
- Remote file deletion.

Claude must call TrojanClaw's typed connector methods, not arbitrary discovered MCP tools.

## 7. Synchronization design

### 7.1 Background sync

Synchronize every 15–30 minutes:

1. Check connector health.
2. Fetch available courses.
3. Resolve confirmed course mappings.
4. Fetch changed assignments, announcements, posts, threads, and materials.
5. Normalize and upsert structured records.
6. Hash normalized content.
7. Re-embed only changed content.
8. Record counts, duration, errors, and freshness.

Use a lightweight scheduler or separate scheduled command for the single-user version. Do not synchronize inside `/chat`.

### 7.2 Live lookup

Call an MCP server live only when:

- The owner selects “Sync now.”
- Local data exceeds its freshness threshold.
- The question requests current submission or grade status.
- A requested post, assignment, or file is missing locally.

### 7.3 Safe replacement

Do not delete the last good data before a sync completes.

Use:

1. A sync-run ID.
2. Staging rows.
3. Validation of item counts and required fields.
4. Atomic publication of the new version.
5. Retention of the last successful version for rollback.

## 8. Course reconciliation

Provide a one-time mapping screen:

```text
Brightspace: CSCI 544 - Applied Natural Language Processing
Piazza:      CSCI 544 Spring 2026
Unified as:  CSCI 544
```

Auto-suggest mappings using normalized course codes and names, but require confirmation.

Never silently route a question to the first course in the profile. If the user has not specified a course and multiple courses match, ask for clarification.

## 9. Chat and RAG flow

```text
User question
   |
   v
Resolve course and requested data type
   |
   v
Query normalized local records
   |
   +-- Fresh and sufficient -> answer
   |
   +-- Missing or stale -> authorized MCP refresh
                              |
                              v
                         normalize/store
   |
   v
Retrieve relevant structured records and chunks
   |
   v
Claude answer with citations and freshness
```

Examples:

- “What is due this week?” queries normalized Brightspace assignments.
- “Did the professor clarify HW3?” searches Piazza threads.
- “Summarize CSCI 544” combines announcements, assignments, and Piazza activity.
- “What feedback did I receive?” queries current Brightspace grade/feedback data.

Every factual answer should include source links and the last successful synchronization time.

## 10. Frontend additions

Create an Integrations page.

### Owner account

- Verified email.
- Session status.
- Logout.

### Brightspace connector

- Connect.
- Reauthenticate.
- Disconnect.
- Status.
- Last successful sync.
- Sync now.
- Included/excluded courses.
- Optional grade-access toggle.

### Piazza connector

- Connect.
- Reauthenticate.
- Disconnect.
- Status.
- Last successful sync.
- Sync now.
- Included classes.

### Course mapping

- Brightspace course.
- Piazza course.
- Unified course.
- Confirm/edit mapping.

### Sync status

- Current operation.
- Items imported/updated.
- Last error.
- Data freshness.

## 11. Security requirements

- Keep both MCP servers read-only.
- Pin package versions or source commits.
- Run MCP processes with minimal filesystem/network permissions.
- Store credentials in the OS keychain or a secrets manager.
- Never send credentials, cookies, or session material to Claude.
- Redact sensitive MCP arguments and results from logs.
- Treat Piazza posts, course files, and announcements as untrusted content.
- Do not follow instructions embedded inside retrieved content.
- Enforce attachment type and size limits.
- Scan downloaded files before parsing.
- Bind local MCP HTTP services to loopback if HTTP is ever used.
- Require a strong bearer token for any MCP HTTP service.
- Provide disconnect and local-data deletion.
- Document academic-integrity limits.

## 12. Implementation phases

### Phase 1: Single-owner authentication

Tasks:

- Add owner and session models.
- Implement one-email login.
- Replace frontend `localStorage` authentication.
- Add `/auth/login`, `/api/me`, and `/auth/logout`.
- Protect chat and integration endpoints.
- Add rate limiting and session expiration.

Acceptance criterion: another email or unauthenticated browser cannot access the application.

### Phase 2: MCP client foundation

Tasks:

- Add an MCP client manager to FastAPI.
- Start and stop local `stdio` processes.
- Implement MCP initialization and tool discovery.
- Add call timeouts and bounded retries.
- Restart crashed processes safely.
- Expose connector health without exposing secrets.

Acceptance criterion: FastAPI can connect to a test MCP server, call an allowed tool, and recover after the process exits.

### Phase 3: Brightspace integration

Tasks:

- Pin and install the Brightspace MCP.
- Run its interactive setup manually.
- Test USC's browser login and Duo flow.
- Implement the typed adapter.
- Import courses, assignments, deadlines, announcements, and content.
- Keep grade access optional.

Acceptance criterion: TrojanClaw displays upcoming assignments with exact Brightspace source links.

### Phase 4: Piazza integration

Tasks:

- Fork/vendor the Piazza MCP.
- Audit its authentication, logs, and filesystem behavior.
- Remove or disable write-capable operations.
- Implement the typed adapter.
- Import classes, posts, threads, and safe attachments.
- Preserve existing JSON import as a fallback.
- Replace destructive refresh with incremental atomic sync.

Acceptance criterion: TrojanClaw searches Piazza content and cites the exact thread.

### Phase 5: Unified academic model

Tasks:

- Build course mapping.
- Normalize both connectors into shared records.
- Add content hashes and change detection.
- Generate RAG documents only for changed content.
- Add freshness timestamps.

Acceptance criterion: one course view combines both sources without duplicates or cross-course contamination.

### Phase 6: Chat integration

Tasks:

- Add course and entity extraction.
- Route structured questions directly to structured records.
- Use RAG for unstructured material.
- Add inline citations.
- Add freshness indicators.
- Add prompt-injection defenses.

Acceptance criterion: “What changed in CSCI 544 this week?” produces a cited cross-source summary.

### Phase 7: Hardening

Tasks:

- Add connector contract tests.
- Add login/session-expiry tests.
- Add MCP crash/timeout tests.
- Add course-mapping tests.
- Add backup, disconnect, and deletion flows.
- Add sync metrics and structured logs.

Acceptance criterion: connector outages degrade gracefully without deleting the last good data or blocking chat indefinitely.

## 13. Recommended decisions

- **Application authentication:** one allowlisted email with a secure server session.
- **MCP transport:** local `stdio`.
- **Brightspace MCP:** use the community server, pinned to an exact tested version.
- **Piazza MCP:** fork and audit the community implementation.
- **Permissions:** read-only.
- **Storage:** normalized PostgreSQL records plus derived pgvector chunks.
- **Synchronization:** scheduled incremental sync with explicit live refresh.
- **Secrets:** OS keychain or secrets manager.
- **Course identity:** confirmed cross-source mapping.
- **Future multi-user support:** retain clean ownership boundaries, but do not build tenancy yet.

## 14. Definition of done for the single-user release

- Only the configured owner can log in.
- Both connectors can be connected and disconnected.
- Secrets are not stored in source, PostgreSQL, logs, or browser storage.
- Brightspace assignments and announcements synchronize successfully.
- Piazza posts and threads synchronize successfully.
- Courses are explicitly mapped across sources.
- Synchronization never runs inside normal chat latency.
- A failed sync preserves the last good data.
- Chat answers include exact citations and freshness.
- The model cannot invoke non-allowlisted MCP tools.
- All integration behavior is read-only.


## 15. Feasibility review addendum (2026-11-12)

Cross-check of this plan against the code, the other review documents, and the upstream MCP servers. Where this section conflicts with sections 1–14, this section wins.

### 15.1 Revisions to the design

- **Piazza does not use MCP.** Use `piazza-api` directly inside a TrojanClaw adapter that wraps the existing `backend/app/scraper/piazza_ingest.py` fetch code. This matches `PIAZZA_INTEGRATION_REVIEW.md` §9. The community Piazza-MCP has 1 star and 8 commits, returns formatted Markdown rather than JSON, and adds a `uv` toolchain dependency. MCP is used for Brightspace only.
- **Transport is stdio, configured by command and pinned version.** `backend/app/integrations/registry.py` currently keys on `*_MCP_URL` (HTTP) and must change to command and arguments.
- **Table names follow the existing migrations.** Use `app_users` (instead of `app_owner`) and `integration_connections` (instead of `connector_accounts`), and keep `documents.user_id`. Add `app_sessions` and the normalized academic tables in new migrations.
- **Connectors run in a separate worker process** (`python -m app.worker`, scheduled with APScheduler or launchd). The worker owns the MCP sessions and writes to PostgreSQL. FastAPI routes stay synchronous and read only from the database. This avoids duplicate MCP processes under `uvicorn --reload` or multiple workers, and avoids mixing async MCP sessions with sync psycopg2 routes.
- **Defer live MCP lookup during chat (§7.2).** It conflicts with "never sync inside chat". Replace it with a "Sync now" button that queues a job for the worker.

### 15.2 Brightspace MCP facts (`brightspace-mcp-server`)

- npm v5.2.3, MIT license, Node >=22. It has had about 250 releases, so pin an exact version and review upgrades.
- There is no USC preset. Use the generic SSO path with Duo options (`D2L_HEADLESS=false`, `D2L_DUO_PASSCODE`).
- Tool results are `JSON.stringify` output inside text blocks, plus extra "update notice" text blocks. The adapter must parse the JSON and discard the notices.
- **Block these tools in addition to §6:**
  - `get-classlist-emails` and `get-roster` (classmates' personal data).
  - All `download-*` tools (they write to the local filesystem).
  - `sign-in`, `sign-out` and `mfa-relay`.
  - The HTTP server mode.

### 15.3 Bottlenecks and gates

1. **USC Brightspace SSO and Duo are unproven (gate before Phase 3).** Run `npx -y brightspace-mcp-server@<pinned> setup` manually and record whether login succeeds, which Duo method works, how long the session lasts, and whether a headless refresh succeeds. If sessions expire within hours, a 15–30 minute background sync will stall waiting for a person to approve a Duo prompt. Sync frequency must then follow the session lifetime.
2. **Local-machine only.** macOS Keychain "Always Allow", visible-browser MFA and Playwright all mean the backend must run on the owner's computer, bound to `127.0.0.1`. A cloud deployment is not viable for this design.
3. **Piazza authentication.** USC users who sign in to Piazza through SSO must create a Piazza-native password. `piazza-api` uses unofficial endpoints and can break without notice, so keep JSON export as the fallback. *Unverified:* fetching a post may mark it as read on Piazza. Test this before enabling background sync.
4. **Data location (open decision).** §2 says "one local academic-data store", but `DATABASE_URL` currently points at hosted Supabase. Grades, feedback and private Piazza posts would leave the machine. Choose one: accept hosted Supabase and document it, or run local Postgres with pgvector (`supabase start`).

### 15.4 Stabilization already applied

- `documents.search` no longer passes a `uuid.UUID` to psycopg2. That bug made every real `/chat` request fail.
- `push_deadlines.py` and `push_dining.py` now upsert, so re-running them no longer hits the unique-constraint error.
- Piazza sync now embeds everything first, then replaces raw posts and chunks in a single transaction. It also deduplicates on `post_id`.
- Configuration, database and model-provider errors now map to stable 503/502 responses.
- `backend/data/` is gitignored.
