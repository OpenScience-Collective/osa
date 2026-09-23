// The worker core, run against the REAL Pyodide from npm.
//
// Everything here used to be verifiable only in a browser, by hand. Pyodide
// boots under Bun in well under a second from its npm package (0.29.5 in about
// 620 ms, measured 2026-09-22), so the Python half of the runtime (the import
// gate, the namespace seal, the data client, the execution harness and its
// summary) now runs in CI against the interpreter that ships, not a stand-in.
//
// Two things stay out of reach here and are covered elsewhere. The egress
// guard is a worker-global shim and would replace this process's own fetch, so
// it is exercised in a real worker by test-egress.js. And a browser's CSP is
// not enforced by Bun at all, which is what frontend/browser-harness/ is for.
//
// The core under test is the STRINGIFIED copy, rebuilt with new Function, which
// is exactly what buildWorkerSource embeds. Importing it directly would pass
// even if it reached for a module-level name, which inside the worker is a
// ReferenceError and nowhere else.
import { loadPyodide } from 'pyodide';
import stockLock from 'pyodide/pyodide-lock.json';
import pyodidePackage from 'pyodide/package.json';
import { buildDataClientSource, buildNamespaceSealSource } from './osa-egress.js';
import { buildHelpersSource, buildOutputCaptureSource, resolveLimits } from './osa-output.js';
import { createWorkerRuntime } from './osa-worker-core.js';
import { rangeResponse } from './test-support/byte-range.js';
import { buildMinimalWheel } from './test-support/minimal-wheel.js';

let passed = 0;
let failed = 0;

function assert(condition, msg) {
  if (condition) {
    passed++;
    console.log(`  ok ${msg}`);
  } else {
    failed++;
    console.error(`\x1b[31m  x FAIL: ${msg}\x1b[0m`);
  }
}

function assertEqual(actual, expected, msg) {
  assert(
    actual === expected,
    `${msg}${actual === expected ? '' : ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`}`
  );
}

// A suite that hangs reports nothing, so it is bounded as a whole.
const SUITE_TIMEOUT_MS = 180_000;
setTimeout(() => {
  console.error(`\x1b[31m  x FAIL: the suite did not finish within ${SUITE_TIMEOUT_MS / 1000}s.\x1b[0m`);
  process.exit(1);
}, SUITE_TIMEOUT_MS).unref();

// Packages are fetched from the CDN once and cached here. CI keeps this
// directory between runs, keyed on the Pyodide version.
const PACKAGE_CACHE = new URL('../.cache/pyodide-packages/', import.meta.url).pathname;

const createFromSource = new Function(`return (${createWorkerRuntime.toString()});`)();

// A request that gets no response at all. A browser rejects one with an ordinary
// TypeError; Bun's own rejection is an object Pyodide's future helper refuses, so
// an await on it never settles (measured 2026-09-22 on 0.29.5). This host is
// refused the way a browser refuses it, which is the one thing that stands in
// here: installed before any runtime boots, since the data client captures fetch.
const UNREACHABLE = 'http://unreachable.invalid/';
const bunFetch = globalThis.fetch;
globalThis.fetch = (input, init) =>
  String(input).startsWith(UNREACHABLE) ? Promise.reject(new TypeError('Failed to fetch')) : bunFetch(input, init);

/**
 * Boot a runtime exactly as the worker does, recording what it sends and seals.
 */
async function bootRuntime({
  preload = [],
  allowInstall = [],
  indexUrls = [],
  fetchAllow = ['http://127.0.0.1/allowed/'],
  limits = {},
  lockPackages = {},
  prelude = '',
  // The lock the installed package ships, which is the one its version serves.
  readStockLock = async () => stockLock,
} = {}) {
  const messages = [];
  const sealed = [];
  const config = {
    // The interpreter comes from npm here; the index is where the core points
    // Pyodide for the distribution's own wheels when it is handed a lock.
    indexURL: `https://cdn.jsdelivr.net/pyodide/v${pyodidePackage.version}/full/`,
    preload,
    allowInstall,
    indexUrls,
    fetchAllow,
    lockPackages,
    prelude,
    python: {
      helpers: buildHelpersSource(),
      outputCapture: buildOutputCaptureSource(resolveLimits(limits)),
      dataClient: buildDataClientSource(),
      namespaceSeal: buildNamespaceSealSource(),
    },
  };
  const runtime = createFromSource(config, {
    load: (indexURL, options) =>
      loadPyodide({ packageCacheDir: PACKAGE_CACHE, stdout: () => {}, stderr: () => {}, ...options }),
    stockLock: readStockLock,
    seal: (prefixes) => sealed.push({ prefixes, sentBefore: messages.length }),
    send: (message) => messages.push(message),
  });
  await runtime.handle({ type: 'boot' });

  let seq = 0;
  const run = async (code, callId) => {
    const id = callId || `call-${++seq}`;
    const from = messages.length;
    await runtime.handle({ type: 'execute', call_id: id, code });
    return messages.slice(from).find((m) => m.type === 'result');
  };
  return { runtime, messages, sealed, run, ready: messages.find((m) => m.type === 'ready') };
}

console.log('='.repeat(60));
console.log('Worker core against the real Pyodide');
console.log('='.repeat(60));

console.log('\nthe stringified core is self-contained');
{
  // If this line throws, the core reached for something outside itself.
  assert(typeof createFromSource === 'function', 'the core rebuilds from its own source text');
}

console.log('\na malformed configuration is named, not discovered deep inside the boot');
{
  const messages = [];
  const good = {
    indexURL: 'x',
    preload: [],
    allowInstall: [],
    indexUrls: [],
    fetchAllow: [],
    lockPackages: {},
    prelude: '',
    python: { helpers: 'a', outputCapture: 'b', dataClient: 'c', namespaceSeal: 'd' },
  };
  const env = { load: () => { throw new Error('must not be reached'); }, seal: () => {}, send: (m) => messages.push(m) };

  await createFromSource({ ...good, fetchAllow: 'https://zarr.nemar.org/' }, env).handle({ type: 'boot' });
  assert(messages.length === 1 && messages[0].kind === 'config' && /config\.fetchAllow is not a list of strings/.test(messages[0].message),
    `a wrong type is reported by the field's name, before loading anything (got ${JSON.stringify(messages[0])})`);

  messages.length = 0;
  await createFromSource({ ...good, python: { ...good.python, namespaceSeal: '' } }, env).handle({ type: 'boot' });
  assert(/config\.python\.namespaceSeal/.test(messages[0] && messages[0].message),
    'an empty generated source is caught too, since running nothing is not sealing');

  messages.length = 0;
  await createFromSource({ ...good, lockPackages: { zarr: {} } }, env).handle({ type: 'boot' });
  assert(/env\.stockLock is not a function/.test(messages[0] && messages[0].message),
    'lock packages with no way to read the stock lock are named before loading anything');

  messages.length = 0;
  await createFromSource({ ...good, prelude: null }, env).handle({ type: 'boot' });
  assert(/config\.prelude is not a string/.test(messages[0] && messages[0].message), 'so is a prelude that is not a string');

  let threw = null;
  try {
    createFromSource(good, { load: () => {}, seal: () => {} });
  } catch (err) {
    threw = err;
  }
  assert(threw instanceof TypeError && /env\.send/.test(threw.message),
    'without a way to report, construction itself refuses');
}

const plain = await bootRuntime();

console.log('\nboot, and the order the seal happens in');
{
  assert(plain.ready !== undefined, `the core boots real Pyodide (got ${JSON.stringify(plain.messages.at(-1))})`);
  assertEqual(plain.ready && plain.ready.version, pyodidePackage.version, 'and reports the version it booted');
  assertEqual(plain.sealed.length, 1, 'the egress allowlist is narrowed exactly once');
  assertEqual(JSON.stringify(plain.sealed[0] && plain.sealed[0].prefixes), JSON.stringify(['http://127.0.0.1/allowed/']),
    "and narrowed to the community's fetch_allow, not the boot allowlist");
  // Sealed BEFORE ready is sent: nothing executable exists until after both.
  const readyIndex = plain.messages.findIndex((m) => m.type === 'ready');
  assertEqual(plain.sealed[0] && plain.sealed[0].sentBefore, readyIndex,
    'the seal happens before ready, so no execution can start against an unsealed runtime');
}

console.log('\na package that fails to load fails the boot, rather than surfacing later as a denial');
{
  const broken = await bootRuntime({ preload: ['definitely-not-a-package'] });
  const error = broken.messages.find((m) => m.type === 'error');
  assert(broken.ready === undefined, 'the runtime does not report ready');
  assert(error !== undefined && /definitely-not-a-package/.test(error.message),
    `the error names the package (got ${JSON.stringify(error && error.message)})`);
}

console.log('\nthe import gate');
{
  const scipy = await plain.run('import scipy');
  assertEqual(scipy.status, 'denied', 'a package nobody installed is denied');
  assertEqual(scipy.stderr, 'denied_import: scipy', 'with the machine-readable reason');

  const submodule = await plain.run('import scipy.signal');
  assertEqual(submodule.stderr, 'denied_import: scipy', 'a submodule is reported by its ROOT, the thing that is missing');

  for (const root of ['micropip', 'js', 'pyodide', 'ctypes']) {
    const blocked = await plain.run(`import ${root}`);
    assertEqual(blocked.status, 'denied', `${root} is denied by the gate after the seal`);
  }

  const stdlib = await plain.run('import json, os.path');
  assertEqual(stdlib.status, 'ok', 'the standard library still imports, so the gate is not denying everything');

  const dynamic = await plain.run('import importlib\nimportlib.import_module("js")');
  assertEqual(dynamic.status, 'error', 'a dynamic import is invisible to the static gate and fails at runtime instead');
  assert(/ImportError: 'js' is not available/.test(dynamic.stderr), 'with the seal naming what was blocked');
}

console.log('\na denied-import list is bounded, since the names come from the code');
{
  const code = Array.from({ length: 30 }, (_, i) => `import fake_package_${String(i).padStart(2, '0')}`).join('\n');
  const r = await plain.run(code);
  assertEqual(r.status, 'denied', 'thirty unknown packages are denied');
  assert(/fake_package_19, and 10 more$/.test(r.stderr), `the list stops at twenty and counts the rest (got ${JSON.stringify(r.stderr.slice(-60))})`);
  assert(!/fake_package_20/.test(r.stderr + r.summary), 'the rest are not named');
}

console.log('\nsyntax errors are reported like any other error');
{
  const r = await plain.run('x = 1\ny = (');
  assertEqual(r.status, 'error', 'a syntax error is an error, not a denial or a crash');
  assert(/^SyntaxError: /.test(r.stderr), `stderr leads with the type (got ${JSON.stringify(r.stderr)})`);
  assert(/line 2: y = \(/.test(r.stderr), 'and names the offending line with its text');
}

console.log('\nthe summary: variables');
{
  const r = await plain.run('answer = 41 + 1\nlabel = "alpha"\n_private = 1\nimport json');
  assertEqual(r.status, 'ok', 'the run succeeds');
  assert(/^variables:$/m.test(r.summary), `the summary lists variables (got ${JSON.stringify(r.summary)})`);
  assert(/^ {2}answer: int = 42$/m.test(r.summary), 'an int is described by its value');
  assert(/^ {2}label: str len=5 = 'alpha'$/m.test(r.summary), 'a string by its length and a bounded preview');
  assert(!/_private/.test(r.summary), 'underscore names are not listed');
  assert(!/json/.test(r.summary), 'nor are modules, which an import statement binds');
  assert(!/\bosa\b|\bdisplay\b/.test(r.summary), "nor the runtime's own names");

  // Only what THIS run created or rebound. A variable from an earlier turn that
  // the run did not touch is not news, and repeating it every turn is exactly
  // the bulk the summary exists to avoid.
  const next = await plain.run('other = answer * 2');
  assert(/other: int = 84/.test(next.summary), 'a new variable is listed');
  assert(!/answer/.test(next.summary), 'a variable the run only READ is not re-listed');
}

console.log('\nerrors: type, message and the offending line, not a traceback');
{
  const r = await plain.run('def f():\n    return 1 / 0\n\nf()', 'call-zero');
  assertEqual(r.status, 'error', 'a raised exception is an error');
  assert(/^ZeroDivisionError: division by zero\n {2}line 2: return 1 \/ 0$/m.test(r.stderr),
    `stderr carries the type, message and the line INSIDE the function that raised (got ${JSON.stringify(r.stderr)})`);
  assert(!/_pyodide\/_base\.py/.test(r.stderr), "Pyodide's own frames are not in what the conversation carries");
  assert(/error: ZeroDivisionError at line 2/.test(r.summary), 'the summary records the failure in one line');
  assert(/traceback: get_full_output\(call_id="call-zero", stream="traceback"\)/.test(r.summary),
    'and says where the full traceback is, by a handle the model can pass back');
  assert(/_pyodide\/_base\.py|File "<cell>"/.test(r.full.traceback), 'the browser keeps the full traceback');

  const exit = await plain.run('raise SystemExit("leaving")');
  assertEqual(exit.status, 'error', 'SystemExit is an error result');
  // Status alone cannot tell where it was caught: if SystemExit escaped the
  // harness, the host's fallback reports 'error' too, but the harness never
  // restores the streams or builds the summary. The message says which path ran.
  assert(/^SystemExit: leaving/.test(exit.stderr) && !/outside Python/.test(exit.stderr),
    `SystemExit is caught INSIDE the harness, not by the host's fallback (got ${JSON.stringify(exit.stderr)})`);
  const after = await plain.run('still_here = True');
  assertEqual(after.status, 'ok', 'and the next run still works');

  const memory = await plain.run('raise MemoryError()');
  assertEqual(memory.status, 'oom', 'a Python MemoryError is reported as oom, not as a generic error');
}

console.log('\nthe value of a trailing expression is shown, as a notebook would');
{
  assertEqual((await plain.run('41 + 1')).stdout, '42\n', 'a trailing expression is printed');
  assertEqual((await plain.run('41 + 1;')).stdout, '', 'a trailing semicolon suppresses it');
  assertEqual((await plain.run('value = 3')).stdout, '', 'an assignment prints nothing');
}

console.log('\nstreams that overflow are clipped here and kept whole in the browser');
{
  const small = await bootRuntime({ limits: { stdout_chars: 512 } });
  const r = await small.run('print("x" * 5000)', 'call-long');
  assert(r.stdout.length <= 512, `what the conversation carries is bounded (got ${r.stdout.length})`);
  assertEqual(r.full.stdout.length, 5001, 'the browser keeps all of it, newline included');
  assert(/stdout: 5001 characters, clipped here; get_full_output\(call_id="call-long", stream="stdout"\) reads them/
    .test(r.summary), `the summary says how to get the rest (got ${JSON.stringify(r.summary)})`);

  const short = await small.run('print("short")', 'call-short');
  assert(!/call_id/.test(short.summary), 'a run with nothing to retrieve does not print a handle');
}

console.log('\nthe summary is deterministic, because the prompt cache is a byte-exact prefix match');
{
  const code = 'import json\nclass Thing:\n    pass\nitem = Thing()\nprint(item)\nitem';
  const first = await plain.run(code, 'call-same');
  const second = await plain.run(code, 'call-same');
  assertEqual(first.summary, second.summary, 'two runs of the same code produce the same summary');
  assertEqual(first.stdout, second.stdout, 'and the same stdout');
  assert(!/0x[0-9a-f]{6,}/i.test(first.stdout + first.summary), 'with no heap address anywhere');
}

// The numeric and plotting half needs packages. They are fetched once and
// cached, so this is slow only the first time on a machine.
const scientific = await bootRuntime({ preload: ['numpy', 'matplotlib'] });

console.log('\nthe whole boot\'s step budget is fixed before it starts, and step reaches it exactly once');
{
  // Every progress message a boot sends, reduced to what a progress bar
  // needs: which step is starting, out of how many, and never index/total
  // (dropped: nothing reads them any more, this suite included).
  const steps = (messages) =>
    messages
      .filter((m) => m.type === 'progress')
      .map((m) => ({ phase: m.phase, package: m.package === undefined ? null : m.package, step: m.step, steps: m.steps }));
  const hasNoIndexOrTotal = (messages) =>
    messages.filter((m) => m.type === 'progress').every((m) => !('index' in m) && !('total' in m));

  // Scenario 1: preload only. Two packages, so `steps` is 3 (interpreter +
  // two names) and the sequence visibly advances past the shared step-1 pair.
  assertEqual(JSON.stringify(steps(scientific.messages)), JSON.stringify([
    { phase: 'loading_runtime', package: null, step: 1, steps: 3 },
    { phase: 'runtime_loaded', package: null, step: 1, steps: 3 },
    { phase: 'loading_package', package: 'numpy', step: 2, steps: 3 },
    { phase: 'loading_package', package: 'matplotlib', step: 3, steps: 3 },
  ]), 'preload only: loading_runtime and runtime_loaded share step 1, then one step per preload name, ending at steps');
  assert(hasNoIndexOrTotal(scientific.messages), 'preload only: no message carries index or total');

  // Scenario 2: preload and allow_install. The installed package is a real,
  // hand-built wheel served by a real local HTTP server, so micropip runs its
  // real install path; nothing here is a stand-in for that network fetch.
  const wheel = buildMinimalWheel({
    distribution: 'osatestpkg', version: '1.0.0', modules: { 'osatestpkg/__init__.py': 'VALUE = 42\n' },
  });
  const wheelServer = Bun.serve({
    port: 0,
    hostname: '127.0.0.1',
    fetch(request) {
      const url = new URL(request.url);
      if (url.pathname === `/${wheel.fileName}`) {
        return new Response(wheel.bytes, { headers: { 'Content-Type': 'application/octet-stream' } });
      }
      return new Response('Not Found', { status: 404 });
    },
  });
  let installed;
  try {
    const wheelUrl = `http://127.0.0.1:${wheelServer.port}/${wheel.fileName}`;
    installed = await bootRuntime({ preload: ['numpy'], allowInstall: [wheelUrl] });
    assert(installed.ready !== undefined, `installing a real wheel over a real local server lets the runtime come up (got ${JSON.stringify(installed.messages.at(-1))})`);
    const readBack = await installed.run('import osatestpkg\nosatestpkg.VALUE');
    assertEqual(readBack.status, 'ok', 'and the installed package actually imports');

    assertEqual(JSON.stringify(steps(installed.messages)), JSON.stringify([
      { phase: 'loading_runtime', package: null, step: 1, steps: 4 },
      { phase: 'runtime_loaded', package: null, step: 1, steps: 4 },
      { phase: 'loading_package', package: 'numpy', step: 2, steps: 4 },
      { phase: 'loading_package', package: 'micropip', step: 3, steps: 4 },
      { phase: 'installing', package: wheelUrl, step: 4, steps: 4 },
    ]), 'preload and allow_install: one step each for numpy, micropip and the installed wheel, ending at steps');
    assert(hasNoIndexOrTotal(installed.messages), 'preload and allow_install: no message carries index or total');
  } finally {
    wheelServer.stop(true);
  }

  // Scenario 3: a prelude, and nothing else. steps is 2 (interpreter + the
  // prelude itself), and runtime_loaded still reports step 1, not step 2:
  // the prelude has not started yet when the interpreter finishes loading.
  const withPrelude = await bootRuntime({ prelude: 'prelude_ran = True\n' });
  assertEqual(JSON.stringify(steps(withPrelude.messages)), JSON.stringify([
    { phase: 'loading_runtime', package: null, step: 1, steps: 2 },
    { phase: 'runtime_loaded', package: null, step: 1, steps: 2 },
    { phase: 'prelude', package: null, step: 2, steps: 2 },
  ]), 'a prelude: one more step than the interpreter alone, and it is the last one');
  assert(hasNoIndexOrTotal(withPrelude.messages), 'a prelude: no message carries index or total');

  // Across every scenario: steps never changes mid-boot, and step strictly
  // increases across the DISTINCT values a boot reports (loading_runtime and
  // runtime_loaded sharing step 1 is the one deliberate repeat), reaching
  // steps exactly at the final progress message and never beyond it.
  for (const { label, messages } of [
    { label: 'preload only', messages: scientific.messages },
    { label: 'preload and allow_install', messages: installed.messages },
    { label: 'a prelude', messages: withPrelude.messages },
  ]) {
    const progressMessages = messages.filter((m) => m.type === 'progress');
    const distinctSteps = [...new Set(progressMessages.map((m) => m.step))];
    const wantedTotal = progressMessages[0].steps;
    assert(progressMessages.every((m) => m.steps === wantedTotal), `${label}: steps is constant across the whole boot`);
    assertEqual(JSON.stringify(distinctSteps), JSON.stringify(Array.from({ length: wantedTotal }, (_, i) => i + 1)),
      `${label}: the distinct step values are exactly 1..steps, in order`);
    assertEqual(progressMessages.at(-1).step, wantedTotal, `${label}: the last progress message reports step === steps`);
  }
}

console.log('\nthe summary: arrays are described by facts, never by their contents');
{
  assert(scientific.ready !== undefined, `numpy and matplotlib load (got ${JSON.stringify(scientific.messages.at(-1))})`);
  const r = await scientific.run('import numpy as np\nsignal = np.arange(10.0)\nsignal[3] = np.nan\ncounts = np.array([1, 2, 3])');
  assert(/signal: ndarray float64 shape=\(10,\) min=0 max=9 mean=4\.66667 nan=1/.test(r.summary),
    `a float array carries dtype, shape, min, max, mean and NaN count (got ${JSON.stringify(r.summary)})`);
  // int32, not int64: numpy's default integer is a C long, which is 32 bits on
  // wasm32. Code written for a laptop overflows silently here once a value
  // passes 2**31 (np.arange(100_000) ** 2 does), so the dtype in the summary is
  // the one place the model can see which platform its integers are on.
  assert(/counts: ndarray int32 shape=\(3,\) min=1 max=3 mean=2/.test(r.summary),
    'an integer array its range and mean, with the platform\'s own default dtype');

  const empty = await scientific.run('import numpy as np\nnothing = np.array([])');
  assert(/nothing: ndarray float64 shape=\(0,\) empty/.test(empty.summary), 'an empty array says so rather than failing');

  const allNan = await scientific.run('import numpy as np\nblank = np.full(4, np.nan)');
  assert(/blank: ndarray float64 shape=\(4,\) no finite values nan=4/.test(allNan.summary),
    'an all-NaN array is described, rather than raising inside the summary');
}

console.log('\nfigures: attached once, and described in words that outlive the image');
{
  const r = await scientific.run(
    'import matplotlib.pyplot as plt\nimport numpy as np\n' +
      'x = np.arange(10)\nplt.plot(x, x ** 2, label="squares")\nplt.title("growth")\n' +
      'plt.xlabel("n")\nplt.ylabel("n squared")\nplt.legend()\nplt.show()'
  );
  assertEqual(r.status, 'ok', 'the plot runs');
  assertEqual(r.images.length, 1, 'one figure comes back as one image');
  assert(!/non-interactive/.test(r.stderr), "plt.show() under Agg does not leave a warning in stderr");
  assert(
    /1: \d+x\d+ png; axes 1: title='growth' x='n' y='n squared' lines=1 series=\['squares'\] data x=\[0, 9\] y=\[0, 81\]/
      .test(r.summary),
    `the summary describes it: title, labels, series and data range (got ${JSON.stringify(r.summary)})`
  );

  // display(fig) attaches it, and it stays open in pyplot's registry. Collected
  // again at the end of the run, the same plot came back twice.
  const shown = await scientific.run('import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1, 2])\ndisplay(fig)');
  assertEqual(shown.images.length, 1, 'a figure passed to display() is not collected a second time');

  // A run ending in a plotting call returns artists, and printing their reprs
  // says nothing the figure does not.
  const trailing = await scientific.run('import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])');
  assertEqual(trailing.stdout, '', 'a trailing plotting call is not printed');
  assertEqual(trailing.images.length, 1, 'while its figure is still returned');

  const next = await scientific.run('z = 1');
  assertEqual(next.images.length, 0, 'a later run does not inherit an earlier figure');
}

console.log('\nthe summary sees an update made in place, which identity alone cannot');
{
  // signal[3] = nan and signal -= 1 keep the array's id, so a summary built only
  // on identity said nothing about the most common update in data code.
  await scientific.run('import numpy as np\nwave = np.arange(5.0)');
  const subscript = await scientific.run('wave[0] = np.nan');
  assert(/wave: ndarray float64 shape=\(5,\) min=1 max=4 mean=2\.5 nan=1/.test(subscript.summary),
    `a subscript assignment is reported with the new facts (got ${JSON.stringify(subscript.summary)})`);
  const augmented = await scientific.run('wave -= 1');
  assert(/wave: .* min=0 max=3/.test(augmented.summary), 'an augmented assignment in place is reported');

  // A name bound inside a function is local to it. Walking into the body would
  // list the module-level variable of the same name as changed when it was not.
  await plain.run('shadowed = 1');
  const local = await plain.run('def f():\n    shadowed = 2\n    return shadowed\nresult = f()');
  assert(/result: int = 2/.test(local.summary), 'the variable the run did create is listed');
  assert(!/shadowed/.test(local.summary), 'a name assigned only inside a function body is not');

  // Pinned rather than fixed: from syntax alone a mutating method call cannot be
  // told apart from one that reads, so items.append(x) is not detected.
  await plain.run('items = [1]');
  const appended = await plain.run('items.append(2)');
  assert(!/items/.test(appended.summary), 'a mutating method call is not detected (a known limit, pinned here)');
}

console.log('\nfigures that cannot even be enumerated are reported, not dropped');
{
  // The figure is drawn BEFORE enumeration breaks, so the run itself succeeds
  // with a figure open. Breaking it first would fail the run instead: plt.plot
  // on an empty registry calls get_fignums to number the new figure.
  const r = await scientific.run(
    'import matplotlib.pyplot as plt\nplt.plot([4, 5, 6])\n_real_fignums = plt.get_fignums\n' +
      'def _broken():\n    raise RuntimeError("registry unavailable")\n' +
      'plt.get_fignums = _broken'
  );
  assertEqual(r.status, 'ok', 'the run itself succeeded, which is what made the loss silent');
  assert(/\[runtime\] figures could not be collected: RuntimeError/.test(r.stderr),
    `a run that plotted does not come back looking like one that did not (got ${JSON.stringify(r.stderr)})`);
  await scientific.run('plt.get_fignums = _real_fignums\nplt.close("all")');
}

console.log('\nthe import gate tells a refusal from its own failure');
{
  // A module in sys.modules with no __spec__ makes find_spec RAISE ValueError.
  // It is importable, since import returns it from the cache, so it must not be
  // reported as missing.
  await plain.run('import sys, types\nsys.modules["no_spec_module"] = types.ModuleType("no_spec_module")');
  const cached = await plain.run('import no_spec_module');
  assertEqual(cached.status, 'ok', 'a cached module without a spec is importable, not denied');

  // Anything else is an internal failure. Catching it as a refusal would tell
  // the model "this runtime has no weird", confident and wrong.
  await plain.run(
    'import sys\nclass _Faulty:\n    def find_spec(self, name, path=None, target=None):\n' +
      '        if name == "weird":\n            raise RuntimeError("finder bug")\n        return None\n' +
      '_faulty = _Faulty()\nsys.meta_path.insert(0, _faulty)'
  );
  const failing = await plain.run('import weird');
  assertEqual(failing.status, 'error', 'a finder that fails is an error');
  assert(/import check failed/.test(failing.stderr) && !/denied_import/.test(failing.stderr),
    `and is reported as the check failing, not as a missing package (got ${JSON.stringify(failing.stderr)})`);
  await plain.run('sys.meta_path.remove(_faulty)');
}

console.log('\na prelude runs after the seal, as executed code, and a failing one fails the boot');
{
  const primed = await bootRuntime({ prelude: 'import asyncio\nawait asyncio.sleep(0)\nprimed = True\n' });
  assert(primed.ready !== undefined, 'a prelude that runs cleanly lets the runtime come up');
  assert(primed.messages.some((m) => m.type === 'progress' && m.phase === 'prelude'), 'and reports that it ran');
  const later = await primed.run('print(primed)');
  assertEqual(later.stdout, 'True\n', 'what it defines is there for the first execution, top-level await included');
  const readyAt = primed.messages.findIndex((m) => m.type === 'ready');
  const preludeAt = primed.messages.findIndex((m) => m.phase === 'prelude');
  assert(primed.sealed[0].sentBefore <= preludeAt && preludeAt < readyAt,
    'it runs after the seal and before ready, never with more reach than executed code');

  const sealedOut = await bootRuntime({ prelude: 'import js\n' });
  assert(sealedOut.ready === undefined, 'a prelude reaching for a sealed module does not get a runtime');
  const refusal = sealedOut.messages.find((m) => m.type === 'error');
  assert(refusal && /^the community's prelude failed: /.test(refusal.message) && /'js' is not available/.test(refusal.message),
    `and the boot fails saying so (got ${JSON.stringify(refusal && refusal.message)})`);
  assertEqual(refusal && refusal.kind, 'prelude', 'as a prelude failure, not a runtime one');
}

console.log('\na lock overlay may add packages and never replace one');
{
  const shadowed = await bootRuntime({
    lockPackages: { numpy: { ...stockLock.packages.numpy, file_name: 'http://127.0.0.1:1/numpy-0-py3-none-any.whl' } },
  });
  assert(shadowed.ready === undefined, 'an entry that would replace a distribution package does not boot');
  const failure = shadowed.messages.find((m) => m.type === 'error');
  assert(failure && /lock entry numpy would replace the Pyodide distribution's own/.test(failure.message),
    `and the refusal names the entry (got ${JSON.stringify(failure && failure.message)})`);
  assertEqual(failure && failure.kind, 'lock', 'as a lock failure, not one of Pyodide itself');
}

console.log('\na distribution lock that cannot be read or has no packages fails the boot as a lock failure');
{
  const zarr = { name: 'zarr', file_name: '/nowhere/zarr-3.4.0-py3-none-any.whl' };
  const unread = await bootRuntime({
    lockPackages: { zarr },
    readStockLock: async () => {
      throw new TypeError('Failed to fetch');
    },
  });
  const unreadFailure = unread.messages.find((m) => m.type === 'error');
  assertEqual(unread.ready, undefined, 'an unreadable lock does not boot');
  assertEqual(JSON.stringify([unreadFailure && unreadFailure.kind, unreadFailure && unreadFailure.message]),
    JSON.stringify(['lock', "the Pyodide distribution's lock could not be read: Failed to fetch"]),
    'and says it was the distribution\'s lock that could not be read');
  assertEqual(unread.messages.some((m) => m.phase === 'runtime_loaded'), false, 'before Pyodide was ever loaded');

  const empty = await bootRuntime({ lockPackages: { zarr }, readStockLock: async () => ({}) });
  const emptyFailure = empty.messages.find((m) => m.type === 'error');
  assertEqual(JSON.stringify([emptyFailure && emptyFailure.kind, emptyFailure && emptyFailure.message]),
    JSON.stringify(['lock', "the Pyodide distribution's lock has no packages"]), 'a lock with no packages is named for what it is');
}

console.log('\nthe data client reads through fetch, inside the same interpreter');
{
  const server = Bun.serve({ port: 0, fetch: () => new Response('payload-bytes') });
  try {
    const url = `http://127.0.0.1:${server.port}/data`;
    const r = await plain.run(`data = await osa.fetch_bytes(${JSON.stringify(url)})\nprint(len(data), data.decode())`);
    assertEqual(r.status, 'ok', 'top-level await works in executed code');
    assertEqual(r.stdout, '13 payload-bytes\n', 'osa.fetch_bytes returns the bytes the server sent');
  } finally {
    server.stop(true);
  }
}

console.log('\nosa.fetch reads byte ranges, and reports a status rather than raising on it');
{
  // Honors Range the way the data plane does, and records what arrived, so a
  // test can assert what was sent and not only what came back.
  const body = new TextEncoder().encode('0123456789'.repeat(32));
  const seen = [];
  const server = Bun.serve({
    port: 0,
    fetch(request) {
      const range = request.headers.get('range');
      seen.push({ path: new URL(request.url).pathname, range, other: request.headers.get('x-extra') });
      if (new URL(request.url).pathname === '/missing') return new Response('gone', { status: 404 });
      return rangeResponse(body, range);
    },
  });
  const base = `http://127.0.0.1:${server.port}`;
  try {
    const ranged = await plain.run(
      `r = await osa.fetch(${JSON.stringify(`${base}/data`)}, headers={"Range": "bytes=10-19"})\n` +
        'print(r.status, r.body.decode(), isinstance(r, osa.Response))'
    );
    assertEqual(ranged.stdout, '206 0123456789 True\n', 'a closed range comes back as the 206 slice, as an osa.Response');
    assertEqual(seen.at(-1) && seen.at(-1).range, 'bytes=10-19', 'and the Range header reached the server as given');

    const tail = await plain.run(
      `status, body = await osa.fetch(${JSON.stringify(`${base}/data`)}, headers={"range": "bytes=-16"})\nprint(status, len(body))`
    );
    assertEqual(tail.stdout, '206 16\n', 'the suffix form the sharding codec needs works, whatever the header name case');

    const missing = await plain.run(`r = await osa.fetch(${JSON.stringify(`${base}/missing`)})\nprint(r.status, r.body)`);
    assertEqual(missing.status, 'ok', 'an HTTP 404 is not an exception from osa.fetch');
    assertEqual(missing.stdout, "404 b'gone'\n", 'it is a status for the caller to judge, with the body kept');

    const strict = await plain.run(
      `try:\n    await osa.fetch_bytes(${JSON.stringify(`${base}/missing`)})\nexcept OSError as e:\n    print("OSError", e)`
    );
    assertEqual(strict.stdout, `OSError HTTP 404 for ${base}/missing\n`, 'while fetch_bytes, built on it, still raises for one');

    const before = seen.length;
    const extra = await plain.run(
      `try:\n    await osa.fetch(${JSON.stringify(`${base}/data`)}, headers={"X-Extra": "1"})\nexcept ValueError as e:\n    print("ValueError", e)`
    );
    assert(/^ValueError osa\.fetch sends a Range header and no other, not 'X-Extra'/.test(extra.stdout),
      `any header but Range is refused by name (got ${JSON.stringify(extra.stdout)})`);
    const typed = await plain.run(
      `try:\n    await osa.fetch(${JSON.stringify(`${base}/data`)}, headers={"Range": 5})\nexcept TypeError as e:\n    print("TypeError", e)`
    );
    assertEqual(typed.stdout, 'TypeError the Range header must be a str, not int\n', 'a Range value that is not a string is refused');
    assertEqual(seen.length, before, 'and neither refused call reached the network');

    // No response at all, through the host that is refused the way a browser
    // refuses it (see UNREACHABLE). The egress guard's refusal of a URL outside
    // fetch_allow is checked in the browser harness, where the guard is real.
    const lost = await plain.run(
      `try:\n    await osa.fetch(${JSON.stringify(`${UNREACHABLE}x`)})\nexcept OSError as e:\n` +
        '    print(type(e.__cause__).__name__)\n    print(e)'
    );
    const [cause, message] = lost.stdout.split('\n');
    assertEqual(cause, 'JsException', 'a fetch the browser rejects is an OSError, chained to what the browser raised');
    assert(/^no response from http:\/\/unreachable\.invalid\/x: a network failure, a redirect .* or a URL outside fetch_allow\. The browser said: TypeError: Failed to fetch$/.test(message),
      `and it lists what "no response" can mean, since the browser says so little (got ${JSON.stringify(message)})`);
  } finally {
    server.stop(true);
  }
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
