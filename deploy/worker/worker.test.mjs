import assert from 'node:assert/strict';
import { test } from 'node:test';
import { relay } from './worker.mjs';

const token = 'test-only-secret-' + 'a'.repeat(48);
const env = { RELAY_TOKEN: token };
function request({ body = JSON.stringify({ ani: '0123456789' }), headers = {}, path = '/lookup', method = 'POST' } = {}) {
  return new Request(`https://relay.example${path}`, {
    method, ...(method === 'POST' ? { body } : {}),
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...headers },
  });
}

test('valid request uses fixed upstream, fresh headers, one attempt and preserves response bytes', async () => {
  let calls = 0;
  const raw = '{"ani":"0123456789","results":[]}';
  const result = await relay(request({ headers: {
    'CF-Connecting-IP': '192.0.2.5', 'X-Forwarded-For': '192.0.2.5', Cookie: 'session=private',
  } }), env, { fetcher: async (url, options) => {
    calls++;
    assert.equal(url, 'https://one.marstools.co/d27supresscheck/ping.php');
    assert.deepEqual(options.headers, { 'Content-Type': 'application/json', Accept: 'application/json' });
    assert.deepEqual(JSON.parse(options.body), { ani: '0123456789' });
    assert.equal(options.redirect, 'manual');
    assert.equal(options.cache, 'no-store');
    return new Response(raw, { headers: { 'Content-Type': 'application/json', 'Set-Cookie': 'secret' } });
  } });
  assert.equal(calls, 1);
  assert.equal(result.status, 200);
  assert.equal(await result.text(), raw);
  assert.equal(result.headers.get('cache-control'), 'no-store');
  assert.equal(result.headers.get('set-cookie'), null);
});

test('unauthorized and unconfigured requests never reach upstream', async () => {
  const fetcher = () => { assert.fail('upstream called'); };
  for (const authorization of ['', 'Bearer wrong', `Bearer ${token}extra`]) {
    const result = await relay(request({ headers: { Authorization: authorization } }), env, { fetcher });
    assert.equal(result.status, 401);
    assert.equal(result.headers.get('cache-control'), 'no-store');
    assert.ok(!(await result.text()).includes(token));
  }
  assert.equal((await relay(request(), {}, { fetcher })).status, 503);
});

test('invalid inputs and arbitrary proxy targets are rejected', async () => {
  const fetcher = () => { assert.fail('upstream called'); };
  for (const body of ['{', 'null', '[]', '{}', JSON.stringify({ ani: 1234567890 }),
    JSON.stringify({ ani: '１２３４５６７８９０' }), JSON.stringify({ ani: '+12345678901' }),
    JSON.stringify({ ani: '1234567890', url: 'https://other.example' })]) {
    assert.equal((await relay(request({ body }), env, { fetcher })).status, 400);
  }
  assert.equal((await relay(request({ body: 'x'.repeat(1025) }), env, { fetcher })).status, 413);
  assert.equal((await relay(request({ headers: { 'Content-Type': 'text/plain' } }), env, { fetcher })).status, 415);
  assert.equal((await relay(request({ path: '/lookup?url=other' }), env, { fetcher })).status, 404);
  assert.equal((await relay(request({ path: '/' }), env, { fetcher })).status, 404);
  assert.equal((await relay(request({ method: 'GET' }), env, { fetcher })).status, 405);
});

test('chunked requests are bounded without relying on content-length', async () => {
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(new Uint8Array(600));
    controller.enqueue(new Uint8Array(600));
    controller.close();
  } });
  const req = new Request('https://relay.example/lookup', {
    method: 'POST', body, duplex: 'half', headers: {
      Authorization: `Bearer ${token}`, 'Content-Type': 'application/json',
    },
  });
  assert.equal((await relay(req, env, { fetcher: () => assert.fail('upstream called') })).status, 413);
});

test('rate limits, failures and redirects preserve status without forwarding redirect location', async () => {
  for (const status of [307, 401, 403, 429, 500, 503]) {
    let calls = 0;
    const result = await relay(request(), env, { fetcher: async () => {
      calls++;
      return new Response('upstream body', { status, headers: { 'Retry-After': '60', Location: 'https://other.example' } });
    } });
    assert.equal(calls, 1);
    assert.equal(result.status, status);
    assert.equal(await result.text(), 'upstream body');
    assert.equal(result.headers.get('retry-after'), '60');
    assert.equal(result.headers.get('location'), null);
  }
});

test('network errors, response size limits and real abort timeout fail closed', async () => {
  let result = await relay(request(), env, { fetcher: async () => { throw new Error('private details'); } });
  assert.equal(result.status, 502);
  assert.ok(!(await result.text()).includes('private details'));
  result = await relay(request(), env, { fetcher: async () => new Response('x'.repeat(1024 * 1024 + 1)) });
  assert.equal(result.status, 502);
  result = await relay(request(), env, { timeoutMs: 5, fetcher: (_, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  }) });
  assert.equal(result.status, 504);
});
