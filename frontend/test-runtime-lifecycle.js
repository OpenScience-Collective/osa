/**
 * Lifecycle tests for the browser Python runtime (#431 step 1).
 *
 * Drives the real PyodideRuntime against REAL Bun workers speaking the real boot
 * protocol. Nothing here stubs the module's own logic; the worker is a platform
 * boundary, and the alternative (downloading Pyodide in CI) would test the CDN
 * rather than the lifecycle. Pyodide itself is covered by the browser spike,
 * because Bun does not enforce Content-Security-Policy and that is exactly the
 * property the boot deadline exists for.
 *
 * Run with: bun frontend/test-runtime-lifecycle.js
 */

import {
  BOOT_FAILURE,
  CLIENT_TOOL_RESULT_FIELDS,
  FullOutputStore,
  MAX_ARTIFACTS,
  MAX_ARTIFACT_NAME_CHARS,
  PyodideRuntime,
  RUNTIME_STATE,
  buildWorkerSource,
  toClientToolResult,
} from './osa-runtime.js';
import { resolveLimits, SERVER_LIMITS } from './osa-output.js';

let passed = 0;
let failed = 0;

// The suite gets its own deadline, for the same reason the runtime does.
// Mutation-testing this file showed that removing the boot deadline, or making
// boot() non-idempotent, makes the SUITE hang rather than fail: an awaited boot
// that never settles never reaches an assertion. In CI a hang reads as a stuck
// job rather than a broken change, which is the exact confusion the runtime's own
// deadline exists to prevent. A test that can only fail by hanging is a test that
// reports the wrong thing.
const SUITE_TIMEOUT_MS = 60_000;
const watchdog = setTimeout(() => {
  console.error(`\n  x FAIL: the suite did not finish within ${SUITE_TIMEOUT_MS / 1000}s.`);
  console.error('    Something awaited a promise that never settles. A boot that cannot');
  console.error('    time out, or a boot() that never resolves its caller, both do this.');
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
  if (actual === expected) {
    console.log(`  ok ${msg}`);
    passed++;
  } else {
    console.error(`  x FAIL: ${msg}`);
    console.error(`    expected: ${expected}`);
    console.error(`    actual:   ${actual}`);
    failed++;
  }
}

const RUNTIME = {
  pyodide_version: '0.28.3',
  lockfile: 'nemar-v1',
  preload: ['numpy'],
  allow_install: ['zarr'],
  preload_on: 'first_run',
  fetch_allow: ['https://zarr.nemar.org/'],
  index_urls: [],
};

function workerFrom(name) {
  return () => new Worker(new URL(`./test-workers/${name}.js`, import.meta.url).href);
}

console.log('='.repeat(60));
console.log('Browser runtime lifecycle');
console.log('='.repeat(60));

console.log('\nbuildWorkerSource bakes the config in');
{
  const src = buildWorkerSource(RUNTIME);
  assert(src.includes('pyodide/v0.28.3/full/'), 'the configured Pyodide version reaches the indexURL');
  assert(src.includes('"numpy"'), 'preload packages are baked into the source');
  assert(
    !src.includes('undefined'),
    'no config field renders as undefined, which would silently load the wrong build'
  );

  // The template was never EXECUTED by any test, so every nesting mistake in it
  // was invisible here and would first appear as a worker that dies on start.
  // It is now two generated sources concatenated, which makes that considerably
  // easier to get wrong.
  let parseError = null;
  try {
    // eslint-disable-next-line no-new-func
    new Function(src);
  } catch (err) {
    parseError = err;
  }
  assert(parseError === null,
    `the assembled worker source is parseable JavaScript${parseError ? ': ' + parseError.message : ''}`);
}

console.log('\nthe worker template does not consume its own escape sequences');
{
  // A template literal interprets escapes in its OWN source, so a bare '\n'
  // written inside the worker template becomes a real newline in the generated
  // file and truncates the string literal it was part of, and a backtick ends
  // the template early. Both have happened, more than once, and the symptom is
  // always a parse error pointing at generated code rather than at this file.
  //
  // The new Function() check above catches these only when the result is
  // INVALID syntax. A bare escape inside a longer string can produce valid
  // JavaScript that is quietly wrong, which is why the source is scanned too.
  const source = await Bun.file(new URL('./osa-runtime.js', import.meta.url)).text();
  const opens = '  return `\n    (function () {';
  const start = source.indexOf(opens);
  assert(start !== -1, 'the worker template is where this test expects it');
  const template = source.slice(start + opens.length, source.indexOf('`;\n}', start));

  const bareEscapes = template.match(/(?<!\\)\\[nrt]/g) || [];
  assert(bareEscapes.length === 0,
    `no bare escape sequence in the worker template, write it doubled (found: ${JSON.stringify(bareEscapes)})`);

  // Escaped ones are fine and are used in comments; an UNESCAPED one ends the
  // template, and the code after it is then parsed as JavaScript rather than
  // emitted as worker source.
  const rawBackticks = (template.match(/(?<!\\)`/g) || []).length;
  assert(rawBackticks === 0,
    `no unescaped backtick inside the worker template, escape it as \\\` (found ${rawBackticks})`);
}

console.log('\nthe worker carries the egress guard, installed before anything is fetched');
{
  const src = buildWorkerSource(RUNTIME);

  assert(src.includes('__install'), 'the egress guard is actually in the worker source, not merely exported');
  assert(src.indexOf('__install') < src.indexOf('pyodide.js'),
    'the guard is installed BEFORE the loader is fetched, since importScripts is one of the transports it shims');
  // WHEN the seal happens, and with what, is asserted by running the real core
  // against the real interpreter (test-worker-core.js), which records the call.
  // What is checkable here is that the runtime is handed the sealing function
  // at all, since a worker built without it would boot and never narrow.
  assert(/seal:\s*__seal/.test(src), 'the runtime is handed the guard\'s own sealing function');

  // The distinction the two allowlists exist for: during boot the runtime may
  // reach the CDN it is assembled from, and executed code may reach the data
  // plane. Neither set may quietly become the other. The guard is everything
  // before the runtime is constructed, so the data plane must not appear there.
  const guardPart = src.slice(0, src.indexOf('const runtime ='));
  assert(guardPart.length > 0 && guardPart.includes('__install'), 'the guard is located where this test expects it');
  assert(!guardPart.includes('zarr.nemar.org'),
    'fetch_allow is NOT in the boot allowlist: the data plane appears nowhere in the guard');
  assert(guardPart.includes('cdn.jsdelivr.net'), 'the boot allowlist does carry the CDN the loader comes from');
}

console.log('\na successful boot reaches READY and reports its version');
{
  const states = [];
  const progress = [];
  const rt = new PyodideRuntime({
    runtime: RUNTIME,
    workerFactory: workerFrom('happy'),
    onStateChange: (s) => states.push(s),
    onProgress: (p) => progress.push(p.phase),
  });
  assertEqual(rt.state, RUNTIME_STATE.IDLE, 'starts IDLE');
  const result = await rt.boot();
  assertEqual(rt.state, RUNTIME_STATE.READY, 'ends READY');
  assertEqual(result.version, '0.28.3', 'resolves with the runtime version');
  assert(rt.isReady, 'isReady is true');
  assertEqual(states.join(','), 'booting,ready', 'transitions IDLE -> BOOTING -> READY');
  assert(progress.includes('loading_runtime'), 'reports runtime-loading progress');
  assert(progress.includes('loading_package'), 'reports per-package progress');
  rt.terminate();
}

console.log('\nboot is idempotent, because widget_open and a first tool call both reach it');
{
  let constructed = 0;
  const rt = new PyodideRuntime({
    runtime: RUNTIME,
    workerFactory: () => {
      constructed++;
      return new Worker(new URL('./test-workers/happy.js', import.meta.url).href);
    },
  });
  const [a, b] = await Promise.all([rt.boot(), rt.boot()]);
  assertEqual(constructed, 1, 'two concurrent boots construct ONE worker, not an orphaned second');
  assertEqual(a.version, b.version, 'both callers get the same result');
  const c = await rt.boot();
  assertEqual(c.version, '0.28.3', 'a boot after READY resolves immediately');
  assertEqual(constructed, 1, 'and still constructs no further worker');
  rt.terminate();
}

console.log('\nTHE CENTRAL CASE: a worker that never answers fails on the deadline');
{
  const states = [];
  const rt = new PyodideRuntime({
    runtime: RUNTIME,
    workerFactory: workerFrom('silent'),
    bootTimeoutMs: 300,
    onStateChange: (s) => states.push(s),
  });
  let error = null;
  try {
    await rt.boot();
  } catch (err) {
    error = err;
  }
  assert(error !== null, 'boot rejects rather than hanging forever');
  assertEqual(error.kind, BOOT_FAILURE.TIMEOUT, 'the failure is classified as a timeout');
  assertEqual(rt.state, RUNTIME_STATE.FAILED, 'state is FAILED, not stuck in BOOTING');
  assert(
    /wasm-unsafe-eval/.test(error.message) && /worker-src/.test(error.message),
    'the message names the two CSP directives, because that is the likely cause and it is invisible otherwise'
  );
  assertEqual(states.join(','), 'booting,failed', 'transitions BOOTING -> FAILED');
  assert(rt.failure !== null && rt.failure.kind === BOOT_FAILURE.TIMEOUT, 'the failure is readable from the instance');
}

console.log('\na worker that reports an error fails with that error, distinctly from a timeout');
{
  const rt = new PyodideRuntime({
    runtime: RUNTIME,
    workerFactory: workerFrom('failing'),
    bootTimeoutMs: 5000,
  });
  let error = null;
  try {
    await rt.boot();
  } catch (err) {
    error = err;
  }
  assert(error !== null, 'boot rejects');
  assertEqual(error.kind, BOOT_FAILURE.RUNTIME_ERROR, 'classified as a runtime error, NOT a timeout');
  assert(/WebAssembly/.test(error.message), "the worker's own message is preserved");
  assertEqual(rt.state, RUNTIME_STATE.FAILED, 'state is FAILED');
}

console.log('\na worker that cannot be constructed fails immediately');
{
  const rt = new PyodideRuntime({
    runtime: RUNTIME,
    workerFactory: () => {
      throw new Error('Refused to create a worker from blob:');
    },
  });
  let error = null;
  try {
    await rt.boot();
  } catch (err) {
    error = err;
  }
  assert(error !== null, 'boot rejects');
  assertEqual(error.kind, BOOT_FAILURE.WORKER_ERROR, 'classified as a worker error');
  assertEqual(rt.state, RUNTIME_STATE.FAILED, 'state is FAILED');
}

console.log('\nterminate is cancellation, and does not silently self-heal');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('happy') });
  await rt.boot();
  rt.terminate();
  assertEqual(rt.state, RUNTIME_STATE.TERMINATED, 'state is TERMINATED');
  assert(!rt.isReady, 'isReady is false');

  let rejected = false;
  try {
    await rt.boot();
  } catch {
    rejected = true;
  }
  assert(rejected, 'boot() after terminate() rejects rather than quietly restarting');

  const again = await rt.reboot();
  assertEqual(again.version, '0.28.3', 'reboot() explicitly starts a new runtime');
  assertEqual(rt.state, RUNTIME_STATE.READY, 'and reaches READY');
  rt.terminate();
}

console.log('\nterminating mid-boot rejects the in-flight boot rather than leaving it pending');
{
  const rt = new PyodideRuntime({
    runtime: RUNTIME,
    workerFactory: workerFrom('silent'),
    bootTimeoutMs: 10_000,
  });
  const booting = rt.boot();
  let rejected = false;
  rt.terminate();
  try {
    await booting;
  } catch {
    rejected = true;
  }
  assert(rejected, 'the pending boot promise rejects');
  assertEqual(rt.state, RUNTIME_STATE.TERMINATED, 'state is TERMINATED');
}

console.log('\npreload_on is read from config, not guessed');
{
  const eager = new PyodideRuntime({
    runtime: { ...RUNTIME, preload_on: 'widget_open' },
    workerFactory: workerFrom('happy'),
  });
  assert(eager.preloadsOnOpen, 'widget_open means boot eagerly');
  const lazy = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('happy') });
  assert(!lazy.preloadsOnOpen, 'first_run means boot lazily');
}

console.log('\na runtime config without a version is refused at construction');
{
  let threw = false;
  try {
    new PyodideRuntime({ runtime: { lockfile: 'x' } });
  } catch {
    threw = true;
  }
  assert(threw, 'constructing without pyodide_version throws rather than booting the wrong build');
}

console.log('\na failed boot TERMINATES its worker rather than leaving it downloading');
{
  // A real worker, with its real terminate() counted rather than replaced: a
  // worker that failed to boot may still be mid-download, and leaving it alive
  // holds a Pyodide heap and an open connection for a runtime nothing will use.
  // Deleting the _disposeWorker() call in _failBoot changed nothing observable
  // before this existed.
  const counting = (name) => {
    const calls = { terminated: 0 };
    const factory = () => {
      const w = new Worker(new URL(`./test-workers/${name}.js`, import.meta.url).href);
      const native = w.terminate.bind(w);
      w.terminate = () => {
        calls.terminated++;
        return native();
      };
      return w;
    };
    return { calls, factory };
  };

  const onTimeout = counting('silent');
  const rt1 = new PyodideRuntime({ runtime: RUNTIME, workerFactory: onTimeout.factory, bootTimeoutMs: 300 });
  try { await rt1.boot(); } catch { /* expected */ }
  assertEqual(onTimeout.calls.terminated, 1, 'a boot that times out terminates its worker exactly once');

  const onError = counting('failing');
  const rt2 = new PyodideRuntime({ runtime: RUNTIME, workerFactory: onError.factory, bootTimeoutMs: 5000 });
  try { await rt2.boot(); } catch { /* expected */ }
  assertEqual(onError.calls.terminated, 1, 'a boot that fails with a runtime error terminates its worker too');
}

console.log('\nthe deadline is the CONFIGURED one, not a constant that happens to fire');
{
  // Every timeout assertion above would pass equally if bootTimeoutMs were
  // ignored and some fixed delay were used instead, because they only assert
  // THAT it fired. These assert the value reaches the timer.
  const timeToFail = async (ms) => {
    const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('silent'), bootTimeoutMs: ms });
    const started = Date.now();
    try { await rt.boot(); } catch { /* expected */ }
    return Date.now() - started;
  };

  const shortWait = await timeToFail(200);
  const longWait = await timeToFail(1200);
  assert(shortWait < 700, `a 200ms deadline fires promptly rather than on a fixed delay (took ${shortWait}ms)`);
  assert(longWait - shortWait > 500,
    `a longer deadline waits measurably longer (200ms -> ${shortWait}ms, 1200ms -> ${longWait}ms)`);
}

console.log('\nexecutions are correlated by call_id, not by arrival order');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });

  // The slow one is sent FIRST and answers LAST. A host that paired results
  // with calls positionally, or kept a single outstanding call, gives the first
  // caller the second caller's result here, which no same-order test can see.
  const slow = rt.execute('DELAY:250 slow', { callId: 'call-slow' });
  const fast = rt.execute('fast', { callId: 'call-fast' });

  // Bounded, so a host that mismatches results fails by NAME here rather than
  // leaving a promise unsettled and reaching the suite watchdog, which reports
  // only that something somewhere hung.
  const withDeadline = (promise, label) =>
    Promise.race([
      promise,
      new Promise((_, rej) => setTimeout(() => rej(new Error(`${label} never resolved`)), 5_000)),
    ]);

  let slowResult = null;
  let fastResult = null;
  let correlationError = null;
  try {
    [slowResult, fastResult] = await Promise.all([
      withDeadline(slow, 'the slow call'),
      withDeadline(fast, 'the fast call'),
    ]);
  } catch (err) {
    correlationError = err;
  }
  assert(correlationError === null,
    `both executions resolve${correlationError ? ': ' + correlationError.message : ''}`);
  if (correlationError) {
    rt.terminate();
  } else {
  assertEqual(slowResult.summary, 'DELAY:250 slow', 'the slow call got its OWN result back');
  assertEqual(fastResult.summary, 'fast', 'the fast call got its OWN result back');
  assertEqual(slowResult.call_id, 'call-slow', 'the result carries the call_id it was requested under');
  assertEqual(fastResult.call_id, 'call-fast', 'and so does the other one');
  rt.terminate();
  }
}

console.log('\nexecute boots on demand, because preload_on: first_run means it has to');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  assertEqual(rt.state, RUNTIME_STATE.IDLE, 'nothing has booted yet');
  const result = await rt.execute('print(1)');
  assertEqual(rt.state, RUNTIME_STATE.READY, 'the runtime booted itself to serve the execution');
  assertEqual(result.status, 'ok', 'and the execution ran');
  rt.terminate();
}

console.log('\nan execution in flight when the runtime is torn down REJECTS rather than hanging');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  await rt.boot();

  // A worker that goes away takes its in-flight work with it. A promise nobody
  // settles looks exactly like code that is still running, which is the same
  // failure the boot deadline exists for.
  const pending = rt.execute('NEVER answers', { callId: 'call-lost' });
  let settled = null;
  pending.then(() => { settled = 'resolved'; }, (err) => { settled = err; });

  // execute() awaits boot() before it registers, so the call is not actually in
  // flight yet on the turn it was made. Terminating here instead would test the
  // race, not the teardown, and would pass for the wrong reason.
  await new Promise((r) => setTimeout(r, 20));
  rt.terminate();
  await new Promise((r) => setTimeout(r, 50));

  assert(settled instanceof Error, `the abandoned execution rejects (got ${settled})`);
  assert(/torn down/.test(settled.message), 'and says the runtime was torn down, rather than a generic failure');
}

console.log('\nthe same call_id cannot be in flight twice');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  await rt.boot();
  const first = rt.execute('NEVER answers', { callId: 'call-dup' });
  await new Promise((r) => setTimeout(r, 20));
  let threw = null;
  try {
    await rt.execute('also', { callId: 'call-dup' });
  } catch (err) {
    threw = err;
  }
  assert(threw !== null, 'a second execution reusing a live call_id is refused');
  assert(/already in flight/.test(threw.message), 'and says why');
  rt.terminate();
  await first.catch(() => {});
}

console.log('\na result for a call nobody awaits is dropped, not misattributed');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  await rt.boot();
  const mine = rt.execute('mine', { callId: 'call-mine' });

  // Injected through the same path the worker uses. Attributing this to the
  // one call that IS waiting would hand a caller someone else's output.
  rt._onWorkerMessage({ type: 'result', call_id: 'call-nobody', status: 'ok', summary: 'stray' });

  const result = await mine;
  assertEqual(result.summary, 'mine', 'the waiting call still got its own result');
  rt.terminate();
}

console.log('\nexecute on a terminated runtime says so, rather than failing on a null worker');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  await rt.boot();
  rt.terminate();
  let threw = null;
  try {
    await rt.execute('print(1)');
  } catch (err) {
    threw = err;
  }
  assert(threw !== null, 'executing on a terminated runtime rejects');
  assert(!/null|undefined/.test(threw.message),
    `the message is about the runtime, not about a null worker (got: ${threw.message})`);
}

console.log('\nterminate landing DURING the await inside execute is cancellation, not a null dereference');
{
  // The narrow race the post-await state check exists for, and the only place
  // it is reachable. execute() awaits boot(); when the runtime is already READY
  // that await still yields, so a terminate() on this very turn lands before
  // the execution is registered. Booting first is what makes this different
  // from the terminated-runtime case above, where boot() itself rejects and the
  // check is never reached.
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  await rt.boot();

  const racing = rt.execute('print(1)');
  rt.terminate();

  let threw = null;
  try {
    await racing;
  } catch (err) {
    threw = err;
  }
  assert(threw !== null, 'the racing execution rejects');
  assert(!/null|undefined|not an object/.test(threw.message),
    `it reports the runtime state, not a dereference of the disposed worker (got: ${threw.message})`);
}

console.log('\nexecute refuses a non-string rather than shipping it to the worker');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  let threw = null;
  try {
    await rt.execute({ code: 'print(1)' });
  } catch (err) {
    threw = err;
  }
  assert(threw instanceof TypeError, 'a non-string code argument is a TypeError');
  rt.terminate();
}

console.log('\na run over its deadline is a RESULT with a status, not a rejection');
{
  // The server expects a ClientToolResult for every call it parked. A
  // rejection would leave the model with a tool_use and no tool_result, which
  // the provider refuses outright and which breaks the session rather than the
  // run.
  const rt = new PyodideRuntime({
    runtime: { ...RUNTIME, limits: { exec_seconds: 1 } },
    workerFactory: workerFrom('executing'),
  });

  const started = Date.now();
  const result = await rt.execute('NEVER answers', { callId: 'call-slow-loop' });
  const took = Date.now() - started;

  assertEqual(result.status, 'timeout', 'the status says it timed out');
  assertEqual(result.call_id, 'call-slow-loop', 'and it is reported against the right call');
  assert(/1s/.test(result.stderr), `the message names the limit (got ${JSON.stringify(result.stderr)})`);
  assert(took >= 900 && took < 4000, `it waited roughly the configured second (took ${took}ms)`);
  assert(typeof result.elapsed_ms === 'number', 'and carries an elapsed time');

  // Recycled, not terminated. The person asked for this RUN to stop; a
  // terminated runtime refuses to boot again and would make one runaway loop
  // cost the rest of the conversation.
  assertEqual(rt.state, RUNTIME_STATE.IDLE, 'the runtime returns to IDLE rather than TERMINATED');
  const after = await rt.execute('fine now', { callId: 'call-after-timeout' });
  assertEqual(after.status, 'ok', 'and the next execution boots a fresh worker and runs');
  rt.terminate();
}

console.log('\na worker that dies mid-execution reports oom rather than hanging');
{
  const rt = new PyodideRuntime({
    runtime: { ...RUNTIME, limits: { exec_seconds: 60 } },
    workerFactory: workerFrom('executing'),
  });
  await rt.boot();

  // A wasm memory abort takes the whole instance down: no exception to catch,
  // no deadline fired, and previously a promise nobody would ever settle.
  // Caught, so a regression that REJECTS instead fails by name here rather than
  // crashing the run with an unhandled rejection and taking every later test
  // with it.
  let result = null;
  let rejection = null;
  try {
    result = await rt.execute('CRASH the instance', { callId: 'call-crash' });
  } catch (err) {
    rejection = err;
  }
  assert(rejection === null,
    `a dead worker RESOLVES with a status rather than rejecting${rejection ? ': ' + rejection.message : ''}`);
  assertEqual(result && result.status, 'oom', 'the dead worker settles its call as oom');
  assert(result !== null && /less data at a time/.test(result.stderr),
    'and says something actionable, rather than repeating an empty error message');
  assertEqual(rt.state, RUNTIME_STATE.IDLE, 'the spent instance is discarded');
}

console.log('\nan instance that reports its own oom is not reused');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  await rt.boot();
  const result = await rt.execute('OOM here', { callId: 'call-oom' });
  assertEqual(result.status, 'oom', 'the worker-reported oom reaches the caller');
  // Keeping it would let the next execution start against a heap that is
  // already exhausted and fail for the previous run's reason.
  assertEqual(rt.state, RUNTIME_STATE.IDLE, 'and the instance is recycled rather than reused');
}

console.log('\na deadline and a late result cannot both settle one call');
{
  const rt = new PyodideRuntime({
    runtime: { ...RUNTIME, limits: { exec_seconds: 1 } },
    workerFactory: workerFrom('executing'),
  });
  const result = await rt.execute('DELAY:2000 late', { callId: 'call-late' });
  assertEqual(result.status, 'timeout', 'the deadline settled it first');

  // The worker's answer arrives after the deadline already resolved the
  // promise. Settling twice is invisible in JavaScript, so the assertion is
  // that nothing is left behind to be attributed to a later call.
  await new Promise((r) => setTimeout(r, 1800));
  assertEqual(rt._pending.size, 0, 'nothing is left pending for the late result to land on');
  rt.terminate();
}

console.log('\nfull output stays in the browser, and never rides along to the caller');
{
  const rt = new PyodideRuntime({
    runtime: { ...RUNTIME, limits: { stdout_chars: 256 } },
    workerFactory: workerFrom('executing'),
  });
  const result = await rt.execute('LONG:1000', { callId: 'call-long' });

  // Split off before the result reaches ANY caller. ClientToolResult is
  // extra="forbid", so a forwarded `full` would cost the whole result a 422,
  // and the point of keeping it here is that bulk output never leaves the tab.
  assert(!('full' in result), 'the result a caller receives carries no full streams');
  assertEqual(rt.outputs.size, 1, 'the runtime kept them');

  const first = rt.getFullOutput({ call_id: 'call-long' }, { callId: 'call-read-1' });
  assertEqual(first.status, 'ok', 'get_full_output answers from what this tab kept');
  assertEqual(first.call_id, 'call-read-1',
    'the answer is reported against the get_full_output call itself, not the run it reads');
  assertEqual(first.stdout.length, 256, 'one page is at most the stdout cap, which the server enforces on it too');
  assert(/characters 0 to 256 of 1000\. More remains: call again with offset=256\./.test(first.summary),
    `the summary says where the page sits and how to get the next (got ${JSON.stringify(first.summary)})`);

  const last = rt.getFullOutput({ call_id: 'call-long', offset: 768 }, { callId: 'call-read-2' });
  assertEqual(last.stdout.length, 232, 'the last page is what remains');
  assert(/This is the end of the stream\./.test(last.summary), 'and says it is the end');

  const past = rt.getFullOutput({ call_id: 'call-long', offset: 5000 }, { callId: 'call-read-3' });
  assertEqual(past.stdout, '', 'an offset past the end returns nothing');
  assert(/past the end of stdout/.test(past.summary), 'and says why, with the real length');

  // Deterministic, because this result enters stored history like any other.
  const again = rt.getFullOutput({ call_id: 'call-long' }, { callId: 'call-read-1' });
  assertEqual(JSON.stringify(again), JSON.stringify(first), 'reading the same page twice gives the same bytes');
  rt.terminate();
}

console.log('\nget_full_output fails in words the model can act on');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  const unknown = rt.getFullOutput({ call_id: 'call-from-before-a-reload' }, { callId: 'call-read' });
  assertEqual(unknown.status, 'error', 'an unknown call_id is an error result, not a rejection');
  assert(/gone after a reload/.test(unknown.stderr) && /last 16 runs/.test(unknown.stderr),
    'and names the two reasons output is not there, rather than implying it never existed');

  const badStream = rt.getFullOutput({ call_id: 'x', stream: 'everything' }, { callId: 'call-read' });
  assertEqual(badStream.status, 'error', 'an unknown stream is refused');
  assert(/stdout, stderr, traceback, figures/.test(badStream.stderr), 'with the streams that do exist');
}

console.log('\nfigures can be read back, which stored history cannot give the model');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  await rt.execute('IMAGE please', { callId: 'call-plot' });
  const figures = rt.getFullOutput({ call_id: 'call-plot', stream: 'figures' }, { callId: 'call-read' });
  assertEqual(figures.images.length, 1, 'the figure is re-attached to the answer');
  assert(/1 figure\(s\) from call_id "call-plot", attached/.test(figures.summary), 'and the summary says so');
  rt.terminate();
}

console.log('\noutput outlives the instance that produced it');
{
  // A timeout is exactly when someone wants what was printed before the stop,
  // and the recycle that follows discards the instance, not the output.
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  await rt.execute('LONG:100', { callId: 'call-kept' });
  rt._recycle();
  assertEqual(rt.getFullOutput({ call_id: 'call-kept' }, { callId: 'r' }).status, 'ok',
    'a recycled runtime still answers for output it produced earlier');
  rt.terminate();
}

console.log('\na result for a call nobody is waiting on is not kept');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  await rt.boot();
  // Injected through the path the worker uses. Keeping it would let
  // get_full_output describe a run no caller was ever told had finished, under
  // a call_id the model may never have seen.
  rt._onWorkerMessage({
    type: 'result',
    call_id: 'call-nobody-asked-for',
    status: 'ok',
    stdout: '',
    stderr: '',
    summary: '',
    images: [],
    artifacts: [],
    elapsed_ms: 0,
    full: { stdout: 'stray', stderr: '', traceback: '' },
  });
  assertEqual(rt.outputs.size, 0, 'a stray result is not stored');
  rt.terminate();
}

console.log('\na run that timed out leaves nothing behind to be read');
{
  // True because the deadline recycles the instance, which terminates the
  // worker before its answer can arrive, not because of a check on arrival.
  // Asserted anyway: it is the behavior a person sees.
  const rt = new PyodideRuntime({
    runtime: { ...RUNTIME, limits: { exec_seconds: 1 } },
    workerFactory: workerFrom('executing'),
  });
  await rt.execute('DELAY:1600 late', { callId: 'call-too-late' });
  await new Promise((r) => setTimeout(r, 900));
  assertEqual(rt.outputs.size, 0, 'nothing is stored for a call that timed out');
  rt.terminate();
}

console.log('\nthe store is bounded by count and by size, least recently used first');
{
  const store = new FullOutputStore({ maxCalls: 3, maxChars: 1000 });
  for (const id of ['a', 'b', 'c']) store.remember(id, { stdout: 'x'.repeat(10) }, []);
  store.get('a'); // a is now the most recently used
  store.remember('d', { stdout: 'x'.repeat(10) }, []);
  assertEqual(store.get('b'), undefined, 'the least recently used run is evicted by count');
  assert(store.get('a') !== undefined, 'a run that was READ counts as recently used and survives');

  store.remember('huge', { stdout: 'x'.repeat(990) }, []);
  assert(store.chars <= 1000, `the size bound holds (${store.chars} characters kept)`);
  assert(store.get('huge') !== undefined, 'the newest run is kept even when it alone nearly fills the budget');

  const images = new FullOutputStore({ maxCalls: 10, maxChars: 100 });
  images.remember('p', { stdout: '' }, [{ data_base64: 'y'.repeat(80) }]);
  images.remember('q', { stdout: '' }, [{ data_base64: 'y'.repeat(80) }]);
  assertEqual(images.get('p'), undefined, 'image bytes count toward the size bound, not just text');
}

console.log('\nevery result a caller receives is exactly the server\'s shape');
{
  // ClientToolResult is extra="forbid": one extra key refuses the WHOLE result
  // with a 422. The worker protocol's own `type` field reached callers before
  // this was checked, and would have been the first thing step 7 forwarded.
  const python = await Bun.file(new URL('../src/api/tool_results.py', import.meta.url)).text();
  const body = python.split('class ClientToolResult(BaseModel):')[1].split(/\n(?:class |def )/)[0];
  const serverFields = [...body.matchAll(/^ {4}(\w+): /gm)].map((m) => m[1]).filter((f) => f !== 'model_config');
  assertEqual(JSON.stringify([...serverFields].sort()), JSON.stringify([...CLIENT_TOOL_RESULT_FIELDS].sort()),
    'the runtime\'s field list matches ClientToolResult in src/api/tool_results.py');

  const allowed = new Set(CLIENT_TOOL_RESULT_FIELDS);
  const onlyServerFields = (result, label) => {
    const extra = Object.keys(result).filter((k) => !allowed.has(k));
    assert(extra.length === 0 && 'call_id' in result && 'status' in result,
      `${label}: carries only ClientToolResult fields${extra.length ? ' (extra: ' + extra.join(', ') + ')' : ''}`);
  };

  const rt = new PyodideRuntime({ runtime: { ...RUNTIME, limits: { exec_seconds: 1 } }, workerFactory: workerFrom('executing') });
  onlyServerFields(await rt.execute('fine', { callId: 'shape-ok' }), 'a worker result');
  onlyServerFields(await rt.execute('NEVER answers', { callId: 'shape-timeout' }), 'a timeout');
  onlyServerFields(await rt.execute('CRASH the instance', { callId: 'shape-oom' }), 'a dead worker');
  await rt.execute('LONG:10', { callId: 'shape-kept' });
  onlyServerFields(rt.getFullOutput({ call_id: 'shape-kept' }, { callId: 'shape-read' }), 'a get_full_output answer');
  onlyServerFields(rt.getFullOutput({ call_id: 'nothing-here' }, { callId: 'shape-miss' }), 'a get_full_output refusal');
  rt.terminate();
}

console.log('\nevery result is sized to what the server accepts, at one choke point');
{
  // Several results are built on the host from values the model chose, and one
  // oversized field refuses the WHOLE result. Sizing at each call site was one
  // forgotten site away from a 422, so toClientToolResult enforces it for all.
  const limits = resolveLimits({});
  const huge = 'z'.repeat(100_000);
  const bounded = toClientToolResult({
    type: 'result',
    call_id: 'c',
    status: 'error',
    stdout: huge,
    stderr: huge,
    summary: huge,
    images: new Array(10).fill({ mime: 'image/png', data_base64: 'iVBORw0KGgo=', width: 1, height: 1 }),
    artifacts: new Array(50).fill('a'.repeat(1000)),
    elapsed_ms: -3.7,
  }, limits);
  assert(bounded.stdout.length <= SERVER_LIMITS.MAX_STDOUT_CHARS, `stdout within the server cap (${bounded.stdout.length})`);
  assert(bounded.stderr.length <= SERVER_LIMITS.MAX_STDERR_CHARS, `stderr within the server cap (${bounded.stderr.length})`);
  assert(bounded.summary.length <= SERVER_LIMITS.MAX_SUMMARY_CHARS, `summary within the server cap (${bounded.summary.length})`);
  assertEqual(bounded.images.length, SERVER_LIMITS.MAX_IMAGES, 'images within the server cap');
  assert(bounded.artifacts.length === MAX_ARTIFACTS && bounded.artifacts.every((a) => a.length <= MAX_ARTIFACT_NAME_CHARS),
    'artifacts within both the count and the per-name cap');
  assertEqual(bounded.elapsed_ms, 0, 'elapsed_ms is a non-negative integer, as the server requires');
  assert(!('type' in bounded), 'and the protocol field is gone');

  // The artifact bounds are not in src/core/limits.py, so they are read from
  // the model's own Field declarations.
  const python = await Bun.file(new URL('../src/api/tool_results.py', import.meta.url)).text();
  const artifacts = python.split('artifacts:')[1].split('elapsed_ms')[0];
  assert(new RegExp(`StringConstraints\\(max_length=${MAX_ARTIFACT_NAME_CHARS}\\)`).test(artifacts),
    'the per-artifact name cap matches ClientToolResult');
  assert(new RegExp(`max_length=${MAX_ARTIFACTS},`).test(artifacts), 'and so does the artifact count cap');
}

console.log('\nget_full_output bounds what it echoes back');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  const longId = 'k'.repeat(10_000);
  const miss = rt.getFullOutput({ call_id: longId }, { callId: 'r1' });
  assert(miss.stderr.length <= SERVER_LIMITS.MAX_STDERR_CHARS && /k{256}\.\.\."/.test(miss.stderr),
    `an enormous call_id is shortened in the message, not reflected whole (${miss.stderr.length} characters)`);
  const badStream = rt.getFullOutput({ call_id: 'x', stream: 's'.repeat(10_000) }, { callId: 'r2' });
  assert(badStream.stderr.length < 200, `so is an enormous stream name (${badStream.stderr.length} characters)`);
}

console.log('\nget_full_output returns each stream in the field it came from');
{
  const rt = new PyodideRuntime({
    runtime: { ...RUNTIME, limits: { stderr_chars: 300 } },
    workerFactory: workerFrom('executing'),
  });
  await rt.execute('ERR:1000', { callId: 'call-err' });

  // The fence labels a field by its name, so a traceback returned as stdout was
  // labelled as ordinary output.
  const stderr = rt.getFullOutput({ call_id: 'call-err', stream: 'stderr' }, { callId: 'r1' });
  assertEqual(stderr.stdout, '', 'stderr is not returned as stdout');
  assertEqual(stderr.stderr.length, 300, 'it comes back in stderr, one page sized by the STDERR cap');
  assert(/More remains: call again with offset=300\./.test(stderr.summary), 'with the next offset');

  const traceback = rt.getFullOutput({ call_id: 'call-err', stream: 'traceback' }, { callId: 'r2' });
  assert(traceback.stdout === '' && /ValueError: boom/.test(traceback.stderr),
    'the traceback, which has no field of its own, comes back in stderr');
  rt.terminate();
}

console.log('\nget_full_output never re-attaches more figures than the cap');
{
  const rt = new PyodideRuntime({ runtime: RUNTIME, workerFactory: workerFrom('executing') });
  const image = { mime: 'image/png', data_base64: 'iVBORw0KGgo=', width: 1, height: 1 };
  rt.outputs.remember('call-many', { stdout: '' }, [image, image, image, image, image]);
  const figures = rt.getFullOutput({ call_id: 'call-many', stream: 'figures' }, { callId: 'r' });
  assertEqual(figures.images.length, SERVER_LIMITS.MAX_IMAGES, 'a stored entry over the cap is cut to it');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
if (failed === 0) {
  console.log('\nAll tests passed!');
  process.exit(0);
} else {
  console.log(`\n${failed} test(s) failed`);
  process.exit(1);
}
