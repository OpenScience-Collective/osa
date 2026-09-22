/**
 * The tool_request controller and the gate's highlighter (#431 step 7c).
 *
 * The controller runs for real against the real PyodideRuntime and a real Bun
 * worker speaking the execute protocol (test-workers/executing.js), the same
 * seam the lifecycle suite uses. The gate is a function the test supplies,
 * because the gate IS the person: what it resolves with is their decision,
 * not a stand-in for any of this module's logic.
 *
 * Run with: bun frontend/test-controller.js
 */

import {
  ClientToolController,
  DENIED_STDERR,
  FULL_OUTPUT_TOOL_NAME,
  GATE_DECISION,
} from './osa-controller.js';
import { HIGHLIGHT_CLASSES, highlightPython, tokenizePython } from './osa-highlight.js';
import { CANCELLED_STDERR, CLIENT_TOOL_RESULT_FIELDS, PyodideRuntime, RUNTIME_STATE } from './osa-runtime.js';

let passed = 0;
let failed = 0;

const SUITE_TIMEOUT_MS = 60_000;
const watchdog = setTimeout(() => {
  console.error(`\n  x FAIL: the suite did not finish within ${SUITE_TIMEOUT_MS / 1000}s.`);
  console.error('    An answer() that never settles looks exactly like this, and it is the');
  console.error('    one thing the controller promises cannot happen.');
  process.exit(1);
}, SUITE_TIMEOUT_MS);
watchdog.unref?.();

function assert(cond, msg) {
  if (cond) {
    console.log(`  ok ${msg}`);
    passed++;
  } else {
    console.error(`  x FAIL: ${msg}`);
    failed++;
  }
}

function assertEqual(actual, expected, msg) {
  assert(actual === expected, `${msg}${actual === expected ? '' : ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`}`);
}

const RUNTIME = {
  pyodide_version: '0.28.3',
  preload: [],
  preload_on: 'first_run',
  fetch_allow: [],
  limits: { exec_seconds: 60 },
};
const TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];

function workerFrom(name) {
  return () => new Worker(new URL(`./test-workers/${name}.js`, import.meta.url).href);
}

/** A gate that records what it was shown and answers as told. */
function person(decision) {
  const asked = [];
  const gate = async (prompt) => {
    asked.push(prompt);
    return typeof decision === 'function' ? decision(prompt) : decision;
  };
  return { gate, asked };
}

function setup({ tools = TOOLS, decision = GATE_DECISION.RUN, worker = 'executing', runtime = RUNTIME } = {}) {
  const rt = new PyodideRuntime({ runtime, workerFactory: workerFrom(worker) });
  const who = person(decision);
  const controller = new ClientToolController({ runtime: rt, tools, gate: who.gate });
  return { rt, controller, asked: who.asked };
}

const request = (callId, code, extra = {}) => ({
  call_id: callId,
  tool: 'execute_code',
  args: { code, description: `describe ${callId}` },
  requires_permission: true,
  ...extra,
});

const onlyServerFields = (result) => Object.keys(result).every((key) => CLIENT_TOOL_RESULT_FIELDS.includes(key));

console.log('='.repeat(60));
console.log('The tool_request controller and the gate highlighter');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// Highlighter
// ---------------------------------------------------------------------------

const ALLOWED_TAG = new RegExp(`<span class="osa-py-(?:${HIGHLIGHT_CLASSES.join('|')})">|</span>`, 'g');
const unescape = (html) =>
  html.replace(/&lt;|&gt;|&quot;|&#39;|&amp;/g, (e) => ({ '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&amp;': '&' })[e]);

/** The two properties that make the output safe to put in innerHTML. */
function checkHighlight(code) {
  const html = highlightPython(code);
  const text = html.replace(ALLOWED_TAG, '');
  return {
    // Every character that reaches markup is escaped: nothing opens a tag or
    // closes an attribute except the spans we wrote.
    safe: !/[<>"']/.test(text) && !/&(?!(?:lt|gt|quot|#39|amp);)/.test(text),
    // Nothing the person must see is dropped or altered.
    lossless: unescape(text) === code,
  };
}

console.log('\nthe highlighter escapes every character and loses none');
{
  const corpus = [
    '',
    'print("hello")',
    'x = "<script>alert(1)</script>"',
    "s = 'it\\'s' # a <b>comment</b> & more",
    '"""unterminated triple\n<img src=x onerror=alert(1)>',
    "'unterminated single\nx = 1 < 2 > 0",
    'rb"\\x00" + f"{x!r:>10}" + u\'\\u00e9\'',
    '@dataclass\nclass A:\n    a: int = 0x_FF\n\n@functools.lru_cache\ndef f(): return a @ b',
    'x = 1_000.5e-3j + .5 + 5. + 0o17 + 0b1010',
    '\\\\\\"\'\'\'\n"""\'',
    'caf\u00e9 = "\u{1F600}" # \u{1F600}',
    'lone = "\uD800" \uDC00',
    '&amp; &lt; &#39; &quot;',
    '\t\r\n\f   ',
  ];
  let all = true;
  for (const code of corpus) {
    const { safe, lossless } = checkHighlight(code);
    if (!safe || !lossless) {
      all = false;
      console.error(`    for ${JSON.stringify(code)}: safe=${safe} lossless=${lossless}`);
    }
  }
  assert(all, `every hand-written case is safe and lossless (${corpus.length} cases)`);

  // Generated input from the characters that matter: quotes, backslashes,
  // comment and decorator markers, newlines and markup. A seeded generator, so
  // a failure reproduces.
  let seed = 0x2545f491;
  const random = () => {
    seed ^= seed << 13;
    seed ^= seed >>> 17;
    seed ^= seed << 5;
    return (seed >>> 0) / 0x100000000;
  };
  const alphabet = ['"', "'", '\\', '#', '@', '\n', '<', '>', '&', ';', ' ', 'r', 'b', 'f', 'def', '0x1', '.5', 'e', '_', '\u00e9', '\u{1F600}', '"""', "'''"];
  let failures = 0;
  let firstFailure = null;
  for (let n = 0; n < 3000; n++) {
    let code = '';
    const length = 1 + Math.floor(random() * 40);
    for (let k = 0; k < length; k++) code += alphabet[Math.floor(random() * alphabet.length)];
    const { safe, lossless } = checkHighlight(code);
    if (!safe || !lossless) {
      failures++;
      firstFailure = firstFailure ?? code;
    }
  }
  assert(failures === 0, `3000 generated inputs are all safe and lossless${firstFailure ? ` (first failure: ${JSON.stringify(firstFailure)})` : ''}`);

  const bad = highlightPython(null);
  assertEqual(bad, '', 'a non-string highlights as nothing rather than throwing');
}

console.log('\nthe highlighter classifies what a reader needs to see');
{
  const kinds = (code) => tokenizePython(code).filter((t) => t.kind !== null).map((t) => `${t.kind}:${t.text}`);
  assertEqual(JSON.stringify(kinds('def f(x): return len(x)')), JSON.stringify(['kw:def', 'kw:return', 'bi:len']),
    'keywords and builtins');
  assertEqual(JSON.stringify(kinds('x = "a#b" # c')), JSON.stringify(['str:"a#b"', 'com:# c']),
    'a # inside a string is not a comment, and one after it is');
  assertEqual(JSON.stringify(kinds("s = 'it\\'s'")), JSON.stringify(["str:'it\\'s'"]),
    'an escaped quote does not end the string');
  assertEqual(JSON.stringify(kinds('a = """x\ny"""\nb = 1')), JSON.stringify(['str:"""x\ny"""', 'num:1']),
    'a triple-quoted string spans lines');
  assertEqual(JSON.stringify(kinds("s = 'open\nx = 2")), JSON.stringify(["str:'open", 'num:2']),
    'an unterminated single-quoted string ends at the line, so the next line is still highlighted');
  assertEqual(JSON.stringify(kinds('rb"\\x00"')), JSON.stringify(['str:rb"\\x00"']), 'a prefixed string is one token');
  assertEqual(JSON.stringify(kinds('@dataclass\nc = a @ b')), JSON.stringify(['dec:@dataclass']),
    'a decorator at line start, and matrix multiplication elsewhere is not one');
  assertEqual(JSON.stringify(kinds('  @functools.lru_cache')), JSON.stringify(['dec:@functools.lru_cache']),
    'an indented, dotted decorator is one token');
  assertEqual(JSON.stringify(kinds('1_000.5e-3j 0xFF x1')), JSON.stringify(['num:1_000.5e-3j', 'num:0xFF']),
    'numbers, and a digit inside a name is not one');
  assertEqual(JSON.stringify(kinds('fr = 1; bf(2)')), JSON.stringify(['num:1', 'num:2']),
    'a name made of prefix letters is not a string when no quote follows');
}

console.log('\nthe highlighter runs in linear time on hostile input');
{
  // The input is as long as the model makes it. These are the shapes that
  // hang a backtracking pattern: long unterminated strings and escape runs.
  const cases = {
    'unterminated triple': '"""' + 'a\\'.repeat(200_000),
    'backslash run': "'" + '\\'.repeat(400_000),
    'quote soup': '"\'"\'"\''.repeat(100_000),
    'one long line': 'x' + ' + x'.repeat(100_000),
  };
  for (const [name, code] of Object.entries(cases)) {
    const started = performance.now();
    const { lossless } = checkHighlight(code);
    const took = performance.now() - started;
    assert(lossless && took < 3000, `${name} (${code.length} chars): ${Math.round(took)}ms`);
  }
}

// ---------------------------------------------------------------------------
// Controller
// ---------------------------------------------------------------------------

console.log('\nthe server-side name of get_full_output is the one the controller answers');
{
  const python = await Bun.file(new URL('../src/core/config/community.py', import.meta.url)).text();
  const declared = (python.match(/^FULL_OUTPUT_TOOL_NAME = "([^"]+)"/m) || [])[1];
  assertEqual(FULL_OUTPUT_TOOL_NAME, declared, 'FULL_OUTPUT_TOOL_NAME matches src/core/config/community.py');
}

console.log('\nthe controller declares only what this page can run');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  const make = (tools) => new ClientToolController({ runtime: rt, tools, gate: async () => GATE_DECISION.DENY });
  assertEqual(JSON.stringify(make(TOOLS).declared), JSON.stringify(['execute_code', FULL_OUTPUT_TOOL_NAME]),
    'a python tool, plus get_full_output beside it');
  assertEqual(JSON.stringify(make([{ name: 'run_r', runtime: 'r', requires_permission: true }]).declared), '[]',
    'a tool of a runtime this bundle lacks is not declared, and neither is get_full_output alone');
  assertEqual(JSON.stringify(make([]).declared), '[]', 'no tools, no declaration');
}

console.log('\nthe controller refuses to be built without a gate');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  let refused = null;
  try {
    new ClientToolController({ runtime: rt, tools: TOOLS });
  } catch (err) {
    refused = err;
  }
  assert(refused instanceof TypeError, 'no gate is a TypeError, since there is no safe default');
}

console.log('\nthe person is shown the code and its description before it runs');
{
  const { rt, controller, asked } = setup();
  const result = await controller.answer(request('call-run', 'print(1)'));
  assertEqual(asked.length, 1, 'the gate is asked once');
  assertEqual(asked[0].code, 'print(1)', 'with the code');
  assertEqual(asked[0].description, 'describe call-run', 'and the description');
  assertEqual(result.status, 'ok', 'Run runs it');
  assertEqual(result.call_id, 'call-run', 'and the result names the call');
  assertEqual(result.summary, 'print(1)', 'and it is this call\'s own result');
  assert(onlyServerFields(result), 'carrying only ClientToolResult fields');
  rt.terminate();
}

console.log('\nDeny posts denied, and nothing starts');
{
  const { rt, controller } = setup({ decision: GATE_DECISION.DENY });
  const result = await controller.answer(request('call-deny', 'print(1)'));
  assertEqual(result.status, 'denied', 'status denied');
  assertEqual(result.stderr, DENIED_STDERR, 'with the fixed explanation');
  assertEqual(result.call_id, 'call-deny', 'for the right call');
  assert(onlyServerFields(result), 'carrying only ClientToolResult fields');
  // Declining must not even download the runtime.
  assertEqual(rt.state, RUNTIME_STATE.IDLE, 'the runtime was never booted');
}

console.log('\nanything but an explicit Run is a refusal');
{
  for (const answer of ['yes', true, undefined, null, 'RUN']) {
    // Through a function: passed directly, undefined would take setup's
    // default and the case would test RUN instead.
    const { rt, controller, asked } = setup({ decision: () => answer });
    const result = await controller.answer(request('call-odd', 'print(1)'));
    assert(asked.length === 1 && result.status === 'denied' && rt.state === RUNTIME_STATE.IDLE,
      `${JSON.stringify(answer) ?? 'undefined'} does not run the code`);
  }
}

console.log('\na gate that fails says so, and does not run the code');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  for (const [label, gate] of [
    ['rejects', async () => { throw new Error('dialog broke'); }],
    ['throws synchronously', () => { throw new Error('dialog broke'); }],
  ]) {
    const controller = new ClientToolController({ runtime: rt, tools: TOOLS, gate });
    const result = await controller.answer(request('call-gate-fail', 'print(1)'));
    assert(result.status === 'error' && /could not be shown.*dialog broke/.test(result.stderr),
      `a gate that ${label} is an error naming the failure, not the reader's refusal`);
    assertEqual(rt.state, RUNTIME_STATE.IDLE, `and after a gate that ${label}, nothing ran`);
  }
}

console.log('\nauto-run skips the gate, and only when turned on');
{
  const { rt, controller, asked } = setup({ decision: GATE_DECISION.DENY });
  assertEqual(controller.autoRun, false, 'it starts off');
  controller.autoRun = 'true';
  assertEqual(controller.autoRun, false, 'only a real true turns it on');
  controller.autoRun = true;
  const result = await controller.answer(request('call-auto', 'print(2)'));
  assertEqual(asked.length, 0, 'the gate is not asked');
  assertEqual(result.status, 'ok', 'and the code runs');
  rt.terminate();
}

console.log('\nthe gate is skipped only when the request AND the config both say so');
{
  const open = [{ name: 'execute_code', runtime: 'python', requires_permission: false }];
  const cases = [
    ['both false', open, { requires_permission: false }, 0],
    ['request false, config true', TOOLS, { requires_permission: false }, 1],
    ['request true, config false', open, { requires_permission: true }, 1],
    ['request missing the flag', open, { requires_permission: undefined }, 1],
  ];
  for (const [label, tools, extra, expected] of cases) {
    const { rt, controller, asked } = setup({ tools });
    await controller.answer(request('call-flag', 'print(3)', extra));
    assertEqual(asked.length, expected, `${label}: asked ${expected} time(s)`);
    rt.terminate();
  }
}

console.log('\nget_full_output is answered from this tab, without asking');
{
  const { rt, controller, asked } = setup();
  controller.autoRun = true;
  await controller.answer(request('call-long', 'LONG:20000'));
  const read = await controller.answer({
    call_id: 'call-read',
    tool: FULL_OUTPUT_TOOL_NAME,
    args: { call_id: 'call-long', stream: 'stdout', offset: 0 },
    requires_permission: false,
  });
  assertEqual(asked.length, 0, 'no gate: it runs nothing');
  assertEqual(read.call_id, 'call-read', 'the answer names the reading call');
  assert(read.status === 'ok' && read.stdout.length > 64, 'and carries the kept output, beyond the clipped copy');
  rt.terminate();
}

console.log('\na request this page cannot serve still gets a result');
{
  const { rt, controller, asked } = setup();
  const unknown = await controller.answer({ call_id: 'call-x', tool: 'rm_rf', args: {}, requires_permission: false });
  assert(unknown.status === 'error' && /"rm_rf"/.test(unknown.stderr), 'an unknown tool is an error result naming it');
  const noCode = await controller.answer(request('call-empty', '   '));
  assert(noCode.status === 'error' && /no code/.test(noCode.stderr), 'a call with no code is an error result');
  const badArgs = await controller.answer({ call_id: 'call-null', tool: 'execute_code', args: null });
  assertEqual(badArgs.status, 'error', 'so is a call whose args are not an object');
  assertEqual(asked.length, 0, 'none of them reached the gate');
  assertEqual(rt.state, RUNTIME_STATE.IDLE, 'or the runtime');
  let threw = null;
  try {
    await controller.answer({ tool: 'execute_code', args: { code: 'x' } });
  } catch (err) {
    threw = err;
  }
  assert(threw instanceof TypeError, 'only a request with no call_id throws, since no result could name it');
}

console.log('\na runtime that cannot start still answers the call');
{
  const { rt, controller } = setup({ worker: 'failing' });
  controller.autoRun = true;
  const result = await controller.answer(request('call-noboot', 'print(4)'));
  assertEqual(result.status, 'error', 'an error result, not a rejection');
  assert(/could not be run/.test(result.stderr), `naming why (got ${JSON.stringify(result.stderr)})`);
  assertEqual(result.call_id, 'call-noboot', 'for the right call');
  assert(onlyServerFields(result), 'carrying only ClientToolResult fields');
}

console.log('\nStop while the person is being asked counts as declining');
{
  let release;
  const { rt, controller } = setup({ decision: () => new Promise((resolve) => { release = resolve; }) });
  const answering = controller.answer(request('call-stop-gate', 'print(5)'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  assertEqual(controller.busy, true, 'the controller is busy while asking');
  assertEqual(controller.cancel(), true, 'cancel() reports it stopped something');
  const result = await answering;
  assertEqual(result.status, 'denied', 'the call is answered as denied');
  release(GATE_DECISION.RUN);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assertEqual(rt.state, RUNTIME_STATE.IDLE, 'and a Run clicked afterwards starts nothing');
  assertEqual(controller.busy, false, 'the controller is free again');
}

console.log('\nStop while the code runs settles it as cancelled');
{
  const { rt, controller } = setup();
  controller.autoRun = true;
  const answering = controller.answer(request('call-stop-run', 'NEVER answers'));
  await new Promise((resolve) => setTimeout(resolve, 50));
  const started = Date.now();
  assertEqual(controller.cancel(), true, 'cancel() reaches the runtime');
  const result = await answering;
  assert(Date.now() - started < 1000, 'at once, not at the deadline');
  assertEqual(result.status, 'cancelled', 'status cancelled');
  assertEqual(result.stderr, CANCELLED_STDERR, 'with the fixed explanation');
  assertEqual(controller.cancel(), false, 'and there is nothing left to stop');
  const after = await controller.answer(request('call-after', 'print(6)'));
  assertEqual(after.status, 'ok', 'the next request runs on a fresh instance');
  rt.terminate();
}

console.log('\na second request while one is answered gets its own result');
{
  let release;
  const { rt, controller } = setup({ decision: () => new Promise((resolve) => { release = resolve; }) });
  const first = controller.answer(request('call-first', 'print(7)'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  const second = await controller.answer(request('call-second', 'print(8)'));
  assert(second.status === 'error' && second.call_id === 'call-second', 'the second is refused with a result of its own');
  release(GATE_DECISION.RUN);
  const one = await first;
  assert(one.status === 'ok' && one.call_id === 'call-first', 'and the first is unaffected');
  rt.terminate();
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed > 0 ? 1 : 0);
