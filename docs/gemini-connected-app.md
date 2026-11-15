# Gemini Connected Apps → TrojanClaw

This remote MCP bridge lets the Gemini mobile/web app ask the same academic
assistant as the TrojanClaw website. It is separate from Android AppFunctions
and does not require that preview approval. Google controls Connected Apps
availability; see [Google's current requirements](https://support.google.com/gemini/answer/17209137).

## Current development connection

Tunnel the frontend's port 5173 with ngrok. The frontend proxies MCP, OAuth and
discovery paths to the backend on port 8000. Keep both servers and ngrok running.
The public endpoint is:

```text
https://antelope-barricade-bluish.ngrok-free.dev/mcp
```

In Gemini's web app open Settings → Connected Apps → Add a custom app. Use the
**full endpoint above**, rather than the website homepage. If an old connection
was added before this endpoint existed, remove it and add it again. Dynamic
client registration supplies client credentials automatically.

The linking flow opens TrojanClaw on the ngrok domain. Sign in to the intended
TrojanClaw account on that domain, review the assistant-access screen, and choose
**Allow assistant access**. A login on localhost has a different cookie and does
not automatically sign in the ngrok domain. If an external sign-in flow returns
to localhost, finish sign-in on the public origin, then restart linking.

Then select `@TrojanClaw` (or the connected app's chosen name) in Gemini and ask,
for example, “What classes am I taking this semester?” Configure and verify the
account's Brightspace/Handshake/Piazza connections in TrojanClaw first.

The tool returns answers and source links to Gemini. Each call is independent:
Gemini must include relevant follow-up context in `message`. It does not type
into an open browser tab or append messages to the website's conversation UI.

### Voice selection

The MCP server advertises the display name **TrojanClaw — USC student assistant**
and the spoken alias **Trojan Claw**. After metadata changes, reconnect the custom
app or start a fresh Gemini chat if it is still using cached descriptions. In a
regular Gemini voice prompt, try: “Use my connected Trojan Claw app to tell me
what classes I am taking.” First verify the connection by selecting the custom
app with `@` and sending the same question in text.

These descriptions help the assistant choose the tool after discovering it;
they cannot change Google's speech recognition, force tool selection, or enable
custom MCP tools in an unsupported voice surface. If Gemini searches unrelated
topics, inspect the speech transcript: an incorrect transcription happens before
the request reaches TrojanClaw. If text with `@` works but voice does not, the
remaining issue is Gemini's transcription or tool selection, not an MCP answer.

## Deployment

Configure the backend's ignored `.env` or your server's secret manager:

```dotenv
MCP_PUBLIC_URL=https://your-public-origin.example
MCP_ENCRYPTION_KEY=<persistent Fernet key>
```

Generate the key once with `Fernet.generate_key()` from Python's `cryptography`
package. Keep it stable and private, shared across all workers. Never commit the
key. Changing it invalidates OAuth client registrations and existing envelopes;
changing the public origin requires relinking. The feature is disabled when
`MCP_PUBLIC_URL` is unset. The URL must be an HTTPS origin without a path.

Include that HTTPS origin in `CORS_ORIGINS`. Restart the backend after changing
environment configuration. A hosted reverse proxy must forward `/mcp`,
`/.well-known/*`, `/authorize`, `/token`, `/register`, `/revoke` and `/api/*`
(strip `/api` for backend API routes, as Vite does). Serve the frontend at `/`.
Proxy Host must match the advertised public origin; do not trust arbitrary hosts.
Forward MCP request headers and allow chat requests lasting up to 240 seconds.

## Access and boundaries

- SDK-managed OAuth authorization code flow with S256 PKCE, exact registered
  HTTPS redirect validation, dynamic registration and resource-bound tokens.
- Explicit approval by the signed-in account; the assistant cannot choose a user.
  The requesting client's display name is unverified and shown as such.
- `trojanclaw:chat` is the only scope. Access tokens last one hour; grants last
  at most seven days. Refresh tokens rotate, invalidating the previous grant.
- Protocol metadata/client secrets are encrypted with Fernet. Revocable grants
  and single-use two-minute codes are hashed in the existing `account_sessions`
  table under an `mcp:` namespace. These entries cannot authenticate website
  requests. No migration or plaintext cookie export is needed.
- The `trojanclaw_chat(message, timezone)` tool invokes `ChatService` with the
  authenticated subject and that account's saved read-only source connections.
  Calendar drafts/writes and connector login actions are excluded.
- Access continues independently of the browser session until expiry/revocation.
  Settings → **Disconnect all assistants** revokes that account's
  assistant codes/grants without signing out the browser or Android app.
- Answer data is sent to Gemini; the normal TrojanClaw assistant's model provider
  also processes the question as it does for website chat.

## Verification

`backend/tests/test_remote_mcp.py` exercises real HTTP MCP tool discovery and chat
with mocked academic answers, DCR, S256 PKCE, wrong-verifier rejection, code replay,
cross-client rejection, redirect validation, encrypted envelope integrity,
refresh rotation, and account revocation. It does not prove an actual Gemini
account has linked successfully or that live connector sign-ins are valid.

Live discovery checks: `/.well-known/oauth-authorization-server` and
`/.well-known/oauth-protected-resource/mcp` return metadata; an unauthenticated
`POST /mcp` returns 401 with a `WWW-Authenticate` discovery challenge. Use Gemini's
Developer Mode or MCP Inspector for client-side diagnosis.
