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

The application is intentionally modeled for one configured user. All knowledge
rows are scoped to `SINGLE_USER_ID`, currently the seeded UUID
`00000000-0000-0000-0000-000000000001`. This avoids premature multi-user auth
while preserving a direct migration path to real identities later.

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
