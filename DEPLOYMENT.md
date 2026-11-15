# Remote multi-user deployment

See [the remote-browser guide](docs/remote-browser.md) for user instructions,
USC bootstrap behavior, implementation details, and troubleshooting.

The deployment serves the web app and `/api` from the same HTTPS origin. PostgreSQL
holds app accounts, app-session hashes, and encrypted per-account connector state.
Browserbase supplies private remote sign-in browsers; the server never opens a
desktop window. All three MCP connectors are installed in the image before serving users.
Piazza and Handshake use separate locked Python environments because their MCP
versions differ; Brightspace uses its pinned Node package.

## 1. Configure secrets

Copy `deploy/.env.example` to `deploy/.env` on the server. This file is ignored by
Git and excluded from Docker build contexts. Restrict it to the deployment owner
or use your hosting platform's secret manager. Never use `VITE_` variables for
database, Browserbase, encryption, or model keys.

Set `APP_DOMAIN` to your public DNS hostname and `PUBLIC_APP_URL` / `CORS_ORIGINS`
to its HTTPS origin, for example `https://copilot.example.edu`. Point that DNS name
at the server. The provided Caddy gateway handles certificates and HTTP-to-HTTPS
redirects on ports 80 and 443. Only the gateway publishes host ports.

Create a Browserbase project and configure `BROWSERBASE_API_KEY` and
`BROWSERBASE_PROJECT_ID` as server secrets. The project must support persistent
contexts, interactive live views, and your expected concurrent users. The server
creates a separate context for each account/source and disables login recordings,
logs, and automatic CAPTCHA solving.

Generate a durable encryption key locally, then store it in the server secret
manager as `CONNECTOR_ENCRYPTION_KEYS`:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Do not paste keys into chat or commit them. Use the same key ring on every API
replica. Losing all keys loses access to saved sessions. For rotation, set
`new-key,old-key` in that order; new writes use the first key and existing records
remain decryptable with the old one. Keep old keys until all saved records have
been re-encrypted through reconnecting or a controlled migration.

Configure Anthropic and Gemini API keys and the Google Calendar OAuth client and
encryption key. Register the hosted `/auth/google/callback` and `/calendar/callback`
URLs in Google Cloud; neither callback has an `/api` prefix. Set MCP/Siri settings
to preserve connected assistant access. App sessions remain hashed opaque tokens;
connector cookies are reversibly encrypted
because the MCP servers need to use them.

## 2. Migrate PostgreSQL and restrict the backend role

Review and apply the checked-in migrations to the intended database. In addition
to the existing schema/auth migrations, the new migration is
`supabase/migrations/20261124000000_hosted_connector_sessions.sql`. It creates the
private encrypted-session store and a shared database-backed authentication rate
limiter. Also apply `supabase/migrations/20261125000000_remote_usc_login.sql` for
cookie-bound anonymous USC attempts and a unique USC identity index. Inspect any
duplicate USC identities before applying that index; never auto-merge accounts.
Browser roles (`anon`, `authenticated`) cannot access the private session tables.

Also apply `supabase/migrations/20261126000000_piazza_connector.sql` to enable
Piazza in the existing session table. It retains existing sessions and owner
policies. See [the Piazza guide](docs/piazza.md) for the focused migration checker,
runtime setup, and interactive sign-in instructions.

For a linked Supabase project, preview before applying:

```sh
supabase db push --dry-run
supabase db push
```

For an existing database already at the hosted-session migration, the focused
USC migration helper can check prerequisites without changing data. From `backend/`
with backend dependencies available:

```sh
python ../scripts/apply_remote_usc_migration.py
python ../scripts/apply_remote_usc_migration.py --apply
```

The second command changes the configured database. It checks duplicate USC
identities first and commits the schema and migration history together.

Run `deploy/backend-role.sql` once as the database administrator after migrations.
It creates a restricted `trojanclaw_api` login and a backend privilege role. Use
psql's interactive `\password trojanclaw_api` to set its password. Put that login's
TLS PostgreSQL DSN in `DATABASE_URL`; do not deploy the app with a superuser or
`BYPASSRLS` login. URL-encode password characters in the DSN.

The role script grants only the tables required by the existing auth, public
events, campus retrieval, and connector features. Connector rows retain their
forced per-account RLS policy, using a transaction-local account UUID. All
repository queries also include explicit user predicates. Use a separate
administrative role for future schema migrations.

## 3. Build and run

From the repository root on a Docker-enabled server:

```sh
docker compose --env-file deploy/.env config --quiet
docker compose --env-file deploy/.env up -d --build
```

The API uses one worker. Connector sessions and USC attempts are database-backed,
but the new Google OAuth onboarding registry remains in memory. A restart during
onboarding requires starting sign-in again; completed app and connector sessions
persist. Use one API instance until that registry is moved to shared storage.
Different users' browser operations use separate locks and can proceed concurrently.

The frontend image contains only compiled static files. The API runs as a nonroot
user with a read-only root filesystem and a temporary memory-backed `/tmp`.
The Docker build excludes local `.env` files, browser profiles, exported account
data, caches, and Git metadata. It does not include the developer's saved sessions.
No local Chromium installation is needed for managed sign-in.

The example network reserves gateway IP `172.30.0.30`; Uvicorn trusts forwarded
client-IP headers only from that gateway. This matters for the shared login rate
limiter. If you change the network or use a platform load balancer, update the
trusted proxy addresses accordingly. Do not expose the API port directly or trust
forwarded headers from arbitrary Internet clients.

## 4. Verify on the hosted domain

1. Create two different TrojanClaw accounts using two separate browser profiles.
2. In account A, connect Brightspace, Handshake and Piazza through the embedded remote
   browser. Complete MFA and choose **Finish sign-in**. All should report connected.
3. Account B must still report not connected and must not be able to resume A's
   login, use A's tools, or decrypt A's saved record.
4. Restart the API and verify A's saved connections still work.
5. Disconnect A's source and verify that reads stop and its Browserbase context is
   deleted. A temporary provider outage leaves the connection blocked, with a retry.
6. Verify HTTPS, Secure/HttpOnly/SameSite app cookies, allowed origins, and absence
   of cookie values, API keys, and live-view links in access/error logs.

Browserbase and your institution may require extra verification from a cloud
browser. MFA must be completed by the student; the app does not bypass it.
Live-view URLs are short-lived bearer links: only return them to their authenticated
owner, and do not add analytics or third-party logging around login responses.

## Local development

Use `APP_ENV=development` and your local frontend origin in `CORS_ORIGINS`.
The Vite `/api` proxy keeps app cookies same-origin. Install:

```sh
uv sync --locked --project integrations/handshake
uv sync --locked --project integrations/piazza
npm ci --prefix integrations/brightspace
```

Run the existing backend and frontend commands. Hosted connector testing still
requires Browserbase secrets, an encryption key, and the database migration;
there is deliberately no fallback to computer-wide cookie files.

## Verification limits

The repository's mocked boundary tests exercise isolation, encryption, helper
dispatch, vendor API options, and the UI build. These do not prove real school MFA
compatibility or successful production deployment. Apply the migration, configure
server secrets, build the containers, and run the two-account check above before
opening the hosted app to users.
