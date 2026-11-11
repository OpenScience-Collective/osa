// The prompt's power-spectrum recipe on a real NEMAR recording, once, by hand (E5.2, #669).
//
// frontend/test-data-lane.js runs the recipe as the prompt writes it over synthetic signals held
// in memory, in CI. This runs the same block, extracted from NEMAR's system prompt, against a
// real recording on the production data host, and checks it against a by-hand Welch estimate
// that shares no code with scipy.signal.welch. It needs the network, so CI does not run it; the
// figures in docs/community-browser-runtime.md come from it.
//
// What is real: NEMAR's config, lock overlay and prelude, the worker core as the widget runs it,
// Pyodide 0.29.5 from npm (the wheels it loads are cached under .cache/pyodide-packages),
// eegprep-lean's index and read_window, and the Zarr store on zarr.nemar.org.
// What this is not: a browser. It runs under Bun, so the heap it reports is Pyodide's
// WebAssembly memory as Bun gives it, which is the same figure the run card reports.
//
//   bun frontend/browser-harness/spectrum-real-recording.mjs
//   bun frontend/browser-harness/spectrum-real-recording.mjs --budget 256
//   bun frontend/browser-harness/spectrum-real-recording.mjs --dataset on002718 \
//     --recording sub-002/eeg/sub-002_task-FaceRecognition_eeg.set --group eeg_250hz --budget 512
//
// It prints one JSON object: the recording's size, the recipe's own printed line, how long the
// read and the recipe took, the heap after the recipe, and the largest difference from the
// by-hand estimate on the first three EEG channels (relative to each spectrum's peak).
// It exits 1 when that difference is over 1e-9 or the recipe did not average every segment.
import { loadPyodide } from 'pyodide';
import stockLock from 'pyodide/pyodide-lock.json';
import pyodidePackage from 'pyodide/package.json';
import { buildWorkerConfig } from '../osa-runtime.js';
import { createWorkerRuntime } from '../osa-worker-core.js';

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, token, i, all) => (token.startsWith('--') ? [...pairs, [token.slice(2), all[i + 1]]] : pairs), [])
);
const DATASET = args.dataset || 'on002718';
const RECORDING = args.recording || 'sub-002/eeg/sub-002_task-FaceRecognition_eeg.set';
const GROUP = args.group || 'eeg_250hz';
const BUDGET_MB = Number(args.budget || 512);
if (![256, 512, 1024, 2048].includes(BUDGET_MB)) {
  console.error(`--budget must be 256, 512, 1024 or 2048, got ${args.budget}`);
  process.exit(2);
}

const ROOT = new URL('../../', import.meta.url);
const COMMUNITY_DIR = new URL('src/assistants/nemar/', ROOT);
const PACKAGE_CACHE = new URL('.cache/pyodide-packages/', ROOT).pathname;
const NEMAR = Bun.YAML.parse(await Bun.file(new URL('config.yaml', COMMUNITY_DIR)).text());
const RAW_PYTHON = NEMAR.runtime.python;
const forProduction = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value.production : value;
const PYTHON = { ...RAW_PYTHON, prelude: forProduction(RAW_PYTHON.prelude), fetch_allow: forProduction(RAW_PYTHON.fetch_allow) };
const LOCKFILE = new URL(PYTHON.lockfile, COMMUNITY_DIR);
const OVERLAY = JSON.parse(await Bun.file(LOCKFILE).text()).packages;
const WHEELS = new URL('wheels/', LOCKFILE).pathname;
// Node's loader cannot take a wheel by URL (see test-data-lane.js), so the overlay names the
// committed wheels by path.
const localPackages = Object.fromEntries(
  Object.entries(OVERLAY).map(([key, entry]) => [key, { ...entry, file_name: WHEELS + entry.file_name }])
);

// The recipe, as the prompt writes it: the first python block after the Power spectrum heading,
// dedented, with the group and the budget written in as the prompt tells the model to.
const bullet = NEMAR.system_prompt.slice(NEMAR.system_prompt.indexOf('**Power spectrum.**'));
const fence = bullet.match(/```python\n([\s\S]*?)\n\s*```/);
if (!fence) throw new Error("NEMAR's prompt has no power-spectrum block");
const lines = fence[1].split('\n');
const indent = Math.min(...lines.filter((l) => l.trim()).map((l) => l.match(/^ */)[0].length));
const BLOCK = lines.map((l) => l.slice(indent)).join('\n')
  .replace('"DATASET_ID"', JSON.stringify(DATASET))
  .replace('"RECORDING_PATH"', JSON.stringify(RECORDING))
  .replace('"GROUP"', JSON.stringify(GROUP))
  .replace('budget_mb = 512', `budget_mb = ${BUDGET_MB}`);

const config = buildWorkerConfig(PYTHON, { packages: OVERLAY, baseUrl: 'https://osa.example/nemar/runtime/' });
const createFromSource = new Function(`return (${createWorkerRuntime.toString()});`)();
const messages = [];
const runtime = createFromSource({ ...config, lockPackages: localPackages }, {
  load: (indexURL, options) => loadPyodide({ packageCacheDir: PACKAGE_CACHE, stdout: () => {}, stderr: () => {}, ...options }),
  stockLock: async () => stockLock,
  seal: () => {},
  send: (message) => messages.push(message),
});

let seq = 0;
async function run(code) {
  const from = messages.length;
  const started = performance.now();
  await runtime.handle({ type: 'execute', call_id: `real-${++seq}`, code });
  const result = messages.slice(from).find((m) => m.type === 'result');
  if (!result || result.status !== 'ok') {
    console.error(`run ${seq} failed:`, result ? result.stderr.slice(-800) : messages.slice(from));
    process.exit(1);
  }
  return { ...result, seconds: (performance.now() - started) / 1000 };
}

await runtime.handle({ type: 'boot' });
if (!messages.some((m) => m.type === 'ready')) {
  console.error('the runtime did not boot:', messages.find((m) => m.type === 'error') || messages.at(-1));
  process.exit(1);
}

await run(`import eegprep_lean
index = await eegprep_lean.read_index(${JSON.stringify(DATASET)})
store = index.store(${JSON.stringify(RECORDING)})
group = store.group(${JSON.stringify(GROUP)})
print(group.n_channels, group.n_samples, group.rate)`).then((r) => { globalThis.shape = r.stdout.trim().split(' ').map(Number); });
const [nChannels, nSamples, rate] = globalThis.shape;

// The recipe, as one run, the way the model's single step would send it.
const recipe = await run(BLOCK);
const recipeLine = recipe.stdout.trim().split('\n').at(-1);
const heapMb = recipe.heap_bytes ? Math.round(recipe.heap_bytes / 2 ** 20) : null;

// The reference: the whole recording in one read (more than the budget allows the recipe, which
// is the point of it), and Welch's estimate by hand on the first three EEG channels.
const reference = await run(`
import json
import numpy as np
from scipy import signal
window = await eegprep_lean.read_window(index, store, group=group, start_sample=0, n_samples=group.n_samples)
labels = window.labels or [""] * window.data.shape[0]
eeg_rows = [i for i, label in enumerate(labels) if not any(tag in label.upper() for tag in ("EOG", "ECG", "EKG", "EMG"))]
assert eeg_rows == keep, (eeg_rows, keep)
def by_hand(x, rate):
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
worst, segments = 0.0, 0
for row in range(3):
    ref, segments = by_hand(window.data[keep[row]], window.rate)
    worst = max(worst, float(np.max(np.abs(power[row] - ref)) / np.max(ref)))
whole = None
try:
    signal.welch(window.data[keep[0]], fs=window.rate, nperseg=int(2 * window.rate))
    whole = "ok"
except ValueError as error:
    whole = str(error)[:60]
print(json.dumps({"worst": worst, "by_hand_segments": segments, "whole_call": whole}))
`);
const checked = JSON.parse(reference.stdout.trim().split('\n').at(-1));

const summary = {
  dataset: DATASET, recording: RECORDING, group: GROUP, budget_mb: BUDGET_MB, pyodide: pyodidePackage.version,
  channels: nChannels, samples: nSamples, rate_hz: rate, seconds: nSamples / rate,
  read_everything_mb: Math.round((nChannels * nSamples * 8) / 2 ** 20),
  recipe_printed: recipeLine, recipe_seconds: Number(recipe.seconds.toFixed(1)),
  heap_mb_after_recipe: heapMb, whole_read_and_check_seconds: Number(reference.seconds.toFixed(1)),
  one_welch_call_over_a_channel: checked.whole_call,
  by_hand_segments: checked.by_hand_segments, largest_difference_from_by_hand_over_peak: checked.worst,
};
console.log(JSON.stringify(summary, null, 2));
const counted = Number(recipeLine.split(' ')[0]);
process.exit(checked.worst < 1e-9 && counted === checked.by_hand_segments ? 0 : 1);
