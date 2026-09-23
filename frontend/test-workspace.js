/**
 * The browser workspace's storage-independent logic (epic #429, phase #433).
 *
 * IndexedDB does not exist under Bun, so `WorkspaceStore`'s actual reads and
 * writes are exercised only by the Chrome harness (browser-harness/). What
 * IS testable here, and what this file tests, is everything that never
 * touches `indexedDB`: path validation, manifest derivation, the `.ipynb`
 * builder, the zip writer, and a store's behavior when IndexedDB is
 * genuinely absent (not stubbed to look absent -- under Bun it really is).
 *
 * The zip is checked with TWO independent readers neither of which is this
 * repository's own writer: `unzip` (system tool) and Python's `zipfile`
 * (stdlib), via `Bun.spawn`. A writer that only reads back its own bytes
 * proves nothing about the format; an external reader proves the archive is
 * actually a zip.
 *
 * Run with: bun frontend/test-workspace.js
 */

import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  autoFigurePath,
  autoResultDir,
  autoScriptPath,
  buildNotebook,
  buildWorkspaceZip,
  deriveManifest,
  runOrdinalTag,
  validateWorkspacePath,
  WORKSPACE_LIMITS,
  WorkspaceStore,
} from './osa-workspace.js';

let passed = 0;
let failed = 0;

function assert(cond, msg) {
  if (cond) {
    console.log(`  ok ${msg}`);
    passed++;
  } else {
    console.error(`  x FAIL: ${msg}`);
    failed++;
  }
}

function assertEqual(actual, expected, msg) {
  const same = JSON.stringify(actual) === JSON.stringify(expected);
  assert(same, `${msg}${same ? '' : ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`}`);
}

function assertThrows(fn, pattern, msg) {
  let err = null;
  try {
    fn();
  } catch (e) {
    err = e;
  }
  assert(err !== null && pattern.test(err.message), `${msg}${err ? ` (got ${JSON.stringify(err.message)})` : ' (did not throw)'}`);
}

console.log('='.repeat(60));
console.log('Workspace: path validation, manifest, notebook, zip');
console.log('='.repeat(60));

console.log('\nvalidateWorkspacePath accepts what the layout actually uses');
{
  for (const path of ['scripts/run-001.py', 'results/run-001/stdout.txt', 'artifacts/a/b/c.csv', 'a.b-c_d.txt']) {
    let threw = null;
    try {
      assertEqual(validateWorkspacePath(path), path, `accepts and returns ${path} unchanged`);
    } catch (err) {
      threw = err;
    }
    assert(threw === null, `${path} does not throw`);
  }
}

console.log('\nvalidateWorkspacePath refuses every rule it documents');
{
  assertThrows(() => validateWorkspacePath(''), /non-empty string/, 'an empty path');
  assertThrows(() => validateWorkspacePath(null), /non-empty string/, 'a non-string path');
  assertThrows(() => validateWorkspacePath('/etc/passwd'), /must be relative/, 'an absolute path');
  assertThrows(() => validateWorkspacePath('\\windows\\path'), /must be relative/, 'a backslash-rooted path');
  assertThrows(() => validateWorkspacePath('a/../b'), /path segment "\.\." is not allowed/, 'a .. segment');
  assertThrows(() => validateWorkspacePath('./a'), /path segment "\." is not allowed/, 'a . segment');
  assertThrows(() => validateWorkspacePath('a b/c'), /must match/, 'a segment with a space');
  assertThrows(() => validateWorkspacePath('a/b/c/d/e'), /5 segments, over the 4-segment limit/, 'five segments');
  assertThrows(() => validateWorkspacePath('x'.repeat(WORKSPACE_LIMITS.MAX_PATH_CHARS + 1)), /over the 200-character limit/, 'over the character limit');
  assertEqual(validateWorkspacePath('x'.repeat(WORKSPACE_LIMITS.MAX_PATH_CHARS)), 'x'.repeat(WORKSPACE_LIMITS.MAX_PATH_CHARS),
    'exactly at the character limit is allowed');
  assertEqual(validateWorkspacePath('a/b/c/d'), 'a/b/c/d', 'exactly four segments is allowed');
}

console.log('\nautoScriptPath/autoResultDir/autoFigurePath name the layout deterministically');
{
  assertEqual(runOrdinalTag(7), '007', 'the ordinal is zero-padded to three digits');
  assertEqual(autoScriptPath(7), 'scripts/run-007.py', 'the run\'s code');
  assertEqual(autoResultDir(7), 'results/run-007', 'its result directory');
  assertEqual(autoFigurePath(7, 2), 'results/run-007/figure-2.png', 'and one of its figures');
  assertThrows(() => runOrdinalTag(0), /positive integer/, 'ordinal 0 is refused');
  assertThrows(() => runOrdinalTag(1.5), /positive integer/, 'a non-integer ordinal is refused');
}

console.log('\nderiveManifest is pure: sorted by ordinal, files sorted and de-duplicated');
{
  const manifest = deriveManifest([
    { ordinal: 2, callId: 'c2', status: 'ok', description: 'second', files: ['b.txt', 'a.txt'], timestamp: '2026-01-02T00:00:00Z' },
    {
      ordinal: 1,
      callId: 'c1',
      status: 'error',
      files: ['scripts/run-001.py', 'scripts/run-001.py', 'results/run-001/stdout.txt'],
      timestamp: '2026-01-01T00:00:00Z',
    },
  ]);
  assertEqual(manifest.runs.map((r) => r.ordinal), [1, 2], 'runs come back in ordinal order, regardless of input order');
  assertEqual(manifest.runs[0].call_id, 'c1', 'call_id is carried through');
  assertEqual(manifest.runs[0].status, 'error', 'and status');
  assertEqual(manifest.runs[0].description, '', 'a missing description becomes the empty string, not undefined');
  assertEqual(manifest.runs[0].files, ['results/run-001/stdout.txt', 'scripts/run-001.py'],
    'the duplicate path is collapsed, and the list is sorted');
  assertEqual(manifest.runs[1].files, ['a.txt', 'b.txt'], 'sorted for the second run too');
  assertEqual(manifest.runs[1].created_at, '2026-01-02T00:00:00Z', 'timestamps are carried through as-is (ISO strings)');

  assertEqual(deriveManifest([]).runs, [], 'no runs derives an empty manifest, not an error');
  assertEqual(deriveManifest(undefined).runs, [], 'and so does no argument at all');
}

console.log('\nbuildNotebook produces nbformat 4.5 with cell ids, one markdown + one code cell per run');
{
  const notebook = buildNotebook([
    {
      ordinal: 1,
      description: 'load the recording',
      code: 'import numpy as np\nx = np.arange(3)',
      stdout: 'ready\n',
      stderr: '',
      images: [{ data_base64: 'iVBORw0KGgo=' }],
    },
    { ordinal: 2, description: '', code: 'print(x)', stdout: '', stderr: 'boom\n', images: [] },
  ]);
  assertEqual(notebook.nbformat, 4, 'nbformat 4');
  assertEqual(notebook.nbformat_minor, 5, 'minor 5, which is what requires cell ids');
  assertEqual(notebook.cells.length, 4, 'two runs make four cells');
  assertEqual(notebook.cells[0].cell_type, 'markdown', 'markdown cell first');
  assertEqual(notebook.cells[0].source, 'load the recording', 'carrying the run\'s description');
  assertEqual(notebook.cells[1].cell_type, 'code', 'then the code cell');
  assertEqual(notebook.cells[1].source, 'import numpy as np\nx = np.arange(3)', 'carrying the run\'s code verbatim');
  assertEqual(notebook.cells[1].execution_count, null, 'never claims a real execution count');
  assertEqual(notebook.cells[1].outputs.length, 2, 'one stream output plus one image output');
  assertEqual(notebook.cells[1].outputs[0], { output_type: 'stream', name: 'stdout', text: 'ready\n' }, 'stdout as a stream output');
  assertEqual(
    notebook.cells[1].outputs[1],
    { output_type: 'display_data', data: { 'image/png': 'iVBORw0KGgo=' }, metadata: {} },
    'the figure as a display_data image/png output'
  );
  assertEqual(notebook.cells[2].source, 'Run 2', 'an empty description falls back to a label, never a blank cell');
  assertEqual(notebook.cells[3].outputs[0], { output_type: 'stream', name: 'stderr', text: 'boom\n' }, 'stderr as its own stream output');

  const ids = notebook.cells.map((c) => c.id);
  assert(new Set(ids).size === ids.length, 'every cell id is unique');
  assert(ids.every((id) => /^[a-zA-Z0-9_-]{1,64}$/.test(id)), 'every cell id matches what nbformat 4.5 requires');

  assertEqual(buildNotebook([]).cells, [], 'no runs is an empty, still-valid notebook');
}

console.log('\nWorkspaceStore reports IndexedDB unavailability as a value, never a throw or a silent success');
{
  // No dbFactory: exactly what running under Bun looks like, and what a
  // real browser with storage disabled looks like too. Not a stand-in.
  const store = new WorkspaceStore({ community: 'test-community' });
  assertEqual(store.available, false, 'available is false when there is no IndexedDB to reach');

  const putResult = await store.putFile('session-1', 'artifacts/ok.txt', new TextEncoder().encode('hi'));
  assertEqual(putResult, { ok: false, reason: 'IndexedDB is not available in this browser' }, 'putFile reports failure as a value');

  // Path and size validation happen BEFORE any IndexedDB access, so these
  // fail for THEIR OWN reason even though the store is also unavailable --
  // proving the checks are ordered validation-first, not merely reachable.
  const badPath = await store.putFile('session-1', '../escape.txt', new TextEncoder().encode('hi'));
  assert(!badPath.ok && /path segment/.test(badPath.reason), `a bad path fails on the path, not on IndexedDB (got ${JSON.stringify(badPath)})`);

  const tooBig = await store.putFile('session-1', 'artifacts/big.bin', new Uint8Array(WORKSPACE_LIMITS.MAX_FILE_BYTES + 1));
  assert(!tooBig.ok && /per-file limit/.test(tooBig.reason), `an oversized file fails on size, not on IndexedDB (got ${JSON.stringify(tooBig)})`);

  // The community cap is checked against a CALLER-SUPPLIED usage figure
  // (recordRun is what actually measures it), so this branch is reachable
  // and testable under Bun even though `available` is false: it must run
  // BEFORE anything touches IndexedDB, and this proves it does.
  const overCommunity = await store.putFile('session-1', 'artifacts/small.bin', new Uint8Array(1), WORKSPACE_LIMITS.MAX_COMMUNITY_BYTES);
  assert(!overCommunity.ok && /community limit/.test(overCommunity.reason),
    `a file that would push the community over its 250 MB cap fails on that, not on IndexedDB (got ${JSON.stringify(overCommunity)})`);
  const underCommunity = await store.putFile('session-1', 'artifacts/small.bin', new Uint8Array(1), WORKSPACE_LIMITS.MAX_COMMUNITY_BYTES - 1);
  assert(!underCommunity.ok && underCommunity.reason === 'IndexedDB is not available in this browser',
    `exactly at the cap still passes the size check, so IndexedDB is the ONLY reason it fails here (got ${JSON.stringify(underCommunity)})`);

  const recorded = await store.recordRun({
    session: 'session-1',
    callId: 'call-1',
    status: 'ok',
    description: 'a run',
    code: 'print(1)',
    stdout: '1\n',
    stderr: '',
    summary: 'variables: none',
    images: [],
    explicitFiles: [{ path: 'artifacts/x.txt', data_base64: 'aGk=' }],
  });
  assertEqual(recorded.ordinal, null, 'no ordinal could be assigned, since nothing could be read');
  assertEqual(recorded.savedArtifacts, [], 'nothing was saved');
  const failedPaths = recorded.failures.map((f) => f.path).sort();
  assertEqual(failedPaths, ['artifacts/x.txt', "scripts/run-NNN.py (and this run's other automatic files)"],
    'both the explicit file and the automatic files are named as failures');
  assert(recorded.failures.every((f) => f.reason === 'IndexedDB is not available in this browser'), 'each names why');

  const deleted = await store.deleteAll();
  assertEqual(deleted, { ok: false, reason: 'IndexedDB is not available in this browser' }, 'deleteAll reports failure as a value too');

  let threw = null;
  try {
    new WorkspaceStore({ community: '' });
  } catch (err) {
    threw = err;
  }
  assert(threw instanceof TypeError, 'a WorkspaceStore requires a non-empty community id');
}

console.log('\nthe zip writer produces bytes an INDEPENDENT reader accepts (unzip and Python zipfile)');
{
  const dir = mkdtempSync(join(tmpdir(), 'osa-workspace-zip-'));
  try {
    const notebookRuns = [{ ordinal: 1, description: 'demo', code: 'print(1)', stdout: '1\n', stderr: '', images: [] }];
    const runs = [{ ordinal: 1, callId: 'call-1', status: 'ok', description: 'demo', files: ['scripts/run-001.py'], timestamp: '2026-01-01T00:00:00Z' }];
    const bytes = buildWorkspaceZip([
      {
        session: 'sess-1',
        files: [
          { path: 'scripts/run-001.py', data: new TextEncoder().encode('print(1)') },
          { path: 'artifacts/data.bin', data: new Uint8Array([0, 1, 2, 255, 254]) },
        ],
        runs,
        notebookRuns,
      },
    ]);
    const zipPath = join(dir, 'workspace.zip');
    writeFileSync(zipPath, bytes);

    // Independent reader 1: the system `unzip`, in test mode (-t), which
    // verifies every entry's CRC-32 against its declared value -- proof the
    // headers and the checksums this module writes by hand are correct, not
    // merely that concat() glued the right pieces together.
    const unzipTest = Bun.spawnSync(['unzip', '-t', zipPath]);
    assert(unzipTest.exitCode === 0, `unzip -t verifies every entry's CRC-32 (got: ${unzipTest.stdout}${unzipTest.stderr})`);

    const listing = Bun.spawnSync(['unzip', '-Z1', zipPath]);
    const names = listing.stdout.toString().trim().split('\n').sort();
    assertEqual(names, [
      'sess-1/artifacts/data.bin',
      'sess-1/manifest.json',
      'sess-1/notebook.ipynb',
      'sess-1/scripts/run-001.py',
    ], 'every expected entry is present, once each');

    const extractedScript = Bun.spawnSync(['unzip', '-p', zipPath, 'sess-1/scripts/run-001.py']);
    assertEqual(extractedScript.stdout.toString(), 'print(1)', 'a text entry round-trips byte for byte');

    // Independent reader 2: Python's stdlib zipfile, via a throwaway script,
    // which also validates the notebook JSON and the manifest JSON parse
    // and carry the fields WorkspaceStore.exportZip is documented to write.
    const checker = `
import json
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as zf:
    bad = zf.testzip()
    if bad is not None:
        print("BAD_CRC:" + bad)
        sys.exit(1)
    manifest = json.loads(zf.read("sess-1/manifest.json"))
    notebook = json.loads(zf.read("sess-1/notebook.ipynb"))
    data = zf.read("sess-1/artifacts/data.bin")
    assert manifest["runs"][0]["call_id"] == "call-1", manifest
    assert notebook["nbformat"] == 4, notebook
    assert data == bytes([0, 1, 2, 255, 254]), data
    print("OK")
`;
    const checkerPath = join(dir, 'check.py');
    writeFileSync(checkerPath, checker);
    const py = Bun.spawnSync(['python3', checkerPath, zipPath]);
    assertEqual(py.stdout.toString().trim(), 'OK', `Python's zipfile reads it back too (got: ${py.stdout}${py.stderr})`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
