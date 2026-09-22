// The worker core, run against the REAL Pyodide from npm.
//
// Everything here used to be verifiable only in a browser, by hand. Pyodide
// 0.28.3 boots under Bun in well under a second from its npm package, so the
// Python half of the runtime (the import gate, the namespace seal, the data
// client, the execution harness and its summary) now runs in CI against the
// interpreter that ships, not a stand-in.
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
import { buildDataClientSource, buildNamespaceSealSource } from './osa-egress.js';
import { buildHelpersSource, buildOutputCaptureSource, resolveLimits } from './osa-output.js';
import { createWorkerRuntime } from './osa-worker-core.js';

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

/**
 * Boot a runtime exactly as the worker does, recording what it sends and seals.
 */
async function bootRuntime({ preload = [], fetchAllow = ['http://127.0.0.1/allowed/'], limits = {} } = {}) {
  const messages = [];
  const sealed = [];
  const config = {
    indexURL: 'unused-under-npm',
    preload,
    allowInstall: [],
    indexUrls: [],
    fetchAllow,
    python: {
      helpers: buildHelpersSource(),
      outputCapture: buildOutputCaptureSource(resolveLimits(limits)),
      dataClient: buildDataClientSource(),
      namespaceSeal: buildNamespaceSealSource(),
    },
  };
  const runtime = createFromSource(config, {
    load: () => loadPyodide({ packageCacheDir: PACKAGE_CACHE, stdout: () => {}, stderr: () => {} }),
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
  assertEqual(plain.ready && plain.ready.version, '0.28.3', 'and reports the version it booted');
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

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
