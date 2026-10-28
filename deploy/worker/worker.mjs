// Paste this module into the existing d27-lookup-relay Worker and deploy.
// Add RELAY_TOKEN as a Cloudflare secret; never put it in this file.
const UPSTREAM = 'https://marstools.co/d27suppressioncheck/ping.php';
const encoder = new TextEncoder();

function error(status, message, extra = {}) {
  return Response.json({ error: message }, {
    status, headers: { 'Cache-Control': 'no-store', ...extra },
  });
}

async function readBounded(body, limit) {
  if (!body) return new Uint8Array();
  const reader = body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel();
        throw new RangeError('Body too large');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

async function tokenMatches(actual, expected) {
  // Compare fixed length digests without depending on a runtime-specific API.
  const [a, b] = await Promise.all([actual, expected].map(value =>
    crypto.subtle.digest('SHA-256', encoder.encode(value))));
  const left = new Uint8Array(a), right = new Uint8Array(b);
  let difference = 0;
  for (let i = 0; i < left.length; i++) difference |= left[i] ^ right[i];
  return difference === 0;
}

export async function relay(request, env, { fetcher = globalThis.fetch, timeoutMs = 18000 } = {}) {
  const url = new URL(request.url);
  if (url.pathname !== '/lookup' || url.search) return error(404, 'Not found.');
  if (request.method !== 'POST') return error(405, 'Use POST.', { Allow: 'POST' });
  if (typeof env.RELAY_TOKEN !== 'string' || !/^[\x21-\x7e]{32,256}$/.test(env.RELAY_TOKEN)) {
    return error(503, 'Relay is not configured.');
  }
  const authorization = request.headers.get('Authorization') || '';
  if (authorization.length > 263 || !await tokenMatches(authorization, `Bearer ${env.RELAY_TOKEN}`)) {
    return error(401, 'Unauthorized.');
  }
  if (request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
    return error(415, 'Use application/json.');
  }
  let data;
  try {
    data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await readBounded(request.body, 1024)));
  } catch (exc) {
    return error(exc instanceof RangeError ? 413 : 400, 'Invalid request body.');
  }
  if (!data || Array.isArray(data) || typeof data !== 'object'
      || Object.keys(data).length !== 1 || typeof data.ani !== 'string' || !/^[0-9]{10}$/.test(data.ani)) {
    return error(400, 'Provide ani as a 10-digit string.');
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    // Construct headers from scratch: do not forward client IP headers,
    // cookies, or the backend's relay authorization to the provider.
    const upstream = await fetcher(UPSTREAM, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ ani: data.ani }),
      redirect: 'manual',
      cache: 'no-store',
      signal: controller.signal,
    });
    const body = await readBounded(upstream.body, 1024 * 1024);
    const headers = new Headers({ 'Cache-Control': 'no-store' });
    for (const name of ['Content-Type', 'Retry-After']) {
      if (upstream.headers.has(name)) headers.set(name, upstream.headers.get(name));
    }
    // Preserve provider status and bytes. The app validates both campaigns.
    // Location is deliberately omitted; neither hop follows redirects.
    return new Response([204, 205, 304].includes(upstream.status) ? null : body, {
      status: upstream.status, headers,
    });
  } catch {
    return controller.signal.aborted
      ? error(504, 'Lookup service timed out.')
      : error(502, 'Lookup service unavailable.');
  } finally {
    clearTimeout(timer);
  }
}

export default {
  fetch(request, env) { return relay(request, env); },
};
