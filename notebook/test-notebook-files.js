/**
 * osa-notebook-files.js's pure logic (#673, #676): how a notebook is named, what
 * an import may hold, and which messages from the widget the notebook page acts
 * on. Nothing here touches `document`, JupyterLite or storage; the page that
 * uses it is exercised by notebook/e2e-check.js in a real browser.
 *
 * The notebooks are the real builder's output over recorded conversations, so
 * the shapes an import actually has are what is validated.
 *
 * Run with: bun notebook/test-notebook-files.js
 */

import { buildConversationNotebook } from '../frontend/osa-notebook-builder.js';
import { NOTEBOOK_CONVERSATIONS } from '../frontend/test-support/notebook-conversations.js';
import {
  AUTORUN_TAG,
  belongsToDataset,
  blankNotebook,
  importedName,
  importedPath,
  kindOf,
  MAX_CELLS,
  MAX_IMPORT_BYTES,
  MAX_TITLE_CHARS,
  renamedPath,
  sourceSignature,
  stripAutorun,
  tabContext,
  tabRequestId,
  TAB_REQUESTS,
  titleFromPath,
  validateImport,
  validateTabMessage,
  withKind,
} from './osa-notebook-files.js';

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

// What the page knows about the community, as starters/index.json has it.
const STARTERS = { nemar: { dataset_pattern: '^(nm|ds|on|xx)[0-9]{6}$' } };
const CONTEXT = { community: 'nemar', datasetPattern: STARTERS.nemar.dataset_pattern };
const DATASET = 'on002718';

const conversation = NOTEBOOK_CONVERSATIONS.erp();
const built = buildConversationNotebook({ ...conversation, datasetId: DATASET });
const importInput = (overrides = {}) => ({
  datasetId: DATASET,
  title: built.notebook.cells[0] ? conversation.title : 'x',
  notebook: structuredClone(built.notebook),
  stats: built.stats,
  ...overrides,
});

const ITEMS = [
  { path: `nemar/${DATASET}.ipynb`, kind: 'playground' },
  { path: `nemar/${DATASET} · ERPs.ipynb`, kind: 'chat' },
  { path: `nemar/${DATASET} · Untitled.ipynb`, kind: 'blank' },
];
const message = (type, fields = {}) => ({ target: 'osa-notebook', type, requestId: 'req-1', ...fields });
const check = (data, items = ITEMS) => validateTabMessage(data, { ...CONTEXT, items });

console.log('='.repeat(60));
console.log('notebook/osa-notebook-files.js: pure logic');
console.log('='.repeat(60));

console.log('\nimportedName: what a title may be in a file name');
{
  assertEqual(importedName('Famous vs scrambled'), 'Famous vs scrambled', 'a plain title is kept');
  assertEqual(importedName('ERPs: famous/vs scrambled'), 'ERPs famousvs scrambled', 'separators and colons are removed');
  assertEqual(importedName('../x'), 'x', 'a parent-directory title cannot climb: the dots at its edge go too');
  assertEqual(importedName('..\\..\\x'), 'x', 'a backslash one the same');
  assertEqual(importedName('a/b'), 'ab', 'a slash inside is removed');
  assertEqual(importedName('a*b?c"d<e>f|g'), 'abcdefg', 'the characters a file system refuses are removed');
  assertEqual(importedName('a\u0000b\u001fc\u007fd\u0085e'), 'abcde', 'control characters are removed');
  assertEqual(importedName('line one\nline two\ttabbed'), 'line one line two tabbed', 'newlines and tabs become spaces');
  assertEqual(importedName('a\u202eb\u2028c'), 'ab c', 'direction overrides are removed, and a line separator is a space');
  assertEqual(importedName('  spaced   out  '), 'spaced out', 'whitespace is collapsed and trimmed');
  assertEqual(importedName('ends with a dot.'), 'ends with a dot', 'a trailing dot is trimmed, so the extension is not doubled');
  for (const empty of ['', '   ', '...', '/\\:*?', null, undefined, 42, {}]) {
    assertEqual(importedName(empty), 'Notebook', `${JSON.stringify(empty)} becomes "Notebook"`);
  }
  const long = importedName('x'.repeat(200));
  assertEqual(Array.from(long).length, MAX_TITLE_CHARS, `a long title is cut to ${MAX_TITLE_CHARS} characters`);
  const wide = importedName('\u{1F9E0}'.repeat(100));
  assertEqual(Array.from(wide).length, MAX_TITLE_CHARS, 'and counted in characters, not code units');
  assert(!importedName(`${'x'.repeat(MAX_TITLE_CHARS - 1)} .y`).endsWith(' '), 'a cut that ends on a space does not leave it');
  assertEqual(importedName('a / b'), 'a b', 'a removed separator between spaces leaves one space');
  assertEqual(importedName('ERPs - a : b'), 'ERPs - a b', 'a removed colon between spaces leaves one space');
  assertEqual(importedName('a​b‌‍⁠c'), 'abc', 'zero-width characters are removed');
  for (const title of ['../a/b: c', 'a / b', 'ERPs - a : b', 'a ​ b', ' / x / ', 'x'.repeat(63) + ' / y']) {
    assertEqual(importedName(importedName(title)), importedName(title), `cleaning twice changes nothing: ${JSON.stringify(title)}`);
  }
}

console.log('\nimportedPath: where an import is stored');
{
  assertEqual(importedPath('nemar', DATASET, 'Famous vs scrambled'), `nemar/${DATASET} · Famous vs scrambled.ipynb`, 'community, dataset, middle dot, title');
  const hostile = importedPath('nemar', DATASET, '../../etc/passwd');
  assert(hostile.startsWith(`nemar/${DATASET} · `) && hostile.split('/').length === 2, 'a hostile title stays one file in the community directory');
  assert(!hostile.includes('..'.concat('/')), 'with no parent-directory step in it');

  const taken = [`nemar/${DATASET} · ERPs.ipynb`];
  assertEqual(importedPath('nemar', DATASET, 'ERPs', taken), `nemar/${DATASET} · ERPs 2.ipynb`, 'a name that exists gets " 2"');
  taken.push(`nemar/${DATASET} · ERPs 2.ipynb`);
  assertEqual(importedPath('nemar', DATASET, 'ERPs', taken), `nemar/${DATASET} · ERPs 3.ipynb`, 'and then " 3"');
  assertEqual(importedPath('nemar', DATASET, 'ERPS', [`nemar/${DATASET} · erps.ipynb`]), `nemar/${DATASET} · ERPS 2.ipynb`, 'collisions ignore case');
  assertEqual(importedPath('nemar', DATASET, 'ERPs', new Set(taken)), `nemar/${DATASET} · ERPs 3.ipynb`, 'taken may be a Set');
  assertEqual(importedPath('nemar', DATASET, '', []), `nemar/${DATASET} · Notebook.ipynb`, 'an empty title is "Notebook"');
  assertEqual(importedPath('nemar', DATASET, 'ERPs', [`nemar/${DATASET}.ipynb`]), `nemar/${DATASET} · ERPs.ipynb`, 'the starter never collides with a title');
}

console.log('\nrenamedPath: a rename keeps the directory and the dataset prefix');
{
  const path = `nemar/${DATASET} · ERPs.ipynb`;
  assertEqual(renamedPath(path, 'Spectra'), `nemar/${DATASET} · Spectra.ipynb`, 'the title changes, the prefix stays');
  assertEqual(renamedPath(path, 'ERPs', [path]), path, 'the same title is no change, and the notebook is not a collision with itself');
  assertEqual(renamedPath(path, 'Spectra', [path, `nemar/${DATASET} · Spectra.ipynb`]), `nemar/${DATASET} · Spectra 2.ipynb`, 'a name another notebook has gets " 2"');
  assertEqual(renamedPath(path, '../../x/y'), `nemar/${DATASET} · xy.ipynb`, 'a hostile title cannot change the directory');
  assertEqual(renamedPath(path, ''), `nemar/${DATASET} · Notebook.ipynb`, 'an empty title is "Notebook"');
  assertEqual(renamedPath(`nemar/${DATASET} · A · B.ipynb`, 'C'), `nemar/${DATASET} · C.ipynb`, 'only the first separator splits the dataset off');
}

console.log('\ntitleFromPath and belongsToDataset');
{
  assertEqual(titleFromPath(`nemar/${DATASET}.ipynb`, DATASET), 'Playground', 'the dataset\'s own file is the Playground');
  assertEqual(titleFromPath(`nemar/${DATASET} · ERPs: x.ipynb`, DATASET), 'ERPs: x', 'an import loses its prefix');
  assertEqual(titleFromPath(`nemar/${DATASET} · ERPs.ipynb`), 'ERPs', 'with no dataset given, it is read from the name');
  assertEqual(titleFromPath(`nemar/${DATASET}.ipynb`), 'Playground', 'and so is the Playground');
  assertEqual(titleFromPath('nemar/other.ipynb', DATASET), 'other', 'a name that is not ours is shown as it is');
  assert(belongsToDataset(`${DATASET}.ipynb`, DATASET), 'the starter belongs');
  assert(belongsToDataset(`${DATASET} · x.ipynb`, DATASET), 'an import belongs');
  assert(!belongsToDataset(`${DATASET}0 · x.ipynb`, DATASET), 'a longer dataset id does not');
  assert(!belongsToDataset('on000001.ipynb', DATASET), 'another dataset\'s starter does not');
  assert(!belongsToDataset(`${DATASET} · x.txt`, DATASET), 'only notebooks do');
  assert(!belongsToDataset(undefined, DATASET), 'a missing name does not');
}

console.log('\nstripAutorun: an import never runs by itself');
{
  const tagged = {
    nbformat: 4,
    cells: [
      { cell_type: 'code', metadata: { tags: [AUTORUN_TAG, 'keep'] }, source: 'a' },
      { cell_type: 'code', metadata: { tags: [AUTORUN_TAG] }, source: 'b' },
      { cell_type: 'markdown', metadata: {}, source: 'c' },
    ],
  };
  const stripped = stripAutorun(tagged);
  assertEqual(stripped.cells[0].metadata.tags, ['keep'], 'another tag stays');
  assert(!('tags' in stripped.cells[1].metadata), 'a list left empty goes');
  assertEqual(tagged.cells[0].metadata.tags, [AUTORUN_TAG, 'keep'], 'the original is not changed');
  assert(!JSON.stringify(stripped).includes(AUTORUN_TAG), 'no autorun tag is left anywhere');
}

console.log('\nvalidateImport: the builder\'s own notebook');
{
  const checked = validateImport(importInput(), CONTEXT);
  assert(checked.ok, `a notebook from buildConversationNotebook is accepted${checked.ok ? '' : `: ${checked.error}`}`);
  assertEqual(checked.notebook.cells.length, built.notebook.cells.length, 'with every cell');
  assertEqual(checked.datasetId, DATASET, 'for its dataset');
  assert(checked.notebook !== built.notebook, 'as a copy');
  const codeCells = checked.notebook.cells.filter((cell) => cell.cell_type === 'code');
  assert(codeCells.length > 0 && codeCells.every((cell) => Array.isArray(cell.outputs) && cell.outputs.length === 0 && cell.execution_count === null), 'code cells carry no outputs and no execution count');
  assertEqual(validateImport(importInput({ title: '../a/b' }), CONTEXT).title, 'ab', 'the title comes back cleaned');
  assert(validateImport(importInput({ stats: undefined }), CONTEXT).ok, 'stats are for the widget\'s banner, so an import without them is fine');
}

console.log('\nvalidateImport: the autorun tag');
{
  const tagged = importInput();
  tagged.notebook.cells[1].metadata = { tags: [AUTORUN_TAG] };
  const checked = validateImport(tagged, CONTEXT);
  assert(checked.ok, 'a notebook with an autorun cell is accepted');
  assert(!JSON.stringify(checked.notebook).includes(AUTORUN_TAG), 'and its tag is stripped');
}

console.log('\nvalidateImport: rejected datasets');
{
  for (const datasetId of ['', null, undefined, 42, '../etc', 'on00271', 'on0027180', 'zz002718', 'ON002718', 'on002718 ', 'on002718/x', 'on002718\n']) {
    const checked = validateImport(importInput({ datasetId }), CONTEXT);
    assert(!checked.ok && /dataset/.test(checked.error), `${JSON.stringify(datasetId)} is refused as a dataset`);
  }
  const loose = validateImport(importInput({ datasetId: '../x' }), { community: 'nemar', datasetPattern: '.*' });
  assert(!loose.ok, 'a community whose own pattern is too loose still cannot take a path-shaped dataset id');
  const broken = validateImport(importInput(), { community: 'nemar', datasetPattern: '(' });
  assert(!broken.ok, 'a pattern that does not compile refuses everything');
  assert(!validateImport(importInput(), { community: '../x', datasetPattern: '.*' }).ok, 'and so does a community that is not a plain id');
}

console.log('\nvalidateImport: rejected notebooks');
{
  const fails = (input, what) => {
    const checked = validateImport(input, CONTEXT);
    assert(!checked.ok && typeof checked.error === 'string' && checked.error !== '', `${what} is refused${checked.ok ? '' : `: ${checked.error}`}`);
    return checked;
  };
  fails(null, 'no input');
  fails('a string', 'a string for an input');
  fails(importInput({ notebook: null }), 'no notebook');
  fails(importInput({ notebook: [] }), 'an array for a notebook');
  fails(importInput({ title: undefined }), 'no title');
  fails(importInput({ title: 7 }), 'a title that is not text');

  const v3 = importInput();
  v3.notebook.nbformat = 3;
  assert(/nbformat 4/.test(fails(v3, 'nbformat 3').error), 'nbformat 3, saying only 4 is taken');
  const none = importInput();
  delete none.notebook.nbformat;
  fails(none, 'a notebook with no nbformat');
  const noCells = importInput();
  noCells.notebook.cells = 'x';
  fails(noCells, 'cells that are not a list');

  const raw = importInput();
  raw.notebook.cells.push({ cell_type: 'raw', metadata: {}, source: 'x' });
  assert(/only markdown and code/.test(fails(raw, 'a raw cell').error), 'a raw cell, saying only markdown and code are taken');
  const odd = importInput();
  odd.notebook.cells[0].cell_type = 'sql';
  fails(odd, 'an unknown cell type');
  const nonObject = importInput();
  nonObject.notebook.cells.push('print(1)');
  fails(nonObject, 'a cell that is not an object');
  const noSource = importInput();
  delete noSource.notebook.cells[0].source;
  fails(noSource, 'a cell with no source');
  const badLines = importInput();
  badLines.notebook.cells[0].source = ['fine', 7];
  fails(badLines, 'source lines that are not text');

  const few = importInput();
  few.notebook.cells = Array.from({ length: MAX_CELLS }, (_, i) => ({ cell_type: 'markdown', id: `c${i}`, metadata: {}, source: 'x' }));
  assert(validateImport(few, CONTEXT).ok, `${MAX_CELLS} cells are taken`);
  few.notebook.cells.push({ cell_type: 'markdown', id: 'one-more', metadata: {}, source: 'x' });
  assert(/200/.test(fails(few, `${MAX_CELLS + 1} cells`).error), 'one more is not, and the limit is named');

  const big = importInput();
  big.notebook.cells[0].source = 'x'.repeat(MAX_IMPORT_BYTES + 1);
  assert(/MB/.test(fails(big, 'a notebook over 2 MB').error), 'and its size, naming the limit');
  const wideChars = importInput();
  wideChars.notebook.cells[0].source = '€'.repeat(Math.ceil(MAX_IMPORT_BYTES / 3) + 10);
  fails(wideChars, 'a notebook whose characters are wider than a byte and pass 2 MB only in bytes');
  const justUnder = importInput();
  justUnder.notebook.cells = [{ cell_type: 'markdown', id: 'a', metadata: {}, source: 'x'.repeat(MAX_IMPORT_BYTES - 1000) }];
  assert(validateImport(justUnder, CONTEXT).ok, 'a notebook just under 2 MB is taken');

  const circular = importInput();
  circular.notebook.self = circular.notebook;
  fails(circular, 'a notebook that is not plain data');
}

console.log('\nthe kind a notebook records');
{
  const chat = withKind(built.notebook, 'chat');
  assertEqual(kindOf(chat), 'chat', 'a chat notebook says so in its own metadata');
  assertEqual(built.notebook.metadata.osa, undefined, 'the original is not changed');
  assertEqual(kindOf(blankNotebook()), 'blank', 'a blank notebook says so');
  assertEqual(kindOf(built.notebook), 'blank', 'a file with no kind is blank, since it is not an import');
  assertEqual(kindOf({ metadata: { osa: { kind: 'playground' } } }), 'blank', 'the playground is decided by its path, not by metadata a reader can write');
  assertEqual(kindOf(null), 'blank', 'nothing is blank');
  const blank = blankNotebook();
  assert(blank.nbformat === 4 && blank.cells.length === 1 && blank.cells[0].source === '', 'a blank notebook is nbformat 4 with one empty cell');
  assert(validateImport({ datasetId: DATASET, title: 'x', notebook: blank }, CONTEXT).ok, 'and passes the import rules itself');
}

console.log('\nsourceSignature: what counts as an edit');
{
  const original = importInput().notebook;
  const run = structuredClone(original);
  for (const cell of run.cells) {
    if (cell.cell_type === 'code') {
      cell.execution_count = 4;
      cell.outputs = [{ output_type: 'stream', name: 'stdout', text: 'hi' }];
    }
  }
  assertEqual(sourceSignature(run), sourceSignature(original), 'running cells is not an edit');
  const lines = structuredClone(original);
  lines.cells[1].source = original.cells[1].source.split(/(?<=\n)/);
  assertEqual(sourceSignature(lines), sourceSignature(original), 'a source as lines is the same source');
  const edited = structuredClone(original);
  edited.cells[1].source += ' ';
  assert(sourceSignature(edited) !== sourceSignature(original), 'a changed source is an edit');
  const added = structuredClone(original);
  added.cells.push({ cell_type: 'code', source: '' });
  assert(sourceSignature(added) !== sourceSignature(original), 'so is an added cell');
  assertEqual(sourceSignature(null), '[]', 'nothing has no cells');
}

console.log('\ntabContext: which notebook the page is for');
{
  const ok = tabContext(`nemar/${DATASET}.ipynb`, STARTERS);
  assertEqual(ok, { ok: true, community: 'nemar', datasetId: DATASET, datasetPattern: STARTERS.nemar.dataset_pattern }, 'the dataset\'s own notebook names the community and dataset');
  for (const [path, what] of [
    [`nemar/${DATASET} · ERPs.ipynb`, 'an import'],
    [`other/${DATASET}.ipynb`, 'a community with no starter'],
    ['nemar/notadataset.ipynb', 'a name that is not a dataset'],
    [`${DATASET}.ipynb`, 'a notebook outside any community'],
    [`nemar/sub/${DATASET}.ipynb`, 'a notebook in a subdirectory'],
    [`nemar/${DATASET}.txt`, 'a file that is not a notebook'],
    [null, 'no path'],
  ]) {
    assert(!tabContext(path, STARTERS).ok, `${what} is not a starter`);
  }
  assert(!tabContext(`nemar/${DATASET}.ipynb`, null).ok, 'an index that did not load is not one');
}

console.log('\nvalidateTabMessage: what the page acts on');
{
  const path = ITEMS[1].path;
  assertEqual(check(message('new-notebook')), { ok: true, request: { type: 'new-notebook', requestId: 'req-1' } }, 'new-notebook needs nothing');
  assertEqual(check(message('open-notebook', { path })), { ok: true, request: { type: 'open-notebook', requestId: 'req-1', path } }, 'open-notebook with a listed path');
  assertEqual(check(message('run-all', { path })).request.path, path, 'run-all with a listed path');
  assertEqual(check(message('undo-import', { path })).request.path, path, 'undo-import with a listed path');
  assertEqual(check(message('close-notebook', { path })).request.type, 'close-notebook', 'close-notebook with a listed path');
  assertEqual(check(message('rename-notebook', { path, title: 'New' })).request, { type: 'rename-notebook', requestId: 'req-1', path, title: 'New' }, 'rename-notebook with a listed path and a title');
  const imported = check(message('import-notebook', importInput()));
  assert(imported.ok && imported.request.type === 'import-notebook' && imported.request.datasetId === DATASET, 'import-notebook with a valid import');
  assert(!JSON.stringify(imported.request.notebook).includes(AUTORUN_TAG), 'carries the notebook already stripped');
  assertEqual(TAB_REQUESTS.length, 7, 'there are seven requests, and every one is covered above');
}

console.log('\nvalidateTabMessage: not ours, or not known');
{
  for (const [data, what] of [
    [null, 'nothing'],
    ['osa-notebook', 'a string'],
    [[], 'an array'],
    [{ type: 'new-notebook', requestId: 'r' }, 'a message with no target'],
    [{ target: 'someone-else', type: 'new-notebook', requestId: 'r' }, 'a foreign target'],
    [{ target: 'osa-notebook', type: 'theme', scheme: 'dark' }, 'a theme message, which is the page\'s own'],
    [message('delete-everything'), 'an unknown type'],
    [message('__proto__'), 'a type named after a prototype'],
    [message('constructor'), 'a type named constructor'],
    [message(undefined), 'no type'],
    [message(7), 'a type that is not text'],
  ]) {
    const checked = check(data);
    assert(!checked.ok && checked.ignored === true, `${what} is ignored, with no reply`);
  }
  for (const requestId of [undefined, '', 7, null, 'x'.repeat(65), 'has space', 'a/b', '../x']) {
    const checked = check({ ...message('new-notebook'), requestId });
    assert(!checked.ok && checked.ignored === true, `${JSON.stringify(requestId)} is no request id, so there is nothing to reply to`);
    assertEqual(tabRequestId({ ...message('new-notebook'), requestId }), null, `tabRequestId gives no id for ${JSON.stringify(requestId)}`);
  }
}

console.log('\ntabRequestId: the messages a page that cannot validate may still answer');
{
  for (const type of TAB_REQUESTS) assertEqual(tabRequestId(message(type)), 'req-1', `a ${type} request is answered under its id`);
  for (const [data, what] of [
    [null, 'null'],
    ['osa-notebook', 'a string'],
    [{ type: 'new-notebook', requestId: 'r' }, 'a message with no target'],
    [{ target: 'someone-else', type: 'new-notebook', requestId: 'r' }, 'a foreign target'],
    [message('theme'), 'a theme message'],
    [message('delete-everything'), 'an unknown type'],
    [message('__proto__'), 'a type named after a prototype'],
  ]) {
    assertEqual(tabRequestId(data), null, `${what} is not answered`);
  }
}

console.log('\nvalidateTabMessage: paths outside the page\'s own list');
{
  const refused = (data, what) => {
    const checked = check(data);
    assert(!checked.ok && !checked.ignored && checked.requestId === 'req-1' && typeof checked.error === 'string', `${what} is refused, and answered under its request id`);
  };
  for (const type of ['open-notebook', 'run-all', 'undo-import', 'close-notebook', 'rename-notebook']) {
    const extra = type === 'rename-notebook' ? { title: 'x' } : {};
    refused(message(type, { ...extra, path: 'nemar/other.ipynb' }), `${type} of a notebook that is not listed`);
    refused(message(type, { ...extra, path: '../etc/passwd' }), `${type} of a path outside the community`);
    refused(message(type, { ...extra, path: 'other/on002718.ipynb' }), `${type} of another community's path`);
    refused(message(type, { ...extra, path: `nemar/../nemar/${DATASET}.ipynb` }), `${type} of a path that climbs and comes back`);
    refused(message(type, { ...extra, path: `nemar/sub/${DATASET}.ipynb` }), `${type} of a path in a subdirectory`);
    refused(message(type, { ...extra, path: `/nemar/${DATASET}.ipynb` }), `${type} of an absolute path`);
    refused(message(type, { ...extra, path: `nemar/${DATASET}.ipynb\u0000` }), `${type} of a path with a control character`);
    refused(message(type, { ...extra }), `${type} with no path`);
    refused(message(type, { ...extra, path: 7 }), `${type} with a path that is not text`);
    refused(message(type, { ...extra, path: { toString: () => ITEMS[1].path } }), `${type} with a path that only looks like one`);
  }
  assert(!check(message('open-notebook', { path: ITEMS[1].path }), []).ok, 'with nothing listed, nothing opens');
  refused(message('rename-notebook', { path: ITEMS[1].path }), 'a rename with no title');
  refused(message('rename-notebook', { path: ITEMS[1].path, title: 7 }), 'a rename with a title that is not text');
  refused(message('rename-notebook', { path: ITEMS[0].path, title: 'x' }), 'a rename of the starter');
  refused(message('close-notebook', { path: ITEMS[0].path }), 'closing the starter');
  assert(check(message('open-notebook', { path: ITEMS[0].path })).ok, 'but the starter can be opened');
  assert(check(message('run-all', { path: ITEMS[0].path })).ok, 'and run');
}

console.log('\nvalidateTabMessage: a bad import is answered, not dropped');
{
  const checked = check(message('import-notebook', importInput({ datasetId: '../x' })));
  assert(!checked.ok && !checked.ignored && checked.requestId === 'req-1' && /dataset/.test(checked.error), 'a rejected dataset');
  const v3 = importInput();
  v3.notebook.nbformat = 3;
  assert(!check(message('import-notebook', v3)).ok, 'a wrong nbformat');
  assert(!check(message('import-notebook', { ...importInput(), notebook: undefined })).ok, 'a missing notebook');
}

console.log(`\n${'='.repeat(60)}`);
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
