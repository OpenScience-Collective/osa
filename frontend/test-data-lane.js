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
const PYTHON = NEMAR.runtime.python;
const LOCKFILE = new URL(PYTHON.lockfile, COMMUNITY_DIR);
const OVERLAY = JSON.parse(await Bun.file(LOCKFILE).text()).packages;
const WHEELS = new URL('wheels/', LOCKFILE).pathname;

// The overlay as the worker would receive it, but naming each wheel by path; see
// the header for why Node cannot take the URL.
const localPackages = Object.fromEntries(
  Object.entries(OVERLAY).map(([key, entry]) => [key, { ...entry, file_name: WHEELS + entry.file_name }])
);

// nemar-cli's python_browser recipe, verbatim from buildHowTo in
// shared/contract/mcp.ts (nemarOrg/nemar-cli, as of 2026-09-22), with the array
// URL left as a placeholder. It is what nemar_read_window hands the model.
const PYTHON_BROWSER_RECIPE = [
  'from eegprep_lean import open_array  # from the runtime\'s lockfile, not micropip',
  '',
  'arr = await open_array("ARRAY_URL")',
  'window = await arr.getitem((slice(None), slice(start_sample, end_sample)))',
  '# async throughout: the loop is already running, so there is no synchronous form',
  '# physical = digital * scale + offset -- see the recipe\'s scale_offset field',
  '# read_window(index, store, ...) does the same read in physical units, with labels',
].join('\n');

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
const server = Bun.serve({
  port: 0,
  fetch(request) {
    const { pathname } = new URL(request.url);
    const range = request.headers.get('range');
    seen.push({ pathname, range });
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

  const INDEX = `
from eegprep_lean import read_window
from eegprep_lean.index import ChannelGroup, DatasetIndex, Store

index = DatasetIndex(
    dataset_id="xx099999", format_version=3, contract_base=${JSON.stringify(BASE)}, store_count=1,
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
} finally {
  server.stop(true);
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
