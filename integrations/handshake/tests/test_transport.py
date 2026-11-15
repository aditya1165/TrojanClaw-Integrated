import asyncio
from types import SimpleNamespace

import pytest

from trojanclaw_handshake.profiles import LoginError
from trojanclaw_handshake.transport import bind_transport, check_response


def response(status=200, text='', headers=None, url='https://usc.joinhandshake.com/explore'):
    return SimpleNamespace(status_code=status, text=text, headers=headers or {}, url=url)


@pytest.mark.parametrize('reply,code', [
    (response(403, headers={'cf-mitigated': 'challenge'}), 'verification_required'),
    (response(200, '<title>Just a moment...</title>', {'Content-Type': 'text/html'}), 'verification_required'),
    (response(401), 'session_expired'),
    (response(429), 'connector_rate_limited'),
])
def test_provider_errors_have_distinct_safe_codes(reply, code):
    with pytest.raises(LoginError) as error:
        check_response(reply)
    assert error.value.code == code


def test_cloudflare_is_not_retried_as_expired_login():
    calls = []
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            calls.append(url)
            return response(403, headers={'cf-mitigated': 'challenge'})
        async def request(self, *args, **kwargs): pytest.fail('Do not retry a challenge')
    client = SimpleNamespace(_csrf_token=None, BASE_URL='https://usc.joinhandshake.com', make_session=Session)
    bind_transport(client)
    with pytest.raises(LoginError) as error:
        asyncio.run(client._request('POST', client.BASE_URL + '/hs/graphql'))
    assert error.value.code == 'verification_required'
    assert len(calls) == 1
