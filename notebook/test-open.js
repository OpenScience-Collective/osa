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
  resolveBaseUrl,
  storageDatabaseName,
  storageOptions,
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
}

console.log(`\n${'='.repeat(60)}`);
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
