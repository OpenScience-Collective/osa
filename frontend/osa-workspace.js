/**
 * The browser workspace: a persistent, per-community store of what a run
 * wrote, kept in IndexedDB on the host side (epic #429, phase #433).
 *
 * WHY THIS EXISTS
 *
 * `FullOutputStore` (osa-runtime.js) is a per-tab memory cache: it answers
 * `get_full_output` and dies on reload. Nothing a run produced was ever kept
 * across a session until now. This module is the persistence layer: one
 * IndexedDB database per community, written by the HOST after each run, never
 * by Pyodide. Python cannot reach browser storage at all -- the namespace
 * seal removes `js`, `mountOPFS` is unreleased, `mountNativeFS` is
 * Chromium-only -- so a run's files travel back from the worker in the
 * result message the way `full` already does (see the comment on
 * `outputs.remember` in osa-runtime.js), and the host writes them to
 * IndexedDB before the result is ever answered to the server.
 *
 * LAYOUT
 *
 * `<community>/<session>/{scripts,results,artifacts}/<path>`. A per-session
 * `manifest.json` is DERIVED from the stored run records whenever it is
 * needed (`deriveManifest`, below) and never stored itself, so it cannot
 * drift from what is actually there.
 *
 * WHAT IS PURE, AND WHAT TOUCHES INDEXEDDB
 *
 * `validateWorkspacePath`, `deriveManifest` and `buildNotebook` are pure
 * functions over plain data: no `indexedDB` reference anywhere in them, so
 * they run and are tested the same way under Bun as they do in a browser.
 * `WorkspaceStore` is the IndexedDB-backed adapter; IndexedDB itself does not
 * exist under Bun, so its actual reads and writes are exercised only by
 * `frontend/browser-harness/`. What IS tested under Bun, deliberately, is
 * `WorkspaceStore`'s behavior when `indexedDB` is unavailable: that is not a
 * browser-only condition, it is the exact shape of "private browsing" and
 * "IndexedDB disabled", and a store that throws synchronously there instead
 * of reporting a failure is the bug decision 4 of the phase plan exists to
 * prevent (a model told a file was saved when it was not).
 */

import { buildStoredZip } from './osa-zip.js';

/**
 * The rules a workspace-relative path must satisfy. Mirrors, not shares
 * (the two run in different languages), `_validate_workspace_path` in
 * `osa-output.js`: a call the Python side would refuse is refused here too,
 * so a bad path is never accepted on the host merely because some future
 * caller skipped the Python helper.
 */
export const WORKSPACE_LIMITS = Object.freeze({
  MAX_PATH_CHARS: 200,
  MAX_PATH_DEPTH: 4,
  MAX_FILE_BYTES: 10 * 1024 * 1024,
  MAX_RUN_BYTES: 25 * 1024 * 1024,
  MAX_COMMUNITY_BYTES: 250 * 1024 * 1024,
});

const PATH_SEGMENT = /^[A-Za-z0-9._-]+$/;

/**
 * Validate a workspace-relative path, or throw.
 *
 * @param {string} path
 * @returns {string} `path`, unchanged, for chaining.
 */
export function validateWorkspacePath(path) {
  if (typeof path !== 'string' || path === '') {
    throw new TypeError('path must be a non-empty string');
  }
  if (path.length > WORKSPACE_LIMITS.MAX_PATH_CHARS) {
    throw new RangeError(`path is ${path.length} characters, over the ${WORKSPACE_LIMITS.MAX_PATH_CHARS}-character limit: ${path}`);
  }
  if (path.startsWith('/') || path.startsWith('\\')) {
    throw new RangeError(`path must be relative, not ${JSON.stringify(path)}`);
  }
  const segments = path.split('/');
  if (segments.length > WORKSPACE_LIMITS.MAX_PATH_DEPTH) {
    throw new RangeError(`path has ${segments.length} segments, over the ${WORKSPACE_LIMITS.MAX_PATH_DEPTH}-segment limit: ${path}`);
  }
  for (const segment of segments) {
    if (segment === '.' || segment === '..') {
      throw new RangeError(`path segment ${JSON.stringify(segment)} is not allowed: ${path}`);
    }
    if (!PATH_SEGMENT.test(segment)) {
      throw new RangeError(`path segment ${JSON.stringify(segment)} must match [A-Za-z0-9._-]+: ${path}`);
    }
  }
  return path;
}

/** The 3-digit ordinal a run's automatic files are named with, e.g. "007". */
export function runOrdinalTag(ordinal) {
  if (!Number.isInteger(ordinal) || ordinal < 1) {
    throw new RangeError(`ordinal must be a positive integer, got ${ordinal}`);
  }
  return String(ordinal).padStart(3, '0');
}

/** Workspace-relative path of a run's saved code. */
export function autoScriptPath(ordinal) {
  return `scripts/run-${runOrdinalTag(ordinal)}.py`;
}

/** Workspace-relative directory of a run's automatic outputs (no trailing slash). */
export function autoResultDir(ordinal) {
  return `results/run-${runOrdinalTag(ordinal)}`;
}

/** Workspace-relative path of one of a run's automatically saved figures. */
export function autoFigurePath(ordinal, figureNumber) {
  return `${autoResultDir(ordinal)}/figure-${figureNumber}.png`;
}

/**
 * Derive a session's manifest from its stored run records.
 *
 * Pure and deterministic given its input, which is the point: this is called
 * both for a listing and for an export, and the two must never be able to
 * disagree because nothing is ever stored as "the manifest" itself.
 *
 * @param {Array<{ordinal: number, callId: string, status: string, description?: string,
 *   files: string[], timestamp: string}>} runs
 * @returns {{runs: Array<object>}}
 */
export function deriveManifest(runs) {
  const sorted = [...(runs || [])].sort((a, b) => a.ordinal - b.ordinal);
  return {
    runs: sorted.map((run) => ({
      ordinal: run.ordinal,
      call_id: run.callId,
      status: run.status,
      description: run.description || '',
      files: [...new Set(run.files || [])].sort(),
      created_at: run.timestamp,
    })),
  };
}

let cellSerial = 0;

/** A cell id nbformat 4.5 accepts: `^[a-zA-Z0-9-_]{1,64}$`, stable per call. */
function cellId(label) {
  cellSerial += 1;
  const safe = String(label).replace(/[^a-zA-Z0-9_-]/g, '-').slice(0, 48);
  return `${safe}-${cellSerial}`;
}

/**
 * Build an `.ipynb` (nbformat 4.5) from a session's runs, one markdown cell
 * plus one code cell per run: the markdown cell carries the run's
 * description, the code cell its code and outputs.
 *
 * Pure: every byte a run produced is passed in already read (`code`,
 * `stdout`, `stderr`, `images`), so this function never touches storage and
 * is exercised directly by `frontend/test-workspace.js` and by
 * `tests/test_frontend/test_workspace_export.py` (which validates the
 * result with the real `nbformat` package).
 *
 * @param {Array<{ordinal: number, description?: string, code?: string,
 *   stdout?: string, stderr?: string, images?: Array<{data_base64: string}>}>} runs
 * @returns {object} An nbformat 4.5 notebook.
 */
export function buildNotebook(runs) {
  const sorted = [...(runs || [])].sort((a, b) => a.ordinal - b.ordinal);
  const cells = [];
  for (const run of sorted) {
    cells.push({
      cell_type: 'markdown',
      id: cellId(`run-${run.ordinal}-md`),
      metadata: {},
      source: run.description && run.description.trim() ? run.description : `Run ${run.ordinal}`,
    });
    const outputs = [];
    if (run.stdout) {
      outputs.push({ output_type: 'stream', name: 'stdout', text: run.stdout });
    }
    if (run.stderr) {
      outputs.push({ output_type: 'stream', name: 'stderr', text: run.stderr });
    }
    for (const image of run.images || []) {
      outputs.push({ output_type: 'display_data', data: { 'image/png': image.data_base64 }, metadata: {} });
    }
    cells.push({
      cell_type: 'code',
      id: cellId(`run-${run.ordinal}-code`),
      metadata: {},
      execution_count: null,
      source: run.code || '',
      outputs,
    });
  }
  return {
    nbformat: 4,
    nbformat_minor: 5,
    metadata: { language_info: { name: 'python', mimetype: 'text/x-python', file_extension: '.py' } },
    cells,
  };
}

/**
 * Assemble one zip of a whole community's workspace: every session's files,
 * plus a derived `manifest.json` and `notebook.ipynb` per session.
 *
 * Pure, given already-read bytes: `WorkspaceStore.exportZip` is the only
 * caller that touches IndexedDB, so this function is exercised directly, and
 * is what `tests/test_frontend/test_workspace_export.py` drives through a
 * spawned Bun script to validate with the real `zipfile` and `nbformat`.
 *
 * @param {Array<{session: string, files: Array<{path: string, data: Uint8Array}>,
 *   runs: Array<object>, notebookRuns: Array<object>}>} sessions
 * @returns {Uint8Array}
 */
export function buildWorkspaceZip(sessions) {
  const entries = {};
  for (const session of [...(sessions || [])].sort((a, b) => a.session.localeCompare(b.session))) {
    for (const file of session.files) {
      entries[`${session.session}/${file.path}`] = file.data;
    }
    entries[`${session.session}/manifest.json`] = JSON.stringify(deriveManifest(session.runs), null, 2);
    entries[`${session.session}/notebook.ipynb`] = JSON.stringify(buildNotebook(session.notebookRuns), null, 2);
  }
  return buildStoredZip(entries);
}

function describeError(err) {
  return String((err && err.message) || err || 'unknown error');
}

function base64ToBytes(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function bytesToBase64(bytes) {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

const DB_NAME_PREFIX = 'osa-workspace-';
const DB_VERSION = 1;
const FILES_STORE = 'files';
const RUNS_STORE = 'runs';
const KEY_SEP = '\u0000';

function rangeFor(prefix) {
  return IDBKeyRange.bound(prefix, `${prefix}￿`, false, false);
}

function requestToPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB request failed'));
  });
}

/**
 * The IndexedDB-backed workspace for ONE community.
 *
 * Every public method that writes reports failure as a return value
 * (`{ok: false, reason}`), never as a thrown or rejected error: a write here
 * happens after Python has already produced a result the model will read,
 * and decision 4 of the phase plan is that a save failure is reported IN
 * THAT RESULT, not lost to an unhandled rejection.
 */
export class WorkspaceStore {
  /**
   * @param {object} options
   * @param {string} options.community
   * @param {IDBFactory|null} [options.dbFactory] - Defaults to the real
   *   `indexedDB` global when present, and to `null` (meaning "unavailable")
   *   otherwise -- which is exactly what running under Bun, or in a browser
   *   with storage disabled, looks like. Injectable so a test can simulate a
   *   quota failure through a real, smaller-quota implementation rather than
   *   a stubbed method.
   */
  constructor({ community, dbFactory = typeof indexedDB !== 'undefined' ? indexedDB : null } = {}) {
    if (typeof community !== 'string' || community === '') {
      throw new TypeError('WorkspaceStore requires a non-empty community id');
    }
    this.community = community;
    this._dbFactory = dbFactory;
    this._dbPromise = null;
  }

  /** Whether this store can reach IndexedDB at all. */
  get available() {
    return this._dbFactory !== null;
  }

  _open() {
    if (!this.available) {
      return Promise.reject(new Error('IndexedDB is not available in this browser'));
    }
    if (!this._dbPromise) {
      this._dbPromise = new Promise((resolve, reject) => {
        const request = this._dbFactory.open(`${DB_NAME_PREFIX}${this.community}`, DB_VERSION);
        request.onupgradeneeded = () => {
          const db = request.result;
          if (!db.objectStoreNames.contains(FILES_STORE)) {
            db.createObjectStore(FILES_STORE, { keyPath: 'key' });
          }
          if (!db.objectStoreNames.contains(RUNS_STORE)) {
            db.createObjectStore(RUNS_STORE, { keyPath: 'key' });
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('could not open the workspace database'));
      });
    }
    return this._dbPromise;
  }

  _fileKey(session, path) {
    return `${this.community}${KEY_SEP}${session}${KEY_SEP}${path}`;
  }

  _runKey(session, ordinal) {
    return `${this.community}${KEY_SEP}${session}${KEY_SEP}${String(ordinal).padStart(6, '0')}`;
  }

  /** Total bytes stored for this community, across every session. */
  async sizeUsed() {
    const db = await this._open();
    const tx = db.transaction(FILES_STORE, 'readonly');
    const rows = await requestToPromise(tx.objectStore(FILES_STORE).getAll(rangeFor(`${this.community}${KEY_SEP}`)));
    return rows.reduce((total, row) => total + row.size, 0);
  }

  /** Every run record for this community, across every session, ordinal order. */
  async _allRuns() {
    const db = await this._open();
    const tx = db.transaction(RUNS_STORE, 'readonly');
    const rows = await requestToPromise(tx.objectStore(RUNS_STORE).getAll(rangeFor(`${this.community}${KEY_SEP}`)));
    return rows.sort((a, b) => a.session.localeCompare(b.session) || a.ordinal - b.ordinal);
  }

  /** Every file record for this community, across every session. */
  async _allFiles() {
    const db = await this._open();
    const tx = db.transaction(FILES_STORE, 'readonly');
    return requestToPromise(tx.objectStore(FILES_STORE).getAll(rangeFor(`${this.community}${KEY_SEP}`)));
  }

  async _nextOrdinal(session) {
    const db = await this._open();
    const tx = db.transaction(RUNS_STORE, 'readonly');
    const rows = await requestToPromise(
      tx.objectStore(RUNS_STORE).getAll(rangeFor(`${this.community}${KEY_SEP}${session}${KEY_SEP}`))
    );
    return rows.reduce((max, row) => Math.max(max, row.ordinal), 0) + 1;
  }

  /**
   * Write one file's bytes, validated, size-capped and reported rather than
   * thrown.
   *
   * @param {string} session
   * @param {string} path - Workspace-relative; validated again here even
   *   though the Python side already checked it, since "again on the host"
   *   is the point (decision 4).
   * @param {Uint8Array} data
   * @param {number} usedSoFar - This community's byte total before this
   *   write, so the 250 MB cap can be enforced without a fresh scan per file.
   * @returns {Promise<{ok: true, size: number}|{ok: false, reason: string}>}
   */
  async putFile(session, path, data, usedSoFar = 0) {
    try {
      validateWorkspacePath(path);
    } catch (err) {
      return { ok: false, reason: describeError(err) };
    }
    if (data.length > WORKSPACE_LIMITS.MAX_FILE_BYTES) {
      return { ok: false, reason: `${data.length} bytes, over the ${WORKSPACE_LIMITS.MAX_FILE_BYTES}-byte per-file limit` };
    }
    if (usedSoFar + data.length > WORKSPACE_LIMITS.MAX_COMMUNITY_BYTES) {
      return {
        ok: false,
        reason: `saving this file would use ${usedSoFar + data.length} bytes for ${this.community}, over the ${WORKSPACE_LIMITS.MAX_COMMUNITY_BYTES}-byte community limit`,
      };
    }
    try {
      const db = await this._open();
      const tx = db.transaction(FILES_STORE, 'readwrite');
      const record = {
        key: this._fileKey(session, path),
        community: this.community,
        session,
        path,
        data,
        size: data.length,
        savedAt: new Date().toISOString(),
      };
      tx.objectStore(FILES_STORE).put(record);
      await new Promise((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error || new Error('the write did not complete'));
        tx.onabort = () => reject(tx.error || new Error('the write was aborted, most likely a storage quota'));
      });
      return { ok: true, size: data.length };
    } catch (err) {
      return { ok: false, reason: describeError(err) };
    }
  }

  /**
   * Persist one run: its automatic files (the code, stdout, stderr, summary
   * and any figures) always, plus whatever `explicitFiles` (from
   * `osa.save_script`/`osa.save_artifact`, base64-decoded) the run saved.
   *
   * Every file this call attempts is capped against BOTH this run's 25 MB
   * budget and this community's 250 MB budget, each file attempted
   * independently: one failure never aborts the rest, so "quota ran out
   * partway through" reports exactly which files that left out. A run
   * record is written whether or not every file succeeded, naming only the
   * files that actually made it in -- that record is `deriveManifest`'s and
   * `buildNotebook`'s only input, so a failed save can never be listed as
   * present in either.
   *
   * @param {object} run
   * @param {string} run.session
   * @param {string} run.callId
   * @param {string} run.status
   * @param {string} [run.description]
   * @param {string} run.code
   * @param {string} [run.stdout]
   * @param {string} [run.stderr]
   * @param {string} [run.summary]
   * @param {Array<{data_base64: string}>} [run.images]
   * @param {Array<{path: string, data_base64: string}>} [run.explicitFiles]
   * @returns {Promise<{ordinal: number|null, savedArtifacts: string[],
   *   failures: Array<{path: string, reason: string}>}>}
   */
  async recordRun({
    session,
    callId,
    status,
    description = '',
    code = '',
    stdout = '',
    stderr = '',
    summary = '',
    images = [],
    explicitFiles = [],
  }) {
    const failures = [];
    const explicitPaths = new Set(explicitFiles.map((f) => f.path));
    if (!this.available) {
      const reason = 'IndexedDB is not available in this browser';
      for (const path of explicitPaths) failures.push({ path, reason });
      failures.push({ path: "scripts/run-NNN.py (and this run's other automatic files)", reason });
      return { ordinal: null, savedArtifacts: [], failures };
    }

    let ordinal;
    let used;
    try {
      ordinal = await this._nextOrdinal(session);
      used = await this.sizeUsed();
    } catch (err) {
      const reason = describeError(err);
      for (const path of explicitPaths) failures.push({ path, reason });
      failures.push({ path: "scripts/run-NNN.py (and this run's other automatic files)", reason });
      return { ordinal: null, savedArtifacts: [], failures };
    }

    const candidates = [
      { path: autoScriptPath(ordinal), data: new TextEncoder().encode(code) },
      { path: `${autoResultDir(ordinal)}/stdout.txt`, data: new TextEncoder().encode(stdout) },
      { path: `${autoResultDir(ordinal)}/stderr.txt`, data: new TextEncoder().encode(stderr) },
      { path: `${autoResultDir(ordinal)}/summary.txt`, data: new TextEncoder().encode(summary) },
    ];
    images.forEach((image, index) => {
      if (image && typeof image.data_base64 === 'string') {
        candidates.push({ path: autoFigurePath(ordinal, index + 1), data: base64ToBytes(image.data_base64) });
      }
    });
    const explicitCandidates = explicitFiles
      .filter((f) => f && typeof f.path === 'string' && typeof f.data_base64 === 'string')
      .map((f) => ({ path: f.path, data: base64ToBytes(f.data_base64), explicit: true }));

    let runBudget = 0;
    const saved = [];
    const savedArtifacts = [];
    for (const candidate of [...candidates, ...explicitCandidates]) {
      if (runBudget + candidate.data.length > WORKSPACE_LIMITS.MAX_RUN_BYTES) {
        failures.push({
          path: candidate.path,
          reason: `this run has already used ${runBudget} bytes, over the ${WORKSPACE_LIMITS.MAX_RUN_BYTES}-byte per-run limit`,
        });
        continue;
      }
      const result = await this.putFile(session, candidate.path, candidate.data, used);
      if (result.ok) {
        runBudget += candidate.data.length;
        used += candidate.data.length;
        saved.push(candidate.path);
        if (candidate.explicit) savedArtifacts.push(candidate.path);
      } else {
        failures.push({ path: candidate.path, reason: result.reason });
      }
    }

    try {
      const db = await this._open();
      const tx = db.transaction(RUNS_STORE, 'readwrite');
      tx.objectStore(RUNS_STORE).put({
        key: this._runKey(session, ordinal),
        community: this.community,
        session,
        ordinal,
        callId,
        status,
        description,
        files: saved,
        timestamp: new Date().toISOString(),
      });
      await new Promise((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error || new Error('the run record did not save'));
      });
    } catch (err) {
      failures.push({ path: `manifest entry for run ${ordinal}`, reason: describeError(err) });
    }

    return { ordinal, savedArtifacts: savedArtifacts.sort(), failures };
  }

  /**
   * Every session's manifest for this community, as `deriveManifest` would
   * report it from the stored run records.
   *
   * @returns {Promise<Record<string, {runs: object[]}>>}
   */
  async manifests() {
    const runs = await this._allRuns();
    const bySession = new Map();
    for (const run of runs) {
      if (!bySession.has(run.session)) bySession.set(run.session, []);
      bySession.get(run.session).push(run);
    }
    const result = {};
    for (const [session, sessionRuns] of bySession) {
      result[session] = deriveManifest(sessionRuns);
    }
    return result;
  }

  /**
   * Build this community's whole-workspace export zip.
   *
   * @returns {Promise<Uint8Array>}
   */
  async exportZip() {
    const [runs, files] = await Promise.all([this._allRuns(), this._allFiles()]);
    const bySession = new Map();
    for (const file of files) {
      if (!bySession.has(file.session)) bySession.set(file.session, { files: [], runs: [] });
      bySession.get(file.session).files.push({ path: file.path, data: file.data });
    }
    for (const run of runs) {
      if (!bySession.has(run.session)) bySession.set(run.session, { files: [], runs: [] });
      bySession.get(run.session).runs.push(run);
    }
    const byPath = new Map(files.map((f) => [`${f.session}\u0000${f.path}`, f]));
    const readText = (session, path) => {
      const found = byPath.get(`${session}\u0000${path}`);
      return found ? new TextDecoder().decode(found.data) : '';
    };
    const sessions = Array.from(bySession.entries()).map(([session, { files: sessionFiles, runs: sessionRuns }]) => {
      const notebookRuns = sessionRuns.map((run) => {
        const images = (run.files || [])
          .filter((p) => p.startsWith(`${autoResultDir(run.ordinal)}/figure-`))
          .sort()
          .map((p) => {
            const found = byPath.get(`${session}\u0000${p}`);
            return found ? { data_base64: bytesToBase64(found.data) } : null;
          })
          .filter(Boolean);
        return {
          ordinal: run.ordinal,
          description: run.description,
          code: readText(session, autoScriptPath(run.ordinal)),
          stdout: readText(session, `${autoResultDir(run.ordinal)}/stdout.txt`),
          stderr: readText(session, `${autoResultDir(run.ordinal)}/stderr.txt`),
          images,
        };
      });
      return { session, files: sessionFiles, runs: sessionRuns, notebookRuns };
    });
    return buildWorkspaceZip(sessions);
  }

  /** Delete every file and run record for this community. */
  async deleteAll() {
    if (!this.available) {
      return { ok: false, reason: 'IndexedDB is not available in this browser' };
    }
    try {
      const db = await this._open();
      const tx = db.transaction([FILES_STORE, RUNS_STORE], 'readwrite');
      const prefix = `${this.community}${KEY_SEP}`;
      tx.objectStore(FILES_STORE).delete(rangeFor(prefix));
      tx.objectStore(RUNS_STORE).delete(rangeFor(prefix));
      await new Promise((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error || new Error('the delete did not complete'));
      });
      return { ok: true };
    } catch (err) {
      return { ok: false, reason: describeError(err) };
    }
  }
}
