#!/usr/bin/env bun
/**
 * Re-records frontend/test-support/run-card-fixtures.js from real Pyodide runs.
 *
 * Each case's code runs through the worker core on the Pyodide from npm, exactly as
 * test-worker-core.js does, and what the widget would store is written out as data:
 * the run's status, stdout, stderr, and the full traceback the browser keeps for
 * get_full_output. The output is never edited by hand, so the run card's tests read
 * the shapes the runtime really writes (osa-output.js _error_text and _clip).
 *
 * Usage: bun frontend/test-support/record-run-card-fixtures.js > frontend/test-support/run-card-fixtures.js
 */
import { loadPyodide } from 'pyodide';
import stockLock from 'pyodide/pyodide-lock.json';
import pyodidePackage from 'pyodide/package.json';
import { buildDataClientSource, buildNamespaceSealSource } from '../osa-egress.js';
import { buildHelpersSource, buildOutputCaptureSource, resolveLimits } from '../osa-output.js';
import { createWorkerRuntime } from '../osa-worker-core.js';

const PACKAGE_CACHE = new URL('../../.cache/pyodide-packages/', import.meta.url).pathname;
const createFromSource = new Function(`return (${createWorkerRuntime.toString()});`)();

const CODE = {
  keyErrorWithLine: [
    'events = {"famous": [1, 2, 3], "scrambled_first": [4, 5]}',
    'erp = {}',
    'for name in ("famous", "Scrambled"):',
    '    erp[name] = sum(events[name]) / len(events[name])',
    'print(erp)',
  ].join('\n'),
  syntaxError: [
    'values = [1, 2, 3',
    'print(sum(values))',
  ].join('\n'),
  // A source string with a NUL byte fails to compile before any line of it runs, so the
  // exception has no frame in the cell and the runtime has no line to report.
  errorWithoutLine: 'total = 0\nprint(total)\u0000',
  cutOutput: [
    'for i in range(900):',
    '    print(f"epoch {i:04d}: 64 channels, 801 samples, rejected 0")',
  ].join('\n'),
  okWithWarnings: [
    'import warnings',
    'warnings.warn("channel EEG065 has flat segments", UserWarning)',
    'warnings.warn("sampling rate 512.0 differs from the file header", RuntimeWarning)',
    'print("averaged 882 epochs")',
  ].join('\n'),
  // A message of several lines, one of which reads like an exception's own first line:
  // the card must name RuntimeError, the type the exception has, and not OtherError.
  multiLineMessage: [
    'def check(rows):',
    '    raise RuntimeError("could not merge the event files\\nOtherError: column onset is missing")',
    'check([])',
  ].join('\n'),
  // An exception whose name does not end like one, raised from a line of the run's own code.
  customName: [
    'class MyFailure(Exception):',
    '    pass',
    'raise MyFailure("nope")',
  ].join('\n'),
  cutStderr: [
    'import sys',
    'for i in range(400):',
    '    print(f"loading recording {i:04d} from the archive, retry 1", file=sys.stderr)',
    'raise ValueError("no recording had a usable event file")',
  ].join('\n'),
};

const messages = [];
const runtime = createFromSource({
  indexURL: `https://cdn.jsdelivr.net/pyodide/v${pyodidePackage.version}/full/`,
  preload: [],
  allowInstall: [],
  indexUrls: [],
  fetchAllow: [],
  importBeforeSeal: [],
  lockPackages: {},
  prelude: '',
  python: {
    helpers: buildHelpersSource(),
    outputCapture: buildOutputCaptureSource(resolveLimits({})),
    dataClient: buildDataClientSource(),
    namespaceSeal: buildNamespaceSealSource(),
  },
}, {
  load: async (indexURL, options) => loadPyodide({ packageCacheDir: PACKAGE_CACHE, stdout: () => {}, stderr: () => {}, ...options }),
  stockLock: async () => stockLock,
  seal: () => {},
  send: (message) => messages.push(message),
});
await runtime.handle({ type: 'boot' });

const recorded = {};
for (const [name, code] of Object.entries(CODE)) {
  const from = messages.length;
  await runtime.handle({ type: 'execute', call_id: `rec-${name}`, code });
  const result = messages.slice(from).find((m) => m.type === 'result');
  recorded[name] = {
    code,
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr,
    traceback: (result.full && result.full.traceback) || '',
  };
}

console.log(`// Recorded from real Pyodide ${pyodidePackage.version} by record-run-card-fixtures.js; do not edit by hand.`);
console.log('// Each entry is a run as the widget stores it: the code, the status, stdout, stderr (osa-output.js');
console.log('// _error_text and _clip wrote them) and the full traceback the browser keeps for get_full_output.');
console.log(`export const RUN_CARD_FIXTURES = ${JSON.stringify(recorded, null, 2)};`);
