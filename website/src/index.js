/**
 * CF Speedtest Worker
 * Routes: /__down, /__up, /empty, /getIP, and inlined HTML/JS assets.
 */

import { INDEX_HTML, SPEEDTEST_CF_JS, BOOTSTRAP_CSS } from './assets.js';

const MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024; // 25 MB cap
const CHUNK_SIZE = 256 * 1024; // 256 KB buffer for streaming

// Pre-generated chunk for /__down (same deterministic pattern, no CPU per request)
const DOWN_CHUNK = (() => {
  const b = new Uint8Array(CHUNK_SIZE);
  for (let i = 0; i < b.length; i++) b[i] = (i * 31) & 0xff;
  return b;
})();

/** Basic Auth: validate password only (any username accepted). Browser shows username+password dialog; user can leave username blank. */
function checkBasicAuth(request, expectedPassword) {
  if (!expectedPassword) return true;
  const auth = request.headers.get('Authorization');
  if (!auth || !auth.startsWith('Basic ')) return false;
  try {
    const decoded = atob(auth.slice(6));
    const password = decoded.includes(':') ? decoded.split(':', 2)[1] : decoded;
    return password === expectedPassword;
  } catch (_) {
    return false;
  }
}

function unauthorized(realm) {
  return new Response('Unauthorized', { status: 401, headers: { 'WWW-Authenticate': `Basic realm="${realm}"` } });
}

const CORS_BASE_HEADERS = new Headers({
  'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
  'Pragma': 'no-cache',
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'content-encoding, authorization',
  'Access-Control-Expose-Headers': 'cf-ray, server-timing',
  'Timing-Allow-Origin': '*',
});

function corsHeaders(extend = {}) {
  const h = new Headers(CORS_BASE_HEADERS);
  for (const [k, v] of Object.entries(extend)) h.set(k, v);
  return h;
}

/** GET /__down?bytes=N - return N bytes using pre-generated chunk (no CPU per request). */
function handleDown(url) {
  const bytesParam = url.searchParams.get('bytes');
  let bytes = bytesParam ? parseInt(bytesParam, 10) : 0;
  if (!Number.isFinite(bytes) || bytes < 0) bytes = 0;
  bytes = Math.min(bytes, MAX_DOWNLOAD_BYTES);

  const start = Date.now();
  if (bytes <= CHUNK_SIZE) {
    const serverTime = Date.now() - start;
    const headers = corsHeaders({
      'Content-Type': 'application/octet-stream',
      'Content-Length': String(bytes),
      'Server-Timing': `dur=${serverTime}`,
    });
    return new Response(DOWN_CHUNK.subarray(0, bytes), { status: 200, headers });
  }

  const CHUNKS_PER_TICK = 32;
  let cancelled = false;
  const stream = new ReadableStream({
    start(controller) {
      let sent = 0;
      const push = () => {
        if (controller.signal?.aborted) cancelled = true;
        if (cancelled || sent >= bytes) {
          if (!cancelled) controller.close();
          return;
        }
        for (let i = 0; i < CHUNKS_PER_TICK && sent < bytes; i++) {
          if (controller.signal?.aborted) { cancelled = true; return; }
          const toSend = Math.min(CHUNK_SIZE, bytes - sent);
          controller.enqueue(DOWN_CHUNK.subarray(0, toSend));
          sent += toSend;
        }
        if (cancelled) return;
        if (controller.signal?.aborted) { cancelled = true; return; }
        if (sent < bytes) setTimeout(push, 0);
        else controller.close();
      };
      push();
    },
    cancel() {
      cancelled = true;
    },
  });
  const serverTime = Date.now() - start;
  const headers = corsHeaders({
    'Content-Type': 'application/octet-stream',
    'Content-Length': String(bytes),
    'Server-Timing': `dur=${serverTime}`,
  });
  return new Response(stream, { status: 200, headers });
}

/** POST /__up - accept body, discard, return 200. Drain body as fast as possible so client upload isn't throttled by slow read. */
async function handleUp(request) {
  const body = request.body;
  if (body) {
    try {
      const cl = request.headers.get('Content-Length');
      const size = cl ? parseInt(cl, 10) : NaN;
      if (Number.isFinite(size) && size <= MAX_DOWNLOAD_BYTES) {
        await request.arrayBuffer();
      } else {
        await body.pipeTo(new WritableStream(
          { write() {} },
          { highWaterMark: 16 * 1024 * 1024 }
        ));
      }
    } catch (_) {}
  }
  const headers = corsHeaders({ 'Content-Length': '0' });
  return new Response('', { status: 200, headers });
}

/** GET /empty - empty body for ping. */
function handleEmpty() {
  const headers = corsHeaders({ 'Content-Length': '0' });
  return new Response('', { status: 200, headers });
}

/** GET /getIP - JSON { ip, country, colo, org } from request.cf. */
function handleGetIP(request) {
  const cf = request.cf || {};
  const body = JSON.stringify({
    ip: request.headers.get('cf-connecting-ip') || cf.clientAddress || '',
    country: cf.country || 'Unknown',
    colo: cf.colo || 'Unknown',
    org: cf.asOrganization || '',
  });
  const headers = corsHeaders({ 'Content-Type': 'application/json' });
  return new Response(body, { status: 200, headers });
}

function notFound() {
  return new Response('Not Found', { status: 404 });
}

const SPEEDTEST_PATHS = new Set(['/', '/index.html', '/speedtest-cf.js', '/__down', '/empty', '/getIP']);

function requiresSpeedtestAuth(path, method) {
  if (path === '/__up' && method === 'POST') return true;
  return SPEEDTEST_PATHS.has(path);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/$/, '') || '/';
    const method = request.method;

    if (requiresSpeedtestAuth(path, method)) {
      if (!checkBasicAuth(request, env.SPEEDTEST_PASSWORD)) return unauthorized('Speedtest');
    }

    if (path === '/bootstrap.css') return new Response(BOOTSTRAP_CSS, { status: 200, headers: corsHeaders({ 'Content-Type': 'text/css; charset=utf-8' }) });
    if (path === '/__down') return handleDown(url);
    if (path === '/__up' && method === 'POST') return handleUp(request);
    if (path === '/empty') return handleEmpty();
    if (path === '/getIP') return handleGetIP(request);
    if (path === '/' || path === '/index.html') return new Response(INDEX_HTML, { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
    if (path === '/speedtest-cf.js') return new Response(SPEEDTEST_CF_JS, { status: 200, headers: { 'Content-Type': 'application/javascript; charset=utf-8' } });

    return notFound();
  },
};
