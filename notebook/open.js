/**
 * The notebook site's same-origin bootstrap (issue #453,
 * docs/adr/0011-the-notebook-site.md). JupyterLite 0.8.4 has no `?fromURL=`
 * content-loading parameter (checked directly against the built site: the string
 * is absent), so this page is the substitute: it writes a starter notebook
 * directly into JupyterLite's own storage, then redirects into it.
 *
 * `open.html?community=<id>&dataset=<dataset_id>` does this:
 *
 *   1. Validate `community` (a plain slug, and one this site has a starter for)
 *      and `dataset` (against that community's own dataset_pattern). An invalid
 *      link shows a plain sentence and writes nothing to storage.
 *   2. Fetch that community's starter (`starters/<community>.ipynb`) and fill in
 *      `{{dataset_id}}` in every cell's source.
 *   3. Write it into JupyterLite's own localforage database -- same library
 *      (1.10.0, vendored as `localforage.min.js`), same options JupyterLite's own
 *      Contents drive uses -- at `<community>/<dataset>.ipynb`, creating the
 *      `<community>` directory entry first if it does not exist yet. NEVER
 *      overwrites an existing notebook at that path: a reader's own edits from an
 *      earlier visit must survive opening the same dataset again.
 *   4. Redirect into `notebooks/index.html?path=<that path>`.
 *
 * PURE vs BROWSER-ONLY, the same split `frontend/osa-workspace.js` uses: every
 * function above `main()` touches no `document`, `window`, `fetch` or
 * `localforage` -- only plain data -- so `notebook/test-open.js` runs them under
 * Bun exactly as they run in a browser. `main()` is the only part that needs a
 * real page, and only runs when `document` exists, so importing this module for
 * its pure functions never fires a network request or a storage write.
 */

/** The token a starter notebook's cells carry (must match
 * `src.core.config.notebook_lock.NOTEBOOK_TOKEN`, the Python side's copy: braces,
 * not a bare name, since a bare `{dataset}` is a common, unrelated substring in
 * an f-string or a dict literal a starter cell might contain). */
export const NOTEBOOK_TOKEN = '{{dataset_id}}';

/** A community id, as this site's own starters/index.json keys them. */
const COMMUNITY_PATTERN = /^[a-z0-9-]{1,64}$/;

/**
 * Why `community` cannot be used, or null if it can.
 *
 * @param {unknown} community
 * @param {string[]} knownCommunities - keys of starters/index.json.
 */
export function communityProblem(community, knownCommunities) {
  if (typeof community !== 'string' || !COMMUNITY_PATTERN.test(community)) {
    return 'not a recognized community id';
  }
  if (!knownCommunities.includes(community)) {
    return 'no starter for this community';
  }
  return null;
}

/**
 * Why `dataset` cannot be used against `datasetPattern`, or null if it can.
 *
 * A pattern that fails to compile as a RegExp is treated as a refusal, not a
 * crash: `dataset_pattern` is server-authored config, validated at config load
 * (`NotebookConfig`) to compile and be anchored, but this page trusts nothing it
 * did not itself just check, including its own fetched index.
 *
 * @param {unknown} dataset
 * @param {string} datasetPattern
 */
export function datasetProblem(dataset, datasetPattern) {
  if (typeof dataset !== 'string' || dataset.length === 0) return 'no dataset id given';
  let pattern;
  try {
    pattern = new RegExp(datasetPattern);
  } catch {
    return 'this community has no usable dataset pattern';
  }
  if (!pattern.test(dataset)) return 'does not look like a valid dataset id for this community';
  return null;
}

/**
 * `source` with every occurrence of `token` replaced by `value`, preserving the
 * nbformat shape: a single string stays a string, a list of lines stays a list
 * of lines (each line's own replacements applied independently, so the token
 * splitting across two array entries -- which nbformat never produces, since
 * each entry is one line -- is not a case this needs to handle).
 *
 * @param {string | string[]} source
 * @param {string} token
 * @param {string} value
 */
export function fillSource(source, token, value) {
  const replaceAll = (text) => text.split(token).join(value);
  if (Array.isArray(source)) return source.map(replaceAll);
  if (typeof source === 'string') return replaceAll(source);
  return source;
}

/**
 * A deep copy of `notebook` with `token` filled in in every cell's source,
 * markdown and code alike. The starter on disk is never mutated: `open.html` may
 * open the same starter for a different dataset id in the same page's lifetime
 * only via a fresh navigation, but keeping this pure (no shared mutable state)
 * is what makes it testable as a plain function.
 *
 * @param {{cells?: Array<{source?: string | string[]}>}} notebook
 * @param {string} token
 * @param {string} value
 */
export function fillNotebook(notebook, token, value) {
  const filled = JSON.parse(JSON.stringify(notebook));
  for (const cell of filled.cells || []) {
    if ('source' in cell) cell.source = fillSource(cell.source, token, value);
  }
  return filled;
}

/** Where a community's filled starter is stored, for a given dataset id. */
export function notebookPath(community, dataset) {
  return `${community}/${dataset}.ipynb`;
}

/**
 * A directory entry, in the same shape JupyterLite's own Contents drive writes
 * one (measured against a live instance; see docs/adr/0011-the-notebook-site.md).
 */
export function buildDirectoryEntry(name, nowIso) {
  return {
    name,
    path: name,
    last_modified: nowIso,
    created: nowIso,
    format: 'json',
    mimetype: '',
    content: null,
    size: 0,
    writable: true,
    type: 'directory',
  };
}

/** A notebook entry, in the same shape JupyterLite's own Contents drive writes one. */
export function buildNotebookEntry(name, path, content, nowIso) {
  return {
    name,
    path,
    last_modified: nowIso,
    created: nowIso,
    format: 'json',
    mimetype: 'application/json',
    content,
    size: JSON.stringify(content).length,
    writable: true,
    type: 'notebook',
  };
}

/**
 * JupyterLite's own resolved `baseUrl` page option, derived the same way
 * `config-utils.js` (bundled into every JupyterLite page) derives it: the root
 * `jupyter-lite.json`'s raw `baseUrl` (normally `"./"`), resolved against the
 * pathname of the directory that file lives in. At a site root this is `"/"`,
 * matching a live instance (see docs/adr/0011-the-notebook-site.md).
 *
 * @param {{['jupyter-config-data']?: {baseUrl?: string}} | null} jupyterLiteConfig
 * @param {string} directoryPathname - pathname of the directory jupyter-lite.json
 *   was fetched from, trailing slash included.
 */
export function resolveBaseUrl(jupyterLiteConfig, directoryPathname) {
  const raw = jupyterLiteConfig?.['jupyter-config-data']?.baseUrl || './';
  if (raw.startsWith('./')) return directoryPathname + raw.slice(2);
  return raw;
}

/**
 * The localforage database name JupyterLite's own drive opens for a given
 * resolved `baseUrl`, so this page writes into the SAME database it reads from.
 */
export function storageDatabaseName(baseUrl) {
  return `JupyterLite Storage - ${baseUrl}`;
}

/** The localforage instance options JupyterLite's own Contents drive uses. */
export function storageOptions(baseUrl) {
  return {
    name: storageDatabaseName(baseUrl),
    storeName: 'files',
    version: 1,
    description: 'Offline Storage for Notebooks and Files',
  };
}

async function main() {
  const app = document.getElementById('app');
  const show = (text) => {
    app.textContent = text;
  };

  const params = new URLSearchParams(window.location.search);
  const community = params.get('community') || '';
  const dataset = params.get('dataset') || '';

  let index;
  try {
    const response = await fetch('./starters/index.json', { cache: 'no-store' });
    index = await response.json();
  } catch {
    show('This notebook site could not load its list of starters. Try again in a moment.');
    return;
  }

  const knownCommunities = Object.keys(index);
  if (communityProblem(community, knownCommunities)) {
    show('This link names a community this notebook site has no starter for.');
    return;
  }

  const datasetPattern = index[community].dataset_pattern;
  if (datasetProblem(dataset, datasetPattern)) {
    show(`This link's dataset id does not look like a valid ${community} dataset id.`);
    return;
  }

  let starter;
  try {
    const response = await fetch(`./starters/${community}.ipynb`, { cache: 'no-store' });
    starter = await response.json();
  } catch {
    show('This notebook starter could not be loaded. Try again in a moment.');
    return;
  }

  const filled = fillNotebook(starter, NOTEBOOK_TOKEN, dataset);

  const configUrl = new URL('./jupyter-lite.json', window.location.href);
  let jupyterLiteConfig = null;
  try {
    const response = await fetch(configUrl.href, { cache: 'no-store' });
    jupyterLiteConfig = await response.json();
  } catch {
    jupyterLiteConfig = null; // resolveBaseUrl falls back to the default "./" below
  }
  const directoryPathname = new URL('.', configUrl).pathname;
  const baseUrl = resolveBaseUrl(jupyterLiteConfig, directoryPathname);

  // `localforage` is a global from localforage.min.js, loaded as a plain
  // (non-module) script before this one; see open.html.
  const store = globalThis.localforage.createInstance(storageOptions(baseUrl));

  const now = new Date().toISOString();
  if ((await store.getItem(community)) == null) {
    await store.setItem(community, buildDirectoryEntry(community, now));
  }

  const path = notebookPath(community, dataset);
  // NEVER overwrite: a reader's own edits from an earlier visit live here.
  if ((await store.getItem(path)) == null) {
    await store.setItem(path, buildNotebookEntry(`${dataset}.ipynb`, path, filled, now));
  }

  window.location.replace(`./notebooks/index.html?path=${encodeURIComponent(path)}`);
}

if (typeof document !== 'undefined') {
  main().catch(() => {
    const app = document.getElementById('app');
    if (app) app.textContent = 'Something went wrong opening this notebook. Try again in a moment.';
  });
}
