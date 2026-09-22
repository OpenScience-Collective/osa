// Drives the real buildWorkerSource output against real Pyodide.
//
// Everything asserted here is invisible to the Bun suites: Pyodide needs a
// browser, so the worker in those tests speaks the protocol and nothing more.
// This is where the Python half of the boundary is actually measured.
import { PyodideRuntime } from '../osa-runtime.js';

const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');
const results = [];

function log(line) {
  logEl.textContent += line + '\n';
  console.log('[harness]', line);
}

function check(name, ok, detail) {
  results.push({ name, ok, detail });
  log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`);
}

const RUNTIME = {
  pyodide_version: '0.28.3',
  lockfile: 'harness',
  preload: ['numpy'],
  allow_install: [],
  preload_on: 'widget_open',
  fetch_allow: [new URL('.', location.href).href],
  index_urls: [],
  limits: {},
};

async function main() {
  const rt = new PyodideRuntime({
    runtime: RUNTIME,
    // Long enough for a cold Pyodide download, short enough that the control
    // variant demonstrates the deadline rather than appearing to hang: under a
    // policy with no wasm grant, loadPyodide never resolves, never rejects and
    // logs nothing, so this is the only thing that turns a blocked runtime into
    // a visible failure.
    bootTimeoutMs: 45_000,
    onProgress: (p) => log(`  progress: ${p.phase}${p.package ? ' ' + p.package : ''}`),
  });

  const t0 = performance.now();
  try {
    const { version } = await rt.boot();
    check('the generated worker boots real Pyodide', true, `v${version} in ${Math.round(performance.now() - t0)}ms`);
  } catch (err) {
    check('the generated worker boots real Pyodide', false, err.message);
    statusEl.textContent = 'BOOT FAILED';
    statusEl.className = 'fail';
    window.__harness = { results, done: true };
    return;
  }

  const run = async (label, code, expect) => {
    let r;
    try {
      r = await rt.execute(code);
    } catch (err) {
      check(label, false, 'threw: ' + err.message);
      return null;
    }
    const ok = r.status === expect;
    check(label, ok, `status=${r.status}${r.stderr ? ' stderr=' + r.stderr.slice(0, 160) : ''}`);
    return r;
  };

  // A preloaded package works, which proves the gate is not simply denying all.
  await run('a preloaded package runs', 'import numpy\nx = numpy.arange(5).sum()', 'ok');

  // The namespace seal, from the inside. Each of these is a capability that
  // would route around the egress boundary if it survived.
  await run('micropip is gone from the execution namespace', 'import micropip', 'denied');
  await run('the js bridge is gone', 'import js', 'denied');
  await run('pyodide_js is gone', 'import pyodide_js', 'denied');
  const ct = await rt.execute('import ctypes');
  check('ctypes is gone', ct.status === 'denied', `status=${ct.status}`);

  // Each blocked root has to explain ITSELF. One shared sentence told someone
  // importing ctypes that network access goes through the runtime's client,
  // which is not why ctypes is blocked and reads as a bug in their own code.
  const ctWhy = await rt.execute('import importlib\nimportlib.import_module("ctypes")');
  check('and says why ctypes specifically, not a generic network sentence',
    /[Ff]oreign function/.test(ctWhy.stderr) && !/fetch_allow/.test(ctWhy.stderr),
    ctWhy.stderr.slice(-180));
  const mpWhy = await rt.execute('import importlib\nimportlib.import_module("micropip")');
  check('and micropip explains the install model rather than the network',
    /allow_install/.test(mpWhy.stderr), mpWhy.stderr.slice(-180));
  const jsWhy = await rt.execute('import importlib\nimportlib.import_module("js")');
  check('while the bridge points at the client that replaces it',
    /osa\.fetch_bytes/.test(jsWhy.stderr), jsWhy.stderr.slice(-180));
  // A dynamic import is NOT visible to the static gate, which sees only
  // `importlib`. It is caught at runtime by the meta_path finder instead, so it
  // is an error rather than a denial. The capability is still blocked; only the
  // status differs, and the message has to say why or it reads as a bug in the
  // person's code.
  const dynamic = await rt.execute('import importlib\nimportlib.import_module("ctypes")');
  check('a dynamic import of a blocked root is refused',
    /ImportError|ModuleNotFoundError/.test(dynamic.stderr),
    `status=${dynamic.status} ${dynamic.stderr.slice(-220)}`);

  // A package that was never installed is denied rather than silently fetched.
  await run('a package nobody installed is denied, not installed on demand', 'import scipy', 'denied');

  // Variables persist across turns: the worker is warm, not per-execution.
  const persisted = await run('state persists across executions', 'y = x + 1', 'ok');
  if (persisted) {
    const readBack = await rt.execute('assert y == 11, y');
    check('and the value is the one the earlier turn computed', readBack.status === 'ok',
      `status=${readBack.status} ${readBack.stderr.slice(0, 120)}`);
  }

  await run('a raised exception is an error, not a denial', 'raise ValueError("boom")', 'error');

  // THE SANCTIONED ROUTE. Without stdout capture (step 4) the only way to read
  // a value back is to raise it, so each probe raises its own outcome.
  const probe = async (label, expr) => {
    const r = await rt.execute(
      'try:\n' +
      `    _v = await ${expr}\n` +
      '    _out = "OK:" + str(len(_v)) + " bytes"\n' +
      'except Exception as _e:\n' +
      '    _out = type(_e).__name__ + ": " + str(_e)[:140]\n' +
      'raise SystemExit("PROBE " + _out)\n'
    );
    const marker = (r.stderr.match(/PROBE ([^\n]*)/) || [])[1] || `(no marker) ${r.status} ${r.stderr.slice(-160)}`;
    log(`      ${label}: ${marker}`);
    return marker;
  };

  await run('the osa client is importable, so the gate resolves it', 'import osa', 'ok');

  const allowedUrl = new URL('./harness.js', location.href).href;
  const sameOriginDenied = new URL('../osa-egress.js', location.href).href;
  const crossOriginDenied = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.js';

  const okRead = await probe('osa.fetch_bytes inside fetch_allow', `osa.fetch_bytes(${JSON.stringify(allowedUrl)})`);
  check('executed code CAN read a URL inside fetch_allow', /^OK:\d+ bytes/.test(okRead), okRead);

  // Same origin, same CSP verdict, different path: whatever refuses this is
  // OUR allowlist and cannot be the browser's connect-src.
  const sameOrigin = await probe('a sibling path on the SAME origin', `osa.fetch_bytes(${JSON.stringify(sameOriginDenied)})`);
  check('a path outside fetch_allow is refused even on an origin the CSP allows',
    !/^OK:/.test(sameOrigin), sameOrigin);

  // The boot CDN: reachable during boot, and reachable under the CSP, so this
  // is the seal and nothing else.
  const afterSeal = await probe('the boot CDN after the seal', `osa.fetch_bytes(${JSON.stringify(crossOriginDenied)})`);
  check('the boot origin is unreachable once sealed', !/^OK:/.test(afterSeal), afterSeal);

  const failures = results.filter((r) => !r.ok).length;
  statusEl.textContent = failures === 0 ? `ALL ${results.length} CHECKS PASSED` : `${failures} of ${results.length} FAILED`;
  statusEl.className = failures === 0 ? 'pass' : 'fail';
  window.__harness = { results, done: true, failures };
}

main().catch((err) => {
  log('harness threw: ' + (err && err.stack || err));
  statusEl.textContent = 'HARNESS ERROR';
  statusEl.className = 'fail';
  window.__harness = { results, done: true, error: String(err) };
});
