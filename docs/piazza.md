# Piazza integration

TrojanClaw reads Piazza through **[mcp-piazza](https://github.com/jadenpxrk/mcp-piazza)**, a community MCP server. It is read-only and runs locally over stdio.

## Why this server

- It works for USC SSO users and does not need a Piazza password.
- It returns structured output, which is easy to store or pass to Claude.
- It has 12 tools, covering catch-up, search, full threads, and attachments.
- It runs locally, so the session cookie stays on this machine.

We also compared `piazza-mcp` (smchase, 4 tools, password only) and `RohanMuppa/Piazza-MCP` (password only).

## Setup

1. Install uv with `brew install uv`.
2. Log in once using one of these methods:
   - `uvx mcp-piazza login --password`. If your account uses SSO only, set a Piazza password first with **Forgot password** on piazza.com. This is the method we use.
   - `uvx mcp-piazza login --browser chrome`. Quit Chrome first. If macOS asks for "Chrome Safe Storage", click **Allow** once, not "Always Allow", because that key unlocks every Chrome cookie.
   - `uvx mcp-piazza login --cookie`. Paste the `session_id` cookie value at the prompt. The input is hidden, so nothing appears on screen. Never pass the cookie as a command-line argument.
3. The session is saved in `~/.config/mcp-piazza/cookies.json`. Use `uvx mcp-piazza logout` to remove it.

## Running

```bash
PIAZZA_KEEP_UNREAD=true uvx mcp-piazza serve
```

Always set `PIAZZA_KEEP_UNREAD=true`. Without it, `get_post` and `get_posts` mark posts as read on Piazza.

## What can be pulled

### Courses
| Tool | Returns |
|---|---|
| `list_courses` | All courses: ID, number, name, term, role, active flag, folders |
| `get_course_info` | Description, syllabus, office hours, dates, staff (names, roles, emails) |
| `get_course_stats` | Course statistics page (may be hidden by the instructor) |

### Posts
| Tool | Returns |
|---|---|
| `list_posts` | Post summaries. Filters: `all`, `unread`, `following`, `unresolved`, `unanswered`, `mine`, `starred`, and folder |
| `search_posts` | Keyword search in one course or in all active courses |
| `get_post` | One full thread: question, student and instructor answers, follow-ups |
| `get_posts` | Up to 20 full threads in one call |
| `get_post_history` | Every version of a post, optionally as diffs |

### Catching up
| Tool | Returns |
|---|---|
| `recent_activity` | Posts with new activity since a time (`24h`, `7d`, `2w`, or an ISO date) |
| `get_unread_counts` | Count of unread posts per course |
| `list_instructor_notes` | Instructor announcements and polls |

### Files
| Tool | Returns |
|---|---|
| `get_attachment` | An image or file from a post, up to 10 MB |

Courses can be referred to by ID, by course number (`CSCI 566`), or by part of the name.

## Limits

- **Read-only.** It cannot post, reply, edit, like, or delete anything.
- **No grades or assignments.** Piazza does not have them; use Brightspace for those.
- **Unofficial.** It is built on `piazza-api`, which uses Piazza's internal endpoints and can break without notice.
- **Early project.** v0.1.0, released October 2026, with a single maintainer. Pin the version once the integration is wired in.

## Example questions it enables

- "What did I miss this week in CSCI 566?"
- "Any instructor announcements about the midterm?"
- "Has anyone already asked about HW3 question 2?"
- "When are office hours for CS 585, and who is the TA?"
- "Show me unanswered questions in my classes."

## Status

- 2026-11-14: Login verified and `list_courses` returns the owner's courses. The integration is not yet wired into the backend.
- 2026-11-14: Piazza cannot use the USC login. Piazza's school SSO page (`https://piazza.com/sso`) lists only four schools, and USC is not one of them, so Sign in with USC cannot connect Piazza. It needs its own login (password, browser session, or cookie).
