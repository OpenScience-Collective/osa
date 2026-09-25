// Drives the real buildWorkerSource output against real Pyodide.
//
// Everything asserted here is invisible to the Bun suites: Pyodide needs a
// browser, so the worker in those tests speaks the protocol and nothing more.
// This is where the Python half of the boundary is actually measured.
import { PyodideRuntime, defaultWorkerFactory } from '../osa-runtime.js';
import { buildEgressGuardSource, DENY_REASON } from '../osa-egress.js';

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

// Above the cold cost of a first figure, and no more. Measured in Chrome on
// 2026-09-22 against Pyodide 0.29.5: `import matplotlib` then pyplot takes about
// 4.3 s in a fresh instance (under Bun, 0.9 s), so a 3-second limit timed out
// every first plot, and the deadline check below would then wait needlessly long.
const EXEC_SECONDS = 10;

async function main() {
  // The Pyodide version the npm package pins, which is the one the Bun suites run,
  // and NEMAR's shipped runtime config and lock overlay, from serve.js.
  const HARNESS = await (await fetch('./harness-config.json')).json();
  const RUNTIME = {
    pyodide_version: HARNESS.pyodide_version,
    preload: ['numpy', 'matplotlib'],
    allow_install: [],
    preload_on: 'widget_open',
    fetch_allow: [new URL('.', location.href).href],
    index_urls: [],
    limits: { exec_seconds: EXEC_SECONDS },
  };

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
  const crossOriginDenied = `https://cdn.jsdelivr.net/pyodide/v${HARNESS.pyodide_version}/full/pyodide.js`;

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

  // osa.fetch, the call a host transport such as eegprep-lean's makes. This
  // server ignores Range, so a 200 is the honest answer; what is checked is
  // that the header passes the guard and the status comes back as a value.
  const fetched = await rt.execute(
    `import osa\nr = await osa.fetch(${JSON.stringify(allowedUrl)}, headers={"Range": "bytes=0-9"})\n` +
      'print(r.status, len(r.body) > 0)'
  );
  check('osa.fetch reads inside fetch_allow with a Range header, returning the status',
    /^(200|206) True\n$/.test(fetched.stdout), `status=${fetched.status} ${fetched.stdout}${fetched.stderr.slice(-160)}`);

  // No response at all, which Bun cannot produce (see test-worker-core.js):
  // here the guard refuses, and the refusal must arrive as a Python OSError.
  const refusedFetch = await rt.execute(
    `import osa\ntry:\n    await osa.fetch(${JSON.stringify(sameOriginDenied)})\n` +
      'except OSError as e:\n    print("OSError", "EgressDenied" in str(e))'
  );
  check('a refused osa.fetch is an OSError that names the egress refusal',
    refusedFetch.stdout === 'OSError True\n', `status=${refusedFetch.status} ${refusedFetch.stdout}${refusedFetch.stderr.slice(-160)}`);

  // THE REDIRECT HOLE: XMLHttpRequest used to check a URL once in open() and
  // then let the native implementation follow any redirect on its own, with
  // no way to stop at the Location header. Only a real browser has a native
  // XMLHttpRequest to try that against; Bun has none at all (checked: `typeof
  // XMLHttpRequest` is undefined even inside a Bun Worker), so this is the one
  // place a genuine redirect response is thrown at the guard.
  await checkEgressRedirect();

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
  check(`and it waited about the configured ${EXEC_SECONDS} seconds, not the boot deadline`,
    loopTook >= EXEC_SECONDS * 1000 - 500 && loopTook < EXEC_SECONDS * 1000 + 9000, `${loopTook}ms`);

  // Recycled, not terminated: one runaway loop must not cost the rest of the
  // conversation. This reboots a whole Pyodide instance, so it is also the
  // check that a cold reboot actually works.
  const afterTimeout = await rt.execute('recovered = 1 + 1\nprint(recovered)');
  check('the runtime recovers and runs the next execution', afterTimeout.status === 'ok',
    `status=${afterTimeout.status} stdout=${JSON.stringify(afterTimeout.stdout)} ${afterTimeout.stderr.slice(-120)}`);

  rt.terminate();
  await checkNemarOverlay(HARNESS.nemar);

  const failures = results.filter((r) => !r.ok).length;
  statusEl.textContent = failures === 0 ? `ALL ${results.length} CHECKS PASSED` : `${failures} of ${results.length} FAILED`;
  statusEl.className = failures === 0 ? 'pass' : 'fail';
  window.__harness = { results, done: true, failures };
}

// A real redirect off the allowlist, the way native XMLHttpRequest could
// exploit it: open() checking a URL once and then letting the browser follow
// a 302 wherever it goes. serve.js's /egress-redirect/allowed 302s to
// /egress-redirect/disallowed on this SAME origin; only /allowed is ever
// sealed into fetch_allow here, so nothing reachable through the guard should
// ever see the disallowed body. This runs the guard in its own worker,
// independent of Pyodide, because the transports under test are its own.
async function checkEgressRedirect() {
  const allowedUrl = new URL('./egress-redirect/allowed', location.href).href;

  // Sanity check FIRST, unguarded: prove the route really redirects and
  // really carries the secret, before asking the guard to refuse it. Without
  // this, a route that was never wired up correctly would look identical to
  // a working guard -- both produce no leak, for entirely different reasons.
  const sanity = await fetch(allowedUrl);
  const sanityBody = await sanity.text();
  check('the redirect route really redirects to the secret body (sanity check)',
    sanity.redirected && sanityBody.includes('EGRESS_REDIRECT_SECRET'),
    `redirected=${sanity.redirected} url=${sanity.url} body=${sanityBody.slice(0, 40)}`);

  // (function () { ... })(), matching buildWorkerSource: the guard must not
  // reach the worker's global scope.
  const workerSource = `
    (function () {
    ${buildEgressGuardSource({ bootAllow: [] })}
    __seal(${JSON.stringify([allowedUrl])});

    self.onmessage = async function () {
      const out = {};

      try {
        await self.fetch(${JSON.stringify(allowedUrl)});
        out.fetchResult = 'REACHED';
      } catch (e) {
        out.fetchResult = e && e.name === 'EgressDenied' ? 'DENIED:' + e.reason
          : (e && e.name === 'TypeError' ? 'network_error_after_guard' : 'other:' + (e && e.message));
      }

      // The transport this check exists for. If construction is not refused,
      // the guard has regressed to the old open()-patch, and this actually
      // sends the request and follows the redirect to see whether the
      // disallowed body comes back -- the same proof a real attack needed.
      try {
        const xhr = new self.XMLHttpRequest();
        await new Promise(function (resolve, reject) {
          xhr.onload = resolve;
          xhr.onerror = function () { reject(new Error('xhr network error')); };
          xhr.open('GET', ${JSON.stringify(allowedUrl)}, true);
          xhr.send();
        });
        out.xhrResult = (xhr.responseText || '').includes('EGRESS_REDIRECT_SECRET')
          ? 'LEAKED:' + xhr.responseURL
          : 'REACHED_NO_LEAK:' + xhr.status;
      } catch (e) {
        out.xhrResult = e && e.name === 'EgressDenied' ? 'DENIED:' + e.reason : 'other:' + (e && e.message);
      }

      self.postMessage(out);
    };
    })();
  `;

  const worker = defaultWorkerFactory(workerSource);
  try {
    const result = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('egress redirect probe timed out')), 15_000);
      worker.onmessage = (e) => { clearTimeout(timer); resolve(e.data); };
      worker.onerror = (e) => { clearTimeout(timer); reject(new Error('probe worker error: ' + (e.message || e))); };
      worker.postMessage('go');
    });
    check('fetch refuses the real redirect off the allowlist (redirect: error)',
      result.fetchResult === 'network_error_after_guard', `fetchResult=${result.fetchResult}`);
    check('XMLHttpRequest is removed, so it cannot follow that redirect either',
      result.xhrResult === `DENIED:${DENY_REASON.TRANSPORT}`, `xhrResult=${result.xhrResult}`);
  } catch (err) {
    check('fetch refuses the real redirect off the allowlist (redirect: error)', false, err.message);
    check('XMLHttpRequest is removed, so it cannot follow that redirect either', false, err.message);
  } finally {
    worker.terminate();
  }
}

// NEMAR'S LOCK OVERLAY, which only a browser can check. Under Bun, Pyodide loads
// a lock entry from a local path and ignores its sha256 (test-data-lane.js), so
// this is the one place the shipped path runs: each wheel fetched by URL from the
// server that sent the hashes, through the egress guard, checked against its
// digest as it loads.
async function checkNemarOverlay(nemar) {
  // Timed, so a cold boot's log says where its time went (the import_before_seal
  // steps included, which name a module rather than a package).
  const onProgress = (p) => log(`  nemar progress: ${Math.round(performance.now())}ms ${p.phase}${p.package ? ' ' + p.package : ''}${p.module ? ' ' + p.module : ''}`);
  const runtimeFrom = (base) => new PyodideRuntime({
    runtime: nemar.runtime,
    lock: { packages: nemar.packages, baseUrl: new URL(base, location.href).href },
    bootTimeoutMs: 120_000,
    onProgress,
  });

  const shipped = runtimeFrom('../runtime/nemar/');
  try {
    const started = performance.now();
    await shipped.boot();
    check("NEMAR's runtime boots, its overlay wheels fetched by URL and checked by digest", true,
      `${Math.round(performance.now() - started)}ms`);
    const imported = await shipped.execute(
      'import eegprep_lean, zarr\n' +
        'print(eegprep_lean.__version__, zarr.__version__, type(eegprep_lean.default_transport()).__name__)'
    );
    const expected = `${nemar.packages['eegprep-lean'].version} ${nemar.packages.zarr.version} FetchTransport\n`;
    check('the overlay packages import, and the prelude made osa.fetch their transport',
      imported.stdout === expected, `status=${imported.status} ${imported.stdout}${imported.stderr.slice(-200)}`);

    // SciPy under the seal (#495), in the browser the prompt's blocks run in:
    // imported before the seal by NEMAR's import_before_seal, welch one channel
    // at a time and butter + sosfiltfilt on the whole array, while executed code
    // is still refused ctypes. test-data-lane.js checks the same under Bun.
    const scipyStarted = performance.now();
    const spectrum = await shipped.execute([
      'import numpy as np',
      'from scipy import signal',
      'rate = 250.0',
      't = np.arange(30000) / rate',
      'eeg = np.random.default_rng(0).standard_normal((33, t.size)) + 3 * np.sin(2 * np.pi * 10 * t)',
      'freqs = signal.welch(eeg[0], fs=rate, nperseg=int(2 * rate))[0]',
      'power = np.array([signal.welch(ch, fs=rate, nperseg=int(2 * rate))[1] for ch in eeg])',
      'sos = signal.butter(4, 30.0, btype="low", fs=rate, output="sos")',
      'filtered = signal.sosfiltfilt(sos, eeg, axis=-1)',
      'print(power.shape, float(freqs[np.argmax(power.mean(axis=0))]), filtered.shape)',
    ].join('\n'));
    check('SciPy imports under the seal, and welch per channel and sosfiltfilt run',
      spectrum.status === 'ok' && spectrum.stdout === '(33, 251) 10.0 (33, 30000)\n',
      `status=${spectrum.status} in ${Math.round(performance.now() - scipyStarted)}ms ${spectrum.stdout}${spectrum.stderr.slice(-200)}`);
    const ctypesRefused = await shipped.execute('import ctypes');
    check('and import ctypes is still refused to executed code',
      ctypesRefused.status === 'denied' && ctypesRefused.stderr === 'denied_import: ctypes',
      `status=${ctypesRefused.status} ${ctypesRefused.stderr.slice(-120)}`);
  } catch (err) {
    check("NEMAR's runtime boots, its overlay wheels fetched by URL and checked by digest", false, err.message);
  } finally {
    shipped.terminate();
  }

  const tampered = runtimeFrom('../tampered/nemar/');
  try {
    await tampered.boot();
    check('a wheel that does not match its sha256 stops the runtime from starting', false, 'it booted');
  } catch (err) {
    // The tampered wheel is a valid wheel, so nothing but the digest refuses it,
    // and a refused digest reaches Pyodide as the browser's "Failed to fetch".
    check('a wheel that does not match its sha256 stops the runtime from starting',
      err.kind === 'runtime_error' && /zarr/.test(err.message) && /Failed to fetch/.test(err.message),
      `${err.kind}: ${err.message.slice(-200)}`);
  } finally {
    tampered.terminate();
  }

  // The cause, not only the outcome: the same tampered URL loads without the
  // digest, so nothing but the integrity check refuses it.
  const entry = nemar.packages.zarr;
  const integrity = `sha256-${btoa(String.fromCharCode(...entry.sha256.match(/../g).map((h) => parseInt(h, 16))))}`;
  const outcome = async (base, init) => {
    try {
      const response = await fetch(new URL(`${base}${entry.file_name}`, location.href), init);
      return `${response.status}:${(await response.arrayBuffer()).byteLength}`;
    } catch (err) {
      return `rejected:${err.name}`;
    }
  };
  const committed = await outcome('../runtime/nemar/', { integrity });
  const refused = await outcome('../tampered/nemar/', { integrity });
  const unchecked = await outcome('../tampered/nemar/', {});
  check("the committed wheel passes the digest its entry records", /^200:\d+$/.test(committed), committed);
  check('the tampered one fails that digest', refused === 'rejected:TypeError', refused);
  check('and loads without it, one byte longer, so the digest alone refused it',
    unchecked === `200:${Number(committed.split(':')[1]) + 1}`, unchecked);
}

main().catch((err) => {
  log('harness threw: ' + (err && err.stack || err));
  statusEl.textContent = 'HARNESS ERROR';
  statusEl.className = 'fail';
  window.__harness = { results, done: true, error: String(err) };
});
