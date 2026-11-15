"""WebReg course bin: reading the page, the saved snapshot, the chat tool and the status route."""

import asyncio
import json
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from app.integrations.webreg import READ_COURSE_BIN, load_snapshot, save_snapshot, summary
from app.main import app
from app.services.chat import _webreg_tool
from app.services.usc_auth import SETUP

USER = UUID("00000000-0000-0000-0000-000000000002")  # the signed_in_account fixture

# Same structure as WebReg's /CourseBin (from a redacted capture); values are made up.
COURSE_BIN = """
<div id="mycbdiv" class="content-wrapper-coursebin">
 <div class="course-header"><a><span class="crsID">CSCI-546</span> <span class="crsTitl">Intelligent Embedded Systems</span></a></div>
 <div class="collapse accordion-content-area" id="courseBin_CSCI-546">
  <div class="section-table">
   <div id="section_30132" class="section"><div class="section-row"><div class="section_crsbin">
    <span class="section_row"><span class="table-headers-xsmall">Action:</span>
     <div id="section-action"><div id="schedY_regN_30132" class="actionbar" style="display:none"><a>Unschedule</a></div>
      <div id="schedN_regN_30132" class="actionbar" style="display:none"><a>Schedule</a></div>
      <div id="schedY_regY_30132" class="actionbar"><a>Drop</a></div>
      <div id="schedN_regY_30132" class="actionbar" style="display:none"><a>x</a></div></div></span>
    <span class="section_row"><span class="table-headers-xsmall">Section:</span> <b>30132</b></span>
    <span class="section_row"><span class="table-headers-xsmall">Type:</span>Lecture</span>
    <span class="section_row"><span class="table-headers-xsmall">Time:</span> <span>2:00-3:50pm<br></span></span>
    <span class="section_row"><span class="table-headers-xsmall">Days:</span> <span>Tue, Thu<br></span></span>
    <span class="section_row"><span class="table-headers-xsmall">Instructor:</span> <span>Ada Lovelace<br></span></span>
   </div></div></div>
   <div id="section_30133" class="section"><div class="section-row"><div class="section_crsbin">
    <span class="section_row"><span class="table-headers-xsmall">Action:</span>
     <div id="section-action"><div id="schedY_regN_30133" class="actionbar"><a>Unschedule</a></div>
      <div id="schedN_regN_30133" class="actionbar" style="display:none"></div>
      <div id="schedY_regY_30133" class="actionbar" style="display:none"></div>
      <div id="schedN_regY_30133" class="actionbar" style="display:none"></div></div></span>
    <span class="section_row"><span class="table-headers-xsmall">Section:</span> <b>30133</b></span>
    <span class="section_row"><span class="table-headers-xsmall">Type:</span>Lab</span>
    <span class="section_row"><span class="table-headers-xsmall">Room:</span></span>
   </div></div></div>
  </div>
 </div>
 <div class="course-header"><a><span class="crsID">WRIT-340</span> <span class="crsTitl">Advanced Writing</span></a></div>
 <div class="collapse accordion-content-area"><div class="section-table"></div></div>
</div>
"""


def test_course_bin_page_is_read_with_webreg_column_labels():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("Chrome is not available")
        page = browser.new_page()
        page.set_content(COURSE_BIN)
        courses = page.evaluate(READ_COURSE_BIN)
        browser.close()
    assert [c["code"] for c in courses] == ["CSCI-546", "WRIT-340"]
    lecture, lab = courses[0]["sections"]
    assert lecture == {"id": "30132", "scheduled": True, "registered": True, "fields": {
        "Section": "30132", "Type": "Lecture", "Time": "2:00-3:50pm", "Days": "Tue, Thu", "Instructor": "Ada Lovelace"}}
    assert lab["scheduled"] is True and lab["registered"] is False
    assert "Room" not in lab["fields"] and "Action" not in lab["fields"]  # empty cells and buttons are left out
    assert courses[1]["sections"] == []


def test_snapshot_is_saved_per_account_and_summarised():
    assert load_snapshot(USER) is None and summary(USER)["connected"] is False
    save_snapshot(USER, [{"term": "Spring 2027", "courses": [{"code": "CSCI-546", "title": "x", "sections": []}]},
                         {"term": "Fall 2026", "courses": []}])
    info = summary(USER)
    assert info["connected"] is True and info["terms"] == [{"term": "Spring 2027", "courses": 1}, {"term": "Fall 2026", "courses": 0}]


def test_chat_tool_exists_only_with_a_snapshot_and_filters_by_term():
    assert _webreg_tool(USER) is None
    save_snapshot(USER, [{"term": "Spring 2027", "courses": [{"code": "CSCI-546"}]}, {"term": "Fall 2026", "courses": []}])
    events = []
    tool = _webreg_tool(USER, events.append)
    assert tool.name == "list_webreg_courses"
    result = json.loads(asyncio.run(tool.call({"term": "spring 2027"})))
    assert [t["term"] for t in result["terms"]] == ["Spring 2027"]
    assert events == [{"type": "activity", "text": "Checking your WebReg course bin"}]


def test_webreg_is_read_during_sign_in_with_usc_after_handshake():
    assert list(SETUP) == ["handshake", "webreg"]


@pytest.mark.usefixtures("signed_in_account")
def test_status_route_shows_the_signed_in_accounts_snapshot():
    with TestClient(app) as client:
        assert client.get("/integrations/webreg").json()["connected"] is False
        save_snapshot(USER, [{"term": "Spring 2027", "courses": []}])
        assert client.get("/integrations/webreg").json()["terms"] == [{"term": "Spring 2027", "courses": 0}]


def test_webreg_that_does_not_open_by_itself_is_skipped_quickly():
    """It must never keep the sign-in window open (e.g. a Cloudflare check or USC asking again)."""
    import time

    from app.integrations.webreg import read_course_bin

    class StuckPage:
        url = "https://login.usc.edu/login/login"
        def goto(self, url, **kwargs): pass
        def locator(self, selector): raise AssertionError("never reaches the Terms page")
        def wait_for_timeout(self, ms): time.sleep(ms / 1000)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="login.usc.edu"):
        read_course_bin(StuckPage(), wait_seconds=1)
    assert time.monotonic() - started < 3
