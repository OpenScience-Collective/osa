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
  preload: ['numpy', 'matplotlib'],
  allow_install: [],
  preload_on: 'widget_open',
  fetch_allow: [new URL('.', location.href).href],
  index_urls: [],
  limits: { exec_seconds: 3 },
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

  // OUTPUT CAPTURE (step 4). matplotlib is the reason MPLBACKEND is set before
  // anything can import it: Pyodide's default backend draws into the page's DOM,
  // and a worker has no DOM, so the import succeeds and the first plot fails
  // somewhere unrelated.
  const printed = await rt.execute('print("captured stdout")\nprint(2 + 2)');
  check('print() reaches the result', printed.stdout === 'captured stdout\n4\n',
    `status=${printed.status} stdout=${JSON.stringify(printed.stdout)}`);

  const plotted = await rt.execute(
    'import matplotlib.pyplot as plt\n' +
    'import numpy\n' +
    'plt.plot(numpy.arange(10), numpy.arange(10) ** 2)\n' +
    'plt.title("squares")\n'
  );
  const figure = (plotted.images || [])[0];
  check('a matplotlib figure comes back as a PNG', Boolean(figure),
    `status=${plotted.status} images=${(plotted.images || []).length} ${plotted.stderr.slice(-160)}`);
  if (figure) {
    check('and it is a real image, sized within the cap',
      figure.mime === 'image/png' && figure.width > 0 && figure.height > 0 &&
      figure.width <= 1024 && figure.height <= 1024,
      `${figure.width}x${figure.height} ${figure.mime}, ${figure.data_base64.length} base64 chars`);
  }

  // A figure left open by one run must not reappear in the next. plt.close("all")
  // after collection is what prevents a plot from being attributed to code that
  // did not draw it.
  const afterPlot = await rt.execute('x_after = 1');
  check('a later run does not inherit the previous figure', (afterPlot.images || []).length === 0,
    `images=${(afterPlot.images || []).length}`);

  const failing = await rt.execute('print("before the failure")\nraise ValueError("boom")');
  check('a failed run still returns the output it produced first',
    failing.status === 'error' && failing.stdout.includes('before the failure'),
    `status=${failing.status} stdout=${JSON.stringify(failing.stdout)}`);

  // THE SUMMARY (step 6): structured facts, which is what the model reasons over.
  const described = await rt.execute(
    'import numpy as np\nsignal = np.arange(10.0)\nsignal[3] = np.nan\ncounts = np.arange(3)',
    { callId: 'call-described' }
  );
  check('the summary describes arrays by facts, never by contents',
    /signal: ndarray float64 shape=\(10,\) min=0 max=9 mean=4\.66667 nan=1/.test(described.summary),
    JSON.stringify(described.summary));
  // numpy's default integer is a C long, 32 bits on wasm32. The summary is the
  // one place the model can see which platform its integers are on.
  check('and shows the wasm32 default integer width', /counts: ndarray int32/.test(described.summary),
    JSON.stringify(described.summary));

  const erred = await rt.execute('def f():\n    return 1 / 0\nf()', { callId: 'call-erred' });
  check('an error carries its type, message and the offending line, not a traceback',
    /^ZeroDivisionError: division by zero\n {2}line 2: return 1 \/ 0$/m.test(erred.stderr) &&
      !/_pyodide/.test(erred.stderr),
    JSON.stringify(erred.stderr));

  const traceback = rt.getFullOutput({ call_id: 'call-erred', stream: 'traceback' }, { callId: 'call-read' });
  check('the full traceback stays in the browser and is readable by call_id',
    traceback.status === 'ok' && /ZeroDivisionError/.test(traceback.stderr) && traceback.stdout === '',
    `status=${traceback.status} ${traceback.summary}`);
  check('and never rode along on the result itself', !('full' in erred), Object.keys(erred).join(','));

  const figured = await rt.execute(
    'import matplotlib.pyplot as plt\nplt.plot([0, 1, 2], [0, 1, 4], label="quad")\nplt.title("growth")\nplt.legend()\nplt.show()',
    { callId: 'call-figured' }
  );
  check('a figure is described in words that outlive the image',
    /axes 1: title='growth' lines=1 series=\['quad'\] data x=\[0, 2\] y=\[0, 4\]/.test(figured.summary),
    JSON.stringify(figured.summary));

  // THE DEADLINE, against a genuinely runaway loop. This is the one thing that
  // cannot be checked without a real runtime: Pyodide cannot interrupt its own
  // Python without a SharedArrayBuffer, so the only way to stop this is for the
  // host to terminate the worker.
  const loopStarted = performance.now();
  const runaway = await rt.execute('while True:\n    pass\n');
  const loopTook = Math.round(performance.now() - loopStarted);
  check('an infinite loop is stopped on the deadline', runaway.status === 'timeout',
    `status=${runaway.status} after ${loopTook}ms`);
  check('and it waited about the configured 3 seconds, not the boot deadline',
    loopTook >= 2500 && loopTook < 12000, `${loopTook}ms`);

  // Recycled, not terminated: one runaway loop must not cost the rest of the
  // conversation. This reboots a whole Pyodide instance, so it is also the
  // check that a cold reboot actually works.
  const afterTimeout = await rt.execute('recovered = 1 + 1\nprint(recovered)');
  check('the runtime recovers and runs the next execution', afterTimeout.status === 'ok',
    `status=${afterTimeout.status} stdout=${JSON.stringify(afterTimeout.stdout)} ${afterTimeout.stderr.slice(-120)}`);

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
