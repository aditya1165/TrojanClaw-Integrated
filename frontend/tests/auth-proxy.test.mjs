import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { after, before, test } from 'node:test';
import { createServer } from 'vite';

let upstream;
let vite;
let base;

before(async () => {
  upstream = http.createServer((request, response) => {
    if (request.url === '/auth/usc/status') {
      response.setHeader('Set-Cookie', 'trojanclaw_session=test-only; Path=/; HttpOnly; SameSite=Lax');
      response.end(JSON.stringify({ status: 'done' }));
    } else if (request.url === '/auth/google/start') {
      response.setHeader('Set-Cookie', 'google_attempt=test-only; Path=/auth/google; HttpOnly; SameSite=Lax');
      response.end('{}');
    } else if (request.url.startsWith('/auth/google/callback')) {
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({ callback: true, hasAttemptCookie: request.headers.cookie === 'google_attempt=test-only' }));
    } else if (request.url === '/integrations') {
      response.statusCode = request.headers.cookie === 'trojanclaw_session=test-only' ? 200 : 401;
      response.end('[]');
    } else {
      response.statusCode = 404;
      response.end();
    }
  });
  upstream.listen(0, '127.0.0.1');
  await once(upstream, 'listening');
  vite = await createServer({
    configFile: 'vite.config.ts', envFile: false, logLevel: 'silent',
    server: { host: '127.0.0.1', port: 0, strictPort: false,
      proxy: {
        '/api': { target: `http://127.0.0.1:${upstream.address().port}` },
        '/auth/google/callback': { target: `http://127.0.0.1:${upstream.address().port}` },
      } },
  });
  await vite.listen();
  base = `http://127.0.0.1:${vite.httpServer.address().port}`;
});

after(async () => {
  await vite?.close();
  if (upstream) {
    upstream.closeAllConnections();
    await new Promise((resolve) => upstream.close(resolve));
  }
});

test('API defaults to the frontend origin and preserves the sign-in cookie', async () => {
  const api = await vite.ssrLoadModule('/src/lib/api.ts');
  assert.equal(api.API_BASE_URL, '/api');
  const login = await fetch(`${base}/api/auth/usc/status`, { method: 'POST' });
  assert.equal(login.status, 200);
  const cookie = login.headers.get('set-cookie');
  assert.match(cookie, /HttpOnly/i);
  assert.match(cookie, /SameSite=Lax/i);
  assert.match(cookie, /Path=\//i);
  const signedIn = await fetch(`${base}/api/integrations`, { headers: { Cookie: cookie.split(';')[0] } });
  assert.equal(signedIn.status, 200);
  assert.equal((await fetch(`${base}/api/integrations`)).status, 401);
});

test('Google attempt cookie remains usable for proxy requests and direct callbacks', async () => {
  const response = await fetch(`${base}/api/auth/google/start`, { method: 'POST' });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('set-cookie'), /Path=\/(?:;|$)/i);
  assert.match(response.headers.get('set-cookie'), /HttpOnly/i);
  const callback = await fetch(`${base}/auth/google/callback?state=test-only`, {
    headers: { Cookie: 'google_attempt=test-only' },
  });
  assert.equal(callback.status, 200);
  assert.deepEqual(await callback.json(), { callback: true, hasAttemptCookie: true });
});

test('assistant approval survives same-tab Google navigation and clears on completion', async () => {
  const {readAssistantRequest, clearAssistantRequest} = await vite.ssrLoadModule('/src/lib/assistantRequest.ts');
  const values = new Map();
  const storage = {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
  assert.equal(readAssistantRequest('?mcp_request=pending-request', storage, 1000), 'pending-request');
  assert.equal(readAssistantRequest('?google_signin=ready', storage, 2000), 'pending-request');
  assert.equal(readAssistantRequest('', storage, 602000), null);
  readAssistantRequest('?mcp_request=new-request', storage, 603000);
  clearAssistantRequest(storage);
  assert.equal(readAssistantRequest('', storage, 604000), null);
});
