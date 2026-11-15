# Brightspace integration

The main chat's **Connect** button now opens a private managed remote browser for
the signed-in account. Session cookies are saved encrypted in PostgreSQL and are
supplied only to that account's MCP process. Use [CONNECTORS.md](../CONNECTORS.md)
and [DEPLOYMENT.md](../DEPLOYMENT.md). The manual cookie steps below document an
earlier desktop-only prototype and are not used by the hosted app.

TrojanClaw reads Brightspace through **[brightspace-mcp-server](https://github.com/RohanMuppa/brightspace-mcp-server)** (npm, MIT). It is a read-only MCP server that runs locally over stdio and points at `https://brightspace.usc.edu`.

## Why this server

- It is the most complete Brightspace MCP: 29 tools covering deadlines, assignments, grades, announcements, content, and transcripts.
- No tool submits, edits, or deletes anything in a course.
- It runs locally, so the session stays on this machine.
- It is actively maintained (about 250 releases), so pin an exact version.

## Setup

Requirements: Node 22 or later (local is v26), and uv for the Python test scripts.

### Sign-in (temporary: browser cookie)

USC uses Duo, and automatic sign-in is still being worked out by a teammate. Until then, reuse a browser login:

1. Log in to `https://brightspace.usc.edu` in your browser. Keep that tab logged in; logging out kills the cookies.
2. Open DevTools (`Cmd + Option + I`), then go to **Application**, then **Cookies**, then `https://brightspace.usc.edu`.
3. Save the two cookies `d2lSessionVal` and `d2lSecureSessionVal` to a private file. The input is hidden:

   ```bash
   read -s "S1?d2lSessionVal: " && echo && read -s "S2?d2lSecureSessionVal: " && echo && \
   printf 'd2lSessionVal=%s; d2lSecureSessionVal=%s' "$S1" "$S2" > ~/.brightspace-cookie && chmod 600 ~/.brightspace-cookie && echo saved
   ```

4. When the cookies expire, the server reports that `D2L_SESSION_COOKIE` has expired. Repeat steps 1 to 3.

Never paste cookie values into chat, commits, or command-line arguments.

### Sign-in (planned: automatic)

Run `npx -y brightspace-mcp-server@5.2.3 setup`, which stores the password in the Keychain and asks for Duo or Microsoft approval on a phone. This is blocked until Duo is available, and the session lifetime is still unknown.

## Running

```bash
D2L_SESSION_COOKIE="$(cat ~/.brightspace-cookie)" D2L_NO_UPDATE_CHECK=1 \
  npx -y brightspace-mcp-server@5.2.3
```

When `D2L_SESSION_COOKIE` is set, the server skips the browser login entirely.

## What can be pulled

✅ = verified on 2026-11-14 with real courses.

### Deadlines and schedule
| Tool | Returns |
|---|---|
| `get_upcoming_due_dates` ✅ | Due dates across all courses (assignments, quizzes, graded discussions, calendar events) |
| `get_calendar_events` ✅ | Exams, labs, review sessions, and schedule changes in a time window |

### Assignments and grades
| Tool | Returns |
|---|---|
| `get_assignments` ✅ | Assignments and quizzes with due dates, submission status, and rubric info |
| `get_my_grades` ✅ | Grade items with points, percentages, and comments |
| `get_assignment_rubric` | Full rubric and your graded outcome once released |
| `get_dropbox_feedback` | Your released feedback on an assignment (omit `entityType`/`entityId`) |
| `get_assignment_files` | Text of instructor handouts attached to assignments (including PDFs) |

### Course material
| Tool | Returns |
|---|---|
| `get_announcements` ✅ | Announcements, newest first, with full text |
| `get_announcement_files` | Text of files attached to announcements |
| `get_syllabus` ✅ | Syllabus or overview text |
| `get_course_content` ✅ | Module tree: topics, files, links |
| `get_video_transcript` | Lecture video transcripts (Kaltura, YouTube) |
| `search_course` ✅ | Keyword search over content, announcements, and discussions |
| `get_discussions` ✅ | Discussion forums, topics, and posts |

### Courses and groups
| Tool | Returns |
|---|---|
| `get_my_courses` ✅ | Enrolled courses with IDs, names, and codes |
| `get_my_groups` ✅ | Your project or lab groups and their members |
| `get_available_groups` | Groups you can still join |
| `get_roster` | Instructor and TA contacts (by default) |

## Tools we do not use

| Tool | Reason |
|---|---|
| `get_classlist_emails`, `get_roster` with `includeStudents` | Classmates' personal data |
| `download_file`, `download_course_files`, `download_dropbox_submission_file` | Write files to disk |
| `get_dropbox_folders`, `get_dropbox_submissions`, `get_dropbox_user_submissions`, `get_rubrics_for_object` | Instructor or TA tools; return nothing useful for a student |
| `sign_in`, `sign_out`, `get_server_info` | Session management stays outside the app |

## Output format

Tool results are JSON inside text blocks. The server may add extra "update notice" text blocks, which `D2L_NO_UPDATE_CHECK=1` turns off. Parse the JSON and ignore any other text.

## Limits

- **Read-only.** It cannot submit, post, or edit anything.
- **Data depends on the course.** Some instructors use Brightspace heavily and others barely use it. For example, CSCI 599 has assignments, grades, announcements, and a syllabus, while CSCI 566 has only content and a weekly schedule.
- **Cookie sessions expire.** The lifetime is unknown; expect to refresh them.
- **Runs only on the owner's laptop.** The session and MFA are tied to this machine.

## Example questions it enables

- "What's due this week across all my classes?"
- "What's my grade in CSCI 599 so far?"
- "Summarize the latest announcements."
- "What does the ASN 04 handout ask for?"
- "Who's in my project team?"

## Status

- 2026-11-14: Standalone cookie sign-in verified on CSCI 599 and CSCI 566. Main-chat MCP tool use and a separate persistent Chromium profile are now implemented; the new browser login still needs verification with the student's USC account.
- 2026-11-14: Sign in with USC (see [CONNECTORS.md](../CONNECTORS.md)) replaces copying cookies by hand: one USC login in a Chrome popup saves the Brightspace session automatically. A scripted USC login with `get_my_courses` returned 3 courses. Brightspace's USC login goes through Microsoft to USC Shibboleth, the same login Handshake uses.
