/**
 * The pure half of the notebook page's tabs and import (issues #673 and #676,
 * docs/adr/0017-notebook-import-and-tabs.md): how a notebook is named, what an
 * import may contain, and which messages from the widget the page acts on.
 *
 * Nothing here touches `document`, `window`, JupyterLite or storage, so
 * `notebook/test-notebook-files.js` runs it under Bun exactly as the page runs it.
 * `osa-bridge.js`, a classic script, loads it with a dynamic `import()`; the
 * build copies it next to the bridge (BOOTSTRAP_FILES in
 * scripts/build_notebook_site.py).
 *
 * The page never trusts a name or a path from a message. A title is reduced to a
 * safe file name here, a dataset is checked against the community's own pattern
 * (`datasetProblem` in open.js, the same check a notebook link gets), and a path
 * is acted on only if it is one the page itself listed under `<community>/`.
 */

import { communityProblem, datasetProblem } from './open.js';

/** The tag a starter puts on the setup cells the bridge runs by itself. */
export const AUTORUN_TAG = 'osa-autorun';

/** Most cells an import may have. */
export const MAX_CELLS = 200;

/** Largest import, as UTF-8 bytes of its JSON. */
export const MAX_IMPORT_BYTES = 2 * 1024 * 1024;

/** Longest title that becomes part of a file name, in characters. */
export const MAX_TITLE_CHARS = 64;

/** Between the dataset and the title in a file name: `on002718 · Famous vs scrambled.ipynb`. */
export const NAME_SEPARATOR = ' · ';

/** What a title that is empty once cleaned becomes. */
export const DEFAULT_TITLE = 'Notebook';

/** The title of a blank notebook, before a reader renames it. */
export const BLANK_TITLE = 'Untitled';

/** What the dataset's own notebook, the starter, is called in the strip. */
export const PLAYGROUND_TITLE = 'Playground';

/** The kinds of notebook the strip lists. */
export const NOTEBOOK_KINDS = ['playground', 'chat', 'blank'];

/** The messages from the widget that this module validates (theme is the bridge's own). */
export const TAB_REQUESTS = [
  'import-notebook',
  'run-all',
  'undo-import',
  'new-notebook',
  'open-notebook',
  'rename-notebook',
  'close-notebook',
];

const EXTENSION = '.ipynb';
const REQUEST_ID_PATTERN = /^[A-Za-z0-9._:-]{1,64}$/;

// Path separators and the characters Windows and macOS refuse in a file name,
// every control character (C0 and C1), and the line, paragraph, zero-width and
// direction-override characters that can make a name read as something else
// (or make two names that look alike differ).
// eslint-disable-next-line no-control-regex
const UNSAFE_NAME_CHARS =
  /[/\\:*?"<>|\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\u2066-\u2069]/g;

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function fail(error) {
  return { ok: false, error };
}

/**
 * A title as it may appear in a file name: `/ \ : * ? " < > |`, control and
 * zero-width characters are removed, whitespace runs become one space (after
 * the removal, so `a / b` is `a b`), dots and spaces at either end are trimmed
 * (so `../x` is `x`), and at most 64 characters stay. Nothing left means
 * "Notebook". Cleaning a cleaned title changes nothing.
 *
 * @param {unknown} title
 */
export function importedName(title) {
  const text = typeof title === 'string' ? title : '';
  // Whitespace is turned into spaces first so a newline (a control character)
  // is not removed and does not glue two words together; the runs the removal
  // leaves behind are collapsed again afterwards.
  const cleaned = text
    .replace(/\s+/g, ' ')
    .replace(UNSAFE_NAME_CHARS, '')
    .replace(/\s+/g, ' ')
    .replace(/^[. ]+|[. ]+$/g, '');
  const capped = Array.from(cleaned).slice(0, MAX_TITLE_CHARS).join('').replace(/[. ]+$/g, '');
  return capped === '' ? DEFAULT_TITLE : capped;
}

function lowerSet(paths) {
  return new Set(Array.from(paths || [], (path) => String(path).toLowerCase()));
}

/** `base` plus ".ipynb", or with " 2", " 3" and so on until no path in `taken` has the name. */
function freePath(base, taken) {
  const used = lowerSet(taken);
  let candidate = `${base}${EXTENSION}`;
  for (let n = 2; used.has(candidate.toLowerCase()); n++) candidate = `${base} ${n}${EXTENSION}`;
  return candidate;
}

/**
 * Where an imported notebook is stored: `<community>/<dataset> · <title>.ipynb`.
 * A name that exists in `taken` gets " 2", then " 3", and so on, so nothing is
 * ever overwritten (compared without regard to case, since a browser's file
 * name may be matched that way elsewhere).
 *
 * @param {string} community
 * @param {string} datasetId
 * @param {unknown} title
 * @param {Iterable<string>} [taken] Paths already stored.
 */
export function importedPath(community, datasetId, title, taken = []) {
  return freePath(`${community}/${datasetId}${NAME_SEPARATOR}${importedName(title)}`, taken);
}

/**
 * The path a rename gives `path`: the directory and the `<dataset> · ` prefix
 * stay, the title becomes `title` cleaned, and a name another notebook has gets
 * " 2". The notebook's own path never counts as taken, so renaming to the same
 * title is no change.
 *
 * @param {string} path
 * @param {unknown} title
 * @param {Iterable<string>} [taken]
 */
export function renamedPath(path, title, taken = []) {
  const slash = path.lastIndexOf('/');
  const directory = path.slice(0, slash + 1);
  const base = path.slice(slash + 1).replace(/\.ipynb$/i, '');
  const separator = base.indexOf(NAME_SEPARATOR);
  const dataset = separator === -1 ? base : base.slice(0, separator);
  const others = Array.from(taken || []).filter((other) => other !== path);
  return freePath(`${directory}${dataset}${NAME_SEPARATOR}${importedName(title)}`, others);
}

function baseName(path) {
  return String(path).slice(String(path).lastIndexOf('/') + 1).replace(/\.ipynb$/i, '');
}

/** The dataset id a notebook's path names: `nemar/on002718 · x.ipynb` is `on002718`. */
export function datasetFromPath(path) {
  const base = baseName(path);
  const separator = base.indexOf(NAME_SEPARATOR);
  return separator === -1 ? base : base.slice(0, separator);
}

/** Whether a file name in a community's directory is one of `datasetId`'s notebooks. */
export function belongsToDataset(name, datasetId) {
  if (typeof name !== 'string' || !name.toLowerCase().endsWith(EXTENSION)) return false;
  const base = name.slice(0, -EXTENSION.length);
  return base === datasetId || base.startsWith(`${datasetId}${NAME_SEPARATOR}`);
}

/**
 * The name a notebook has in the strip. With `datasetId`, the dataset's own file
 * (`<dataset>.ipynb`) is the "Playground" and the others lose their
 * `<dataset> · ` prefix; any other name is its file name.
 *
 * @param {string} path
 * @param {string} [datasetId]
 */
export function titleFromPath(path, datasetId) {
  const base = baseName(path);
  const dataset = datasetId ?? datasetFromPath(path);
  if (base === dataset) return PLAYGROUND_TITLE;
  const prefix = `${dataset}${NAME_SEPARATOR}`;
  return base.startsWith(prefix) ? base.slice(prefix.length) : base;
}

function joinSource(source) {
  return Array.isArray(source) ? source.join('') : String(source ?? '');
}

/**
 * A deep copy of `notebook` with the `osa-autorun` tag taken off every cell, so
 * an import never runs a cell by itself. A tags list left empty is removed.
 *
 * @param {object} notebook
 */
export function stripAutorun(notebook) {
  const copy = JSON.parse(JSON.stringify(notebook));
  for (const cell of copy.cells || []) {
    const tags = cell?.metadata?.tags;
    if (!Array.isArray(tags)) continue;
    const kept = tags.filter((tag) => tag !== AUTORUN_TAG);
    if (kept.length > 0) cell.metadata.tags = kept;
    else delete cell.metadata.tags;
  }
  return copy;
}

/**
 * What a reader's edit changes: each cell's type and source, and nothing else
 * (running a cell changes outputs and counts, which is not an edit).
 *
 * @param {{cells?: Array<{cell_type?: string, source?: string | string[]}>}} notebook
 */
export function sourceSignature(notebook) {
  const cells = Array.isArray(notebook?.cells) ? notebook.cells : [];
  return JSON.stringify(cells.map((cell) => [cell?.cell_type, joinSource(cell?.source)]));
}

/** `notebook` marked as a `kind` of notebook, in its own metadata, so the kind stays with the file. */
export function withKind(notebook, kind) {
  const copy = JSON.parse(JSON.stringify(notebook));
  copy.metadata = isPlainObject(copy.metadata) ? copy.metadata : {};
  copy.metadata.osa = { ...(isPlainObject(copy.metadata.osa) ? copy.metadata.osa : {}), kind };
  return copy;
}

/** The kind a notebook's metadata records, for a file that is not the playground; "blank" if none. */
export function kindOf(notebook) {
  const kind = notebook?.metadata?.osa?.kind;
  return kind === 'chat' || kind === 'blank' ? kind : 'blank';
}

/** An empty notebook for the Pyodide kernel: one empty code cell. */
export function blankNotebook() {
  return withKind(
    {
      nbformat: 4,
      nbformat_minor: 5,
      metadata: {
        kernelspec: { display_name: 'Python (Pyodide)', language: 'python', name: 'python' },
        language_info: { name: 'python' },
      },
      cells: [{ cell_type: 'code', id: 'cell-1', metadata: {}, execution_count: null, source: '', outputs: [] }],
    },
    'blank'
  );
}

/**
 * Which community and dataset the notebook page is for, from the path it
 * opened first and the starters' index (`starters/index.json`), or why it is
 * not one of ours. The first notebook open.js redirects into is always the
 * dataset's own, `<community>/<dataset>.ipynb`.
 *
 * @param {unknown} path
 * @param {Record<string, {dataset_pattern: string}>} startersIndex
 * @returns {{ok: true, community: string, datasetId: string, datasetPattern: string} | {ok: false, error: string}}
 */
export function tabContext(path, startersIndex) {
  if (typeof path !== 'string' || path.split('/').length !== 2 || !path.endsWith(EXTENSION)) {
    return fail('not a notebook directly under a community');
  }
  const community = path.split('/')[0];
  const communityError = communityProblem(community, Object.keys(startersIndex || {}));
  if (communityError) return fail(communityError);
  const datasetPattern = startersIndex[community].dataset_pattern;
  const datasetId = datasetFromPath(path);
  const datasetError = datasetProblem(datasetId, datasetPattern);
  if (datasetError) return fail(datasetError);
  if (baseName(path) !== datasetId) return fail('not the dataset\'s own notebook');
  return { ok: true, community, datasetId, datasetPattern };
}

/**
 * Whether `input` may be written as a notebook, and the notebook to write.
 *
 * The dataset must pass `datasetProblem` against the community's own pattern;
 * the notebook must be nbformat 4 with only markdown and code cells, at most
 * 200 of them and 2 MB of JSON. What comes back is a copy with the autorun tag
 * stripped and the code cells' outputs and execution counts cleared, since an
 * import is code to read and run, not results to trust.
 *
 * @param {unknown} input `{datasetId, title, notebook}`.
 * @param {{community: string, datasetPattern: string}} context
 * @returns {{ok: true, datasetId: string, title: string, notebook: object} | {ok: false, error: string}}
 */
export function validateImport(input, { community, datasetPattern }) {
  if (!isPlainObject(input)) return fail('nothing to import');
  const communityError = communityProblem(community, [community]);
  if (communityError) return fail(`community: ${communityError}`);
  const datasetError = datasetProblem(input.datasetId, datasetPattern);
  if (datasetError) return fail(`dataset: ${datasetError}`);
  if (typeof input.title !== 'string') return fail('the notebook has no title');
  const notebook = input.notebook;
  if (!isPlainObject(notebook)) return fail('the notebook is not an object');
  if (notebook.nbformat !== 4) return fail('only nbformat 4 notebooks can be imported');
  if (!Array.isArray(notebook.cells)) return fail('the notebook has no cells');
  if (notebook.cells.length > MAX_CELLS) {
    return fail(`the notebook has ${notebook.cells.length} cells; at most ${MAX_CELLS} can be imported`);
  }
  let json;
  try {
    json = JSON.stringify(notebook);
  } catch {
    return fail('the notebook is not plain data');
  }
  if (new TextEncoder().encode(json).length > MAX_IMPORT_BYTES) {
    return fail(`the notebook is larger than ${MAX_IMPORT_BYTES / (1024 * 1024)} MB`);
  }
  for (const [index, cell] of notebook.cells.entries()) {
    if (!isPlainObject(cell)) return fail(`cell ${index + 1} is not an object`);
    if (cell.cell_type !== 'markdown' && cell.cell_type !== 'code') {
      return fail(`cell ${index + 1} is a ${JSON.stringify(cell.cell_type)} cell; only markdown and code cells can be imported`);
    }
    const source = cell.source;
    if (typeof source !== 'string' && !(Array.isArray(source) && source.every((line) => typeof line === 'string'))) {
      return fail(`cell ${index + 1} has no source text`);
    }
  }
  const cleaned = stripAutorun(JSON.parse(json));
  for (const cell of cleaned.cells) {
    if (cell.cell_type === 'code') {
      cell.outputs = [];
      cell.execution_count = null;
    }
  }
  return { ok: true, datasetId: input.datasetId, title: importedName(input.title), notebook: cleaned };
}

/**
 * The request id a reply to this message goes under, or null when the message
 * is not a request this page answers: another target, a type it does not know,
 * or no usable id. It is the check validateTabMessage starts with, for a caller
 * that must answer without the rest of the validation (the page's tabs failed
 * to start, or the check itself threw), and still leave unknown messages alone.
 *
 * @param {unknown} data The message's data.
 * @returns {string | null}
 */
export function tabRequestId(data) {
  if (!isPlainObject(data) || data.target !== 'osa-notebook') return null;
  if (!TAB_REQUESTS.includes(data.type)) return null;
  const requestId = data.requestId;
  return typeof requestId === 'string' && REQUEST_ID_PATTERN.test(requestId) ? requestId : null;
}

/**
 * Whether the page acts on a message from the widget, and what it asks for.
 *
 * `ignored` marks a message that is not a request at all (another target, a
 * type this page does not know, no usable request id); the page replies to none
 * of those. Anything else gets a reply either way: `{ok: true, request}` to act
 * on, or `{ok: false, requestId, error}` to answer with. A path is acted on
 * only if it is one of `items`, the notebooks the page itself listed under
 * `<community>/`; the starter (the playground) cannot be renamed or closed.
 *
 * @param {unknown} data The message's data.
 * @param {{community: string, datasetPattern: string, items: Array<{path: string, kind: string}>}} context
 */
export function validateTabMessage(data, { community, datasetPattern, items }) {
  if (!isPlainObject(data) || data.target !== 'osa-notebook') return { ok: false, ignored: true };
  if (!TAB_REQUESTS.includes(data.type)) return { ok: false, ignored: true, error: `unknown message type ${JSON.stringify(data.type)}` };
  const requestId = tabRequestId(data);
  if (requestId === null) return { ok: false, ignored: true, error: 'a request needs a requestId' };
  const refuse = (error) => ({ ok: false, requestId, error });
  const type = data.type;
  if (type === 'new-notebook') return { ok: true, request: { type, requestId } };
  if (type === 'import-notebook') {
    const checked = validateImport(data, { community, datasetPattern });
    if (!checked.ok) return refuse(checked.error);
    return {
      ok: true,
      request: { type, requestId, datasetId: checked.datasetId, title: checked.title, notebook: checked.notebook },
    };
  }
  const path = data.path;
  const inside =
    typeof path === 'string' &&
    path.startsWith(`${community}/`) &&
    path.indexOf('/', community.length + 1) === -1 &&
    path.endsWith(EXTENSION);
  const item = inside ? (items || []).find((candidate) => candidate.path === path) : undefined;
  if (!item) return refuse('that notebook is not in the list');
  if ((type === 'rename-notebook' || type === 'close-notebook') && item.kind === 'playground') {
    return refuse(`the ${PLAYGROUND_TITLE.toLowerCase()} notebook cannot be ${type === 'rename-notebook' ? 'renamed' : 'closed'}`);
  }
  if (type === 'rename-notebook') {
    if (typeof data.title !== 'string') return refuse('a rename needs a title');
    return { ok: true, request: { type, requestId, path, title: data.title } };
  }
  return { ok: true, request: { type, requestId, path } };
}
