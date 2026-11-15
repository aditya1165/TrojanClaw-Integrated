"""USC Web Registration (webreg.usc.edu): a read-only snapshot of the student's course bin.

WebReg has no data API and sits behind Cloudflare, so it is read in the same Chrome
window as Sign in with USC (the USC login is reused, so no extra Duo is expected). For
each term listed, the course bin page is read: every course with its sections, each
section's columns (as WebReg labels them) and whether it is scheduled and/or registered.
Nothing is clicked except term links and page loads: no registering or dropping.

The snapshot is saved in the account's own folder (`users/<id>/webreg/coursebin.json`)
and refreshed on every Sign in with USC.
"""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable
from uuid import UUID

from app.integrations.local import user_root
from app.integrations.saved_sessions import save_json

logger = logging.getLogger(__name__)

WEBREG = "https://webreg.usc.edu"
SIGN_IN = WEBREG + "/auth/login?returnUrl=%2FTerms"
FILE = "coursebin.json"

# Reads the course bin page. Column labels come from WebReg's own per-cell labels
# (`.table-headers-xsmall`), so the snapshot keeps up if WebReg adds or renames columns.
READ_COURSE_BIN = r"""
() => {
  const text = (node) => (node ? node.textContent : "").replace(/\s+/g, " ").trim();
  const shown = (node) => node && getComputedStyle(node).display !== "none";
  return [...document.querySelectorAll(".course-header")].map((header) => {
    const area = header.nextElementSibling;
    const sections = [...(area ? area.querySelectorAll(".section[id^='section_']") : [])].map((section) => {
      const id = section.id.replace("section_", "");
      const fields = {};
      section.querySelectorAll(".section_row").forEach((cell) => {
        const label = text(cell.querySelector(".table-headers-xsmall")).replace(/:$/, "");
        if (!label) return;
        const copy = cell.cloneNode(true);
        copy.querySelectorAll(".table-headers-xsmall, .actionbar, form, script, #section-action").forEach((n) => n.remove());
        const value = text(copy);
        if (value) fields[label] = value;
      });
      const state = ["schedY_regY", "schedN_regY", "schedY_regN", "schedN_regN"]
        .find((name) => shown(section.querySelector("#" + name + "_" + id)));
      return {
        id, fields,
        scheduled: state ? state.startsWith("schedY") : null,
        registered: state ? state.endsWith("regY") : null,
      };
    });
    return { code: text(header.querySelector(".crsID")), title: text(header.querySelector(".crsTitl")), sections };
  }).filter((course) => course.code);
}
"""


def _wait(check: Callable[[], bool], seconds: float, page) -> bool:
    deadline = datetime.now().timestamp() + seconds
    while datetime.now().timestamp() < deadline:
        try:
            if check():
                return True
        except Exception:
            pass  # pages change while signing in
        page.wait_for_timeout(500)
    return False


def read_course_bin(page, wait_seconds: float = 30) -> list[dict[str, Any]]:
    """Open WebReg in the signed-in window and read every listed term's course bin.

    Best effort and silent: WebReg is optional, so it never brings the sign-in window back.
    If it doesn't reach its Terms page by itself (USC asks again, or a Cloudflare check),
    it is skipped and the window closes; signing in again tries once more."""
    page.goto(SIGN_IN, wait_until="domcontentloaded")

    def on_terms() -> bool:
        return page.url.startswith(WEBREG + "/Terms") and page.locator(".terms-list .term-title").count() > 0

    if not _wait(on_terms, wait_seconds, page):
        host = (page.url.split("/")[2] if "://" in page.url else page.url) or "unknown"
        raise TimeoutError(f"WebReg did not open by itself (stopped at {host}); skipped this time.")

    terms = [title.strip() for title in page.locator(".terms-list .term-title").all_inner_texts() if title.strip()]
    results = []
    for index, term in enumerate(terms):
        try:
            if index:
                page.goto(WEBREG + "/Terms", wait_until="domcontentloaded")
            page.locator(".terms-list li").nth(index).locator("a").first.click()
            page.wait_for_load_state("domcontentloaded")
            page.goto(WEBREG + "/CourseBin", wait_until="domcontentloaded")
            page.wait_for_selector("#mycbdiv, .content-wrapper-coursebin", timeout=15000)
            active = page.locator("#activeTermTab").first.inner_text().strip()
            if term.lower() not in active.lower():
                logger.warning("USC setup: WebReg showed %r instead of %r; skipping that term", active, term)
                continue
            results.append({"term": term, "courses": page.evaluate(READ_COURSE_BIN)})
        except Exception:
            logger.exception("USC setup: could not read the WebReg course bin for %s", term)
    return results


def save_snapshot(user_id: UUID, terms: list[dict[str, Any]]) -> None:
    save_json(user_id, "webreg", FILE, {"updated_at": datetime.now(timezone.utc).isoformat(), "terms": terms})


def load_snapshot(user_id: UUID) -> dict[str, Any] | None:
    path = user_root(user_id) / "webreg" / FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summary(user_id: UUID) -> dict[str, Any]:
    """For the Sources window: when WebReg was read and how much it found."""
    snapshot = load_snapshot(user_id)
    if not snapshot:
        return {"connected": False, "updated_at": None, "terms": []}
    return {"connected": True, "updated_at": snapshot.get("updated_at"),
            "terms": [{"term": t.get("term"), "courses": len(t.get("courses") or [])} for t in snapshot.get("terms") or []]}
