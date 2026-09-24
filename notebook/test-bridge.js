/**
 * The rejection guard osa-bridge.js sends to each new notebook kernel (#496),
 * checked two ways under Bun against the real Pyodide from npm, the version the
 * notebook site pins:
 *
 * 1. The bridge's copy is the same Python the chat's runtime runs,
 *    buildRejectionGuardSource() in frontend/osa-egress.js, where why it works
 *    is written down once.
 * 2. That copy, run as the bridge runs it, turns a failed request through
 *    pyodide.http.pyfetch (the call eegprep-lean's notebook reads make) from an
 *    await that never returns into an exception. Bun's fetch rejects a refused
 *    connection with a TypeError that has no stack, the same shape Safari's has,
 *    since both run JavaScriptCore; so the hang is real here, and is checked
 *    first, before the guard, so a Pyodide that stops hanging on its own is
 *    reported rather than silently making this test meaningless.
 *
 * Driving the bridge itself needs JupyterLite in a real browser, which is
 * notebook/e2e-check.js.
 *
 * Run with: bun notebook/test-bridge.js
 */

import { loadPyodide } from 'pyodide';
import { buildRejectionGuardSource } from '../frontend/osa-egress.js';

let passed = 0;
let failed = 0;

function assert(cond, msg) {
  if (cond) {
    console.log(`  ok ${msg}`);
    passed++;
  } else {
    console.error(`  x FAIL: ${msg}`);
    failed++;
  }
}

// A suite that hangs reports nothing, so it is bounded as a whole.
setTimeout(() => {
  console.error('  x FAIL: the suite did not finish within 120s.');
  process.exit(1);
}, 120_000).unref();

// Null when the timer wins, which is what an await that never returns looks like.
function settleWithin(promise, ms) {
  let timer;
  const expired = new Promise((resolve) => {
    timer = setTimeout(() => resolve(null), ms);
  });
  return Promise.race([promise, expired]).finally(() => clearTimeout(timer));
}

// A loopback port that was just released, so nothing listens on it.
async function refusedUrl() {
  const probe = Bun.serve({ port: 0, hostname: '127.0.0.1', fetch: () => new Response('') });
  const url = `http://127.0.0.1:${probe.port}/`;
  await probe.stop(true);
  return url;
}

console.log('='.repeat(60));
console.log("The notebook bridge's rejection guard");
console.log('='.repeat(60));

console.log("\nthe bridge sends the chat runtime's own guard");
const bridgeText = await Bun.file(new URL('./osa-bridge.js', import.meta.url)).text();
const declared = bridgeText.match(/const REJECTION_GUARD = (\[[\s\S]*?\])\.join\('\\n'\);/);
assert(declared !== null, 'osa-bridge.js declares REJECTION_GUARD as a list of lines');
const bridgeGuard = declared ? new Function(`return ${declared[1]};`)().join('\n') : '';
assert(bridgeGuard === buildRejectionGuardSource(), "and it is exactly buildRejectionGuardSource()'s Python");
assert(/requestExecute\(\{ code: REJECTION_GUARD, silent: true, store_history: false \}\)/.test(bridgeText),
  'which it sends silently, keeping it out of the execution count and the history');

console.log('\nrun as the bridge runs it, a failed request raises instead of hanging');
{
  const pyodide = await loadPyodide({ stdout: () => {}, stderr: () => {} });
  const refused = await refusedUrl();
  const attempt = (url) =>
    pyodide.runPythonAsync(
      'from pyodide.http import pyfetch\n' +
        `try:\n    await pyfetch(${JSON.stringify(url)})\n    out = "no error"\n` +
        'except Exception as e:\n    out = f"{type(e).__name__}: {e}"\nout'
    );

  // The control: without the guard this await never returns.
  const unguarded = await settleWithin(attempt(refused), 3_000);
  assert(unguarded === null,
    `without the guard, the await on a refused connection never returns (got ${JSON.stringify(unguarded)}); ` +
      'if this fails, Pyodide no longer hangs here and the guard may be removable');

  pyodide.runPython(bridgeGuard);
  const guarded = await settleWithin(attempt(refused), 10_000);
  assert(typeof guarded === 'string' && /^AbortError: \S/.test(guarded),
    `with it, pyfetch raises its own AbortError, carrying what the browser said (got ${JSON.stringify(guarded)})`);

  assert(pyodide.runPython('"_osa_guard_rejections" in globals()') === false,
    "the guard leaves no name behind in the reader's namespace");

  // A restart reruns setup in a fresh kernel; a second setup in the SAME one
  // must not stack a second wrapper on the first.
  pyodide.runPython('import _pyodide._future_helper as _h\n_first = _h.set_exception');
  pyodide.runPython(bridgeGuard);
  assert(pyodide.runPython('_h.set_exception is _first'), 'sent twice to one kernel, it wraps once');
  pyodide.runPython('del _h, _first');

  const passthrough = await settleWithin(
    pyodide.runPythonAsync(
      'import js\ntry:\n    await js.Promise.reject(js.Error.new("an ordinary error"))\n' +
        'except Exception as e:\n    out = f"{type(e).__name__}: {e}"\nout'
    ),
    10_000
  );
  assert(passthrough === 'JsException: Error: an ordinary error',
    `an error Pyodide already recognizes is passed through untouched (got ${JSON.stringify(passthrough)})`);

  const plainValue = await settleWithin(
    pyodide.runPythonAsync(
      'import js\ntry:\n    await js.Promise.reject("just a string")\n' +
        'except Exception as e:\n    out = f"{type(e).__name__}: {e}"\nout'
    ),
    10_000
  );
  assert(plainValue === 'JsException: Error: just a string',
    `and a rejection with no error at all raises too, naming the value (got ${JSON.stringify(plainValue)})`);

  const noReason = await settleWithin(
    pyodide.runPythonAsync(
      'import js\ntry:\n    await js.Promise.reject(js.undefined)\n' +
        'except Exception as e:\n    out = f"{type(e).__name__}: {e}"\nout'
    ),
    10_000
  );
  assert(noReason === 'JsException: Error: a promise was rejected with no reason',
    `and so does one with no reason at all, saying so rather than "None" (got ${JSON.stringify(noReason)})`);
}

console.log(`\n${'='.repeat(60)}`);
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
