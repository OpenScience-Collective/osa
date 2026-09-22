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
