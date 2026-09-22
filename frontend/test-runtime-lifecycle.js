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
  PyodideRuntime,
  RUNTIME_STATE,
  BOOT_FAILURE,
  buildWorkerSource,
} from './osa-runtime.js';

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

console.log('\nthe worker carries the egress guard, installed before anything is fetched');
{
  const src = buildWorkerSource(RUNTIME);

  assert(src.includes('__install'), 'the egress guard is actually in the worker source, not merely exported');
  assert(src.indexOf('__install') < src.indexOf('pyodide.js'),
    'the guard is installed BEFORE the loader is fetched, since importScripts is one of the transports it shims');
  assert(src.includes('__seal(["https://zarr.nemar.org/"])'),
    "the boot ends by sealing the allowlist down to the community's fetch_allow");

  // The distinction the two allowlists exist for: during boot the runtime may
  // reach the CDN it is assembled from, and executed code may reach the data
  // plane. Neither set may quietly become the other.
  const beforeSeal = src.slice(0, src.indexOf('__seal('));
  assert(!beforeSeal.includes('zarr.nemar.org'),
    'fetch_allow is NOT reachable during boot: the data plane appears nowhere before the seal');
  assert(beforeSeal.includes('cdn.jsdelivr.net'), 'the boot allowlist does carry the CDN the loader comes from');
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
  assertEqual(slowResult.call_id, 'call-slow', 'the result carries the call_id it was minted for');
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
