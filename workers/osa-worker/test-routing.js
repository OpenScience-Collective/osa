/**
 * Routing tests for the OSA Cloudflare Worker (#437).
 *
 * widget.osc.earth/osa/* is a path-mounted Cloudflare route: unlike a
 * whole-hostname Custom Domain, Cloudflare delivers the FULL path, "/osa"
 * prefix included, to the worker: it sees "/osa/hed/chat", not
 * "/hed/chat". Every route matcher in index.js is anchored and counts path
 * segments, so an unstripped prefix makes every one of them miss and the
 * request falls through to the router's own 404 default. Nothing throws;
 * it just answers 404 uniformly, which reads like a DNS or route
 * misconfiguration rather than a prefix bug.
 *
 * index.js fixes this with stripMountPrefix(), applied once at the top of
 * fetch() before any route is matched. This file drives the REAL exported
 * `fetch(request, env, ctx)` handler with a real Request and a stub env
 * (no framework, following frontend/test-endpoint-resolution.js's
 * precedent) and asserts that every "/osa"-prefixed path reaches the same
 * handler as its unprefixed form, and that the unprefixed forms, the
 * ones the existing *.workers.dev hostnames and `wrangler dev` use --
 * still work unchanged.
 *
 * Note: this branch's index.js has no "/:communityId/chat/resume" route
 * (that route exists only on the still-unmerged
 * feature/issue-429-epic-browser-execution branch). It is intentionally
 * not tested here; testing a route this file doesn't define would not be
 * exercising real code. Every route this file DOES define is covered.
 *
 * Run with: bun workers/osa-worker/test-routing.js
 */

import worker from './index.js';

let testsPassed = 0;
let testsFailed = 0;

function assert(condition, message) {
  if (!condition) {
    console.error(`  x FAIL: ${message}`);
    testsFailed++;
  } else {
    console.log(`  ok ${message}`);
    testsPassed++;
  }
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    console.error(`  x FAIL: ${message}`);
    console.error(`    Expected: ${expected}`);
    console.error(`    Actual:   ${actual}`);
    testsFailed++;
  } else {
    console.log(`  ok ${message}`);
    testsPassed++;
  }
}

// A stub env with no BACKEND_URL, no rate-limit bindings, and no Turnstile
// secret. index.js is written to fail OPEN for all three when they are
// absent (see checkRateLimit and verifyTurnstileToken), and every proxying
// route returns a 503 "Backend not configured" the moment it sees
// BACKEND_URL is unset, before ever calling the real network fetch(). That
// is what makes it possible to observe ROUTING here, which handler a
// path reaches, without a live backend or any network access: a 404
// means the router's matchers never fired at all, which is categorically
// different from the 503/400/403 a route that DID match can legitimately
// return.
function stubEnv(overrides = {}) {
  return { ENVIRONMENT: 'development', ...overrides };
}

// The host matters: stripMountPrefix only strips on a host Cloudflare
// actually mounts the worker on at /osa. MOUNTED_HOST is the default here
// because that is where the prefix behavior lives; UNMOUNTED_HOST is the
// bare workers.dev name, which stays live and must keep seeing raw paths.
const MOUNTED_HOST = 'widget.osc.earth';
const UNMOUNTED_HOST = 'osa-worker.shirazi-10f.workers.dev';

async function call(pathname, { method = 'GET', body, host = MOUNTED_HOST, env } = {}) {
  const init = { method };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { 'Content-Type': 'application/json' };
  }
  const request = new Request(`https://${host}${pathname}`, init);
  return worker.fetch(request, stubEnv(env), {});
}

async function statusOf(pathname, opts) {
  const response = await call(pathname, opts);
  return response.status;
}

async function assertReachesHandler(pathname, opts, message) {
  const status = await statusOf(pathname, opts);
  assert(status !== 404, `${message} (got ${status}, not the 404 fall-through)`);
}

async function assertNotFound(pathname, opts, message) {
  const status = await statusOf(pathname, opts);
  assertEqual(status, 404, message);
}

console.log('='.repeat(60));
console.log('OSA Worker Routing Tests');
console.log('='.repeat(60));

const COMMUNITY = 'hed';

// Every route this worker defines, unprefixed, with the request options
// needed to reach its handler.
const ROUTES = [
  ['/health', {}],
  ['/version', {}],
  ['/communities', {}],
  ['/metrics/public/overview', {}],
  ['/metrics/overview', {}],
  ['/metrics/tokens', {}],
  ['/metrics/quality', {}],
  ['/sync/status', {}],
  ['/sync/health', {}],
  [`/${COMMUNITY}/`, {}],
  [`/${COMMUNITY}/logo`, {}],
  [`/${COMMUNITY}/sessions`, {}],
  [`/${COMMUNITY}/metrics/public`, {}],
  [`/${COMMUNITY}/ask`, { method: 'POST', body: {} }],
  [`/${COMMUNITY}/chat`, { method: 'POST', body: {} }],
];

console.log('\nUnprefixed routes reach their handler (the *.workers.dev / wrangler dev baseline)');
for (const [path, opts] of ROUTES) {
  await assertReachesHandler(path, opts, `${opts.method || 'GET'} ${path} reaches its handler`);
}

console.log('\n/osa-prefixed routes (widget.osc.earth/osa/*) reach the SAME handler as their unprefixed form');
for (const [path, opts] of ROUTES) {
  const unprefixedStatus = await statusOf(path, opts);
  const prefixedStatus = await statusOf(`/osa${path}`, opts);
  assertEqual(prefixedStatus, unprefixedStatus, `/osa${path} status matches unprefixed ${path} (both ${prefixedStatus})`);
  assert(prefixedStatus !== 404, `/osa${path} does not fall through to the 404 default`);
}

console.log('\nThe bare mount root and root-level absolute routes behave the same prefixed and not');
assertEqual(await statusOf('/osa'), await statusOf('/'), '/osa (bare) matches / (root)');
await assertReachesHandler('/osa/feedback', { method: 'POST', body: {} }, 'POST /osa/feedback reaches the feedback handler');

console.log('\ncommunity-id validation still holds under the /osa prefix');
await assertNotFound('/osa/health/chat', { method: 'POST', body: {} }, '/osa/health/chat is rejected (community id "health" is reserved)');
await assertNotFound('/health/chat', { method: 'POST', body: {} }, '/health/chat is rejected unprefixed too (same reserved-path check)');
assertEqual(
  await statusOf('/osa/invalid!id/chat', { method: 'POST', body: {} }),
  400,
  '/osa/<invalid community id>/chat is rejected as a 400 (matched, then validated), not a 404 (unmatched)'
);
assertEqual(
  await statusOf('/invalid!id/chat', { method: 'POST', body: {} }),
  400,
  '/invalid!id/chat is rejected as a 400 unprefixed too'
);

console.log('\nreserved paths are still reserved, and still not community ids, under the /osa prefix');
for (const reserved of ['health', 'version', 'feedback', 'communities', 'metrics', 'sync']) {
  await assertNotFound(`/osa/${reserved}/`, {}, `/osa/${reserved}/ is rejected (reserved path, not a community)`);
  await assertNotFound(`/${reserved}/`, {}, `/${reserved}/ is rejected unprefixed too`);
}

console.log('\na path that merely starts with the letters "osa" is not misread as the mount prefix');
await assertReachesHandler(
  '/osafoo/chat',
  { method: 'POST', body: {} },
  '/osafoo/chat is read as community "osafoo" (stripMountPrefix requires "/osa/" or exactly "/osa")'
);

// The bare *.workers.dev hostname stays live (wrangler.toml sets
// workers_dev = true) and Cloudflare adds no prefix there, so a path-only
// strip would eat a real leading segment. "osa" is a valid community id
// today, isValidCommunityId accepts it and RESERVED_PATHS does not list
// it, and the product is called OSA, so it is a plausible id for someone
// to create. Without host gating every one of these is silently wrong:
// /osa/ answers the API root instead of the community, /osa/chat 404s, and
// /osa/logo is re-read as community "logo" and fails for an unrelated
// reason, which in production reads as "the backend is down".
console.log('\non an UNMOUNTED host, /osa is a real path segment and must NOT be stripped');
for (const [path, opts] of ROUTES) {
  const mountedPrefixed = await statusOf(`/osa${path}`, { ...opts, host: MOUNTED_HOST });
  const unmountedPrefixed = await statusOf(`/osa${path}`, { ...opts, host: UNMOUNTED_HOST });
  assert(
    unmountedPrefixed === 404 || unmountedPrefixed !== mountedPrefixed,
    `/osa${path} on ${UNMOUNTED_HOST} is not treated as the mount prefix (got ${unmountedPrefixed}, mounted host gives ${mountedPrefixed})`
  );
}
assertEqual(
  await statusOf('/osa/', { host: UNMOUNTED_HOST }),
  await statusOf('/hed/', { host: UNMOUNTED_HOST }),
  '/osa/ on the unmounted host is read as community "osa", matching any other community route'
);
// On the mounted host a bare /osa IS the mount root and answers the API root
// (200). On the unmounted host the same path is community "osa", so it
// proxies and returns 503 here only because this env has no BACKEND_URL. The
// point is that the two hosts must NOT agree: agreement would mean the strip
// fired where no prefix exists.
assertEqual(
  await statusOf('/osa', { host: MOUNTED_HOST }),
  await statusOf('/', { host: MOUNTED_HOST }),
  '/osa (bare) on the mounted host is the API root'
);
assert(
  (await statusOf('/osa', { host: UNMOUNTED_HOST })) !== (await statusOf('/', { host: UNMOUNTED_HOST })),
  '/osa (bare) on the unmounted host is community "osa", NOT silently rewritten to the API root'
);
assertEqual(
  await statusOf('/osa', { host: UNMOUNTED_HOST }),
  await statusOf('/hed', { host: UNMOUNTED_HOST }),
  '/osa (bare) on the unmounted host behaves like any other community route'
);
assertEqual(
  await statusOf('/health', { host: UNMOUNTED_HOST }),
  await statusOf('/health', { host: MOUNTED_HOST }),
  'unprefixed routes are identical on both hosts'
);

console.log('\nquery strings survive the strip on the route that forwards them');
for (const host of [MOUNTED_HOST, UNMOUNTED_HOST]) {
  assertEqual(
    await statusOf('/metrics/overview?range=7d', { host }),
    await statusOf('/metrics/overview', { host }),
    `a query string does not change which handler /metrics/overview reaches on ${host}`
  );
}
assertEqual(
  await statusOf('/osa/metrics/overview?range=7d', { host: MOUNTED_HOST }),
  await statusOf('/metrics/overview?range=7d', { host: MOUNTED_HOST }),
  '/osa/metrics/overview?range=7d reaches the same handler as its unprefixed form'
);

// Everything above observes a STATUS CODE, which proves which handler ran
// but not what that handler sent upstream. A route could match correctly and
// still forward "/osa/hed/chat" verbatim to the backend, and every assertion
// above would stay green. So: stand up a real HTTP server, point BACKEND_URL
// at it, and read the path it actually receives. This is an HTTP-boundary
// fixture, not a mock: the worker's real routing, real prefix stripping and
// real fetch all run; only the far side of the socket is ours.
console.log('\nthe path FORWARDED to the backend is the stripped one, not the raw one');
const receivedPaths = [];
const backend = Bun.serve({
  port: 0,
  fetch(request) {
    const url = new URL(request.url);
    receivedPaths.push(url.pathname + url.search);
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  },
});
const backendEnv = { BACKEND_URL: `http://localhost:${backend.port}`, BACKEND_API_KEY: 'test-key' };

async function forwardedPathFor(pathname, opts = {}) {
  receivedPaths.length = 0;
  await call(pathname, { ...opts, env: backendEnv });
  return receivedPaths[0];
}

try {
  assertEqual(
    await forwardedPathFor('/osa/hed/chat', { method: 'POST', body: {}, host: MOUNTED_HOST }),
    '/hed/chat',
    'POST /osa/hed/chat forwards /hed/chat upstream, with the mount prefix removed'
  );
  assertEqual(
    await forwardedPathFor('/hed/chat', { method: 'POST', body: {}, host: MOUNTED_HOST }),
    '/hed/chat',
    'POST /hed/chat forwards the same path, so prefixed and unprefixed agree upstream'
  );
  assertEqual(
    await forwardedPathFor('/osa/communities', { host: MOUNTED_HOST }),
    '/communities',
    'GET /osa/communities forwards /communities upstream'
  );
  assertEqual(
    await forwardedPathFor('/osa/metrics/overview?range=7d', { host: MOUNTED_HOST }),
    '/metrics/overview?range=7d',
    'the query string reaches the backend intact alongside the stripped path'
  );
  // Two segments, because that is what the community-action matcher takes.
  // On the unmounted host this is community "osa" asking for /chat, and the
  // whole point of the host gate is that it survives to the backend intact
  // rather than being eaten down to "/chat".
  assertEqual(
    await forwardedPathFor('/osa/chat', { method: 'POST', body: {}, host: UNMOUNTED_HOST }),
    '/osa/chat',
    'on the unmounted host /osa/chat forwards intact, "osa" treated as a community id'
  );
  // The same path on the mounted host is the prefix plus "/chat", which is a
  // single segment and matches no route, so nothing is forwarded at all. The
  // contrast is the assertion: identical bytes on the wire, two different
  // meanings, decided solely by the host.
  assertEqual(
    await forwardedPathFor('/osa/chat', { method: 'POST', body: {}, host: MOUNTED_HOST }),
    undefined,
    'the same path on the mounted host strips to /chat, matches no route, and forwards nothing'
  );
} finally {
  backend.stop(true);
}

console.log('\n' + '='.repeat(60));
console.log('Test Summary');
console.log('='.repeat(60));
console.log(`Total: ${testsPassed + testsFailed} tests`);
console.log(`Passed: ${testsPassed}`);
console.log(`Failed: ${testsFailed}`);

if (testsFailed === 0) {
  console.log('\nAll tests passed!');
  process.exit(0);
} else {
  console.log(`\n${testsFailed} test(s) failed`);
  process.exit(1);
}
