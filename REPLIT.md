# Host TrojanClaw on Replit

The included `.replit` configuration serves React, FastAPI, Google callbacks,
remote MCP and Siri on one public port. Authentication uses Browserbase Chromium.
No local browser, Docker, ngrok or separate frontend server is needed on Replit.

## Quick start

1. Import this repository into Replit, keeping `.replit` and `replit.nix`.
2. Open **Tools → Secrets** and add the existing backend secrets listed below.
3. Click **Run**, or execute in Replit Shell:

   ```sh
   python3 scripts/replit.py run
   ```

The first run installs the backend, both locked Python MCP helpers, Brightspace
and Google Calendar Node packages, then builds the frontend. Later runs reuse
unchanged builds. The app listens on `0.0.0.0:5000`. Choose **Open in new tab**
for Google sign-in rather than signing in inside Replit's embedded preview.

## Secrets

Copy these values from your ignored local `backend/.env` into Replit Secrets:

| Secret | Purpose |
| --- | --- |
| `DATABASE_URL` | Existing Supabase PostgreSQL database |
| `ANTHROPIC_API_KEY` | Chat agent |
| `GEMINI_API_KEY` | Campus search embeddings |
| `BROWSERBASE_API_KEY` | Managed browser access |
| `BROWSERBASE_PROJECT_ID` | Browserbase project UUID |
| `CONNECTOR_ENCRYPTION_KEYS` | Stable encryption key ring for connector sessions |
| `GOOGLE_CALENDAR_CLIENT_ID` | Google Web OAuth client |
| `GOOGLE_CALENDAR_CLIENT_SECRET` | Google Web OAuth secret |
| `GOOGLE_CALENDAR_ENCRYPTION_KEY` | Existing Google token encryption key |
| `MCP_ENCRYPTION_KEY` | Existing assistant authorization encryption key |

Also copy `SUPABASE_URL` and `SUPABASE_SECRET_KEY` for file uploads, plus
`SUPABASE_KEY` if used by your configuration. Preserve embedding model/dimension
overrides if your database uses values other than the repository defaults.

Use your existing migrated Supabase database. A new Replit database does not
automatically contain TrojanClaw's schema or accounts. The launcher does not
apply migrations or modify existing account data.

**Keep encryption keys stable.** These are random encryption keys, not API keys.
For the same database, copy the existing keys rather than generating replacements.
For a completely new installation, generate a key using:

```sh
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Generate separate values for each encryption setting and save them in Secrets
once. Never commit them. The launcher validates formats without printing values
and never replaces keys at startup.

After a connector key is lost, a newly verified USC login or explicit reconnect
can replace that account's unreadable connector session. Reads remain blocked.
Old Browserbase context IDs cannot be recovered without their key; review unused
contexts in Browserbase separately. Google/MCP token stores still need their
original keys.

## Google callbacks

The workspace run derives its origin from `REPLIT_DEV_DOMAIN`. Register these
exact URLs in your Google Cloud Web OAuth client's Authorized redirect URIs:

```text
https://YOUR-PREVIEW-DOMAIN.replit.dev/auth/google/callback
https://YOUR-PREVIEW-DOMAIN.replit.dev/calendar/callback
```

Both callbacks have **no `/api` prefix**. Keep localhost/ngrok URLs too if still
used. The launcher automatically aligns Google, frontend, CORS, MCP and Siri URL
settings with the active Replit domain, overriding stale ngrok/localhost URLs.

## Publish

1. In **Publishing → Adjust settings**, choose **Reserved VM**, web server mode,
   one instance. The current Google sign-in registry is process-local, so use
   one worker/instance. Avoid Autoscale for now.
2. Set `PUBLIC_APP_URL` to your published HTTPS origin, for example
   `https://YOUR-APP.replit.app`, without a path. Use your custom domain if applicable.
3. Review **Production app secrets** and ensure the same required secrets and
   stable encryption keys are available there.
4. Add the published Google redirect URIs:

   ```text
   https://YOUR-APP.replit.app/auth/google/callback
   https://YOUR-APP.replit.app/calendar/callback
   ```

5. Use the included commands and publish:

   ```sh
   # Build command
   python3 scripts/replit.py build

   # Run command
   python3 scripts/replit.py start
   ```

6. Check `/healthcheck` (process running) and `/api/ready` (database reachable),
   then complete Google/USC sign-in in a new browser tab.

Publishing starts already-built dependencies on one public port. It uses secure
production cookies, Browserbase, one worker, no automatic reload and no query-bearing
access logs. It trusts forwarded headers from Replit's managed TLS ingress; this
launcher is intended for that hosting environment.

Gemini connects to `https://YOUR-APP.replit.app/mcp`. Authorize on the same domain;
existing grants may need reconnecting after moving domains. Siri's API base is
the published origin plus `/api`.

## Commands and troubleshooting

| Command | What it does |
| --- | --- |
| `python3 scripts/replit.py run` | Install/build if needed, then start; used by Run |
| `python3 scripts/replit.py build` | Install dependencies and compile the site |
| `python3 scripts/replit.py start` | Validate settings and start; no installation |
| `backend/.venv/bin/python scripts/replit.py check` | Validate URLs, required settings and keys |

- **Missing setting / invalid Fernet key:** add the named value in Secrets and
  Production app secrets. Keep the generated value's trailing `=`. Restart after changes.
- **Google redirect mismatch:** register the active preview/published callbacks
  above, without `/api`.
- **Sign-in stopped after a local code edit:** the development reloader discards
  pending Google attempts. Start Google sign-in again. Published Replit runs have no reload.
- **Handshake dashboard visible, saved session unreadable:** start a fresh
  Google/USC sign-in or reconnect with the recovery fix; an attempt interrupted
  by a server restart cannot resume.
- **Cloudflare/MFA:** complete verification in the live browser or skip Handshake
  and reconnect later. Browserbase does not guarantee institutional verification success.
- **Missing database schema:** use your existing migrated Supabase database; see
  [deployment](DEPLOYMENT.md) before applying migrations elsewhere.

Local tests cover routing, cookies, streaming, configuration and launcher steps.
A real Replit publication and live school MFA still need interactive verification.

References: [Replit configuration](https://docs.replit.com/features/project-setup/configuration),
[ports](https://docs.replit.com/features/project-setup/ports),
[Secrets](https://docs.replit.com/core-concepts/project-editor/app-setup/secrets),
[deployment types](https://docs.replit.com/features/publishing/deployment-types).
