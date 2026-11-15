# TrojanClaw Backend (FastAPI + Supabase)

This is the Python backend for **TrojanClaw**, built with **FastAPI** and configured to use **Supabase**.

Google-first USC onboarding and Google Calendar MCP setup, including the two
OAuth callback URIs and mandatory event approval, are documented in
[Google Calendar setup](../docs/google-calendar.md).

## Architecture

```text
backend/
├── app/
│   ├── agent/               # Claude agent runtime (tool runner, streaming)
│   ├── api/routes/          # HTTP-only request handling
│   ├── integrations/        # Piazza and Brightspace MCP boundaries
│   ├── mcp_server/          # TrojanClaw MCP tools, built per student
│   ├── models/              # Validated API and domain models
│   ├── repositories/        # PostgreSQL access
│   ├── services/            # Chat, calendar, embeddings, sources and activity text
│   ├── config.py            # Typed environment configuration
│   ├── database.py          # Lazy bounded connection pool
│   ├── dependencies.py      # Dependency wiring
│   └── main.py              # FastAPI application factory
├── tests/
├── .env.example
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

The application supports username/password accounts. Chat runs the agent for the
signed-in account: course tools read only that account's rows, while campus search
and EngageSC events are shared public data. See `TrojanClaw_v2_Architecture_and_Spec.md` (§2.0, §5.9).
Account authentication is separate from extension linking.

## EngageSC public events

Sign in, open **Sources**, and select **Turn on** next to EngageSC. This
subscribes that account to the public campus event feed; no USC credentials,
private event access, registration, or calendar writes are involved. View events
supports a text search and a 7/30/90-day range. Other extensions remain deferred.

Apply `supabase/migrations/20261117000000_engagesc_public_events.sql`, or run
`python scripts/apply_engagesc_migration.py` from the repository root using the
backend environment. Install updated backend requirements before restarting.

The fixed source is `https://engage.usc.edu/ical/usc/ical_usc.ics`, published by
the public EngageSC events page. The backend caches the next 90 days in
`campus_events` and refreshes on demand at most hourly. Successful refreshes
atomically replace only EngageSC's cached rows, removing disappeared events.
If the source fails, the last successful cache is retained and marked stale,
with a five-minute retry backoff. Empty feeds are also cached. Feed text is
treated as untrusted data; links are restricted to EngageSC and hidden locations
remain hidden. The feed currently contains expanded event occurrences; if it
changes to recurrence rules the reader fails safely and retains the old cache.

Authenticated endpoints:

- `GET /integrations/engagesc`: account connection state.
- `POST /integrations/engagesc/connect`: validate/cache the feed and link the account.
- `POST /integrations/engagesc/disconnect`: unlink only the current account.
- `GET /integrations/engagesc/events?days=30&search=&limit=30`: public cached events.

When the account is linked, the chat agent can call its `list_campus_events` tool
(keyword search, up to 90 days ahead). **View events** in the sidebar does the same. Linking
does not copy another account's extension settings or private academic data.
Set `RUN_ENGAGESC_DB_TESTS=1` when running pytest to exercise the live feed,
database persistence, search, and account isolation with temporary accounts.

## Account authentication

Apply the existing base migrations, then `supabase/migrations/20261116000000_password_auth.sql`.
To apply just the auth migration using the configured `backend/.env`, run from the
repository root:

```powershell
.\backend\.venv\Scripts\python.exe scripts/apply_auth_migration.py --apply
```

The helper also updates existing Supabase migration history. Without `--apply`,
it only checks for the prerequisite account tables.

- `POST /auth/register`: `{ "name", "username", "password", "confirm_password" }`
- `POST /auth/login`: `{ "username", "password" }`
- `GET /auth/me`: returns `{ "id", "name", "username" }` or 401.
- `POST /auth/logout`: revokes the session and clears the cookie.

Usernames are case-insensitive, 3–32 ASCII letters/numbers/underscores; passwords
are 8–128 characters at registration. Passwords use salted scrypt (N=2^17, r=8,
p=1). Only hashes are stored. Session cookies are HttpOnly, SameSite=Lax, expire
after seven days, and contain a random token whose SHA-256 hash is stored in
`account_sessions`. Registration writes the profile, password hash, and session
in a single transaction. Credential and session tables deny browser database roles.

Use HTTPS and set `APP_ENV=production` in production to require Secure cookies.
Frontend and API must be hosted on the same site (for example sibling subdomains),
and `CORS_ORIGINS` must include the exact frontend origin. In local development,
use `localhost` for both hosts, or `127.0.0.1` for both. Browser requests include cookies.
No password or session token is stored in localStorage.

Login and registration share a per-process limit of 20 attempts per IP per five
minutes. For multiple workers or instances, also configure a shared rate limit
at the ingress; the app does not trust forwarded client-IP headers.

Run backend tests with `python -m pytest backend/tests -q` from the repository root.
Set `RUN_AUTH_DB_TESTS=1` to include the real PostgreSQL account lifecycle test;
it creates a randomly named temporary account and removes it afterward.

## Prerequisites

- Python 3.11. Python 3.14 is not currently supported by the pinned Pydantic
  version and may attempt a failing local Rust build.
- A Supabase project
- A PostgreSQL session-pooler connection string from Supabase **Connect**

## 1) Create and activate a virtual environment

From this directory (`TrojanClaw/backend`):

```/dev/null/setup.sh#L1-2
python3.11 -m venv .venv
source .venv/bin/activate
```

On Windows (PowerShell):

```/dev/null/setup.ps1#L1-2
python -m venv .venv
.venv\Scripts\Activate.ps1
```

## 2) Install dependencies

```/dev/null/install.sh#L1-1
pip install -r requirements.txt
```

For development and tests:

```sh
pip install -r requirements-dev.txt
pytest -q
```

## 3) Configure environment variables

Copy `.env.example` to `.env` and fill in your Supabase credentials.

```/dev/null/env-setup.sh#L1-2
cp .env.example .env
# then edit .env
```

Required database variables:

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `DATABASE_URL`

Example:

```/dev/null/env.example#L1-2
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_KEY=your-server-side-key
DATABASE_URL=postgresql://postgres.your-project-ref:password@pooler-host:5432/postgres?sslmode=require
```

`SUPABASE_URL` is always the HTTPS API URL. `DATABASE_URL` is always the
PostgreSQL DSN. Do not put a PostgreSQL connection string in `SUPABASE_URL`.

The application also needs `OPENAI_API_KEY` for embeddings and
`ANTHROPIC_API_KEY` for chat responses. See `.env.example` for optional Piazza
and CORS settings.

## Deploy the database schema

From the repository root, authenticate and locate your new organization:

```sh
npx supabase login
npx supabase orgs list
```

If the organization does not have a project yet, create one. The command will
prompt for the database password so it is not stored in shell history:

```sh
npx supabase projects create TrojanClaw --org-id <ORG_ID> --region <REGION>
npx supabase projects list
```

Deploy and verify the checked-in migration with the guarded helper:

```sh
chmod +x scripts/deploy_supabase.sh
./scripts/deploy_supabase.sh <PROJECT_REF>
```

The script links the project, performs a remote dry run, asks for confirmation,
applies `supabase/migrations`, and checks the two tables and pgvector extension.
It never runs `db reset`.

You can perform the same steps manually:

```sh
npx supabase link --project-ref <PROJECT_REF>
npx supabase db push --dry-run --remote
npx supabase db push --remote
```

After deployment, copy the session-pooler string from **Supabase → Connect**
into `DATABASE_URL`. URL-encode special characters in the database password.

## 4) Run the API

```/dev/null/run.sh#L1-1
uvicorn app.main:app --reload
```

Server will start at:

- `http://127.0.0.1:8000`

## 5) Verify health endpoints

Open:

- `http://127.0.0.1:8000/healthcheck`
- `http://127.0.0.1:8000/ready`

The liveness response is:

```/dev/null/health.json#L1-3
{
  "status": "ok"
}
```

`/ready` additionally checks PostgreSQL and returns HTTP 503 until
`DATABASE_URL` is configured and reachable.

## API Docs

`GET /integrations` lists Brightspace, Handshake and Piazza.
Local-only `POST /integrations/{provider}/connect` opens a persistent Chromium
sign-in window in a background job; poll the list for the result.
`POST /integrations/{provider}/verify` checks the saved session through MCP.
`POST /chat` runs the agent; its `connectors` list (`brightspace`, `handshake`,
`piazza`) is the sources turned on in chat, which limits the saved sessions the
agent may use (this computer only). See [CONNECTORS.md](../CONNECTORS.md) for setup.

### Chats

Each account has many saved chats (`public.conversations` and
`public.conversation_messages`). The server keeps the history: `POST /chat`
takes `{conversation_id, message, connectors, timezone}`, loads the last 20
saved messages, and saves the new question and answer together. Leave out
`conversation_id` to start a new chat; the reply returns its id and a
first-line title, and an AI-written title replaces it a moment later.

The reply streams as newline-delimited JSON (`application/x-ndjson`):
`activity` (what is being looked up, such as "Checking Brightspace: upcoming
due dates"), `text` (part of the reply as it is written), `reset` (drop the
text so far; the reply is being rewritten after tool calls), then `done` (the
saved reply with `response`, `sources`, `calendar_proposals`,
`conversation_id`, `title`) or `error`. A reply keeps running and is saved
even if the browser stops listening.

`sources` are links shown under a reply. A link is included only if it came
from a connector tool result or retrieved document and the reply cites it;
otherwise the reply just names the sources it checked.

- `GET /conversations?q=`: your chats, pinned first, then newest; `q` searches titles and messages
- `POST /conversations/{id}/pin` with `{pinned}`
- `GET /conversations/{id}/messages`: one chat's messages
- `POST /conversations/{id}/rename` with `{title}`
- `POST /conversations/{id}/delete`: also deletes that chat's calendar drafts

### Attached files

- `POST /uploads` (multipart, field `file`): checks the type by extension **and** contents (PDF, PNG, JPEG, GIF, WebP, UTF-8 text and code, DOCX, PPTX, XLSX; no SVG or other active content), refuses files over 5 MB, and stores the file in the private `chat-uploads` bucket at `<account id>/<upload id>/<file name>`. Returns `{id, name, mime, size, kind}`.
- `GET /uploads/{id}`: downloads one of the signed-in account's own files (always as a download, `nosniff`).
- `POST /chat` takes `attachments: [upload ids]` (up to 5). Each must belong to the signed-in account. PDFs and images are sent to Claude as document and image blocks; text as a text document; Word, PowerPoint and Excel as extracted text (`python-docx`, `python-pptx`, `openpyxl`). The IDs are saved with the message. The next message (one follow-up) includes those files again, up to 18 MB in total (a Claude request is limited to 32 MB); after that they are replaced by a note asking the student to attach them again, because every file is paid for again on each message and tool round.

### Token use

Every chat message runs the agent, and each tool round re-sends the whole request, so the backend keeps requests small and cached:

- **Prompt caching**: the tools and the fixed instructions (`prompts/ai_instructions.md`) carry a cache breakpoint, and the growing conversation is cached automatically (top-level `cache_control`), so repeated input is billed at the cache-read rate. The date and time, connector list and calendar rules change on every request, so they go in a `role: "system"` message after the conversation (supported on Sonnet 5.5, Opus 5.5 and Haiku 5.5); the next message in a chat then re-reads the earlier turns from the cache. Older models get them as a second `system` block instead.
- At most 5 tool rounds per reply (`max_tool_rounds` in `config/agent.yaml`), the last 20 saved messages as history, connector results capped at 20,000 characters (with a note to ask a narrower question), and files re-sent for one follow-up only.
- Each model call is logged as `Claude call: input=… cache_read=… cache_write=… output=…`, so cache hits can be checked in the backend terminal.
- The model is set in `config/agent.yaml` (`claude-sonnet-5-5`); `claude-opus-5-5` costs twice as much, `claude-haiku-5-5` much less.
- Deleting a chat deletes its files. Uploads that were never sent stay in the bucket.

The bucket has no storage policies, so browser keys can't reach it; the backend uses `SUPABASE_SECRET_KEY` (Supabase Dashboard → Project Settings → API Keys → Secret keys). Migration: `20261123000000_chat_uploads_bucket.sql`.

Calendar drafts belong to the chat that created them
(`calendar_proposals.conversation_id`, migration `20261121000000`);
`GET /calendar/proposals?conversation_id=` lists one chat's drafts. A chat
from another account always returns 404. Pins are `conversations.pinned_at`
(migration `20261122000000`).

FastAPI auto-generates interactive docs:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## Notes on Supabase

- Keep keys in `.env` and never commit secrets to version control.
- Use the **anon key** for client-safe operations; use **service role key** only on trusted backend paths.
- If Supabase client initialization fails, verify `SUPABASE_URL` and `SUPABASE_KEY`.

## Development Tips

- Add formatting/linting as the project grows (e.g., `black`, `ruff`).
- Add tests with `pytest` for endpoint and integration coverage.
- Consider environment-specific `.env` files for local/staging/production.
