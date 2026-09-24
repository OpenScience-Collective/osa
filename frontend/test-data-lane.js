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

  // The low-pass the prompt's ERP section teaches, run as the prompt writes it. A
  // model copies it, so a kernel that stops filtering (np.sinc without the cutoff is
  // a single spike) would put unfiltered epochs into every ERP image it draws.
  console.log('\nthe prompt\'s ERP low-pass keeps 5 Hz and removes 50 Hz');
  {
    const erpAt = NEMAR.system_prompt.indexOf('**Epochs and ERP images.**');
    const fence = erpAt === -1 ? null : NEMAR.system_prompt.slice(erpAt).match(/```python\n([\s\S]*?)\n\s*```/);
    assert(fence !== null, 'the ERP section carries a python block');
    if (fence) {
      const lines = fence[1].split('\n');
      const indent = Math.min(...lines.filter((l) => l.trim()).map((l) => l.match(/^ */)[0].length));
      const kernelCode = lines.map((l) => l.slice(indent)).join('\n');
      const result = await run(`import json
import numpy as np
rate = 250.0
t = np.arange(int(4 * rate)) / rate
slow = np.sin(2 * np.pi * 5 * t)
channel = slow + np.sin(2 * np.pi * 50 * t)
${kernelCode}
f = np.fft.rfftfreq(8192, 1 / rate)
H = np.abs(np.fft.rfft(kernel, 8192))
gain = lambda hz: float(H[np.argmin(np.abs(f - hz))])
edge = taps
print(json.dumps({
    "taps": int(taps),
    "gain_5": gain(5), "gain_50": gain(50),
    "residual": float(np.max(np.abs(filtered[edge:-edge] - slow[edge:-edge]))),
}))
`);
      assertEqual(result.status, 'ok', `it runs (stderr: ${result.stderr.slice(-300)})`);
      const out = JSON.parse(result.stdout || '{}');
      assert(out.taps % 2 === 1, `an odd number of taps (${out.taps})`);
      assert(Math.abs(out.gain_5 - 1) < 0.01, `unity gain at 5 Hz (${out.gain_5})`);
      assert(out.gain_50 < 0.01, `under 1% at 50 Hz (${out.gain_50})`);
      assert(out.residual < 0.02,
        `a 5 Hz sine plus a 50 Hz sine comes out as the 5 Hz sine, unshifted (largest difference ${out.residual})`);
    }
  }
} finally {
  server.stop(true);
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
