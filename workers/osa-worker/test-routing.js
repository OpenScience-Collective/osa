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
