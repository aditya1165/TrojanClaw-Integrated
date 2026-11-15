# Siri → TrojanClaw without an iOS app

Apple Shortcuts captures a question, calls TrojanClaw's authenticated API, and
speaks the answer. No native app, Mac or Xcode is needed for this manual setup.
See Apple's guides to [running shortcuts with Siri](https://support.apple.com/guide/shortcuts/run-shortcuts-with-siri-apd07c25bb38/ios)
and [calling APIs](https://support.apple.com/en-mt/guide/shortcuts/apd58d46713f/ios).

## Connect your account

Keep the site/backend online at a public HTTPS address. Sign into TrojanClaw and
open **Settings → Ask TrojanClaw with Siri → Create Siri access key**. Copy the
**Authorization header** and **Shortcut URL**. No website cookie or password
needs to be copied. The page discards the key after two minutes or when Settings
closes; the key itself remains valid for 30 days. A lost key must be replaced.

The key is visible to anyone who can inspect your configured shortcut. Keep it
private and never share a shortcut containing it. This is not Keychain storage.
Replacing a key disconnects old shortcuts for that account. **Disconnect Siri**
revokes Siri access without affecting website sessions or Gemini connections.

## Build “Ask TrojanClaw” once on iPhone

Create a shortcut named **Ask TrojanClaw** in Apple's Shortcuts app. Add:

1. **Ask for Input**: type **Text**, prompt “What would you like to ask TrojanClaw?”
2. **Get Contents of URL**: paste the URL from Settings. Expand options, choose
   method **POST**, and add headers `Authorization` (the copied `Bearer …` value)
   and `Content-Type` = `application/json`. Choose **Request Body → JSON**:
   - `question`: select the **Provided Input** variable from action 1. Do not type
     the words “Provided Input” as literal text.
   - `timezone`: your IANA zone, for example `America/Los_Angeles`.
3. **Get Dictionary Value**: key **answer**, from the URL response.
4. **Speak Text**: speak that dictionary value.

Run it manually once with the iPhone unlocked and allow requested permissions.
Then say **“Hey Siri, Ask TrojanClaw.”** If recognition fails, try a distinct name
such as “Ask my USC assistant.” Each invocation handles one independent question.

The backend returns normal HTTP errors with `detail` messages. On a failed
request, Shortcuts may stop; inspect the URL action's result when testing manually.
Do not claim success or speak an empty response after an error.

## Hosting and configuration

`SIRI_PUBLIC_URL` is the externally reachable **API base**, not the chat endpoint:

```dotenv
SIRI_PUBLIC_URL=https://your-public-origin.example/api
```

If unset, it defaults to `MCP_PUBLIC_URL` plus `/api`. The current ngrok endpoint
is therefore `https://antelope-barricade-bluish.ngrok-free.dev/api/siri/chat`.
Settings shows the actual configured URL. A proxy must strip `/api` as Vite does.
If hosting the backend directly, set `SIRI_PUBLIC_URL` to its HTTPS origin instead.
Add the website's origin to `CORS_ORIGINS` and restart after environment changes.

Ngrok works while its tunnel and local servers run. To work with your computer
off, deploy the backend and required source-access infrastructure to an always-on
host with stable HTTPS. This change does not provision hosting or change Chromium
connector behavior. Exclude Authorization headers from proxy logs.

## API and security boundaries

| Endpoint | Authentication | Purpose |
|---|---|---|
| `GET /siri/status` | Website session | Public URL, connection status and expiry; never the key |
| `POST /siri/connect` | Website session + trusted Origin | Replace this account's Siri key; reveal once, no-store response |
| `POST /siri/disconnect` | Website session + trusted Origin | Revoke only this account's Siri keys |
| `POST /siri/chat` | Dedicated Bearer key | JSON `question` and optional IANA `timezone`; return `answer` and `sources` |

Keys contain 256 random bits. Only namespaced hashes are stored in the existing
`account_sessions` table, preserving its 64-character constraint; no migration is
needed. The `siri:` namespace cannot match website hashes or MCP grants. Website
cookies cannot authorize Siri chat, and Siri keys cannot authorize website APIs.
Caller-selected user IDs, connectors and other unknown request fields are rejected.

Chat reads the authenticated account's saved sources through the existing model
provider. It cannot draft/approve calendar events, sign into connectors, upload
files or append a website conversation. Use Gemini's separately authorized
calendar draft tool or the website for calendar proposals.

Requests are limited to 20 per five minutes per account per backend worker.
Invalid credentials have a separate IP limiter. Add gateway-wide quotas for a
deployment needing distributed limits. Questions are limited to 4,000 characters,
speech to 6,000 characters, and backend execution to 90 seconds. Siri/Shortcuts
may impose different limits or require unlocking/opening Shortcuts for slow calls.

## Verification and troubleshooting

Test a short question on the website, then run the Shortcut manually, then invoke
it through Siri. Verify it speaks `answer`, not the entire JSON response. Revoke
access and confirm the old shortcut stops working; replace its header to reconnect.

- **401:** key missing, expired or revoked; include the complete `Bearer …` header.
- **422:** incorrect JSON, blank question, literal variable name or invalid timezone.
- **429:** wait five minutes.
- **504 / shortcut timeout:** ask a narrower question and check connector latency.
- **HTML response:** wrong URL, tunnel warning, or missing API proxy route.
- **Wrong classes:** verify Brightspace for the same account. Enrollment is not a transcript.

`backend/tests/test_siri.py` checks isolation, expiry, rotation, revocation,
authentication separation, validation, rate limits and timeout behavior with
mocked assistant replies. Actual iPhone voice activation, lock-screen behavior
and a live academic answer have not been verified here.
