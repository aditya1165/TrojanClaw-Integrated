"""Distinguish authentication expiry from provider challenges and outages."""

from urllib.parse import urlparse

from .profiles import LoginError

_failure: LoginError | None = None


def fail(code: str, message: str):
    global _failure
    _failure = LoginError(code, message)
    raise _failure


def last_failure():
    # Hosted helpers serve exactly one request, in a separate process per owner.
    return _failure


def check_response(response):
    headers = {str(key).lower(): str(value).lower() for key, value in response.headers.items()}
    html = response.text[:16000].lower() if "html" in headers.get("content-type", "") else ""
    if (headers.get("cf-mitigated") == "challenge"
            or any(marker in html for marker in ("/cdn-cgi/challenge-platform/", "cf-chl-", "<title>just a moment"))):
        fail("verification_required", "Handshake requires verification in the remote browser. Your saved connection has been kept.")
    if response.status_code == 401:
        fail("session_expired", "Your Handshake sign-in expired. Reconnect Handshake.")
    if response.status_code == 429:
        fail("connector_rate_limited", "Handshake is limiting requests. Wait a little before trying again.")


def bind_transport(client):
    global _failure
    _failure = None

    async def csrf(session):
        if client._csrf_token:
            return client._csrf_token
        response = await session.get(client.BASE_URL + "/explore")
        check_response(response)
        match = client._CSRF_RE.search(response.text)
        if not match:
            path = urlparse(str(response.url)).path.lower()
            if response.status_code < 400 and any(part in path for part in ("/login", "/sign_in", "/auth/")):
                fail("session_expired", "Your Handshake sign-in expired. Reconnect Handshake.")
            fail("connector_unavailable", "Handshake could not verify this request. Try again later; your saved connection has been kept.")
        client._csrf_token = match.group(1)
        return client._csrf_token

    async def request(method, url, **kwargs):
        async with client.make_session() as session:
            token = await csrf(session)
            response = await session.request(method, url, headers={"X-CSRF-Token": token}, **kwargs)
            check_response(response)
            if response.status_code == 403:
                # Retry a genuine CSRF rejection once; challenges never loop.
                client._csrf_token = None
                token = await csrf(session)
                response = await session.request(method, url, headers={"X-CSRF-Token": token}, **kwargs)
                check_response(response)
            return response

    client._fetch_csrf_token = csrf
    client._request = request
