/**
 * Tests for OSA Widget Streaming Functionality
 *
 * These tests verify the streaming response handling, SSE parsing,
 * error handling, and timeout behavior.
 *
 * Run with: node frontend/test-streaming.js
 */

// Test utilities
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let testsPassed = 0;
let testsFailed = 0;
let currentTest = '';
let testWidgetWindow = null;

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
  currentTest = name;
  console.log(`\n${name}`);
  try {
    fn();
  } catch (error) {
    console.error(`  Test failed:`, error.message);
  }
}

async function asyncTest(name, fn) {
  currentTest = name;
  console.log(`\n${name}`);
  try {
    await fn();
  } catch (error) {
    console.error(`  Test failed:`, error.message);
  }
}

// Mock parseSSE function (extracted from widget)
function parseSSE(line) {
  if (!line || !line.startsWith('data: ')) {
    return null;
  }
  try {
    const jsonStr = line.substring(6);
    return JSON.parse(jsonStr);
  } catch (error) {
    console.warn('[OSA] Failed to parse SSE line:', line, error);
    return null;
  }
}

// Tests
console.log('='.repeat(60));
console.log('OSA Widget Streaming Tests');
console.log('='.repeat(60));

// Test Suite 1: SSE Parser
console.log('\n' + '='.repeat(60));
console.log('Test Suite 1: SSE Parser');
console.log('='.repeat(60));

test('parseSSE: Valid content event', () => {
  const line = 'data: {"event": "content", "content": "Hello"}';
  const result = parseSSE(line);
  assertEqual(result.event, 'content', 'Event type should be content');
  assertEqual(result.content, 'Hello', 'Content should be Hello');
});

test('parseSSE: Valid done event', () => {
  const line = 'data: {"event": "done"}';
  const result = parseSSE(line);
  assertEqual(result.event, 'done', 'Event type should be done');
});

test('parseSSE: Valid error event', () => {
  const line = 'data: {"event": "error", "message": "Something went wrong"}';
  const result = parseSSE(line);
  assertEqual(result.event, 'error', 'Event type should be error');
  assertEqual(result.message, 'Something went wrong', 'Error message should match');
});

test('parseSSE: Valid tool_start event', () => {
  const line = 'data: {"event": "tool_start", "name": "retrieve_docs", "input": {"query": "test"}}';
  const result = parseSSE(line);
  assertEqual(result.event, 'tool_start', 'Event type should be tool_start');
  assertEqual(result.name, 'retrieve_docs', 'Tool name should match');
  assertEqual(result.input.query, 'test', 'Tool input should match');
});

test('parseSSE: Valid tool_end event', () => {
  const line = 'data: {"event": "tool_end", "name": "retrieve_docs", "output": "results"}';
  const result = parseSSE(line);
  assertEqual(result.event, 'tool_end', 'Event type should be tool_end');
  assertEqual(result.name, 'retrieve_docs', 'Tool name should match');
});

test('parseSSE: Empty line returns null', () => {
  const result = parseSSE('');
  assertEqual(result, null, 'Empty line should return null');
});

test('parseSSE: Comment line returns null', () => {
  const result = parseSSE(': this is a comment');
  assertEqual(result, null, 'Comment line should return null');
});

test('parseSSE: Malformed JSON returns null', () => {
  const line = 'data: {invalid json}';
  const result = parseSSE(line);
  assertEqual(result, null, 'Malformed JSON should return null');
});

test('parseSSE: Unicode content', () => {
  const line = 'data: {"event": "content", "content": "Hello 世界 🌍"}';
  const result = parseSSE(line);
  assertEqual(result.content, 'Hello 世界 🌍', 'Unicode content should be preserved');
});

test('parseSSE: Escaped quotes in content', () => {
  const line = 'data: {"event": "content", "content": "He said \\"hello\\""}';
  const result = parseSSE(line);
  assertEqual(result.content, 'He said "hello"', 'Escaped quotes should be handled');
});

test('parseSSE: Newlines in content', () => {
  const line = 'data: {"event": "content", "content": "Line 1\\nLine 2"}';
  const result = parseSSE(line);
  assertEqual(result.content, 'Line 1\nLine 2', 'Newlines should be preserved');
});

// Test Suite 2: Event Processing Logic
console.log('\n' + '='.repeat(60));
console.log('Test Suite 2: Event Processing Logic');
console.log('='.repeat(60));

test('Event processing: Multiple content chunks accumulate', () => {
  let accumulated = '';
  const lines = [
    'data: {"event": "content", "content": "Hello "}',
    'data: {"event": "content", "content": "world"}',
    'data: {"event": "content", "content": "!"}',
  ];

  for (const line of lines) {
    const event = parseSSE(line);
    if (event && event.event === 'content') {
      accumulated += event.content;
    }
  }

  assertEqual(accumulated, 'Hello world!', 'Content chunks should accumulate');
});

test('Event processing: done event after content', () => {
  let accumulated = '';
  let done = false;
  const lines = [
    'data: {"event": "content", "content": "Test"}',
    'data: {"event": "done"}',
  ];

  for (const line of lines) {
    const event = parseSSE(line);
    if (event && event.event === 'content') {
      accumulated += event.content;
    } else if (event && event.event === 'done') {
      done = true;
    }
  }

  assertEqual(accumulated, 'Test', 'Content should be accumulated');
  assertEqual(done, true, 'Done event should be received');
});

test('Event processing: done content replaces raw citation boundary', () => {
  let accumulated = '';
  const lines = [
    'data: {"event": "content", "content": "Infomax in ru[1]nica.m"}',
    'data: {"event": "done", "content": "Infomax in runica.m.[1]"}',
  ];

  for (const line of lines) {
    const event = parseSSE(line);
    if (event && event.event === 'content') {
      accumulated += event.content;
    } else if (event && event.event === 'done' && typeof event.content === 'string') {
      accumulated = event.content;
    }
  }

  assertEqual(
    accumulated,
    'Infomax in runica.m.[1]',
    'Canonical done content should replace raw streamed markers'
  );
});

test('Production done reducer preserves response metadata across replacement', () => {
  const source = fs.readFileSync(path.join(__dirname, 'osa-chat-widget.js'), 'utf8');
  const window = {
    __OSA_TEST__: true,
    location: { hostname: 'localhost', origin: 'http://localhost', href: 'http://localhost/' },
  };
  const context = {
    console,
    document: {
      currentScript: { hasAttribute: () => true },
      readyState: 'loading',
    },
    window,
  };
  vm.runInNewContext(source, context);
  testWidgetWindow = window;

  const applyDoneEvent = window.OSAChatWidget.__applyDoneEvent;
  assert(typeof applyDoneEvent === 'function', 'Production done reducer should be exposed in test mode');

  const originalMessage = {
    role: 'assistant',
    content: 'raw[1]stream',
    citations: [{ marker: 1, source: 'https://old.example' }],
    feedback: 'up',
    _feedbackCommitting: true,
    _responseId: 'response-1',
  };
  const messages = [originalMessage];
  const finalContent = applyDoneEvent(messages, 0, {
    event: 'done',
    request_id: 'request-1',
    content: 'Canonical answer.[1]',
    citations: [{ marker: 1, source: 'https://source.example', title: 'Source' }],
  }, originalMessage.content);

  assertEqual(finalContent, 'Canonical answer.[1]', 'Reducer should return canonical done content');
  assert(messages[0] !== originalMessage, 'Reducer should replace the completed message state');
  assertEqual(messages[0].content, 'Canonical answer.[1]', 'Reducer should replace raw streamed content');
  assertEqual(messages[0].requestId, 'request-1', 'Reducer should preserve request metadata');
  assertEqual(messages[0].citations[0].source, 'https://source.example', 'Reducer should use final citations');
  assertEqual(messages[0].feedback, 'up', 'Reducer should preserve response feedback state');
  assertEqual(messages[0]._responseId, 'response-1', 'Reducer should preserve the local response identity');
});

test('Feedback race keeps the same response after done-state replacement', () => {
  const isSameResponseMessage = testWidgetWindow.OSAChatWidget.__isSameResponseMessage;
  assert(typeof isSameResponseMessage === 'function', 'Response identity helper should be exposed in test mode');
  assert(
    isSameResponseMessage({ _responseId: 'response-1' }, { _responseId: 'response-1' }),
    'A replacement with the same local response identity should be accepted'
  );
  assert(
    !isSameResponseMessage({ _responseId: 'response-1' }, { _responseId: 'response-2' }),
    'A different local response identity should be rejected'
  );
  assert(
    isSameResponseMessage({ requestId: 'request-1' }, { requestId: 'request-1' }),
    'A matching server request identity should be accepted'
  );
});

test('Legacy citation migration repairs cached mid-word markers', () => {
  const migrateLegacyCitationMarkers = testWidgetWindow.OSAChatWidget.__migrateLegacyCitationMarkers;
  assert(typeof migrateLegacyCitationMarkers === 'function', 'Legacy citation migration should be exposed in test mode');

  const citations = [{ marker: 1, source: 'https://source.example' }];
  assertEqual(
    migrateLegacyCitationMarkers('Infomax is ru[1]nica.m, a MATLAB version.', citations),
    'Infomax is runica.m, a MATLAB version.[1]',
    'Migration should move a mid-word marker to the sentence end'
  );
  assertEqual(
    migrateLegacyCitationMarkers('[1]The cited claim.', citations),
    'The cited claim.[1]',
    'Migration should move a leading marker to the sentence end'
  );
  assertEqual(
    migrateLegacyCitationMarkers('First claim.[1] Next claim.', citations),
    'First claim.[1] Next claim.',
    'Migration should leave an already-canonical marker unchanged'
  );
  assertEqual(
    migrateLegacyCitationMarkers('The cited claim [1].', citations),
    'The cited claim.[1]',
    'Migration should remove the space before sentence punctuation'
  );
  assertEqual(
    migrateLegacyCitationMarkers('- First claim [1]\n- Second claim.', citations),
    '- First claim[1]\n- Second claim.',
    'Migration should not move a list marker across list items'
  );
  assertEqual(
    migrateLegacyCitationMarkers('`arr[1]` contains code.', citations),
    '`arr[1]` contains code.',
    'Migration should preserve a marker inside inline code'
  );
  assertEqual(
    migrateLegacyCitationMarkers('arr[1] contains an index.', citations),
    'arr contains an index.[1]',
    'An unwrapped array-index-style marker is treated as a real citation; wrap it in backticks to preserve it as code'
  );
  assertEqual(
    migrateLegacyCitationMarkers('The documentation[1] explains this in detail.', citations),
    'The documentation explains this in detail.[1]',
    'A word directly followed by a marker should still migrate to the sentence end'
  );
});

test('Event processing: error event stops processing', () => {
  let accumulated = '';
  let errorMessage = null;
  const lines = [
    'data: {"event": "content", "content": "Partial"}',
    'data: {"event": "error", "message": "API failed"}',
    'data: {"event": "content", "content": " more"}',
  ];

  for (const line of lines) {
    const event = parseSSE(line);
    if (event && event.event === 'content') {
      accumulated += event.content;
    } else if (event && event.event === 'error') {
      errorMessage = event.message;
      break; // Stop processing on error
    }
  }

  assertEqual(accumulated, 'Partial', 'Content before error should be accumulated');
  assertEqual(errorMessage, 'API failed', 'Error message should be captured');
});

// Test Suite 3: Buffer Splitting Logic
console.log('\n' + '='.repeat(60));
console.log('Test Suite 3: Buffer Splitting Logic');
console.log('='.repeat(60));

test('Buffer splitting: Single complete event', () => {
  const chunk = 'data: {"event": "content", "content": "test"}\n';
  const lines = chunk.split('\n');
  const buffer = lines.pop() || '';

  assertEqual(lines.length, 1, 'Should have one complete line');
  assertEqual(buffer, '', 'Buffer should be empty');

  const event = parseSSE(lines[0]);
  assertEqual(event.event, 'content', 'Should parse event correctly');
});

test('Buffer splitting: Incomplete event in buffer', () => {
  const chunk = 'data: {"event": "content"';
  const lines = chunk.split('\n');
  const buffer = lines.pop() || '';

  assertEqual(lines.length, 0, 'Should have no complete lines');
  assertEqual(buffer, 'data: {"event": "content"', 'Incomplete data should stay in buffer');
});

test('Buffer splitting: Multiple complete events', () => {
  const chunk = 'data: {"event": "content", "content": "1"}\ndata: {"event": "content", "content": "2"}\n';
  const lines = chunk.split('\n');
  const buffer = lines.pop() || '';

  assertEqual(lines.length, 2, 'Should have two complete lines');
  assertEqual(buffer, '', 'Buffer should be empty');

  const event1 = parseSSE(lines[0]);
  const event2 = parseSSE(lines[1]);
  assertEqual(event1.content, '1', 'First event should be parsed');
  assertEqual(event2.content, '2', 'Second event should be parsed');
});

test('Buffer splitting: Event split across chunks', () => {
  let buffer = '';
  const chunk1 = 'data: {"event": "cont';
  const chunk2 = 'ent", "content": "test"}\n';

  // Process chunk1
  buffer += chunk1;
  let lines = buffer.split('\n');
  buffer = lines.pop() || '';
  assertEqual(lines.length, 0, 'First chunk should have no complete lines');
  assertEqual(buffer, 'data: {"event": "cont', 'Buffer should contain partial data');

  // Process chunk2
  buffer += chunk2;
  lines = buffer.split('\n');
  buffer = lines.pop() || '';
  assertEqual(lines.length, 1, 'Combined chunks should have one complete line');

  const event = parseSSE(lines[0]);
  assertEqual(event.event, 'content', 'Should parse combined event');
  assertEqual(event.content, 'test', 'Content should be correct');
});

test('Buffer splitting: Empty lines between events', () => {
  const chunk = 'data: {"event": "content", "content": "1"}\n\ndata: {"event": "content", "content": "2"}\n';
  const lines = chunk.split('\n');
  const buffer = lines.pop() || '';

  const events = [];
  for (const line of lines) {
    const event = parseSSE(line);
    if (event) events.push(event);
  }

  assertEqual(events.length, 2, 'Should parse two events despite empty line');
  assertEqual(events[0].content, '1', 'First event should be correct');
  assertEqual(events[1].content, '2', 'Second event should be correct');
});

// Test Suite 4: Edge Cases
console.log('\n' + '='.repeat(60));
console.log('Test Suite 4: Edge Cases');
console.log('='.repeat(60));

test('Edge case: Very long content', () => {
  const longContent = 'x'.repeat(10000);
  const line = `data: {"event": "content", "content": "${longContent}"}`;
  const result = parseSSE(line);
  assertEqual(result.content.length, 10000, 'Long content should be preserved');
});

test('Edge case: Special characters in content', () => {
  const line = 'data: {"event": "content", "content": "< > & \\\\ / \\" \'"}';
  const result = parseSSE(line);
  assertEqual(result.content, '< > & \\ / " \'', 'Special characters should be preserved');
});

test('Edge case: Nested JSON in content', () => {
  const line = 'data: {"event": "content", "content": "{\\"key\\": \\"value\\"}"}';
  const result = parseSSE(line);
  assertEqual(result.content, '{"key": "value"}', 'Nested JSON should be preserved');
});

test('Edge case: Multiple events in single chunk', () => {
  const chunk = [
    'data: {"event": "content", "content": "a"}',
    'data: {"event": "content", "content": "b"}',
    'data: {"event": "content", "content": "c"}',
    'data: {"event": "done"}',
  ].join('\n') + '\n';

  const lines = chunk.split('\n');
  lines.pop(); // Remove buffer

  let accumulated = '';
  let done = false;

  for (const line of lines) {
    const event = parseSSE(line);
    if (event && event.event === 'content') {
      accumulated += event.content;
    } else if (event && event.event === 'done') {
      done = true;
    }
  }

  assertEqual(accumulated, 'abc', 'All content should be accumulated');
  assertEqual(done, true, 'Done event should be processed');
});

test('Edge case: Event with additional fields', () => {
  const line = 'data: {"event": "content", "content": "test", "timestamp": 123, "meta": {"key": "value"}}';
  const result = parseSSE(line);
  assertEqual(result.event, 'content', 'Event type should be parsed');
  assertEqual(result.content, 'test', 'Content should be parsed');
  assertEqual(result.timestamp, 123, 'Additional fields should be preserved');
  assertEqual(result.meta.key, 'value', 'Nested additional fields should be preserved');
});

// Test Suite 5: Timeout Simulation
console.log('\n' + '='.repeat(60));
console.log('Test Suite 5: Timeout Behavior');
console.log('='.repeat(60));

asyncTest('Timeout: Last chunk timestamp tracking', async () => {
  let lastChunkTime = Date.now();
  const TIMEOUT = 100; // 100ms for testing

  // Simulate receiving a chunk
  lastChunkTime = Date.now();

  // Wait 50ms (within timeout)
  await new Promise(resolve => setTimeout(resolve, 50));
  const elapsed1 = Date.now() - lastChunkTime;
  assert(elapsed1 < TIMEOUT, `Should not timeout after ${elapsed1}ms`);

  // Wait another 60ms (total 110ms, exceeds timeout)
  await new Promise(resolve => setTimeout(resolve, 60));
  const elapsed2 = Date.now() - lastChunkTime;
  assert(elapsed2 > TIMEOUT, `Should timeout after ${elapsed2}ms`);
});

asyncTest('Timeout: Reset on each chunk', async () => {
  let lastChunkTime = Date.now();
  const TIMEOUT = 100;

  // Wait 50ms
  await new Promise(resolve => setTimeout(resolve, 50));

  // Receive another chunk (reset timer)
  lastChunkTime = Date.now();

  // Wait another 50ms
  await new Promise(resolve => setTimeout(resolve, 50));

  const elapsed = Date.now() - lastChunkTime;
  assert(elapsed < TIMEOUT, 'Timer should be reset on chunk reception');
});

// Print summary
console.log('\n' + '='.repeat(60));
console.log('Test Summary');
console.log('='.repeat(60));
console.log(`Total: ${testsPassed + testsFailed} tests`);
console.log(`✓ Passed: ${testsPassed}`);
console.log(`✗ Failed: ${testsFailed}`);

if (testsFailed === 0) {
  console.log('\n🎉 All tests passed!');
  process.exit(0);
} else {
  console.log(`\n❌ ${testsFailed} test(s) failed`);
  process.exit(1);
}
