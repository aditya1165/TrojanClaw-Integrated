# TrojanClaw Improvement Document

_Concise priority guide for the next implementation cycle._

## Product direction

Build TrojanClaw first as a secure, read-only, single-user academic assistant. Integrate Piazza and Brightspace through local MCP connectors, normalize their data into PostgreSQL, and provide cited answers. Defer USC-wide identity, multi-tenancy, and write actions.

## Priority 0: Release blockers

### 1. Replace fake authentication — 10/10

- Remove the `localStorage` authentication flag.
- Allow one configured owner email.
- Use a secure server-side session and `HttpOnly` cookie.
- Protect chat, integrations, grades, files, and sync endpoints.

### 2. Keep connectors and application auth separate — 10/10

- TrojanClaw login identifies the owner.
- Brightspace handles its own SSO/MFA session.
- Piazza handles its own login/session.
- Never expose connector secrets to the model or browser.

### 3. Make all MCP access read-only — 10/10

- Explicitly allow safe tools.
- Block submissions, posts, edits, messages, enrollment changes, and deletion.
- Do not let Claude call arbitrary discovered MCP tools.

### 4. Protect academic credentials and data — 10/10

- Use an OS keychain or secrets manager.
- Pin MCP versions/commits.
- Run MCP servers locally through `stdio`.
- Add disconnect and data-deletion controls.

## Priority 1: Core integrations

### 5. Add an MCP connector manager — 9.5/10

- Start/stop local MCP processes.
- Discover and call allowlisted tools.
- Add timeouts, retries, crash recovery, and health status.
- Normalize tool results through typed adapters.

### 6. Integrate Brightspace — 9.5/10

- Test USC's visible-browser SSO/Duo flow.
- Import courses, assignments, deadlines, announcements, and content.
- Keep grades optional.
- Preserve exact source links and timestamps.

### 7. Integrate Piazza safely — 9.5/10

- Fork and audit the community Piazza MCP.
- Preserve JSON export as a fallback.
- Import classes, posts, threads, and safe attachments.
- Treat all posts as untrusted evidence.

### 8. Create a unified course model — 9/10

- Map Brightspace and Piazza identifiers to one course.
- Require user confirmation for mappings.
- Never silently use the first course in the profile.

## Priority 2: Reliability and answer quality

### 9. Move synchronization out of chat — 9/10

- Run incremental background sync every 15–30 minutes.
- Use live refresh only for stale or explicitly requested data.
- Display last-sync time and failures.

### 10. Make synchronization atomic — 9/10

- Stage each sync version.
- Validate it before publication.
- Preserve the last good version on failure.
- Re-embed only changed content.

### 11. Store structured academic records — 8.5/10

- Use normalized assignments, announcements, grades, posts, threads, materials, and attachments.
- Treat vector chunks as derived search data, not the system of record.

### 12. Add citations and freshness — 8.5/10

- Link answers to exact Brightspace items and Piazza threads.
- Show source type and synchronization time.
- Admit when information is stale or unavailable.

### 13. Defend against prompt injection — 8.5/10

- Treat course content as data, never instructions.
- Separate retrieved content from system/tool policy.
- Prevent retrieved text from authorizing tool calls.

## Priority 3: Engineering quality

### 14. Add automated tests — 8/10

- Owner authentication and session expiry.
- MCP connection and process crashes.
- Connector output contracts.
- Course mapping.
- Sync rollback.
- Retrieval and citation accuracy.

### 15. Complete backend setup — 7.5/10

- Add the missing PostgreSQL driver.
- Add a backend `.env.example`.
- Pin all dependencies.
- Document connector setup and recovery.

### 16. Add observability — 7.5/10

- Structured logs and request IDs.
- Connector health.
- Last successful sync.
- Items created/updated/skipped.
- Model, embedding, and sync costs.

### 17. Improve the frontend — 7/10

- Add an Integrations page.
- Show connection state, sync state, and errors.
- Add course mapping and included-course controls.
- Add logout and data deletion.

## Recommended delivery order

1. Single-owner authentication.
2. MCP client foundation.
3. Brightspace integration.
4. Piazza integration.
5. Unified course model.
6. Background incremental sync.
7. Cited cross-source chat answers.
8. Tests, monitoring, and recovery.

## Single-user release target

The release is ready when:

- Only the configured owner can enter.
- Piazza and Brightspace connect independently.
- All integrations are read-only.
- Credentials remain outside application data and logs.
- Courses are explicitly mapped.
- Sync failures preserve prior data.
- Answers cite exact sources and show freshness.
- MCP tools are allowlisted and cannot be expanded by model choice.

## Deferred work

- USC production Shibboleth registration.
- Multiple user accounts and row-level tenancy.
- Institution-wide course enrollment lookup.
- Posting to Piazza.
- Assignment submission.
- Automatic messages or bookings.
- Remote public MCP hosting.

Detailed references:

- `PROJECT_REVIEW.md`
- `PIAZZA_INTEGRATION_REVIEW.md`
- `SINGLE_USER_MCP_IMPLEMENTATION_PLAN.md`

