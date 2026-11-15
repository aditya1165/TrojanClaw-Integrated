# Local Google Calendar MCP

Pinned upstream: [nspady/google-calendar-mcp](https://github.com/nspady/google-calendar-mcp),
MIT, npm `@cocal/google-calendar-mcp@2.8.1` (upstream commit
`29a7090d17175745c2b3a9aa10d0ff464432f3ca`).

```sh
npm ci
npm test
```

Requires Node 20+; the app's project-local Node runtime also works.

`bridge.mjs` runs the upstream tool registry and handlers behind the MCP SDK's
in-memory transport. The existing backend supplies a short-lived access token
on private subprocess stdin. OAuth refresh tokens and client secrets are never
passed to this server or written to a second token store. Each invocation gets
a separate authenticated account map; no account is shared between requests.

The published npm CLI bundle does not export its registry. `build-bridge.mjs`
rebuilds it from the same package's included source map, preserving upstream
code and its MIT license. It recreates the re-export-only conflict-detection
barrel omitted from the source map. Generated files are ignored by Git. Package
and dependency versions/integrities are pinned by `package-lock.json`.

Only `list-calendars`, `list-events`, `get-event`, and `get-freebusy` are registered for reads.
`create-event` is registered only for an approval request whose stored proposal
marker matches its deterministic event ID. `update-event` is enabled only for an
approved change proposal targeting an existing event. The original Google ETag
is supplied as `If-Match` on the target PATCH to prevent overwriting later edits.
An update proposal stores its target and before-image in the existing private
JSON document; legacy create proposals remain readable. No schema migration is needed.
The model only drafts proposals; backend approval remains mandatory.

New schedules support bounded daily, weekly and monthly recurrence (up to 52
occurrences within one year), local IANA time zones and an optional popup reminder.
The review card lists the occurrence dates. Availability is checked across every
new occurrence using the native free/busy handler. Existing timed events can be
edited individually or as an entire recurring series; unchanged recurrence,
reminders and guests are preserved. All-day edits, deletions and guest changes
are not currently supported. Uncertain updates recover using the stored proposal
marker without issuing a second PATCH.

The standard Google Calendar API must be enabled in the OAuth client's project.
Google's hosted MCP service, Developer Preview registration, and agent-client
flag are not required by this local open-source server. The existing app-owned
web OAuth client and callback addresses continue to handle authentication.

Tests exercise the actual upstream schemas and handlers with a mocked Google
HTTP boundary. They never modify a real calendar.
