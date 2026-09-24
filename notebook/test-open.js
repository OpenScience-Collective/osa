/**
 * open.js's pure logic: input validation, token filling in every cell of both
 * kinds, path/entry shape, and the browser-only baseUrl derivation (given plain
 * data rather than a real fetch). Nothing here touches `document`, `fetch` or
 * `localforage`; what does is `main()`, exercised only by
 * frontend/browser-harness-adjacent `notebook/e2e-check.js` in a real browser.
 *
 * Run with: bun notebook/test-open.js
 */

import {
  buildDirectoryEntry,
  buildNotebookEntry,
  communityProblem,
  datasetProblem,
  fillNotebook,
  fillSource,
  NOTEBOOK_TOKEN,
  notebookPath,
  raceStorage,
  resolveBaseUrl,
  STORAGE_TIMEOUT,
  storageDatabaseName,
  storageOptions,
  writeToStorage,
} from './open.js';

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

console.log('='.repeat(60));
console.log('notebook/open.js: pure logic');
console.log('='.repeat(60));

console.log('\ncommunityProblem');
{
  assertEqual(communityProblem('nemar', ['nemar', 'hed']), null, 'a known, well-formed community is fine');
  assert(communityProblem('Nemar', ['nemar']) !== null, 'uppercase is refused');
  assert(communityProblem('nemar;drop', ['nemar']) !== null, 'punctuation is refused');
  assert(communityProblem('', ['nemar']) !== null, 'empty is refused');
  assert(communityProblem('a'.repeat(65), ['a'.repeat(65)]) !== null, 'over 64 chars is refused');
  assert(communityProblem('hed', ['nemar']) !== null, 'well-formed but unknown is refused');
  assert(communityProblem(null, ['nemar']) !== null, 'non-string is refused');
}

console.log('\ndatasetProblem');
{
  const pattern = '^(nm|ds|on)[0-9]{6}$';
  assertEqual(datasetProblem('nm000103', pattern), null, 'a real NEMAR id matches');
  assertEqual(datasetProblem('ds005506', pattern), null, 'a ds id matches');
  assertEqual(datasetProblem('on005506', pattern), null, 'an on id matches');
  assert(datasetProblem('nm0001030', pattern) !== null, 'too many digits is refused');
  assert(datasetProblem('nm00010', pattern) !== null, 'too few digits is refused');
  assert(datasetProblem('xx000103', pattern) !== null, 'wrong prefix is refused');
  assert(datasetProblem('', pattern) !== null, 'empty is refused');
  assert(datasetProblem(null, pattern) !== null, 'non-string is refused');
  assert(datasetProblem('nm000103', '(unterminated') !== null, 'a pattern that will not compile refuses rather than throws');
  assert(
    datasetProblem('nm000103<script>', pattern) !== null,
    'a value with markup embedded alongside a valid prefix is refused, not partially matched'
  );
}

console.log('\ndatasetProblem: the generic safe-shape check runs BEFORE any community pattern');
{
  // Deliberately as loose as a community's own dataset_pattern could ever be,
  // standing in for one that is accidentally far too permissive (the Python
  // side refuses shipping this for real, but open.js must not depend on that
  // alone: defense in depth, docs/adr/0011-the-notebook-site.md).
  const wideOpenPattern = '^.*$';
  assertEqual(datasetProblem('nm000103', wideOpenPattern), null, 'a safe-shaped id still passes');
  assert(
    datasetProblem('nm000103"; import os; #', wideOpenPattern) !== null,
    'a quote is refused even though the community pattern would accept it'
  );
  assert(datasetProblem('nm 000103', wideOpenPattern) !== null, 'a space is refused even under a wide-open pattern');
  assert(datasetProblem('nm\\000103', wideOpenPattern) !== null, 'a backslash is refused even under a wide-open pattern');
  assert(datasetProblem('nm\n000103', wideOpenPattern) !== null, 'a newline is refused even under a wide-open pattern');
  assert(datasetProblem('a'.repeat(65), wideOpenPattern) !== null, 'over 64 chars is refused even under a wide-open pattern');
}

console.log('\nfillSource');
{
  assertEqual(fillSource('# {{dataset_id}} here', NOTEBOOK_TOKEN, 'nm000103'), '# nm000103 here', 'a plain string');
  assertEqual(
    fillSource(['line one\n', '{{dataset_id}} line two\n'], NOTEBOOK_TOKEN, 'nm000103'),
    ['line one\n', 'nm000103 line two\n'],
    'a list of lines, replaced line by line'
  );
  assertEqual(
    fillSource('{{dataset_id}} and {{dataset_id}} again', NOTEBOOK_TOKEN, 'nm000103'),
    'nm000103 and nm000103 again',
    'every occurrence in one line is replaced'
  );
  assertEqual(fillSource(42, NOTEBOOK_TOKEN, 'nm000103'), 42, 'a non-string/array source passes through unchanged');
}

console.log('\nfillNotebook');
{
  const notebook = {
    cells: [
      { cell_type: 'markdown', source: '# {{dataset_id}} in your browser' },
      { cell_type: 'code', source: ['index = await eegprep_lean.read_index("{{dataset_id}}")\n'] },
      { cell_type: 'code', source: 'print(1)' },
    ],
    metadata: {},
    nbformat: 4,
    nbformat_minor: 5,
  };

  const filled = fillNotebook(notebook, NOTEBOOK_TOKEN, 'nm000103');

  assertEqual(filled.cells[0].source, '# nm000103 in your browser', 'markdown cell filled');
  assertEqual(
    filled.cells[1].source,
    ['index = await eegprep_lean.read_index("nm000103")\n'],
    'code cell filled'
  );
  assertEqual(filled.cells[2].source, 'print(1)', 'a cell with no token is left alone');
  assertEqual(notebook.cells[0].source, '# {{dataset_id}} in your browser', 'the source notebook is never mutated');
}

console.log('\nnotebookPath, buildDirectoryEntry, buildNotebookEntry');
{
  assertEqual(notebookPath('nemar', 'nm000103'), 'nemar/nm000103.ipynb', 'path shape');

  const dir = buildDirectoryEntry('nemar', '2026-09-23T00:00:00.000Z');
  assertEqual(dir.type, 'directory', 'directory entry type');
  assertEqual(dir.path, 'nemar', 'directory entry path equals its name at the top level');
  assertEqual(dir.content, null, 'a directory entry has no content');

  const content = { nbformat: 4, cells: [] };
  const nb = buildNotebookEntry('nm000103.ipynb', 'nemar/nm000103.ipynb', content, '2026-09-23T00:00:00.000Z');
  assertEqual(nb.type, 'notebook', 'notebook entry type');
  assertEqual(nb.mimetype, 'application/json', 'notebook entry mimetype');
  assertEqual(nb.content, content, 'notebook entry carries the filled content verbatim');
  assertEqual(nb.size, JSON.stringify(content).length, 'notebook entry size is the serialized content length');
}

console.log('\nresolveBaseUrl, storageDatabaseName, storageOptions');
{
  assertEqual(resolveBaseUrl({ 'jupyter-config-data': { baseUrl: './' } }, '/'), '/', 'a site root resolves to "/"');
  assertEqual(
    resolveBaseUrl({ 'jupyter-config-data': { baseUrl: './' } }, '/preview/pr-42/'),
    '/preview/pr-42/',
    'a subdirectory deploy resolves against its own directory'
  );
  assertEqual(resolveBaseUrl(null, '/'), '/', 'a missing config falls back to the default "./"');
  assertEqual(resolveBaseUrl({ 'jupyter-config-data': { baseUrl: '/already/absolute/' } }, '/'), '/already/absolute/', 'an already-absolute baseUrl passes through');

  assertEqual(storageDatabaseName('/'), 'JupyterLite Storage - /', 'database name matches a live instance (see docs/adr/0011-the-notebook-site.md)');
  assertEqual(
    storageOptions('/'),
    { name: 'JupyterLite Storage - /', storeName: 'files', version: 1, description: 'Offline Storage for Notebooks and Files' },
    'storage options match JupyterLite\'s own drive'
  );

  // Composed, at the production shape: notebook.osc.earth/osa serves under
  // "/osa/", never at a bare host root (see this file's own module docstring
  // and docs/adr/0011-the-notebook-site.md's OSC-naming-rule decision).
  assertEqual(
    storageDatabaseName(resolveBaseUrl({ 'jupyter-config-data': { baseUrl: './' } }, '/osa/')),
    'JupyterLite Storage - /osa/',
    'the production /osa/ deploy opens the exact database name a live instance uses'
  );
}

/**
 * A stand-in for the third-party storage library at the boundary (localforage
 * itself), not for this module's own logic: real getItem/setItem calls, real
 * Promises, an in-memory Map rather than IndexedDB. NO MOCK of writeToStorage
 * or raceStorage themselves runs anywhere below -- both run for real.
 */
function fakeStore(initialEntries = []) {
  const data = new Map(initialEntries);
  return {
    data,
    async getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    async setItem(key, value) {
      data.set(key, value);
      return value;
    },
  };
}

/**
 * Also a stand-in for localforage at the boundary, but one whose calls never
 * settle -- what some private-browsing and storage-blocked modes actually do
 * to a real IndexedDB request (issue: a reader stuck on "Opening your
 * notebook..." forever). A real, never-resolving Promise, not a mocked timeout.
 */
function hangingStore() {
  return {
    getItem: () => new Promise(() => {}),
    setItem: () => new Promise(() => {}),
  };
}

console.log('\nwriteToStorage: the no-overwrite rule');
{
  const store = fakeStore();
  const now = '2026-09-23T00:00:00.000Z';
  const original = { nbformat: 4, cells: [] };

  await writeToStorage(store, 'nemar', 'nm000103', 'nemar/nm000103.ipynb', original, now);
  assert(store.data.has('nemar'), 'the directory entry is written on first open');
  assert(store.data.has('nemar/nm000103.ipynb'), 'the notebook is written on first open');
  assertEqual(store.data.get('nemar/nm000103.ipynb').content, original, 'the first write carries the filled starter');

  // A reader's own edit, as if from an earlier visit -- writeToStorage must
  // never touch this again.
  const edited = { name: 'nm000103.ipynb', path: 'nemar/nm000103.ipynb', content: { nbformat: 4, cells: [{ cell_type: 'code', source: 'EDITED' }] } };
  store.data.set('nemar/nm000103.ipynb', edited);

  await writeToStorage(store, 'nemar', 'nm000103', 'nemar/nm000103.ipynb', original, now);
  assertEqual(store.data.get('nemar/nm000103.ipynb'), edited, "a second open never overwrites the reader's own edit");
}

console.log('\nraceStorage: a storage call that never settles');
{
  const outcome = await raceStorage(
    writeToStorage(hangingStore(), 'nemar', 'nm000103', 'nemar/nm000103.ipynb', { nbformat: 4, cells: [] }, '2026-09-23T00:00:00.000Z'),
    25 // short on purpose for a fast test; main() itself uses STORAGE_TIMEOUT_MS
  );
  assertEqual(outcome, STORAGE_TIMEOUT, 'a storage call that never settles resolves to the timeout sentinel rather than hanging forever');
}

console.log('\nraceStorage: a storage call that settles well within the timeout');
{
  const outcome = await raceStorage(
    writeToStorage(fakeStore(), 'nemar', 'nm000103', 'nemar/nm000103.ipynb', { nbformat: 4, cells: [] }, '2026-09-23T00:00:00.000Z'),
    5000
  );
  assert(outcome !== STORAGE_TIMEOUT, 'a storage call that settles normally is never mistaken for a timeout');
}

console.log(`\n${'='.repeat(60)}`);
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
