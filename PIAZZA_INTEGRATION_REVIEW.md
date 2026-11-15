# TrojanClaw Piazza Integration Review

_Prepared on 2026-11-05. This is a focused companion to `PROJECT_REVIEW.md` and covers Piazza extraction, MCP options, implementation bottlenecks, and ranked improvements._

## 1. Executive conclusion

TrojanClaw can extract Piazza content today. The repository already contains a Piazza ingestion pipeline that can:

- Log in through the community `piazza-api` Python package.
- Fetch course posts from a configured Piazza network.
- Import local Piazza JSON exports.
- Scrape explicitly configured Piazza thread URLs with an optional session cookie.
- Normalize questions, answers, replies, follow-ups, and other nested text.
- Store both raw posts and embedded chunks in PostgreSQL.
- Retrieve Piazza content through vector similarity and keyword fallback.
- Trigger a refresh when a course question lacks enough retrieved Piazza evidence.

However, Piazza has no official public API or official Piazza MCP server identified during this review. The available clients and MCP servers rely on Piazza's unpublished internal endpoints or authenticated browser sessions. They can break without notice and need careful handling of credentials, private course content, and authorization.

The current TrojanClaw implementation is suitable as a proof of concept, but it should not be exposed to real student course data yet. The highest-priority blocker is not MCP support. It is establishing trustworthy user identity and ensuring that every Piazza request is limited to courses the authenticated user is actually authorized to access.

## 2. Current implementation findings

### 2.1 Supported extraction paths

#### Direct `piazza-api` extraction

The primary code path is `_fetch_posts_via_piazza_api` in `backend/app/scraper/piazza_ingest.py`.

It requires:

- `PIAZZA_EMAIL`
- `PIAZZA_PASSWORD`
- A `course_id` to Piazza network-ID mapping through `PIAZZA_NETWORK_MAP`, or a global `PIAZZA_NETWORK_ID`

It then:

1. Creates a `Piazza` client.
2. Logs in with email and password.
3. Opens the configured Piazza network.
4. Iterates through posts.
5. Extracts post identifiers, subjects, histories, questions, replies, follow-ups, and source URLs.

The underlying [`piazza-api`](https://github.com/hfaran/piazza-api) project explicitly identifies itself as an unofficial client for Piazza's internal API. It is therefore a reverse-engineered dependency rather than a supported Piazza integration.

#### JSON export ingestion

The pipeline recursively loads every `.json` file under `PIAZZA_EXPORT_DIR`, defaulting to `backend/data/piazza`.

It supports payloads shaped as:

- A list of posts.
- An object containing `posts`.
- An object containing `results`.
- A single post-like object.

This is a useful fallback for instructor-approved or user-provided exports because it avoids retaining a live password or session.

#### Authenticated thread scraping

`PIAZZA_THREAD_URLS` can contain comma-separated thread URLs. If `PIAZZA_COOKIE` is configured, it is sent as the HTTP `Cookie` header.

The scraper removes page navigation/scripts/styles and stores visible text. This path is less structured and less reliable than API/JSON extraction because it loses thread semantics and depends on the returned HTML containing the desired content.

### 2.2 Data normalization and storage

The ingestion code stores data in two forms:

1. **Raw form:** `piazza_data`
   - `course_id`
   - `post_id`
   - `title`
   - `source_url`
   - `content_text`
   - Complete raw JSON payload
   - Creation and update timestamps

2. **RAG form:** `documents`
   - Piazza content is chunked into approximately 320-word chunks with 40-word overlap.
   - OpenAI `text-embedding-3-small` creates embeddings.
   - Documents use `data_type='piazza_course'`.
   - The `campus` column is repurposed to contain the course ID.

The raw representation helps keyword fallback and future reprocessing. The chunked representation supports semantic search.

### 2.3 Retrieval behavior

For queries containing course-like terms such as `class`, `Piazza`, `assignment`, `homework`, `midterm`, or `professor`, retrieval:

1. Searches Piazza vector chunks.
2. Filters by `course_id` when one is provided.
3. Falls back to `ILIKE` keyword matching over `piazza_data` when vector candidates are sparse.
4. Combines vector distance with title hits, body hits, Piazza bonus, and course-match bonus.
5. Mixes those candidates with general USC documents.
6. Returns up to the requested number of chunks.

This hybrid vector-and-keyword approach is a good prototype baseline.

### 2.4 Automatic refresh behavior

The chat endpoint considers Piazza content insufficient when a course-like query retrieves fewer than two matching Piazza chunks. It then performs a synchronous refresh and runs retrieval again.

Refresh throttling uses an in-memory dictionary:

- Normal cooldown: 15 minutes.
- Forced refresh minimum gap: 2 minutes.

This only works within one Python process and is lost on restart.

### 2.5 Course selection behavior

The backend selects a course using this order:

1. Explicit client-supplied `user_context.course_id`.
2. First item in `enrolled_courses`.
3. First item in the legacy `courses` array.

The frontend also sets `course_id` to the first comma-separated course entered by the user. There is no server-side enrollment verification.

### 2.6 Dependency status

`backend/requirements.txt` includes `piazza-api==0.15.0`, but omits `psycopg2` even though Piazza ingestion and retrieval import it. A fresh backend installation therefore remains incomplete.

## 3. Piazza MCP findings

### 3.1 No official Piazza MCP was identified

No Piazza integration appears in the [official MCP reference-server collection](https://github.com/modelcontextprotocol/servers). No official Piazza-owned MCP server or official public Piazza developer API was identified.

This does not mean MCP cannot be used. It means any current Piazza MCP is a community wrapper around unofficial access methods.

### 3.2 Community Piazza MCP

[`RohanMuppa/Piazza-MCP`](https://github.com/RohanMuppa/Piazza-MCP) is a read-only MCP server exposing:

- Enrolled class listing.
- Recent feeds.
- Folder filtering.
- Post search.
- Complete thread retrieval.
- Attachment text extraction.
- Cross-class daily digests.

It uses `piazza-api` and requires a Piazza-native email/password. Its own documentation warns that it relies on unpublished internal endpoints and may break. At review time it was a very new repository with two commits and no meaningful adoption signal, so it should be treated as a reference implementation rather than a production dependency.

### 3.3 Broader course-data MCP

[`hamzakammar/mcp-workspace`](https://github.com/hamzakammar/mcp-workspace) exposes Piazza and Brightspace tools, including synchronization and semantic search. It is architecturally more complete, but also significantly more complex: it manages API keys/JWTs, browser sessions, stored state, background refresh, Supabase, object storage, and embeddings.

Its design demonstrates that Piazza can be exposed over MCP, but adopting a hosted third-party service for private USC course content would require a separate security, privacy, retention, and institutional review.

### 3.4 Does TrojanClaw need MCP?

Not for the current web application.

TrojanClaw's FastAPI backend can call a Piazza connector directly. Adding MCP between two components owned by the same application introduces protocol, deployment, authentication, and observability overhead without automatically improving extraction reliability.

MCP becomes valuable if the goal is to make one authorized Piazza connector reusable by:

- TrojanClaw.
- ChatGPT or Claude.
- Cursor or another development agent.
- A mobile application.
- Other USC-approved student tools.

The recommended design is therefore:

```text
Piazza connection/session
          |
          v
Read-only Piazza connector
          |
          v
Normalized course/post/thread service
          |
          +-------------------+
          |                   |
          v                   v
TrojanClaw internal API   Optional MCP server
```

The connector should be the system of record for access and normalization. MCP should be only one interface over it.

## 4. Authentication findings

### 4.1 USC Shibboleth and Piazza are separate sessions

Authenticating a user to TrojanClaw through USC Shibboleth does not automatically provide a Piazza session. The application still needs an authorized way to access Piazza for that user.

Possible approaches are:

1. Piazza-native credentials supplied by the user.
2. A delegated Piazza browser/session-cookie flow.
3. Instructor-approved or user-provided exports.
4. An institutionally approved integration if USC/Piazza offers one privately.

### 4.2 Shared credentials are unsafe

The current environment-variable design implies one shared Piazza identity for the backend. That identity may have access to multiple courses and may expose more information than the requesting student should see.

A single shared instructor or team account creates risks including:

- Cross-course disclosure.
- Access continuing after a student drops a course.
- No per-user revocation.
- No reliable audit trail.
- Credential compromise affecting every course.
- Potential conflict with Piazza, course, or USC policies.

### 4.3 SSO-only Piazza users

Some community clients require a Piazza-native password even when the user's institution normally enters Piazza through SSO. The community Piazza MCP recommends creating a Piazza password in account settings for SSO-only accounts. Whether this is permitted and available for USC users should be verified before relying on it.

### 4.4 SSO does not prove course membership by itself

Even after TrojanClaw implements USC Shibboleth correctly, the backend must obtain or verify course membership separately. It must not trust a course name typed into the onboarding form.

## 5. Ranked bottlenecks

Ratings represent combined impact on security, product correctness, reliability, and ability to launch. A rating of **10/10** is an immediate release blocker.

### 1. Unverified course authorization — 10/10

The requesting user controls `course_id`. Retrieval trusts it without checking that the authenticated user belongs to the course.

Consequences:

- Private posts may be exposed across courses.
- A user can request a guessed network/course identifier.
- Future student-specific data would inherit the same vulnerability.

### 2. TrojanClaw authentication is currently simulated — 10/10

The frontend's authentication state is a `localStorage` boolean. The backend has no verified USC identity to associate with a Piazza connection.

Consequences:

- No accountable user identity.
- No trustworthy authorization or audit log.
- No safe way to store per-user Piazza connections.

### 3. Unofficial and unstable upstream API — 9.5/10

`piazza-api` calls unpublished internal endpoints. Changes to Piazza login, JSON structures, rate limits, or anti-bot controls can break ingestion without notice.

Consequences:

- Unpredictable outages.
- Ongoing maintenance burden.
- No vendor SLA or compatibility promise.

### 4. Centralized/shared Piazza credentials — 9.5/10

One environment-level email/password or cookie is used by the entire backend.

Consequences:

- Excessive privilege.
- Large breach radius.
- Inability to revoke a single user's access.
- Ambiguous consent and course ownership.

### 5. Cross-course contamination from JSON imports — 9/10

Every JSON file in one directory is loaded and assigned to whichever `course_id` is currently being synchronized. The file's true course/network is not verified.

Consequences:

- Posts can be mislabeled as belonging to another course.
- Retrieval can leak content across courses.
- Answers can confidently cite the wrong class.

### 6. Destructive non-atomic refresh — 9/10

The pipeline deletes existing vector documents and commits that deletion before replacement embeddings have succeeded.

Consequences:

- A transient OpenAI or database error can remove the last good searchable corpus.
- Users can receive no Piazza results until a later successful refresh.

### 7. Piazza synchronization blocks the chat request — 8.5/10

Login, post download, raw replacement, potentially hundreds of embedding calls, and database insertion can occur synchronously inside `POST /chat`.

Consequences:

- Very high latency.
- Request timeouts.
- Duplicate paid work under concurrency.
- Poor user experience and difficult cancellation.

### 8. User-generated posts are treated as trusted model evidence — 8.5/10

Piazza posts can contain arbitrary instructions, links, code, or adversarial text. The global RAG prompt tells the model to treat retrieved data as absolute fact.

Consequences:

- Prompt injection.
- Incorrect claims being amplified as official course instructions.
- Potential influence over enabled tools.

### 9. No institutional/privacy approval path — 8.5/10

The repository does not document instructor consent, USC approval, FERPA analysis, retention, deletion, or acceptable-use constraints for centrally collecting course discussions.

Consequences:

- The integration may be technically functional but unsuitable for deployment.
- Private student questions and identities may be retained unnecessarily.

### 10. Full raw payload retention — 8/10

Every raw post object is stored as JSONB, potentially including author identifiers and metadata that are not needed for answering questions.

Consequences:

- Larger privacy exposure.
- Unclear retention obligations.
- More sensitive data in backups and logs.

### 11. First-course-only routing — 8/10

The first course entered in the profile becomes the default `course_id`, even when the question concerns another enrolled course.

Consequences:

- Wrong course is synchronized.
- Irrelevant or misleading answers.
- Unnecessary access to unrelated course content.

### 12. Process-local cooldown and no distributed lock — 7.5/10

Refresh timestamps live in a Python dictionary.

Consequences:

- Every worker has a different cooldown.
- Restarts erase refresh state.
- Concurrent users can refresh the same course simultaneously.

### 13. Sequential embedding and insertion — 7.5/10

Each chunk generates one OpenAI request, followed by individual SQL execution.

Consequences:

- Slow ingestion.
- Higher network overhead.
- More opportunities for partial failure.
- Poor scalability across many courses.

### 14. Weak deduplication and version tracking — 7/10

Posts are deduplicated only by `(source_url, title)`. There is no content hash, post revision version, deletion marker, or stable unique constraint on `(course_id, post_id)`.

Consequences:

- Edited content can be mishandled.
- Duplicates can survive under changed titles/URLs.
- Deleted posts are hard to audit correctly.

### 15. No freshness/status exposed to users — 7/10

Although rows contain timestamps, responses do not tell the student when Piazza was last synchronized.

Consequences:

- Stale information appears current.
- Missing announcements may not be apparent.

### 16. Search quality is not evaluated — 7/10

Hybrid retrieval weights, keyword lists, chunk sizes, and top-k values are manually chosen without a Piazza-specific evaluation set.

Consequences:

- Important instructor posts may be missed.
- Old or irrelevant threads may outrank current answers.
- Improvements cannot be measured confidently.

### 17. No attachment pipeline in TrojanClaw — 6.5/10

Piazza attachments are not downloaded, scanned, parsed, or linked into the current ingestion pipeline.

Consequences:

- Syllabi, assignment PDFs, and instructor-provided documents remain unavailable.
- Answers may omit the most authoritative material in a thread.

### 18. Generic text flattening loses thread structure — 6.5/10

Questions, instructor answers, student answers, edits, endorsements, and follow-ups are concatenated into plain text.

Consequences:

- The model may not know who said what.
- Outdated history can be confused with the latest answer.
- Instructor-endorsed content receives no explicit ranking advantage.

### 19. Failures are silently converted to empty results — 6/10

Login/fetch errors print messages and return empty arrays; retrieval errors return empty context.

Consequences:

- Outages look like “no posts found.”
- Operators and users receive no actionable status.

### 20. Incomplete dependency and deployment setup — 6/10

`psycopg2` is missing from requirements, credentials are only implicitly documented in code, and no background worker/deployment model exists.

Consequences:

- Fresh installations fail.
- Environment setup is easy to misconfigure.

## 6. Ranked improvements

Priority ratings combine risk reduction, user value, and prerequisite importance. They are ordered from highest to lowest.

### 1. Implement real USC authentication and server-derived identity — 10/10

Actions:

- Complete the Shibboleth/IAM registration process.
- Create a secure backend session after Shibboleth validation.
- Remove the `localStorage` authentication flag.
- Never accept an `authenticated` boolean from the browser.
- Add `/auth/login`, `/api/me`, and `/auth/logout` flows.

Success condition: every Piazza request is attributable to a verified USC identity.

### 2. Enforce course-level authorization — 10/10

Actions:

- Maintain a server-side `user_course_memberships` mapping.
- Obtain membership through an approved source or explicit instructor-controlled enrollment.
- Verify membership before sync, raw lookup, vector lookup, or MCP access.
- Add database row-level security or an equivalent service-layer boundary.
- Add cross-course leakage tests.

Success condition: changing a client-supplied course ID cannot expose another course.

### 3. Decide and document the permitted Piazza access model — 9.8/10

Choose one approved model before production:

- Per-user delegated Piazza connection.
- Instructor-authorized course ingestion.
- User/instructor-provided exports.
- An official/private institutional integration.

Document consent, ownership, retention, deletion, acceptable use, and support responsibility.

Success condition: the team can explain why it is permitted to collect every stored field.

### 4. Replace destructive refresh with versioned atomic publishing — 9.5/10

Actions:

- Assign each sync a version/job ID.
- Write raw posts and chunks to staging rows.
- Complete embeddings and validation first.
- Atomically mark the new version active.
- Retain the last good version for rollback.
- Clean older versions asynchronously.

Success condition: an interrupted refresh never removes the last working corpus.

### 5. Move synchronization to a background job system — 9.5/10

Actions:

- Queue course syncs outside `/chat`.
- Add persistent job state and distributed course locks.
- Refresh on a schedule or explicit authorized request.
- Return last-sync status immediately in chat.
- Retry transient failures with bounded exponential backoff.

Success condition: chat latency does not include Piazza login, crawling, or embedding.

### 6. Use per-user or per-course scoped secrets — 9.5/10

Actions:

- Eliminate one global Piazza account.
- Store delegated credentials/session material in a secrets manager using envelope encryption.
- Never expose secrets to model prompts, logs, frontend storage, or MCP tool output.
- Support disconnect, revocation, and credential expiry.
- Minimize session lifetime and privileges.

Success condition: compromising one connection cannot expose every connected course.

### 7. Treat Piazza content as untrusted data — 9/10

Actions:

- Update the system prompt to state that posts are evidence, never instructions.
- Separate content from system/tool instructions structurally.
- Strip hidden markup and suspicious embedded instructions.
- Do not allow retrieved posts to authorize tool calls.
- Add adversarial prompt-injection tests.

Success condition: a Piazza post cannot cause the agent to reveal data or invoke unrelated actions.

### 8. Preserve citations, authorship roles, and timestamps — 9/10

Actions:

- Normalize question, instructor answer, endorsed answer, student reply, and follow-up separately.
- Retain latest revision and original source URL.
- Mark instructor/TA/endorsed status where reliably present.
- Render citations linking to the exact Piazza thread.
- Display “last synchronized” time.

Success condition: students can distinguish official/instructor responses from general discussion and verify the answer.

### 9. Scope and validate JSON exports by course — 9/10

Actions:

- Use one directory or signed manifest per course/network.
- Require an embedded network/course identifier.
- Reject files whose identity does not match the requested course.
- Record uploader, import time, checksum, and consent basis.

Success condition: an export cannot silently enter another course's corpus.

### 10. Build a normalized Piazza domain model — 8.5/10

Suggested entities:

- `piazza_connections`
- `courses`
- `user_course_memberships`
- `piazza_posts`
- `piazza_post_revisions`
- `piazza_answers`
- `piazza_followups`
- `piazza_attachments`
- `piazza_sync_jobs`

Do not overload `documents.campus` with course identity. Generate RAG documents from normalized records.

Success condition: authorization, updates, deletions, and citations do not depend on flattened chunk metadata.

### 11. Add robust change detection and incremental sync — 8.5/10

Actions:

- Unique constraint on `(course_id, post_id)`.
- Hash normalized content and attachments.
- Re-embed only new or changed content.
- Track updated/deleted posts and revisions.
- Use a durable cursor or last-successful-sync timestamp where supported.

Success condition: regular sync cost scales with changes, not total course history.

### 12. Batch embeddings and database writes — 8/10

Actions:

- Batch multiple chunks per OpenAI embedding call within API limits.
- Use PostgreSQL bulk insert/upsert.
- Cache identical content hashes.
- Record token usage and ingestion cost.

Success condition: sync duration and cost remain predictable across large courses.

### 13. Add Piazza-specific retrieval evaluation — 8/10

Build a test corpus covering:

- Latest instructor announcement.
- Assignment deadline change.
- Instructor-endorsed clarification.
- Unanswered question.
- Exam logistics.
- Similar topics across multiple courses.
- Old answer superseded by a new edit.
- Adversarial/prompt-injection post.

Measure retrieval recall, ranking, course isolation, citation accuracy, freshness, and answer faithfulness.

Success condition: retrieval changes are evaluated against measurable acceptance thresholds.

### 14. Add explicit course selection and query routing — 8/10

Actions:

- Show the user's authorized courses in the UI.
- Resolve course codes mentioned in the query.
- Ask a clarification question when ambiguous.
- Never silently use the first course when multiple courses are possible.

Success condition: a question about course B cannot silently search course A.

### 15. Build an attachment ingestion pipeline — 7.5/10

Actions:

- Allowlist supported file types.
- Enforce size and download limits.
- Scan files before parsing.
- Extract PDF/text safely.
- Preserve attachment-to-post relationships and citations.
- Apply the same course authorization and retention rules.

Success condition: authoritative course documents are searchable without allowing arbitrary file execution or uncontrolled storage.

### 16. Add structured monitoring and failure states — 7.5/10

Track:

- Login success/failure.
- Upstream response time and status.
- Posts discovered, created, changed, deleted, and skipped.
- Embedding failures and cost.
- Active corpus version.
- Last successful sync and staleness.
- Retrieval misses and course-authorization denials.

Expose a safe status such as “Piazza last synced 18 minutes ago” rather than silently returning empty results.

### 17. Create a read-only internal connector API — 7.5/10

Recommended operations:

- `list_authorized_courses(user_id)`
- `get_recent_posts(user_id, course_id)`
- `get_thread(user_id, course_id, post_id)`
- `search_posts(user_id, course_id, query)`
- `get_digest(user_id, course_ids, since)`
- `request_sync(user_id, course_id)`

Every operation should accept the verified user identity and enforce authorization internally.

### 18. Add an optional read-only MCP interface — 7/10

Only after the connector API is secure, expose equivalent MCP tools:

- `piazza_list_courses`
- `piazza_get_recent_posts`
- `piazza_get_thread`
- `piazza_search_posts`
- `piazza_get_digest`
- `piazza_get_sync_status`

MCP requirements:

- OAuth or short-lived scoped tokens.
- User/course scopes on every call.
- Read-only tools initially.
- Rate limits and audit logs.
- No credentials in MCP configuration or tool results.
- Streamable HTTP only if a multi-user remote service is required; otherwise a local stdio server may reduce exposure.

Success condition: MCP clients cannot bypass the same authorization enforced for TrojanClaw.

### 19. Add data minimization and retention controls — 7/10

Actions:

- Store only fields required for the product.
- Avoid raw payload retention by default.
- Define course-end expiry.
- Support user disconnect and course deletion.
- Remove data when authorization is revoked.
- Document backup deletion behavior.

### 20. Complete dependency and developer setup — 6.5/10

Actions:

- Add the required PostgreSQL driver.
- Add a backend `.env.example` without secrets.
- Document every Piazza configuration option.
- Pin and test supported dependency versions.
- Add mocked unit and integration tests that do not require real credentials.

## 7. Recommended implementation order

### Milestone 1: Safe identity boundary

1. Implement verified USC authentication.
2. Add user and course-membership models.
3. Remove client authority over `course_id`.
4. Protect every raw/vector Piazza query.
5. Add authorization and leakage tests.

Do not expose real Piazza data before this milestone is complete.

### Milestone 2: Reliable connector

1. Decide the approved Piazza connection model.
2. Move credentials/session material into a secrets manager.
3. Normalize the Piazza data model.
4. Implement incremental, versioned, atomic synchronization.
5. Run synchronization through background workers with distributed locks.
6. Add observability and freshness status.

### Milestone 3: Trustworthy course answers

1. Preserve thread roles, timestamps, endorsements, and exact links.
2. Add citations to assistant responses.
3. Treat posts as untrusted evidence.
4. Add course-aware retrieval evaluation.
5. Add explicit course selection and ambiguity handling.
6. Add safe attachment processing.

### Milestone 4: Optional MCP distribution

1. Stabilize the read-only internal connector API.
2. Wrap it in authenticated read-only MCP tools.
3. Use the same authorization service for web and MCP clients.
4. Add scopes, rate limits, audit logs, revocation, and security testing.
5. Add write tools only if there is a proven need, institutional approval, and explicit per-action user confirmation.

## 8. Recommended first production scope

The safest useful first release would provide:

- Verified USC login.
- Explicit instructor-approved courses or user-provided exports.
- Read-only access.
- Recent posts and instructor-answer summaries.
- Keyword and semantic search.
- Exact Piazza thread citations.
- Last-synchronized timestamp.
- User disconnect and data-deletion controls.

It should not initially provide:

- Posting or editing.
- Automatic answers on a student's behalf.
- Marking posts read.
- Shared instructor credentials.
- Unverified user-entered course access.
- Indefinite storage of raw payloads.
- Third-party hosted processing of course content without review.

## 9. Final recommendation

Do not begin by installing a community Piazza MCP into TrojanClaw. Reuse the existing extraction concepts, but refactor them into a secure read-only Piazza connector with verified users, course-level authorization, normalized records, and background synchronization.

Once that connector is safe and stable, adding an MCP server is relatively small work. It can expose the same read-only operations to other clients without duplicating credentials, data, or authorization logic.

The priority order is:

1. Identity.
2. Course authorization.
3. Approved access and privacy model.
4. Reliable synchronization.
5. Trustworthy citations and retrieval.
6. Optional MCP distribution.

MCP improves interoperability. It does not solve Piazza authentication, authorization, unofficial API instability, or privacy by itself.
