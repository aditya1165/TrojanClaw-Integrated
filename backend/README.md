# TrojanClaw Backend (FastAPI + Supabase)

This is the Python backend for **TrojanClaw**, built with **FastAPI** and configured to use **Supabase**.

## Architecture

```text
backend/
├── app/
│   ├── api/routes/          # HTTP-only request handling
│   ├── integrations/        # Piazza and Brightspace MCP boundaries
│   ├── models/              # Validated API and domain models
│   ├── repositories/        # PostgreSQL access
│   ├── services/            # Chat, embeddings, prompts, and LLM use cases
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

The application supports username/password accounts. Chat searches are scoped to
the signed-in account, never the seeded demo user. Existing ingestion commands
still use `SINGLE_USER_ID`; their historical rows are not reassigned to new accounts.
Account authentication is separate from extension linking.

## EngageSC public events

Sign in and select **Link public events** in the EngageSC sidebar card. This
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

The copilot receives a limited selection of up to 30 events in the next 30 days
when the account is linked. **View events** supports broader searches. Linking
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

`GET /integrations` lists Piazza (unavailable), Brightspace, and Handshake.
Local-only `POST /integrations/{provider}/connect` opens a persistent Chromium
sign-in window in a background job; poll the list for the result.
`POST /integrations/{provider}/verify` checks the saved session through MCP.
`POST /chat` accepts `connectors: ["brightspace", "handshake"]` to enable
read-only live tool use. See [CONNECTORS.md](../CONNECTORS.md) for setup.

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
