/**
 * Routing tests for the OSA Cloudflare Worker (#437).
 *
 * widget.osc.earth/osa/* is a path-mounted Cloudflare route: unlike a
 * whole-hostname Custom Domain, Cloudflare delivers the FULL path, "/osa"
 * prefix included, to the worker -- it sees "/osa/hed/chat", not
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
 * handler as its unprefixed form, and that the unprefixed forms -- the
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
// is what makes it possible to observe ROUTING here -- which handler a
// path reaches -- without a live backend or any network access: a 404
// means the router's matchers never fired at all, which is categorically
// different from the 503/400/403 a route that DID match can legitimately
// return.
function stubEnv(overrides = {}) {
  return { ENVIRONMENT: 'development', ...overrides };
}

async function call(pathname, { method = 'GET', body } = {}) {
  const init = { method };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { 'Content-Type': 'application/json' };
  }
  const request = new Request(`https://widget.osc.earth${pathname}`, init);
  return worker.fetch(request, stubEnv(), {});
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

console.log('\nreserved paths are still reserved -- and still not community ids -- under the /osa prefix');
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
