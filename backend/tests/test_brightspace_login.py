"""The USC login wait must notice when the student reaches Brightspace."""

from app.integrations.brightspace_login import wait_for_brightspace


class StaleBrowser:
    """Like Playwright's sync API: page.url only refreshes while a browser call runs."""

    def __init__(self, ticks_until_home: int):
        self.ticks, self.until = 0, ticks_until_home
        self.url = "https://login.microsoftonline.com/login"

    def _refresh(self):
        if self.ticks >= self.until:
            self.url = "https://brightspace.usc.edu/d2l/home"

    def goto(self, url):
        pass

    def wait_for_timeout(self, ms):  # a browser call: events (and page.url) are processed
        self.ticks += 1
        self._refresh()

    def cookies(self):  # the context's cookies, also a browser call
        self._refresh()
        if self.url.startswith("https://brightspace.usc.edu"):
            return [{"name": n, "value": "x", "domain": "brightspace.usc.edu"} for n in ("d2lSessionVal", "d2lSecureSessionVal")]
        return []


def test_reaching_brightspace_home_is_noticed_even_though_page_url_refreshes_lazily():
    browser = StaleBrowser(ticks_until_home=3)
    assert wait_for_brightspace(browser, browser) is True
    assert browser.ticks == 3
