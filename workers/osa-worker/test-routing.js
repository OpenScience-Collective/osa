/**
 * Tests for OSA Worker Routing
 *
 * These tests exercise the route matchers the worker's fetch() handler
 * actually uses (ROUTE_PATTERNS, exported from index.js) plus the community
 * ID guard (validateCommunityId), rather than a parallel copy of the
 * patterns that could drift from what routes real traffic.
 *
 * Run with: bun workers/osa-worker/test-routing.js
 */

import { ROUTE_PATTERNS, RESERVED_PATHS, isValidCommunityId, validateCommunityId } from './index.js';
import worker from './index.js';

let testsPassed = 0;
let testsFailed = 0;

function assert(condition, message) {
  if (!condition) {
    console.error(`  ✗ FAIL: ${message}`);
    testsFailed++;
    throw new Error(message);
  } else {
    console.log(`  ✓ PASS: ${message}`);
    testsPassed++;
  }
}

function assertEqual(actual, expected, message) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    console.error(`  ✗ FAIL: ${message}`);
    console.error(`    Expected:`, expected);
    console.error(`    Actual:`, actual);
    testsFailed++;
    throw new Error(message);
  } else {
    console.log(`  ✓ PASS: ${message}`);
    testsPassed++;
  }
}

function test(name, fn) {
  console.log(`\n${name}`);
  try {
    fn();
  } catch (error) {
    console.error(`  Test failed:`, error.message);
  }
}

async function testAsync(name, fn) {
  console.log(`\n${name}`);
  try {
    await fn();
  } catch (error) {
    console.error(`  Test failed:`, error.message);
  }
}

// ---------------------------------------------------------------------------
// Dispatch-level test helpers
//
// The suites below drive the real, default-exported fetch() handler with a
// real Request and a stub env, instead of re-implementing routing or rate
// limiting logic in the test. The doubles here are boundary stand-ins for
// the Cloudflare bindings the worker actually calls (KV, the built-in
// per-minute limiter, and the outbound fetch to the backend) -- never a
// stand-in for handleChatResume, checkRateLimit, or any other function
// under test.
// ---------------------------------------------------------------------------

/**
 * Minimal KV namespace stand-in backed by a plain Map. Records every
 * get/put call and keeps real key -> value state, so tests can assert on
 * both the final counts and which keys were touched.
 */
function createFakeKv(initial = {}) {
  const store = new Map(Object.entries(initial));
  const calls = { get: [], put: [] };
  return {
    async get(key) {
      calls.get.push(key);
      return store.has(key) ? store.get(key) : null;
    },
    async put(key, value, options) {
      calls.put.push({ key, value, options });
      store.set(key, value);
    },
    _store: store,
    _calls: calls,
  };
}

/**
 * Minimal stand-in for the built-in per-minute rate limiter binding
 * (env.RATE_LIMITER_MINUTE), whose real shape is
 * `{ limit({ key }) => Promise<{ success }> }`. Records every key it was
 * asked to check.
 */
function createFakeMinuteLimiter(succeed) {
  const calls = [];
  return {
    async limit({ key }) {
      calls.push(key);
      return { success: succeed };
    },
    _calls: calls,
  };
}

function buildEnv({ kv, minuteLimiter, turnstileSecretKey, environment = 'production' }) {
  const env = {
    ENVIRONMENT: environment,
    BACKEND_URL: 'https://backend.example.test',
    BACKEND_API_KEY: 'test-backend-key',
    RATE_LIMITER_KV: kv,
    RATE_LIMITER_MINUTE: minuteLimiter,
  };
  if (turnstileSecretKey) {
    env.TURNSTILE_SECRET_KEY = turnstileSecretKey;
  }
  return env;
}

function buildRequest(path, { method = 'POST', ip = '203.0.113.5', body } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (ip) headers['CF-Connecting-IP'] = ip;
  return new Request(`https://osa-worker.example.test${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/**
 * Runs fn with globalThis.fetch replaced by a stand-in that never makes a
 * real network call -- a boundary stand-in for the backend the real worker
 * would proxy to, not a mock of the worker's own routing or rate-limiting
 * logic. Restores the original fetch afterward even if fn throws.
 */
async function withStubBackend(fn) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  try {
    return await fn(calls);
  } finally {
    globalThis.fetch = original;
  }
}

/**
 * Builds the same per-IP, per-hour KV key the worker computes internally,
 * so tests can pre-seed or read back a counter under its real key.
 */
function hourBucketKey(prefix, ip) {
  const now = Math.floor(Date.now() / 1000);
  return `${prefix}:${ip}:${Math.floor(now / 3600)}`;
}

console.log('='.repeat(60));
console.log('OSA Worker Routing Tests');
console.log('='.repeat(60));

// Test Suite 1: /:communityId/chat/resume matches the resume route
console.log('\n' + '='.repeat(60));
console.log('Test Suite 1: /:communityId/chat/resume routing');
console.log('='.repeat(60));

test('/nemar/chat/resume matches the resume route pattern', () => {
  const match = '/nemar/chat/resume'.match(ROUTE_PATTERNS.communityChatResume);
  assert(match !== null, 'Path should match communityChatResume');
  assertEqual(match[1], 'nemar', 'Captured community id should be "nemar"');
});

test('/nemar/chat/resume does NOT match the ask|chat action route', () => {
  const match = '/nemar/chat/resume'.match(ROUTE_PATTERNS.communityAction);
  assertEqual(match, null, 'Resume path must not match the two-segment action route');
});

// Test Suite 2: existing /:communityId/ask and /:communityId/chat are unaffected
console.log('\n' + '='.repeat(60));
console.log('Test Suite 2: existing (ask|chat) route is preserved');
console.log('='.repeat(60));

test('/nemar/chat matches the action route with action "chat"', () => {
  const match = '/nemar/chat'.match(ROUTE_PATTERNS.communityAction);
  assert(match !== null, 'Path should match communityAction');
  assertEqual(match[1], 'nemar', 'Captured community id should be "nemar"');
  assertEqual(match[2], 'chat', 'Captured action should be "chat"');
});

test('/nemar/ask matches the action route with action "ask"', () => {
  const match = '/nemar/ask'.match(ROUTE_PATTERNS.communityAction);
  assert(match !== null, 'Path should match communityAction');
  assertEqual(match[1], 'nemar', 'Captured community id should be "nemar"');
  assertEqual(match[2], 'ask', 'Captured action should be "ask"');
});

test('/nemar/chat does NOT match the resume route', () => {
  const match = '/nemar/chat'.match(ROUTE_PATTERNS.communityChatResume);
  assertEqual(match, null, '/nemar/chat must not match communityChatResume');
});

test('/nemar/ask does NOT match the resume route', () => {
  const match = '/nemar/ask'.match(ROUTE_PATTERNS.communityChatResume);
  assertEqual(match, null, '/nemar/ask must not match communityChatResume');
});

// Test Suite 3: an extra trailing segment matches nothing
console.log('\n' + '='.repeat(60));
console.log('Test Suite 3: over-long paths fall through to 404');
console.log('='.repeat(60));

test('/nemar/chat/resume/extra matches neither route', () => {
  const resumeMatch = '/nemar/chat/resume/extra'.match(ROUTE_PATTERNS.communityChatResume);
  const actionMatch = '/nemar/chat/resume/extra'.match(ROUTE_PATTERNS.communityAction);
  assertEqual(resumeMatch, null, 'Extra trailing segment must not match communityChatResume');
  assertEqual(actionMatch, null, 'Extra trailing segment must not match communityAction');
});

// Test Suite 4: community ID validation applies on the resume path too
console.log('\n' + '='.repeat(60));
console.log('Test Suite 4: community ID validation on the resume path');
console.log('='.repeat(60));

test('A community id validateCommunityId accepts is accepted on the resume path', () => {
  const match = '/nemar/chat/resume'.match(ROUTE_PATTERNS.communityChatResume);
  const communityId = match[1];
  assert(isValidCommunityId(communityId), 'nemar should be a valid community id format');
  const rejection = validateCommunityId(communityId, {});
  assertEqual(rejection, null, 'A valid, non-reserved community id should not be rejected');
});

test('A community id validateCommunityId rejects on the resume path is rejected the same way', () => {
  // "bad id!" contains a space and punctuation, so it fails the community id
  // format check, but it still matches the resume route's capturing group
  // (which only excludes literal slashes).
  const path = '/bad id!/chat/resume';
  const match = path.match(ROUTE_PATTERNS.communityChatResume);
  assert(match !== null, 'The resume route pattern should still capture a malformed id');
  const communityId = match[1];
  assertEqual(communityId, 'bad id!', 'Captured community id should be the malformed segment');
  assert(!isValidCommunityId(communityId), 'Malformed community id should fail the format check');

  const rejection = validateCommunityId(communityId, {});
  assert(rejection !== null, 'validateCommunityId should reject a malformed community id');
  assertEqual(rejection.status, 400, 'A malformed community id should be rejected with 400');
});

// Test Suite 5: reserved paths are still handled before community matching
console.log('\n' + '='.repeat(60));
console.log('Test Suite 5: reserved paths stay ahead of community matching');
console.log('='.repeat(60));

test('RESERVED_PATHS still lists exactly the six reserved top-level routes', () => {
  assertEqual(
    [...RESERVED_PATHS].sort(),
    ['communities', 'feedback', 'health', 'metrics', 'sync', 'version'],
    'RESERVED_PATHS should be unchanged by the resume route addition'
  );
});

for (const reserved of RESERVED_PATHS) {
  test(`Bare reserved path /${reserved} matches neither community route`, () => {
    const bare = `/${reserved}`;
    assertEqual(
      bare.match(ROUTE_PATTERNS.communityAction),
      null,
      `/${reserved} alone must not match the ask|chat action route`
    );
    assertEqual(
      bare.match(ROUTE_PATTERNS.communityChatResume),
      null,
      `/${reserved} alone must not match the resume route`
    );
  });

  test(`Reserved word "${reserved}" used as a community id is rejected on /chat, /ask and /chat/resume`, () => {
    for (const path of [`/${reserved}/chat`, `/${reserved}/ask`, `/${reserved}/chat/resume`]) {
      const match =
        path.match(ROUTE_PATTERNS.communityChatResume) || path.match(ROUTE_PATTERNS.communityAction);
      assert(match !== null, `${path} should still match a community route pattern`);
      const communityId = match[1];
      assertEqual(communityId, reserved, `Captured community id should be "${reserved}"`);

      const rejection = validateCommunityId(communityId, {});
      assert(rejection !== null, `"${reserved}" as a community id should be rejected on ${path}`);
      assertEqual(rejection.status, 404, `A reserved word used as a community id should 404 on ${path}`);
    }
  });
}

// Test Suites 6-9: dispatch-level behavior of the real fetch() handler.
//
// These do not exist to re-check the regex work Suites 1-5 already cover.
// They drive worker.fetch() itself, so a regression in *wiring* (the wrong
// handler attached to a route) or in the *rate-limiting behavior*
// (handleChatResume calling rateLimitOrReject with the wrong options, or
// skipping the resume-chain budget) fails a test here even though every
// regex in ROUTE_PATTERNS is still correct.
async function runDispatchTests() {
  console.log('\n' + '='.repeat(60));
  console.log('Test Suite 6: dispatch -- Turnstile applies to /chat, not /chat/resume');
  console.log('='.repeat(60));

  await testAsync('POST /nemar/chat is rejected with 403 when no Turnstile token is supplied', async () => {
    await withStubBackend(async () => {
      const env = buildEnv({
        kv: createFakeKv(),
        minuteLimiter: createFakeMinuteLimiter(true),
        turnstileSecretKey: 'test-turnstile-secret',
      });
      const request = buildRequest('/nemar/chat', { ip: '203.0.113.10', body: { message: 'hi' } });
      const response = await worker.fetch(request, env, {});
      assertEqual(response.status, 403, '/chat without a Turnstile token should be rejected with 403');
      const payload = await response.json();
      assertEqual(payload.error, 'Bot verification failed', '/chat 403 body should name bot verification as the cause');
    });
  });

  await testAsync('POST /nemar/chat/resume succeeds with no Turnstile token even when TURNSTILE_SECRET_KEY is set', async () => {
    await withStubBackend(async () => {
      const env = buildEnv({
        kv: createFakeKv(),
        minuteLimiter: createFakeMinuteLimiter(true),
        turnstileSecretKey: 'test-turnstile-secret',
      });
      const request = buildRequest('/nemar/chat/resume', {
        ip: '203.0.113.11',
        body: { session_id: 's1', call_id: 'c1', result: 'ok' },
      });
      const response = await worker.fetch(request, env, {});
      assertEqual(response.status, 200, '/chat/resume with no Turnstile token should still succeed');
    });
  });

  console.log('\n' + '='.repeat(60));
  console.log('Test Suite 7: dispatch -- /chat/resume is exempt from the /chat hourly KV counter');
  console.log('='.repeat(60));

  await testAsync('POST /nemar/chat is blocked once the shared hourly KV counter is at its cap', async () => {
    await withStubBackend(async () => {
      const ip = '203.0.113.20';
      const kv = createFakeKv({ [hourBucketKey('rl:hour', ip)]: '20' }); // production RATE_LIMIT_PER_HOUR is 20
      const env = buildEnv({ kv, minuteLimiter: createFakeMinuteLimiter(true) });
      const request = buildRequest('/nemar/chat', { ip, body: { message: 'hi' } });
      const response = await worker.fetch(request, env, {});
      assertEqual(response.status, 429, '/chat at the hourly cap should be rejected with 429');
      const payload = await response.json();
      assertEqual(payload.details, 'Too many requests per hour', '/chat 429 should name the chat hourly counter');
    });
  });

  await testAsync('POST /nemar/chat/resume succeeds even when that same IP is at the /chat hourly cap', async () => {
    await withStubBackend(async () => {
      const ip = '203.0.113.20'; // same IP, same key the /chat test above hit its cap on
      const kv = createFakeKv({ [hourBucketKey('rl:hour', ip)]: '20' });
      const env = buildEnv({ kv, minuteLimiter: createFakeMinuteLimiter(true) });
      const request = buildRequest('/nemar/chat/resume', {
        ip,
        body: { session_id: 's1', call_id: 'c1', result: 'ok' },
      });
      const response = await worker.fetch(request, env, {});
      assertEqual(response.status, 200, '/chat/resume must not be blocked by the /chat hourly counter');
    });
  });

  console.log('\n' + '='.repeat(60));
  console.log('Test Suite 8: dispatch -- the per-minute limiter still guards /chat/resume');
  console.log('='.repeat(60));

  await testAsync('The per-minute limiter is consulted exactly once for a successful /chat/resume call', async () => {
    await withStubBackend(async () => {
      const ip = '203.0.113.30';
      const minuteLimiter = createFakeMinuteLimiter(true);
      const env = buildEnv({ kv: createFakeKv(), minuteLimiter });
      const request = buildRequest('/nemar/chat/resume', {
        ip,
        body: { session_id: 's1', call_id: 'c1', result: 'ok' },
      });
      const response = await worker.fetch(request, env, {});
      assertEqual(response.status, 200, 'a within-budget resume call should succeed');
      assertEqual(minuteLimiter._calls, [ip], 'the per-minute limiter should have been consulted exactly once, for this IP');
    });
  });

  await testAsync('A 429 is still returned when the per-minute limiter rejects a /chat/resume call', async () => {
    await withStubBackend(async () => {
      const ip = '203.0.113.31';
      const env = buildEnv({ kv: createFakeKv(), minuteLimiter: createFakeMinuteLimiter(false) });
      const request = buildRequest('/nemar/chat/resume', {
        ip,
        body: { session_id: 's1', call_id: 'c1', result: 'ok' },
      });
      const response = await worker.fetch(request, env, {});
      assertEqual(response.status, 429, 'a per-minute rejection must still 429 on the resume path');
      const payload = await response.json();
      assertEqual(payload.details, 'Too many requests per minute', '429 should name the per-minute limiter as the cause');
    });
  });

  console.log('\n' + '='.repeat(60));
  console.log('Test Suite 9: dispatch -- the resume-chain hourly budget bounds unbounded chains');
  console.log('='.repeat(60));

  await testAsync('A resume call one under the resume-chain budget succeeds and increments its own counter', async () => {
    await withStubBackend(async () => {
      const ip = '203.0.113.40';
      const key = hourBucketKey('rl:resume:hour', ip);
      const kv = createFakeKv({ [key]: '99' }); // production RESUME_LIMIT_PER_HOUR is 100
      const env = buildEnv({ kv, minuteLimiter: createFakeMinuteLimiter(true) });
      const request = buildRequest('/nemar/chat/resume', {
        ip,
        body: { session_id: 's1', call_id: 'c1', result: 'ok' },
      });
      const response = await worker.fetch(request, env, {});
      assertEqual(response.status, 200, 'one under the resume-chain cap should still succeed');
      assertEqual(kv._store.get(key), '100', 'the resume-chain counter should have been incremented to the cap');
    });
  });

  await testAsync('A resume call at the resume-chain budget gets 429, distinct from the /chat hourly counter', async () => {
    await withStubBackend(async () => {
      const ip = '203.0.113.41';
      const kv = createFakeKv({ [hourBucketKey('rl:resume:hour', ip)]: '100' }); // at production RESUME_LIMIT_PER_HOUR
      const env = buildEnv({ kv, minuteLimiter: createFakeMinuteLimiter(true) });
      const request = buildRequest('/nemar/chat/resume', {
        ip,
        body: { session_id: 's1', call_id: 'c1', result: 'ok' },
      });
      const response = await worker.fetch(request, env, {});
      assertEqual(response.status, 429, 'a resume call at the resume-chain cap should be rejected with 429');
      const payload = await response.json();
      assertEqual(
        payload.details,
        'Too many resume requests per hour',
        'the resume-chain 429 reason must be distinct from the /chat hourly reason'
      );
      assertEqual(
        kv._store.get(hourBucketKey('rl:hour', ip)) || '0',
        '0',
        'a rejected resume call must not touch the unrelated /chat hourly counter'
      );
    });
  });
}

runDispatchTests().then(() => {
  // Print summary
  console.log('\n' + '='.repeat(60));
  console.log('Test Summary');
  console.log('='.repeat(60));
  console.log(`Total: ${testsPassed + testsFailed} tests`);
  console.log(`✓ Passed: ${testsPassed}`);
  console.log(`✗ Failed: ${testsFailed}`);

  if (testsFailed === 0) {
    console.log('\nAll tests passed!');
    process.exit(0);
  } else {
    console.log(`\n${testsFailed} test(s) failed`);
    process.exit(1);
  }
});
