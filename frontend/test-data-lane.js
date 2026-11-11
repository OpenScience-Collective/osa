// NEMAR's data lane, end to end on the real Pyodide from npm (#431, step 8).
//
// What is real: NEMAR's config.yaml (preload, prelude), its lock overlay and the
// wheels committed beside it, the worker config the widget builds, the worker
// core as the worker embeds it (stringified), Pyodide's own lock resolution,
// zarr, eegprep-lean and the osa client, reading a sharded Zarr v3 store that the
// same zarr built, over HTTP with byte ranges.
//
// What stands in: the data host. The store is served by Bun.serve on loopback in
// place of zarr.nemar.org, so fetch_allow names the loopback base.
//
// What this cannot check, measured on Pyodide 0.29.5 and covered in headless
// Chrome, in CI, by frontend/browser-harness/chrome.js instead:
// - Wheels by URL. Under Node, Pyodide resolves a lock file_name with
//   path.resolve against its package cache, so an http URL becomes a local path
//   that does not exist; a browser resolves it with new URL. The overlay entries
//   here therefore name the committed wheels by absolute path.
// - Integrity. Node's loader ignores the sha256 digest altogether; only a
//   browser hands it to fetch, which enforces it.
// - The egress guard, which is a worker-global shim (see test-egress.js).
import { loadPyodide } from 'pyodide';
import stockLock from 'pyodide/pyodide-lock.json';
import pyodidePackage from 'pyodide/package.json';
import { buildWorkerConfig } from './osa-runtime.js';
import { createWorkerRuntime } from './osa-worker-core.js';
import { rangeResponse } from './test-support/byte-range.js';

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
  const same = JSON.stringify(actual) === JSON.stringify(expected);
  assert(same, `${msg}${same ? '' : ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`}`);
}

// A suite that hangs reports nothing, so it is bounded as a whole.
const SUITE_TIMEOUT_MS = 240_000;
setTimeout(() => {
  console.error(`\x1b[31m  x FAIL: the suite did not finish within ${SUITE_TIMEOUT_MS / 1000}s.\x1b[0m`);
  process.exit(1);
}, SUITE_TIMEOUT_MS).unref();

const ROOT = new URL('../', import.meta.url);
const PACKAGE_CACHE = new URL('.cache/pyodide-packages/', ROOT).pathname;
const CDN = `https://cdn.jsdelivr.net/pyodide/v${pyodidePackage.version}/full/`;
const COMMUNITY_DIR = new URL('src/assistants/nemar/', ROOT);
const NEMAR = Bun.YAML.parse(await Bun.file(new URL('config.yaml', COMMUNITY_DIR)).text());
// The server resolves a per-deployment value before the widget sees one
// (docs/adr/0013-the-chat-follows-its-deployment.md), so the widget's config here is
// production's, the deployment a local run and CI resolve to. The develop prelude
// boots in a section of its own below.
const RAW_PYTHON = NEMAR.runtime.python;
const forDeployment = (value, deployment) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value[deployment] : value;
const PYTHON = {
  ...RAW_PYTHON,
  prelude: forDeployment(RAW_PYTHON.prelude, 'production'),
  fetch_allow: forDeployment(RAW_PYTHON.fetch_allow, 'production'),
};
const LOCKFILE = new URL(PYTHON.lockfile, COMMUNITY_DIR);
const OVERLAY = JSON.parse(await Bun.file(LOCKFILE).text()).packages;
const WHEELS = new URL('wheels/', LOCKFILE).pathname;

// The overlay as the worker would receive it, but naming each wheel by path; see
// the header for why Node cannot take the URL.
const localPackages = Object.fromEntries(
  Object.entries(OVERLAY).map(([key, entry]) => [key, { ...entry, file_name: WHEELS + entry.file_name }])
);

// Production's python_browser recipe as of nemar-cli v0.10.5: its buildHowTo in
// shared/contract/mcp.ts, copied as text, with the array URL left as a placeholder.
// It leads with open_array and has no read_index or index_url. It stays live until
// nemar-cli's next release replaces it with the level-0 recipe below.
const PYTHON_BROWSER_RECIPE = [
  'from eegprep_lean import open_array  # from the runtime\'s lockfile, not micropip',
  '',
  'arr = await open_array("ARRAY_URL")',
  'window = await arr.getitem((slice(None), slice(start_sample, end_sample)))',
  '# async throughout: the loop is already running, so there is no synchronous form',
  '# physical = digital * scale + offset -- see the recipe\'s scale_offset field',
  '# read_window(index, store, ...) does the same read in physical units, with labels',
].join('\n');

// The ONE canonical snippet in NEMAR's system prompt: the first ```python fence
// after this heading. Extracted from the real prompt text rather than
// hand-copied here, so an edit that breaks the snippet fails this suite instead of
// only failing in a reader's browser.
const PROMPT_HEADING = "## Running code in the reader's browser";

function extractPromptSnippet(promptText) {
  const headingAt = promptText.indexOf(PROMPT_HEADING);
  if (headingAt === -1) return null;
  const afterHeading = promptText.slice(headingAt + PROMPT_HEADING.length);
  const fenceMatch = afterHeading.match(/```python\n([\s\S]*?)\n```/);
  return fenceMatch ? fenceMatch[1] : null;
}

// Whole-word substitution: a placeholder is an identifier-shaped token, and `\b`
// treats `_` as a word character, so DATASET_ID never matches inside a longer name.
// The dataset id every loopback index here names. It is NEMAR's own declared id
// that never resolves (nemar-cli's ABSENT_DATASET_ID, the floor of its reserved
// fixture band), so a read that escaped the loopback would find nothing rather than
// a real or fixture dataset.
const ABSENT_DATASET_ID = 'nm099900';

// A format-3 index.json for one store with one group, served from the loopback
// server, whose contract_base is BASE so it resolves to the store the rest of this
// file builds.
function buildIndexDoc({ datasetId, path, group }) {
  return {
    format: 'nemar-zarr-index',
    format_version: 3,
    dataset_id: datasetId,
    contract_base: BASE,
    data_base: BASE,
    store_count: 1,
    stores: [
      {
        path,
        zarr: 'rec.zarr',
        groups: [
          {
            name: group,
            modality: 'EEG',
            rate: 250.0,
            n_channels: N_CHANNELS,
            n_samples: N_SAMPLES,
            n_view_levels: 0,
          },
        ],
      },
    ],
  };
}

function fillPlaceholders(snippet, substitutions) {
  let filled = snippet;
  for (const [name, value] of substitutions) {
    filled = filled.replace(new RegExp(`\\b${name}\\b`, 'g'), value);
  }
  return filled;
}

// nemar-cli DEV's LEVEL-0 python_browser recipe: buildHowTo's isLevel0 branch in
// shared/contract/mcp.ts, transcribed line for line from nemarOrg/nemar-cli's origin/dev at
// 0.10.6-dev7 (2026-09-22) -- unreleased. This is the recipe #432 exists to guard:
// it leads with read_index(dataset_id, index_url=...), which needs eegprep-lean
// 0.1.0.dev2 or later (see test_nemar_contract_live.py's index_url test). Built the
// same way buildHowTo builds it -- JSON.stringify (`q`) on every value the index
// document supplies -- and filled here for the loopback dataset exactly as
// nemar-cli fills it: contractBase is BASE, so index_url is `${BASE}index.json`.
function nemarCliDevLevel0Recipe({ contractBase, datasetId, storePath, groupName, relativePath }) {
  const q = (value) => JSON.stringify(value);
  const rawRead = [
    `arr = await eegprep_lean.open_array(${q(`${contractBase}${relativePath}`)})`,
    'digital = await arr.getitem((slice(None), slice(start_sample, end_sample)))',
    "# physical = digital * scale + offset -- see the recipe's scale_offset field",
  ];
  return [
    "import eegprep_lean  # from the runtime's lockfile, not micropip",
    '',
    '# index_url needs eegprep-lean 0.1.0.dev2 or later',
    `index = await eegprep_lean.read_index(${q(datasetId)}, index_url=${q(`${contractBase}index.json`)})`,
    `store = index.store(${q(storePath)})`,
    'window = await eegprep_lean.read_window(',
    `    index, store, group=store.group(${q(groupName)}),`,
    '    start_sample=start_sample, n_samples=end_sample - start_sample,',
    ')',
    "# window.data is in physical units (window.unit), one row per channel in window.labels",
    '# async throughout: the loop is already running, so there is no synchronous form',
    '',
    '# the stored digital counts instead, when those are what you need:',
    ...rawRead,
  ].join('\n');
}

console.log('='.repeat(60));
console.log('NEMAR data lane on the real Pyodide');
console.log('='.repeat(60));

console.log('\nevery community runtime resolves against the Pyodide these tests run');
{
  // Every community, not only NEMAR: a runtime pinned to another Pyodide, or an
  // overlay whose dependencies that Pyodide lacks, would pass every test here and
  // fail only in a reader's browser.
  const ASSISTANTS = new URL('src/assistants/', ROOT);
  const checked = [];
  for await (const path of new Bun.Glob('*/config.yaml').scan(ASSISTANTS.pathname)) {
    const config = Bun.YAML.parse(await Bun.file(new URL(path, ASSISTANTS)).text());
    const python = config.runtime && config.runtime.python;
    if (!python) continue;
    const id = config.id;
    checked.push(id);
    assertEqual(python.pyodide_version, pyodidePackage.version,
      `${id} pins the Pyodide these tests run on, the only one CI can vouch for`);
    let overlay = {};
    if (python.lockfile) {
      const lockfile = new URL(python.lockfile, new URL(`${path.split('/')[0]}/`, ASSISTANTS));
      overlay = JSON.parse(await Bun.file(lockfile).text()).packages;
    }
    const known = new Set([...Object.keys(stockLock.packages), ...Object.keys(overlay)]);
    for (const [key, entry] of Object.entries(overlay)) {
      const missing = entry.depends.filter((name) => !known.has(name));
      assertEqual(missing, [], `${id}: every dependency of ${key} is an entry in the distribution or the overlay`);
      assert(!(key in stockLock.packages), `${id}: ${key} adds a package rather than replacing one`);
    }
    for (const name of python.preload || []) {
      assert(known.has(name), `${id}: preload ${name} is something the lock can load`);
    }
    // The config check reads a preload name as its import name (`-` as `_`),
    // having no lock to ask; this asks the lock, so a package whose import name
    // is not its lock name is caught here rather than as a failed boot.
    const entries = { ...stockLock.packages, ...overlay };
    for (const name of python.import_before_seal || []) {
      const root = name.split('.')[0];
      const owner = (python.preload || []).find((p) => p.replace(/-/g, '_') === root);
      assert(owner !== undefined && entries[owner] && entries[owner].imports.includes(root),
        `${id}: import_before_seal ${name} is imported from preload ${owner}, whose lock entry provides ${root}`);
    }
  }
  assert(checked.includes('nemar'), `the search found NEMAR's runtime among ${JSON.stringify(checked)}`);
}

// ---------------------------------------------------------------------------
// The store: built by the same zarr, in a separate unsealed instance, then
// served over HTTP the way the data plane serves it.
// ---------------------------------------------------------------------------

const N_CHANNELS = 3;
const N_SAMPLES = 1000;
const digital = (channel, sample) => channel * N_SAMPLES + sample;
const SCALE = [2.0, 10.0, 100.0];
const OFFSET = [1.0, -5.0, 0.0];
const LABELS = ['E1', 'E2', 'E3'];

// Async throughout, since zarr's synchronous API starts a thread Pyodide cannot.
// Sharded like production (two shards of four inner chunks), so a read takes a
// suffix range for the shard index and a bounded range for the inner chunk.
const BUILD_STORE = `
import numpy as np
import zarr
from zarr.storage import MemoryStore

files = {}
root = await zarr.api.asynchronous.open_group(store=MemoryStore(files), mode="w")
group = await root.create_group("eeg_250hz")
await group.update_attributes({
    "modality": "EEG", "rate": 250.0, "original_rate": 500.0, "n_channels": ${N_CHANNELS},
    "channels": [
        {"label": label, "unit": "uV", "channel_type": "EEG", "row_index": i,
         "original_rate": 500.0, "target_rate": 250.0, "usable_for_inference": True}
        for i, label in enumerate(${JSON.stringify(LABELS)})
    ],
})
array = await group.create_array(
    "0", shape=(${N_CHANNELS}, ${N_SAMPLES}), dtype="int16", chunks=(${N_CHANNELS}, 125), shards=(${N_CHANNELS}, 500)
)
await array.setitem(
    slice(None),
    np.array([[c * ${N_SAMPLES} + s for s in range(${N_SAMPLES})] for c in range(${N_CHANNELS})], dtype=np.int16),
)
await array.update_attributes({
    "physical_formula": "physical = digital * scale + offset",
    "scale": ${JSON.stringify(SCALE)}, "offset": ${JSON.stringify(OFFSET)},
    "level": 0, "rate": 250.0, "source_rate_hz": 500.0,
})
{key: value.to_bytes() for key, value in files.items()}
`;

async function buildStore() {
  const lockFileContents = { info: stockLock.info, packages: { ...stockLock.packages, ...localPackages } };
  const py = await loadPyodide({
    packageCacheDir: PACKAGE_CACHE, lockFileContents, packageBaseUrl: CDN, stdout: () => {}, stderr: () => {},
  });
  await py.loadPackage(['numpy', 'zarr'], { messageCallback: () => {} });
  const proxy = await py.runPythonAsync(BUILD_STORE);
  const files = new Map();
  const converted = proxy.toJs({ dict_converter: Object.fromEntries });
  for (const [key, value] of Object.entries(converted)) files.set(key, Uint8Array.from(value));
  proxy.destroy();
  return files;
}

const STORE = await buildStore();
const seen = [];
// Populated once BASE is known (a format-3 index.json is keyed by dataset_id and
// served from this same loopback server; see "the prompt's own python snippet").
const INDEX_DOCS = new Map();
// nemar-cli's own index_url convention (buildHowTo: `${contract_base}index.json`,
// flat, with no dataset segment -- contract_base already carries the dataset in a
// real deployment's path). Populated by "nemar-cli dev's level-0 recipe" below.
let LEVEL0_INDEX_DOC = null;
const server = Bun.serve({
  port: 0,
  fetch(request) {
    const { pathname } = new URL(request.url);
    const range = request.headers.get('range');
    seen.push({ pathname, range });
    if (pathname === '/index.json') {
      if (LEVEL0_INDEX_DOC === null) return new Response('Not Found', { status: 404 });
      return new Response(JSON.stringify(LEVEL0_INDEX_DOC), {
        headers: { 'content-type': 'application/json' },
      });
    }
    const indexMatch = pathname.match(/^\/([^/]+)\/zarr\/index\.json$/);
    if (indexMatch) {
      const doc = INDEX_DOCS.get(indexMatch[1]);
      if (doc === undefined) return new Response('Not Found', { status: 404 });
      return new Response(JSON.stringify(doc), { headers: { 'content-type': 'application/json' } });
    }
    // /ignores-range/ serves the same store answering every request in full,
    // which is what a misconfigured host does and what the reader must refuse.
    const honorsRange = !pathname.startsWith('/ignores-range/');
    const key = pathname.replace(/^\/(ignores-range\/)?rec\.zarr\//, '');
    const body = STORE.get(key);
    if (body === undefined) return new Response('Not Found', { status: 404 });
    return rangeResponse(body, honorsRange ? range : null);
  },
});
const BASE = `http://127.0.0.1:${server.port}/`;
const ARRAY_URL = `${BASE}rec.zarr/eeg_250hz/0`;

// ---------------------------------------------------------------------------
// The runtime, configured as the widget configures it from NEMAR's /config.
// ---------------------------------------------------------------------------

const messages = [];
const sealed = [];
const config = buildWorkerConfig(
  { ...PYTHON, fetch_allow: [BASE] },
  { packages: OVERLAY, baseUrl: 'https://osa.example/nemar/runtime/' }
);
const createFromSource = new Function(`return (${createWorkerRuntime.toString()});`)();
const runtime = createFromSource({ ...config, lockPackages: localPackages }, {
  load: (indexURL, options) =>
    loadPyodide({ packageCacheDir: PACKAGE_CACHE, stdout: () => {}, stderr: () => {}, ...options }),
  stockLock: async () => stockLock,
  seal: (prefixes) => sealed.push(prefixes),
  send: (message) => messages.push(message),
});

let seq = 0;
async function run(code) {
  const from = messages.length;
  await runtime.handle({ type: 'execute', call_id: `lane-${++seq}`, code });
  return messages.slice(from).find((m) => m.type === 'result');
}

try {
  console.log('\nNEMAR\'s runtime boots with its overlay and its prelude');
  {
    assertEqual(Object.keys(config.lockPackages).sort(), Object.keys(OVERLAY).sort(),
      'the widget\'s worker config carries every overlay entry');
    assertEqual(config.lockPackages.zarr.file_name, 'https://osa.example/nemar/runtime/zarr-3.4.0-py3-none-any.whl',
      'each resolved to its wheel route on the API that sent it');
    assertEqual(config.prelude, PYTHON.prelude, 'and the prelude as NEMAR wrote it');

    const started = performance.now();
    await runtime.handle({ type: 'boot' });
    const ready = messages.find((m) => m.type === 'ready');
    assert(ready !== undefined,
      `it boots (got ${JSON.stringify(messages.find((m) => m.type === 'error') || messages.at(-1))})`);
    console.log(`      booted in ${Math.round(performance.now() - started)} ms`);
    const loaded = messages.filter((m) => m.phase === 'loading_package').map((m) => m.package);
    assertEqual(loaded, PYTHON.preload, 'loading each preload package in order');
    const imported = messages.filter((m) => m.phase === 'importing').map((m) => m.module);
    assertEqual(imported, PYTHON.import_before_seal, 'importing each import_before_seal module in order');
    assert(messages.some((m) => m.phase === 'prelude'), 'and running the prelude');
    assertEqual(sealed, [[BASE]], 'sealed to fetch_allow');

    const versions = await run('import zarr, eegprep_lean\nprint(zarr.__version__, eegprep_lean.__version__)');
    assertEqual(versions.stdout, `${OVERLAY.zarr.version} ${OVERLAY['eegprep-lean'].version}\n`,
      'the versions running are the ones the overlay pins');
    const transport = await run('import eegprep_lean\nprint(type(eegprep_lean.default_transport()).__name__)');
    assertEqual(transport.stdout, 'FetchTransport\n', 'the prelude made the runtime\'s own client eegprep-lean\'s default');
  }

  console.log('\nthe develop prelude boots too, and points read_index at the staging host');
  {
    // Compiled by the config check, but never run until now: a prelude that
    // compiles can still fail under the seal, and then every staging reader's
    // runtime fails to boot.
    const developMessages = [];
    const developConfig = buildWorkerConfig(
      { ...PYTHON, prelude: forDeployment(RAW_PYTHON.prelude, 'develop'), fetch_allow: [BASE] },
      { packages: OVERLAY, baseUrl: 'https://osa.example/nemar/runtime/' }
    );
    assert(developConfig.prelude !== config.prelude, 'the develop prelude is not production\'s');
    const develop = createFromSource({ ...developConfig, lockPackages: localPackages }, {
      load: (indexURL, options) =>
        loadPyodide({ packageCacheDir: PACKAGE_CACHE, stdout: () => {}, stderr: () => {}, ...options }),
      stockLock: async () => stockLock,
      seal: () => {},
      send: (message) => developMessages.push(message),
    });
    await develop.handle({ type: 'boot' });
    assert(
      developMessages.some((m) => m.type === 'ready'),
      `it boots (got ${JSON.stringify(developMessages.find((m) => m.type === 'error') || developMessages.at(-1))})`
    );
    await develop.handle({
      type: 'execute',
      call_id: 'develop-1',
      code:
        'import eegprep_lean, eegprep_lean.index\n' +
        'print(type(eegprep_lean.default_transport()).__name__, eegprep_lean.index.INDEX_URL_TEMPLATE)',
    });
    const shown = developMessages.find((m) => m.type === 'result');
    assertEqual(
      shown && shown.stdout,
      'FetchTransport https://zarr-test.nemar.org/{dataset_id}/zarr/index.json\n',
      'with the runtime\'s own client as the default, and read_index on zarr-test.nemar.org'
    );
  }

  console.log('\nSciPy under the seal: every public module imports, and ctypes stays refused (#495)');
  {
    // Walked from SciPy's own package tree rather than listed here, so a SciPy
    // upgrade that adds a module importing ctypes at its top level fails this
    // check instead of a reader's analysis.
    const walked = await run(`import importlib, json, pkgutil, scipy
names = sorted(
    info.name for info in pkgutil.walk_packages(scipy.__path__, "scipy.")
    if not any(part.startswith("_") or part in ("tests", "conftest") for part in info.name.split("."))
)
failed = {}
for name in names:
    try:
        importlib.import_module(name)
    except Exception as err:
        failed[name] = f"{type(err).__name__}: {err}"[:160]
print(json.dumps({"names": names, "failed": failed, "subpackages": list(scipy.submodules)}))
`);
    assertEqual(walked.status, 'ok', `the walk runs (stderr: ${walked.stderr.slice(-300)})`);
    const tree = JSON.parse(walked.stdout || '{"names": [], "failed": {}, "subpackages": []}');
    assert(tree.names.length > 50 && tree.subpackages.every((sub) => tree.names.includes(`scipy.${sub}`)),
      `it covers every subpackage SciPy lists and their public modules (${tree.names.length} modules)`);
    assertEqual(tree.failed, {}, 'and every one of them imports in the sealed runtime');

    const statement = await run('import ctypes');
    assertEqual(statement.stderr, 'denied_import: ctypes', 'import ctypes is still denied to executed code');
    const dynamic = await run('import importlib\nimportlib.import_module("ctypes")');
    assert(/ImportError: 'ctypes' is not available to executed code/.test(dynamic.stderr),
      `and so is importlib.import_module("ctypes") (got ${JSON.stringify(dynamic.stderr.slice(0, 100))})`);
    // The cost docs/community-browser-runtime.md states: SciPy's own module
    // keeps the ctypes it imported. Asserted so the sentence stays true, and so
    // a SciPy that stops holding one says the sentence can go.
    const reference = await run('import scipy._lib._ccallback as cc\nprint(cc.ctypes.__name__)');
    assertEqual(reference.stdout, 'ctypes\n', 'the documented cost: scipy._lib._ccallback keeps its own ctypes');

    // The control: NEMAR's runtime without import_before_seal is the runtime
    // #495 found, where SciPy refuses to import. If a later SciPy stops
    // importing ctypes at its top level, this says so and the key can go.
    const bareMessages = [];
    const bare = createFromSource({ ...config, importBeforeSeal: [], lockPackages: localPackages }, {
      load: (indexURL, options) =>
        loadPyodide({ packageCacheDir: PACKAGE_CACHE, stdout: () => {}, stderr: () => {}, ...options }),
      stockLock: async () => stockLock,
      seal: () => {},
      send: (message) => bareMessages.push(message),
    });
    await bare.handle({ type: 'boot' });
    assert(bareMessages.some((m) => m.type === 'ready'), 'control: the runtime boots without import_before_seal');
    await bare.handle({ type: 'execute', call_id: 'bare-1', code: 'from scipy import signal' });
    const broken = bareMessages.find((m) => m.type === 'result');
    assert(broken && /The `scipy` install you are using seems to be broken/.test(broken.stderr),
      `control: and there SciPy does not import under the seal (got ${JSON.stringify(broken && broken.stderr.slice(0, 120))})`);
  }

  console.log('\nthe python_browser recipe runs as nemar_read_window hands it out');
  {
    seen.length = 0;
    const code = 'start_sample, end_sample = 10, 13\n' + PYTHON_BROWSER_RECIPE.replace('ARRAY_URL', ARRAY_URL) +
      '\nimport json\nprint(json.dumps({"dtype": str(window.dtype), "values": window.tolist()}))';
    const result = await run(code);
    assertEqual(result.status, 'ok', `it runs (stderr: ${result.stderr.slice(-300)})`);
    const expected = [0, 1, 2].map((c) => [10, 11, 12].map((s) => digital(c, s)));
    assertEqual(JSON.parse(result.stdout || 'null'), { dtype: 'int16', values: expected },
      'and reads exactly the stored counts');
    assert(seen.some((r) => r.range && r.range.startsWith('bytes=-')), 'the shard index was read with a suffix range');
    assert(seen.some((r) => r.range && /^bytes=\d+-\d+$/.test(r.range)), 'and the inner chunk with a bounded range');
  }

  console.log("\nnemar-cli dev's level-0 recipe, as its buildHowTo writes it (read_index, index_url)");
  {
    const LEVEL0_DATASET_ID = ABSENT_DATASET_ID;
    const LEVEL0_STORE_PATH = 'sub-01/eeg/sub-01_task-test_eeg.set';
    const LEVEL0_GROUP = 'eeg_250hz';
    const LEVEL0_RELATIVE_PATH = 'rec.zarr/eeg_250hz/0';

    // nemar-cli's own index_url convention: BASE + "index.json", flat, no dataset
    // segment (see the server's `pathname === '/index.json'` route above).
    LEVEL0_INDEX_DOC = buildIndexDoc({ datasetId: LEVEL0_DATASET_ID, path: LEVEL0_STORE_PATH, group: LEVEL0_GROUP });

    const recipe = nemarCliDevLevel0Recipe({
      contractBase: BASE,
      datasetId: LEVEL0_DATASET_ID,
      storePath: LEVEL0_STORE_PATH,
      groupName: LEVEL0_GROUP,
      relativePath: LEVEL0_RELATIVE_PATH,
    });

    seen.length = 0;
    const code =
      'start_sample, end_sample = 5, 8\n' +
      recipe +
      '\nimport json\n' +
      'print(json.dumps({"shape": list(window.data.shape), "unit": window.unit, "rate": window.rate, ' +
      '"labels": list(window.labels or ()), "data": window.data.tolist(), "digital": digital.tolist()}))';
    const result = await run(code);
    assertEqual(result.status, 'ok', `it runs (stderr: ${result.stderr.slice(-300)})`);
    assert(seen.some((r) => r.pathname === '/index.json'), "index_url landed on contract_base + 'index.json', as nemar-cli fills it");

    const info = JSON.parse(result.stdout || 'null') || {};
    assertEqual(info.shape, [N_CHANNELS, 3], 'physical shape (3 channels, 3 samples), from start_sample=5, end_sample=8');
    assertEqual(info.unit, 'uV', "unit uV, from the store's channel metadata");
    assertEqual(info.rate, 250, 'rate 250, from the channel group');
    assertEqual(info.labels, LABELS, 'labels E1, E2, E3');
    const expectedPhysical = [0, 1, 2].map((c) => [5, 6, 7].map((s) => digital(c, s) * SCALE[c] + OFFSET[c]));
    assertEqual(info.data, expectedPhysical,
      'the primary read (read_window): physical values equal digital * scale + offset');
    const expectedDigital = [0, 1, 2].map((c) => [5, 6, 7].map((s) => digital(c, s)));
    assertEqual(info.digital, expectedDigital,
      'the raw read the recipe keeps (open_array/getitem): the stored digital counts, unconverted');
  }

  const INDEX = `
from eegprep_lean import read_window
from eegprep_lean.index import ChannelGroup, DatasetIndex, Store

index = DatasetIndex(
    dataset_id="${ABSENT_DATASET_ID}", format_version=3, contract_base=${JSON.stringify(BASE)}, store_count=1,
    stores=(Store(path="sub-01/eeg/sub-01_task-test_eeg.set", zarr="rec.zarr", groups=(
        ChannelGroup(name="eeg_250hz", modality="EEG", rate=250.0, n_channels=${N_CHANNELS},
                     n_samples=${N_SAMPLES}, n_view_levels=0),)),),
)
window = await read_window(index, index.stores[0], start_sample=0, n_samples=2)
`;

  console.log('\nread_window returns physical units, with the labels the store declares');
  {
    const result = await run(
      INDEX + 'import json\nprint(json.dumps({"data": window.data.tolist(), "labels": list(window.labels), "unit": window.unit}))'
    );
    assertEqual(result.status, 'ok', `it runs (stderr: ${result.stderr.slice(-300)})`);
    const physical = [0, 1, 2].map((c) => [0, 1].map((s) => digital(c, s) * SCALE[c] + OFFSET[c]));
    assertEqual(JSON.parse(result.stdout || 'null'), { data: physical, labels: LABELS, unit: 'uV' },
      'each channel converted with its own scale and offset, labeled and in the unit the group declares');
  }

  console.log('\na plot comes back as an image when its figure is handed to display');
  {
    const result = await run(INDEX + 'import eegprep_lean\ndisplay(eegprep_lean.plot_window(window).figure)');
    assertEqual(result.status, 'ok', `it runs (stderr: ${result.stderr.slice(-300)})`);
    assertEqual(result.images.length, 1, 'one image');
    assertEqual(result.images[0] && result.images[0].mime, 'image/png', 'a PNG');
    const bare = await run(INDEX + 'import eegprep_lean\nax = eegprep_lean.plot_window(window)');
    assertEqual(bare.images.length, 0,
      'while a figure left undisplayed is not collected, since it never joined pyplot, which is why the tool says to display it');
  }

  console.log('\na host that answers a range in full is refused, not sliced');
  {
    const result = await run(
      `from eegprep_lean import open_array\narr = await open_array(${JSON.stringify(`${BASE}ignores-range/rec.zarr/eeg_250hz/0`)})\n` +
        'await arr.getitem((slice(None), slice(0, 4)))'
    );
    assertEqual(result.status, 'error', 'the read fails');
    assert(/TransportError/.test(result.stderr) && /answered in full/.test(result.stderr),
      `as a TransportError saying why (got ${JSON.stringify(result.stderr.slice(0, 200))})`);
  }

  console.log("\nthe prompt's own python snippet is executable, through eegprep-lean's real index fetch");
  {
    const snippet = extractPromptSnippet(NEMAR.system_prompt);
    assert(
      snippet !== null,
      `NEMAR's system_prompt has a \`\`\`python fence after the heading ${JSON.stringify(PROMPT_HEADING)}`
    );

    if (snippet !== null) {
      const SNIPPET_DATASET_ID = ABSENT_DATASET_ID;
      const SNIPPET_PATH = 'sub-01/eeg/sub-01_task-test_eeg.set';
      const SNIPPET_GROUP = 'eeg_250hz';
      const READ_N_SAMPLES = 100;
      // DATASET_ID, RECORDING_PATH and GROUP sit inside quotes the snippet already
      // has (e.g. `read_index("DATASET_ID")`), so the substitution is the bare
      // value, not a re-quoted one -- JSON.stringify here would double the quotes.
      const substitutions = [
        ['DATASET_ID', SNIPPET_DATASET_ID],
        ['RECORDING_PATH', SNIPPET_PATH],
        ['GROUP', SNIPPET_GROUP],
        ['START_SAMPLE', '0'],
        ['N_SAMPLES', String(READ_N_SAMPLES)],
        ['CHANNELS', '[0, 1, 2]'],
      ];
      const filled = fillPlaceholders(snippet, substitutions);
      for (const [name] of substitutions) {
        assert(!new RegExp(`\\b${name}\\b`).test(filled), `no ${name} placeholder token remains after substitution`);
      }

      // A real format-3 index, served from this same loopback server. contract_base
      // is BASE itself, so eegprep-lean's level0_url resolves to the existing
      // rec.zarr/eeg_250hz/0 array the rest of this file already reads.
      INDEX_DOCS.set(SNIPPET_DATASET_ID, buildIndexDoc({ datasetId: SNIPPET_DATASET_ID, path: SNIPPET_PATH, group: SNIPPET_GROUP }));

      seen.length = 0;
      const pointAtLoopback = await run(
        'import eegprep_lean.index as _index\n' +
          `_index.INDEX_URL_TEMPLATE = ${JSON.stringify(`${BASE}{dataset_id}/zarr/index.json`)}\n`
      );
      assertEqual(
        pointAtLoopback.status,
        'ok',
        `pointing eegprep-lean at the loopback index, in a run() of its own (stderr: ${pointAtLoopback.stderr.slice(-300)})`
      );

      const result = await run(filled);
      assertEqual(result.status, 'ok', `the prompt's own snippet runs unchanged (stderr: ${result.stderr.slice(-300)})`);
      assert(
        seen.some((r) => r.pathname === `/${SNIPPET_DATASET_ID}/zarr/index.json`),
        'the server saw the index.json request the snippet made'
      );

      // A follow-up run() in the same namespace: `window` survives from the run above.
      const printed = await run(
        'import json\n' +
          'print(json.dumps({"shape": list(window.data.shape), "unit": window.unit, "rate": window.rate, ' +
          '"labels": list(window.labels or ()), "data": window.data.tolist()}))'
      );
      assertEqual(
        printed.status,
        'ok',
        `printing window.data from a follow-up run() in the same namespace (stderr: ${printed.stderr.slice(-300)})`
      );
      const info = JSON.parse(printed.stdout || 'null') || {};
      assertEqual(info.shape, [3, READ_N_SAMPLES], 'shape (3, 100), from three CHANNELS and N_SAMPLES=100');
      assertEqual(info.unit, 'uV', "unit uV, from the store's channel metadata");
      assertEqual(info.rate, 250, 'rate 250, from the channel group');
      assertEqual(info.labels, LABELS, 'labels E1, E2, E3');
      const expectedData = [0, 1, 2].map((c) =>
        Array.from({ length: READ_N_SAMPLES }, (_, s) => digital(c, s) * SCALE[c] + OFFSET[c])
      );
      assertEqual(info.data, expectedData, 'physical values equal digital * scale + offset, per channel');
      assertEqual(result.images.length, 1, "exactly one PNG image in the snippet's own result");
      assertEqual(result.images[0] && result.images[0].mime, 'image/png', 'and it is a PNG');
    }
  }

  // The procedure documents' own blocks (#719), run as the documents write them: a model
  // fetches a document with get_procedure and copies its blocks, so a filter that stops
  // filtering or a spectrum call this runtime refuses would reach every figure it draws.
  // The first python fence of the document the community declares under `id`; `before` is
  // the text ahead of it, which names what the block is for.
  const procedureBlock = async (id) => {
    const entry = (NEMAR.procedures || []).find((p) => p.id === id);
    if (!entry) return { fence: null, code: '', before: '' };
    const document = await Bun.file(new URL(entry.file, COMMUNITY_DIR)).text();
    const fence = document.match(/```python\n([\s\S]*?)\n\s*```/);
    if (!fence) return { fence: null, code: '', before: '' };
    const lines = fence[1].split('\n');
    const indent = Math.min(...lines.filter((l) => l.trim()).map((l) => l.match(/^ */)[0].length));
    return { fence, code: lines.map((l) => l.slice(indent)).join('\n'), before: document.slice(0, fence.index) };
  };
  const indented = (code) => code.split('\n').map((l) => `    ${l}`).join('\n');

  console.log('\nthe ERP procedure\'s low-pass keeps 5 Hz and removes the high tone, at 60 to 5000 Hz');
  {
    const { fence, code, before } = await procedureBlock('erp-by-condition');
    assert(fence !== null, 'the ERP procedure carries a python block');
    // The block belongs to the sentence that introduces it, not to a later section.
    assert(before.trimEnd().endsWith('the whole channels-by-samples array:'), 'it follows the sentence that introduces the filter');
    assert(before.includes('low-pass at 30 Hz'), 'and that sentence is the 30 Hz low-pass');
    if (fence) {
      // Defined as a function so each rate runs the block exactly as written, with
      // only `rate` and `x` bound, the two names the prompt says it takes.
      const result = await run(`import json
import numpy as np

def prompt_lowpass(x, rate):
${indented(code)}
    return filtered, cutoff

def tone(y, t, hz):
    # The amplitude of one frequency in y, by projection.
    return float(2 * abs(np.mean(y * np.exp(-2j * np.pi * hz * t))))

out = []
for rate in (60.0, 125.0, 250.0, 1000.0, 5000.0):
    t = np.arange(int(8 * rate)) / rate
    high = min(50.0, 0.45 * rate)
    slow = np.sin(2 * np.pi * 5 * t)
    x = slow + np.sin(2 * np.pi * high * t)
    filtered, cutoff = prompt_lowpass(x, rate)
    both, _ = prompt_lowpass(np.vstack([x, 2 * x]), rate)
    edge = int(rate)
    inner = slice(edge, -edge)
    out.append({
        "rate": rate, "high": high, "cutoff": float(cutoff),
        "gain_5": tone(filtered[inner], t[inner], 5.0),
        "gain_high": tone(filtered[inner], t[inner], high),
        "residual": float(np.max(np.abs(filtered[inner] - slow[inner]))),
        "vs_rows": float(max(np.max(np.abs(both[0] - filtered)), np.max(np.abs(both[1] - 2 * filtered)))),
    })
print(json.dumps(out))
`);
      assertEqual(result.status, 'ok', `it runs (stderr: ${result.stderr.slice(-300)})`);
      for (const r of JSON.parse(result.stdout || '[]')) {
        const at = `at ${r.rate} Hz (cutoff ${r.cutoff} Hz)`;
        assert(Math.abs(r.gain_5 - 1) < 0.01, `${at}: unity gain at 5 Hz (${r.gain_5})`);
        assert(r.gain_high < 0.02, `${at}: under 2% at ${r.high} Hz (${r.gain_high})`);
        assert(r.residual < 0.03,
          `${at}: 5 Hz plus ${r.high} Hz comes out as the 5 Hz sine, unshifted (largest difference ${r.residual})`);
        assert(r.vs_rows < 1e-9, `${at}: a channels-by-samples array filters row by row (${r.vs_rows})`);
      }
    }
  }

  console.log('\nthe spectrum procedure reads the recording in pieces, one channel at a time, and averages them over the whole recording');
  {
    const { fence, code } = await procedureBlock('power-spectrum');
    assert(fence !== null, 'the spectrum procedure carries a python block');
    // The recipe takes its piece from the reader's budget as well as from this runtime's
    // limit, and the document has the model write that budget in. The block runs as written,
    // and again with another budget on that line; a swap that found nothing to replace
    // would run the same code twice, so the line is checked.
    // The recipe prints a line of its own (how many segments and seconds it covered), so what a
    // run reports as data is its last line.
    const lastJson = (stdout, fallback) => JSON.parse((stdout || '').trim().split('\n').at(-1) || fallback);
    const BUDGET_LINE = 'budget_mb = 512';
    assert(code.includes(BUDGET_LINE), `the block names the reader's budget on one line ("${BUDGET_LINE}")`);
    // The block opens its own read, so a fresh execute_code can copy it alone: the three
    // lines the model fills in with the dataset and the recording. Here they give way to a
    // pass, since the recording is held in memory and the stand-in library is handed in.
    const SETUP = 'import eegprep_lean\nindex = await eegprep_lean.read_index("DATASET_ID")\nstore = index.store("RECORDING_PATH")';
    assert(code.includes(`${SETUP}\ngroup = store.group("GROUP")`), 'the block opens its own index and store, so it stands alone');
    if (fence && code.includes(BUDGET_LINE) && code.includes(SETUP)) {
      // The block as an async function over a recording held in memory. The only thing that
      // stands in is its read: `eegprep_lean.read_window` slices an array here (and records
      // each read), where the shipped runtime reads NEMAR's Zarr store, which the checks
      // above this one cover. Everything the block does with the data, scipy.signal.welch
      // included, is real, and a by-hand Welch estimate that shares no code with
      // scipy.signal.welch is the reference: every segment is cut, detrended, windowed and
      // transformed with numpy alone.
      const preamble = `import json
import numpy as np
from scipy import signal

BLOCK = ${JSON.stringify(code.replace(SETUP, 'pass  # the runtime opens the index and the store'))}
BUDGET_LINE = ${JSON.stringify(BUDGET_LINE)}

class Window:
    def __init__(self, data, labels):
        self.data, self.labels = data, labels

class Group:
    def __init__(self, rate, n_samples, n_channels):
        self.rate, self.n_samples, self.n_channels = rate, n_samples, n_channels

class Store:
    def __init__(self, group):
        self._group = group
    def group(self, name):
        return self._group

class Lib:
    def __init__(self, eeg, labels):
        self.eeg, self.labels, self.reads = eeg, labels, []
    async def read_window(self, index, store, group, start_sample, n_samples):
        self.reads.append((start_sample, n_samples))
        return Window(self.eeg[:, start_sample:start_sample + n_samples], self.labels)

async def spectrum_with(eeg, rate, budget=None, labels=None):
    # The prompt's block, with a budget swapped onto its budget line (None runs it as
    # written), over eeg as the recording of one channel group.
    labels = labels or [f"E{i}" for i in range(eeg.shape[0])]
    text = BLOCK if budget is None else BLOCK.replace(BUDGET_LINE, f"budget_mb = {budget}")
    body = "\\n".join("    " + line for line in text.split("\\n"))
    scope = {}
    exec("async def prompt_spectrum(eegprep_lean, index, store):\\n" + body
         + "\\n    return dict(freqs=freqs, power=power, total=total, used=used, m=m, piece=piece, keep=keep)",
         {"np": np}, scope)
    lib = Lib(eeg, labels)
    out = await scope["prompt_spectrum"](lib, None, Store(Group(rate, eeg.shape[1], eeg.shape[0])))
    out["reads"] = lib.reads
    return out

def by_hand(x, rate):
    # Welch's estimate segment by segment: constant detrend, Hann window, density scaling,
    # one-sided, the mean of every segment's periodogram.
    nperseg = int(2 * rate)
    noverlap = nperseg // 2
    hop = nperseg - noverlap
    win = signal.get_window("hann", nperseg)
    scale = 1.0 / (rate * (win * win).sum())
    n_seg = (x.size - noverlap) // hop
    acc = np.zeros(nperseg // 2 + 1)
    for first in range(0, n_seg, 400):
        k = min(400, n_seg - first)
        segs = np.stack([x[(first + i) * hop:(first + i) * hop + nperseg] for i in range(k)])
        segs = segs - segs.mean(axis=1, keepdims=True)
        spec = np.abs(np.fft.rfft(segs * win, axis=1)) ** 2 * scale
        if nperseg % 2 == 0:
            spec[:, 1:-1] *= 2
        else:
            spec[:, 1:] *= 2
        acc += spec.sum(axis=0)
    return acc / n_seg, n_seg

def refused(x, rate):
    try:
        signal.welch(x, fs=rate, nperseg=int(2 * rate), axis=-1)
        return "ok"
    except ValueError as e:
        return str(e)

def relative(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return float(np.max(np.abs(a - b) / np.abs(b)))
`;

      // (a) A signal that fits one welch call: the pieces' average is that call's, to 1e-9, at
      // rates with an even and an odd segment length, with one piece and with many. One channel
      // of each is an electrooculography channel, which the block leaves out by its label.
      {
        const result = await run(`${preamble}
out = []
rng = np.random.default_rng(1)
for rate, channels, seconds in ((250.0, 4, 150), (128.5, 3, 120), (1000.0, 2, 60)):
    t = np.arange(int(seconds * rate)) / rate
    eeg = rng.standard_normal((channels, t.size)) + 3 * np.sin(2 * np.pi * 10 * t)
    labels = [f"E{i}" for i in range(channels - 1)] + ["VEOG_lower"]
    nperseg = int(2 * rate)
    whole = np.array([signal.welch(ch, fs=rate, nperseg=nperseg)[1] for ch in eeg[:-1]])
    whole_freqs = signal.welch(eeg[0], fs=rate, nperseg=nperseg)[0]
    for budget in (None, 1):
        # None: the block as written. 1: a 1 MB budget, which cuts the read into pieces.
        r = await spectrum_with(eeg, rate, budget, labels)
        hop = nperseg - nperseg // 2
        out.append({
            "rate": rate, "nperseg": nperseg, "samples": int(t.size), "budget": budget, "m": int(r["m"]), "total": int(r["total"]),
            "used": int(r["used"]), "pieces": len(r["reads"]), "channels": channels, "rows": int(r["power"].shape[0]),
            "keep": r["keep"], "difference": relative(r["power"], whole), "freqs_equal": bool(np.array_equal(r["freqs"], whole_freqs)),
            "by_hand": max(relative(r["power"][c], by_hand(eeg[c], rate)[0]) for c in range(channels - 1)),
            "starts_on_hops": all(start == i * int(r["m"]) * hop for i, (start, n) in enumerate(r["reads"])),
            "longest_read": max(n for start, n in r["reads"]),
            "read_bytes": max(n for start, n in r["reads"]) * channels * 8,
        })
print(json.dumps(out))
`);
        assertEqual(result.status, 'ok', `pieces against one call: it runs (stderr: ${result.stderr.slice(-400)})`);
        const rows = lastJson(result.stdout, '[]');
        assert(rows.length === 6, `six runs: three rates, with and without a small budget (got ${rows.length})`);
        for (const r of rows) {
          const at = `${r.rate} Hz, nperseg ${r.nperseg}, ${r.samples} samples, ${r.budget === null ? 'the block as written' : `a ${r.budget} MB budget`}`;
          assert(r.difference < 1e-9, `${at}: equals one welch call per channel (largest relative difference ${r.difference})`);
          assert(r.freqs_equal, `${at}: the same frequencies`);
          assert(r.by_hand < 1e-9, `${at}: and a by-hand estimate that shares no code with welch (${r.by_hand})`);
          assertEqual(r.keep, Array.from({ length: r.channels - 1 }, (_, i) => i), `${at}: the electrooculography channel is left out by its label`);
          assertEqual(r.rows, r.channels - 1, `${at}: one row of power per EEG channel`);
          assertEqual(r.used, r.total, `${at}: all ${r.total} segments were averaged, on every channel (not the first piece's)`);
          assert(r.starts_on_hops, `${at}: each piece starts at a multiple of m * hop`);
          if (r.budget === 1) {
            assert(r.pieces > 1, `${at}: the small budget made ${r.pieces} pieces, so the carried sums were exercised`);
            assert(r.read_bytes <= 0.25 * 2 ** 20, `${at}: no read passes a quarter of the budget (${r.read_bytes} bytes)`);
          } else {
            assert(r.pieces === 1, `${at}: this read fits one piece (${r.pieces})`);
          }
        }
      }

      // (b) Fifty minutes at 250 Hz, which one welch call refuses, read in pieces of a small
      // budget: every segment is counted, and the average is a by-hand estimate over all of
      // them, not over the first `2**28 // nperseg` samples a capped read would take (536,870 of
      // these 750,000).
      {
        const MINUTES = 50;
        const result = await run(`${preamble}
rate = 250.0
n = int(${MINUTES} * 60 * rate)
rng = np.random.default_rng(2)
t = np.arange(n) / rate
# A tone carried only by the last tenth of the recording, so a read that stopped early
# shows it missing.
tail = (t > 0.9 * t[-1]).astype(float)
eeg = rng.standard_normal((3, n)) + 4 * np.sin(2 * np.pi * 10 * t) * tail
nperseg = int(2 * rate)
hop = nperseg // 2
# 64 MB: the recording is read in several pieces, each of which fits the budget.
r = await spectrum_with(eeg, rate, 64)
freqs, power, total = r["freqs"], r["power"], r["total"]
by = [by_hand(eeg[c], rate) for c in range(3)]
expected_segments = (n - nperseg // 2) // hop
most = 2**28 // nperseg
capped, _ = by_hand(eeg[0, :most], rate)
peak = int(np.argmax(power[0]))
reads = r["reads"]
print(json.dumps({
    "samples": n, "most": most, "m": int(r["m"]), "total": int(total), "expected": expected_segments, "used": int(r["used"]),
    "pieces": len(reads), "by_hand_segments": by[0][1], "rows": int(power.shape[0]),
    "difference": max(relative(power[c], by[c][0]) for c in range(3)),
    "tone_hz": float(freqs[peak]), "tone_over_capped": float(power[0][peak] / capped[peak]),
    "whole_call": refused(eeg[0], rate),
    "covered_seconds": float((total * hop + hop) / rate),
    "read_to": max(start + n_ for start, n_ in reads), "longest_read_mb": max(n_ for start, n_ in reads) * 3 * 8 / 2**20,
}))
`);
        assertEqual(result.status, 'ok', `fifty minutes at 250 Hz: it runs (stderr: ${result.stderr.slice(-400)})`);
        const r = lastJson(result.stdout, '{}');
        assert(r.samples > r.most, `the recording is longer than one welch call takes (${r.samples} > ${r.most})`);
        assert(/array is too big/.test(r.whole_call), `control: one welch call over the channel is refused (${String(r.whole_call).slice(0, 60)})`);
        assertEqual(r.total, r.expected, `every segment one call would average is counted (${r.total} of ${r.expected})`);
        assertEqual(r.used, r.total, 'and every one of them was averaged, on every channel');
        assertEqual(r.by_hand_segments, r.total, 'by hand, the same number of segments');
        assert(r.pieces > 1, `in ${r.pieces} pieces of up to ${r.m} segments`);
        assert(r.longest_read_mb <= 16, `each read fits a quarter of the 64 MB budget (${r.longest_read_mb.toFixed(1)} MB)`);
        assertEqual(r.read_to, r.samples, 'the last piece ends at the end of the recording');
        assertEqual(r.covered_seconds, MINUTES * 60, `the segments span the whole ${MINUTES} minutes (${r.covered_seconds} s)`);
        assert(result.stdout.startsWith(`${r.total} segments of 2 s, ${r.covered_seconds} s of the recording, in pieces of ${r.m} segments, 3 EEG channels, left out: none`),
          `the recipe says what it covered, for the model to report (${JSON.stringify(result.stdout.split('\n')[0])})`);
        assert(r.difference < 1e-9, `and the average is the by-hand estimate over all of them (${r.difference})`);
        assert(Math.abs(r.tone_hz - 10) < 0.6, `the tone only the last tenth carries is the peak (${r.tone_hz} Hz)`);
        assert(r.tone_over_capped > 2, `a read capped at ${r.most} samples ends before it starts, and its value at that frequency is ${r.tone_over_capped.toFixed(1)} times smaller`);
      }

      // (c) The reasons for the limits, and the budget's part in the piece.
      {
        const result = await run(`${preamble}
out = {}
rng = np.random.default_rng(3)
# The whole channels-by-samples array, and a channel past one welch call's limit.
out["whole_array"] = refused(rng.standard_normal((33, 30000)), 250.0)
long = rng.standard_normal((1, 300000))
r = await spectrum_with(long, 1000.0)
out["piece_1000"] = int(r["piece"])
out["one_piece"] = refused(long[0, :r["piece"]], 1000.0)
out["twice_piece"] = refused(long[0, : 2 * r["piece"]], 1000.0)
# The piece follows the budget while the budget binds, and never passes the runtime's limit.
short = rng.standard_normal((1, 20000))
out["pieces"] = {}
for rate in (60.0, 1000.0):
    for budget in (256, 512, 1024, 2048):
        out["pieces"][f"{int(rate)}:{budget}"] = int((await spectrum_with(short, rate, budget))["piece"])
out["limits"] = {str(int(rate)): 2**28 // int(2 * rate) for rate in (60.0, 1000.0)}
# A group of 33 channels at 250 Hz: the budget binds there too, since a piece is every channel's read.
many = rng.standard_normal((33, 20000))
out["many"] = {str(budget): int((await spectrum_with(many, 250.0, budget))["piece"]) for budget in (256, 2048)}
out["many_limit"] = 2**28 // 500
print(json.dumps(out))
`);
        assertEqual(result.status, 'ok', `the controls: it runs (stderr: ${result.stderr.slice(-400)})`);
        const r = lastJson(result.stdout, '{}');
        assert(/array is too big/.test(r.whole_array),
          `control: welch on the whole 33-channel array is refused here (${String(r.whole_array).slice(0, 80)})`);
        assertEqual(r.one_piece, 'ok', `one piece (${r.piece_1000} samples at 1000 Hz) is accepted`);
        assert(/array is too big/.test(r.twice_piece),
          `control: one channel of twice a piece is refused, so the piece is not needlessly short (${String(r.twice_piece).slice(0, 80)})`);
        for (const [rate, limit] of Object.entries(r.limits)) {
          const sizes = [256, 512, 1024, 2048].map((budget) => r.pieces[`${rate}:${budget}`]);
          assert(sizes.every((size) => size <= limit), `${rate} Hz: no piece is past the runtime's ${limit} samples (${sizes.join(', ')})`);
          assert(sizes.every((size, i) => i === 0 || size >= sizes[i - 1]), `${rate} Hz: a larger budget never means a smaller piece (${sizes.join(', ')})`);
        }
        assert(r.pieces['60:256'] < r.pieces['60:2048'],
          `at 60 Hz the budget sets the piece, so 256 MB is smaller than 2 GB (${r.pieces['60:256']} < ${r.pieces['60:2048']})`);
        assertEqual(r.pieces['1000:256'], r.limits['1000'], 'at 1000 Hz the runtime\'s limit sets it, for every budget');
        assert(r.many['256'] < r.many['2048'] && r.many['256'] < r.many_limit,
          `at 250 Hz with 33 channels the budget binds: 256 MB gives ${r.many['256']} samples, 2 GB ${r.many['2048']}, the limit ${r.many_limit}`);
      }
    }
  }

  console.log('\nthe band-power procedure integrates the whole-recording spectrum over each band');
  {
    // Both python fences of the document, as written: the Welch recipe, then the integration
    // the document says to run directly after it, in the same run.
    const entry = (NEMAR.procedures || []).find((p) => p.id === 'band-power');
    const document = entry ? await Bun.file(new URL(entry.file, COMMUNITY_DIR)).text() : '';
    const fences = [...document.matchAll(/```python\n([\s\S]*?)\n\s*```/g)].map((m) => m[1]);
    assertEqual(fences.length, 2, 'the band-power procedure carries the Welch block and the integration block');
    const SETUP = 'import eegprep_lean\nindex = await eegprep_lean.read_index("DATASET_ID")\nstore = index.store("RECORDING_PATH")';
    assert(fences[0]?.includes(SETUP), 'the band-power Welch block opens its own index and store, so it stands alone');
    assert(fences[0]?.includes('budget_mb = 512'), 'the band-power Welch block names the reader\'s budget on one line ("budget_mb = 512")');
    if (fences.length === 2 && fences[0].includes(SETUP) && fences[0].includes('budget_mb = 512')) {
      // A 1 MB budget cuts the read into several pieces, so the carried sums are exercised.
      const welch = fences[0].replace(SETUP, 'pass  # the runtime opens the index and the store').replace('budget_mb = 512', 'budget_mb = 1');
      const result = await run(`import json
import numpy as np
from scipy import signal

WELCH = ${JSON.stringify(welch)}
INTEGRATION = ${JSON.stringify(fences[1])}

class Window:
    def __init__(self, data, labels):
        self.data, self.labels = data, labels

class Group:
    def __init__(self, rate, n_samples, n_channels):
        self.rate, self.n_samples, self.n_channels = rate, n_samples, n_channels

class Store:
    def __init__(self, group):
        self._group = group
    def group(self, name):
        return self._group

class Lib:
    def __init__(self, eeg, labels):
        self.eeg, self.labels, self.reads = eeg, labels, 0
    async def read_window(self, index, store, group, start_sample, n_samples):
        self.reads += 1
        return Window(self.eeg[:, start_sample:start_sample + n_samples], self.labels)

async def band_power_run(eegprep_lean, index, store):
    # Both blocks in one function body, so they share names as two blocks of one run do.
    text = WELCH + "\\n" + INTEGRATION
    body = "\\n".join("    " + line for line in text.split("\\n"))
    scope = {}
    exec("async def run_blocks(eegprep_lean, index, store):\\n" + body
         + "\\n    return dict(bands=bands, band_power=band_power, keep=keep)", {"np": np, "signal": signal}, scope)
    return await scope["run_blocks"](eegprep_lean, index, store)

rate = 250.0
t = np.arange(int(120 * rate)) / rate
rng = np.random.default_rng(4)
amplitude = 3.0
eeg = rng.standard_normal((4, t.size)) + amplitude * np.sin(2 * np.pi * 10 * t)
lib = Lib(eeg, ["E0", "E1", "E2", "VEOG_lower"])
out = await band_power_run(lib, None, Store(Group(rate, t.size, 4)))
print(json.dumps({
    "bands": {k: list(v) for k, v in out["bands"].items()},
    "band_power": {k: [float(x) for x in v] for k, v in out["band_power"].items()},
    "keep": out["keep"], "reads": lib.reads, "expected": amplitude ** 2 / 2,
}))
`);
      assertEqual(result.status, 'ok', `band power: both blocks run in one namespace (stderr: ${result.stderr.slice(-400)})`);
      const r = JSON.parse((result.stdout || '').trim().split('\n').at(-1) || '{}');
      assertEqual(Object.keys(r.bands), ['delta', 'theta', 'alpha', 'beta'], 'the default bands are named for step 2');
      assertEqual(Object.keys(r.band_power), Object.keys(r.bands), 'band_power has one entry per band');
      assertEqual(r.keep, [0, 1, 2], 'the electrooculography channel is left out by its label');
      assert(r.reads > 1, `the small budget made ${r.reads} reads, so the pieces were averaged`);
      for (const [name, values] of Object.entries(r.band_power)) {
        assertEqual(values.length, 3, `${name}: one value per EEG channel`);
      }
      for (let channel = 0; channel < 3; channel++) {
        const power = Object.fromEntries(Object.entries(r.band_power).map(([name, values]) => [name, values[channel]]));
        const others = ['delta', 'theta', 'beta'].map((name) => power[name]);
        assert(others.every((value) => power.alpha > 20 * value),
          `channel ${channel}: the 10 Hz tone puts the power in alpha (${JSON.stringify(power)})`);
        assert(Math.abs(power.alpha / r.expected - 1) < 0.05,
          `channel ${channel}: alpha holds the tone's power, amplitude squared over two (${power.alpha} against ${r.expected})`);
      }
    }
  }

  // The scalp-map, time-frequency and matching blocks of the topography and time-frequency
  // documents (#721), run as the documents write them in the real Pyodide runtime. Every
  // python fence of a document, in order, so a block is found by what it defines.
  const documentFences = async (id) => {
    const entry = (NEMAR.procedures || []).find((p) => p.id === id);
    const document = entry ? await Bun.file(new URL(entry.file, COMMUNITY_DIR)).text() : '';
    return [...document.matchAll(/```python\n([\s\S]*?)\n```/g)].map((m) => m[1]);
  };
  const fenceWith = (fences, marker) => fences.filter((f) => f.includes(marker));

  console.log('\nthe scalp-map block draws an interpolated head for 30 channels and markers only for 3, one figure each');
  {
    const band = await documentFences('band-power-topography');
    const erp = await documentFences('erp-topography');
    const [positions] = fenceWith(band, 'positions = {');
    const [locate] = fenceWith(band, 'def locate(');
    const [scalp] = fenceWith(band, 'def scalp_maps(');
    assert(positions && locate && scalp, 'the band power topography carries the table, the matching and the drawing blocks');
    assertEqual(
      [positions, locate, scalp],
      [fenceWith(erp, 'positions = {')[0], fenceWith(erp, 'def locate(')[0], fenceWith(erp, 'def scalp_maps(')[0]],
      'and the ERP topography carries the same three, byte for byte'
    );
    if (positions && locate && scalp) {
      // The 30 EEG labels of ERP CORE's nm000132, and values that are a plane over the head:
      // an interpolation through them returns the plane at any point inside their hull.
      const labels30 = JSON.stringify(['FP1', 'F3', 'F7', 'FC3', 'C3', 'C5', 'P3', 'P7', 'P9', 'PO7', 'PO3', 'O1', 'Oz', 'Pz', 'CPz',
        'FP2', 'Fz', 'F4', 'F8', 'FC4', 'FCz', 'Cz', 'C4', 'C6', 'P4', 'P8', 'P10', 'PO8', 'PO4', 'O2']);
      const blocks = `${positions}\n${locate}\n${scalp}\n`;
      const summary = (labels) => `import json
labels = ${labels}
at, xy = locate(labels)
values = 10 * xy[:, 0] + 4 * xy[:, 1]  # a plane over the head
fig = scalp_maps([("synthetic", values)], xy, [labels[i] for i in at], "synthetic unit", "synthetic title",
                 float(values.min()), float(values.max()))
ax, bar = fig.axes[0], fig.axes[-1]
out = {
    "positioned": len(at), "axes": len(fig.axes), "images": len(ax.images), "markers": int(ax.collections[0].get_offsets().shape[0]),
    "named": len(ax.texts), "bar_label": bar.get_ylabel(), "title": fig._suptitle.get_text(),
    "patches": len(ax.patches), "lines": len(ax.lines),
}
if ax.images:
    surface = np.ma.filled(np.ma.masked_invalid(ax.images[0].get_array()).astype(float), np.nan)
    n = surface.shape[0]
    mid = n // 2
    step = round(0.1 / (1.0 / (n - 1)))  # grid cells in 0.1
    out["outside"] = float(np.isnan(surface).mean())
    out["corner_is_empty"] = bool(np.isnan(surface[0, 0]) and np.isnan(surface[-1, -1]))
    out["plane_error"] = float(abs(surface[mid + step, mid + step] - (10 * 0.1 + 4 * 0.1)))
    out["centre_error"] = float(abs(surface[mid, mid]))
print(json.dumps(out))
`;
      const thirty = await run(blocks + summary(labels30));
      assertEqual(thirty.status, 'ok', `30 positioned channels: the blocks run (stderr: ${thirty.stderr.slice(-400)})`);
      const r30 = JSON.parse((thirty.stdout || '').trim().split('\n').at(-1) || '{}');
      assertEqual(thirty.images.length, 1, '30 channels: exactly one figure comes out');
      assertEqual(thirty.images[0] && thirty.images[0].mime, 'image/png', '30 channels: and it is a PNG');
      assertEqual(r30.positioned, 30, '30 channels: every label has a position');
      assertEqual([r30.images, r30.markers, r30.named], [1, 30, 0], '30 channels: one interpolated surface, 30 markers, none named');
      assertEqual(r30.axes, 2, '30 channels: one head and one color bar');
      assertEqual([r30.bar_label, r30.title], ['synthetic unit', 'synthetic title'], '30 channels: the color bar carries the unit and the figure its title');
      assert(r30.patches >= 2 && r30.lines >= 2, `30 channels: a head outline, a nose and two ears (${r30.patches} patches, ${r30.lines} lines)`);
      assert(r30.corner_is_empty, '30 channels: nothing is interpolated outside the head');
      assert(r30.outside > 0.19 && r30.outside < 0.24, `30 channels: about a fifth of the square is outside the circle (${r30.outside})`);
      assert(r30.plane_error < 0.02, `30 channels: the interpolation returns the plane inside the head (error ${r30.plane_error})`);
      assert(r30.centre_error < 0.02, `30 channels: and 0 at Cz, which is the origin (error ${r30.centre_error})`);

      const three = await run(blocks + summary(JSON.stringify(['C3', 'Cz', 'C4'])));
      assertEqual(three.status, 'ok', `3 positioned channels: the blocks run (stderr: ${three.stderr.slice(-400)})`);
      const r3 = JSON.parse((three.stdout || '').trim().split('\n').at(-1) || '{}');
      assertEqual(three.images.length, 1, '3 channels: exactly one figure comes out');
      assertEqual(r3.positioned, 3, '3 channels: all three have a position');
      assertEqual([r3.images, r3.markers, r3.named], [0, 3, 3], '3 channels: no interpolated surface, only 3 named colored markers');
      assertEqual(r3.axes, 2, '3 channels: one head and one color bar');

      // Labels as other recordings write them: a prefix, a reference suffix, other case, and some with no place.
      const odd = await run(`${positions}\n${locate}\nimport json\nat, xy = locate(["EEG Fp1-REF", "eeg cz-le", "Pz-AVG", "HEOG_left", "T3", "T7", "Foo9"])\nprint(json.dumps(at.tolist()))\n`);
      assertEqual(odd.status, 'ok', `the matching runs on odd labels (stderr: ${odd.stderr.slice(-300)})`);
      assertEqual(JSON.parse((odd.stdout || '').trim().split('\n').at(-1) || '[]'), [0, 1, 2, 4],
        'prefix, suffix and case are ignored, the unmatched are left out, and T7 repeats the place of T3');
      assert(/left out, no position: \['HEOG_left', 'Foo9'\]/.test(odd.stdout), 'and the labels left out are printed');
    }
  }

  console.log('\nthe time-frequency block puts a tone of known frequency at that frequency, over the burst, in dB');
  {
    const fences = await documentFences('time-frequency-by-condition');
    const [epoching] = fenceWith(fences, 'pad_s =');
    const [compute] = fenceWith(fences, 'fftconvolve');
    assert(epoching && compute, 'the time-frequency document carries the epoch block and the compute block');
    if (epoching && compute) {
      // Two conditions of 40 epochs each: a burst of a Gaussian envelope (sd 80 ms) around 400 ms,
      // at 12 Hz in one and 24 Hz in the other, with a random phase in each epoch, on noise.
      // A rectangle or a fixed phase would be a different test.
      const result = await run(`import json
import numpy as np
rate = 250.0
${epoching}
rng = np.random.default_rng(7)
t = (np.arange(pre + post) - pre) / rate
envelope = np.exp(-((t - 0.4) ** 2) / (2 * 0.08**2))
def condition(hz):
    phase = rng.uniform(0, 2 * np.pi, size=(40, 1))
    return 20 * envelope * np.sin(2 * np.pi * hz * t + phase) + rng.standard_normal((40, t.size))
epochs = {"twelve": condition(12.0), "twenty_four": condition(24.0)}
${compute}
out = {"freqs": freqs.tolist(), "shown": [float(shown[0]), float(shown[-1]), len(shown)], "pre": pre, "post": post, "kept": kept, "maps": {}}
for name, db in tf_db.items():
    f, k = np.unravel_index(np.argmax(db), db.shape)
    before = (shown >= -0.4) & (shown <= -0.1)
    out["maps"][name] = {"shape": list(db.shape), "peak_hz": float(freqs[f]), "peak_s": float(shown[k]), "peak_db": float(db[f, k]),
                         "baseline_db": float(db[:, before].mean()), "off_hz_db": float(db[0, (shown > 0.2) & (shown < 0.6)].mean())}
print(json.dumps(out))
`);
      assertEqual(result.status, 'ok', `the epoch and compute blocks run (stderr: ${result.stderr.slice(-400)})`);
      const r = JSON.parse((result.stdout || '').trim().split('\n').at(-1) || '{}');
      assertEqual(r.pre + r.post, 615, 'at 250 Hz an epoch is 0.5 s and 1 s plus a 0.48 s pad on each side: 615 samples');
      assertEqual(r.kept, { twelve: 40, twenty_four: 40 }, 'every synthetic epoch is counted');
      assertEqual(r.freqs[0], 4, 'the frequencies start at 4 Hz');
      assertEqual(r.freqs.at(-1), 40, 'and stop at 40 Hz, a quarter of the rate being 62.5');
      assert(Math.abs(r.shown[0] + 0.5) < 0.005 && Math.abs(r.shown[1] - 1.0) < 0.005, `the maps show -500 to 1,000 ms (${r.shown[0]} to ${r.shown[1]})`);
      assertEqual(r.shown[2], 376, 'with 376 time points, the pad cut off');
      for (const [name, hz] of [['twelve', 12], ['twenty_four', 24]]) {
        const m = r.maps[name];
        assertEqual(m.shape, [r.freqs.length, 376], `${name}: frequencies by times`);
        assertEqual(m.peak_hz, hz, `${name}: the dB map peaks at the tone's ${hz} Hz (${m.peak_hz})`);
        assert(Math.abs(m.peak_s - 0.4) < 0.1, `${name}: at the burst's 400 ms (${m.peak_s} s)`);
        assert(m.peak_db > 20, `${name}: the burst is more than 20 dB over the pre-event power (${m.peak_db} dB)`);
        assert(Math.abs(m.baseline_db) < 1.5, `${name}: the pre-event window is near 0 dB by construction (${m.baseline_db} dB)`);
        assert(Math.abs(m.off_hz_db) < 3, `${name}: the lowest frequency, far from the tone, stays near 0 dB during the burst (${m.off_hz_db} dB)`);
      }
    }
  }
} finally {
  server.stop(true);
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
