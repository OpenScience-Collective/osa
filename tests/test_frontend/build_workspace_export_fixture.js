#!/usr/bin/env bun
// Build one workspace export zip with the REAL frontend modules, for
// tests/test_frontend/test_workspace_export.py to open with Python's
// zipfile and validate with the real nbformat package.
//
// Not a stand-in for WorkspaceStore.exportZip: it calls the same
// buildWorkspaceZip that method calls, with the same shape of input
// (already-read file bytes and run records), so what is proven here is the
// zip's bytes and the notebook/manifest JSON inside them, exactly what
// exportZip would produce for this data. IndexedDB itself is not under
// test; see frontend/test-workspace.js's own docstring for why.
//
// Usage: bun build_workspace_export_fixture.js <output-zip-path>

import { writeFileSync } from 'node:fs';
import { buildWorkspaceZip } from '../../frontend/osa-workspace.js';

const outPath = process.argv[2];
if (!outPath) {
  console.error('usage: bun build_workspace_export_fixture.js <output-zip-path>');
  process.exit(1);
}

const encoder = new TextEncoder();
// A tiny transparent PNG (1x1), so the notebook's figure output is a real,
// decodable image, not a placeholder string.
const PNG_1X1_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

const runs = [
  {
    ordinal: 1,
    callId: 'call-1',
    status: 'ok',
    description: 'Load the recording and plot channel E1.',
    files: ['scripts/run-001.py', 'results/run-001/stdout.txt', 'results/run-001/stderr.txt', 'results/run-001/summary.txt', 'results/run-001/figure-1.png'],
    timestamp: '2026-09-23T00:00:00.000Z',
  },
  {
    ordinal: 2,
    callId: 'call-2',
    status: 'ok',
    description: 'Save a named script and a CSV artifact.',
    files: ['scripts/run-002.py', 'results/run-002/stdout.txt', 'results/run-002/stderr.txt', 'results/run-002/summary.txt', 'scripts/analysis.py', 'artifacts/table.csv'],
    timestamp: '2026-09-23T00:05:00.000Z',
  },
];

const notebookRuns = [
  {
    ordinal: 1,
    description: runs[0].description,
    code: 'import numpy as np\nchannel = np.arange(10)\nprint(channel.mean())',
    stdout: '4.5\n',
    stderr: '',
    images: [{ data_base64: PNG_1X1_BASE64 }],
  },
  {
    ordinal: 2,
    description: runs[1].description,
    code: 'osa.save_script("analysis", "print(1)")\nosa.save_artifact("table.csv", "a,b\\n1,2\\n")',
    stdout: '',
    stderr: '',
    images: [],
  },
];

const files = [
  { path: 'scripts/run-001.py', data: encoder.encode(notebookRuns[0].code) },
  { path: 'results/run-001/stdout.txt', data: encoder.encode(notebookRuns[0].stdout) },
  { path: 'results/run-001/stderr.txt', data: encoder.encode('') },
  { path: 'results/run-001/summary.txt', data: encoder.encode('variables:\n  channel: ndarray int64 shape=(10,) min=0 max=9 mean=4.5') },
  { path: 'results/run-001/figure-1.png', data: Uint8Array.from(atob(PNG_1X1_BASE64), (c) => c.charCodeAt(0)) },
  { path: 'scripts/run-002.py', data: encoder.encode(notebookRuns[1].code) },
  { path: 'results/run-002/stdout.txt', data: encoder.encode('') },
  { path: 'results/run-002/stderr.txt', data: encoder.encode('') },
  { path: 'results/run-002/summary.txt', data: encoder.encode('') },
  { path: 'scripts/analysis.py', data: encoder.encode('print(1)') },
  { path: 'artifacts/table.csv', data: encoder.encode('a,b\n1,2\n') },
];

const bytes = buildWorkspaceZip([{ session: 'session-abc123', files, runs, notebookRuns }]);
writeFileSync(outPath, bytes);
