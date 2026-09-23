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
 * "IndexedDB disabled", and this store reports that as a value on every
 * public method, never as a thrown or unhandled rejection -- a model told a
 * file was saved when it was not is the one thing this module exists to
 * prevent.
 *
 * NOTHING HERE MAY HANG
 *
 * IndexedDB requests carry no deadline of their own. A version-change block
 * (another tab holding an older connection open) never resolves and never
 * rejects on its own; `_open`'s `onblocked` handler turns that into a
 * rejection. Beyond that, every public method races itself against
 * `OPERATION_TIMEOUT_MS`, the same way the runtime already refuses to let a
 * boot or an execution hang forever (osa-runtime.js, the boot deadline and
 * `exec_seconds`): a chat turn must never stall waiting on a browser storage
 * subsystem that never answers.
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
  MAX_EXPLICIT_FILES: 32,
  MAX_COMMUNITY_BYTES: 250 * 1024 * 1024,
});

const PATH_SEGMENT = /^[A-Za-z0-9._-]+$/;

/**
 * The rules on a path that hold REGARDLESS of where it ends up nested:
 * relative, and every segment a bare, safe name. Exported and checked on its
 * own, before prefixing, mirroring `_validate_relative_and_shape` in
 * `osa-output.js` -- `save_script`/`save_artifact` there call it on the
 * caller's own `name`/`path` argument first, so an absolute-looking argument
 * is refused for what it actually is ("must be relative") rather than for
 * the empty segment a "scripts/"/"artifacts/" prefix would otherwise turn it
 * into. Depth and length are deliberately NOT checked here: those bound the
 * path actually stored, which only exists once a prefix is on.
 *
 * @param {string} path
 * @returns {string} `path`, unchanged, for chaining.
 */
export function validateRelativeAndShape(path) {
  if (typeof path !== 'string' || path === '') {
    throw new TypeError('path must be a non-empty string');
  }
  if (path.startsWith('/') || path.startsWith('\\')) {
    throw new RangeError(`path must be relative, not ${JSON.stringify(path)}`);
  }
  for (const segment of path.split('/')) {
    if (segment === '.' || segment === '..') {
      throw new RangeError(`path segment ${JSON.stringify(segment)} is not allowed: ${path}`);
    }
    if (!PATH_SEGMENT.test(segment)) {
      throw new RangeError(`path segment ${JSON.stringify(segment)} must match [A-Za-z0-9._-]+: ${path}`);
    }
  }
  return path;
}

/**
 * Validate a FULL workspace-relative path (already prefixed with
 * "scripts/"/"artifacts/"), or throw. This is the defense-in-depth pass
 * `WorkspaceStore.putFile` always runs on what will actually be stored, the
 * host-side mirror of `_validate_workspace_path` in `osa-output.js`: it
 * checks everything `validateRelativeAndShape` does, PLUS the depth and
 * length limits, which are properties of the path as stored and so can only
 * be checked once the prefix is on.
 *
 * @param {string} path
 * @returns {string} `path`, unchanged, for chaining.
 */
export function validateWorkspacePath(path) {
  validateRelativeAndShape(path);
  if (path.length > WORKSPACE_LIMITS.MAX_PATH_CHARS) {
    throw new RangeError(`path is ${path.length} characters, over the ${WORKSPACE_LIMITS.MAX_PATH_CHARS}-character limit: ${path}`);
  }
  const segments = path.split('/');
  if (segments.length > WORKSPACE_LIMITS.MAX_PATH_DEPTH) {
    throw new RangeError(`path has ${segments.length} segments, over the ${WORKSPACE_LIMITS.MAX_PATH_DEPTH}-segment limit: ${path}`);
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

/** The status `_reserveOrdinal` writes for a placeholder run record. */
export const RESERVED_RUN_STATUS = 'reserved';

/**
 * Derive a session's manifest from its stored run records.
 *
 * Pure and deterministic given its input, which is the point: this is called
 * both for a listing and for an export, and the two must never be able to
 * disagree because nothing is ever stored as "the manifest" itself.
 *
 * A run record whose ordinal is still only RESERVED (its files have not
 * finished saving, or a crash left the reservation pending, see
 * `WorkspaceStore._reserveOrdinal`) is left out: it never got real content,
 * so listing it would show the model or the reader a run that is not
 * really there yet, or never will be.
 *
 * @param {Array<{ordinal: number, callId: string, status: string, description?: string,
 *   files: string[], timestamp: string}>} runs
 * @returns {{runs: Array<object>}}
 */
export function deriveManifest(runs) {
  const sorted = [...(runs || [])]
    .filter((run) => run.status !== RESERVED_RUN_STATUS)
    .sort((a, b) => a.ordinal - b.ordinal);
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

/**
 * The default for how long any single WorkspaceStore operation may take
 * before it is treated as failed rather than left to hang. An IndexedDB
 * request carries no deadline of its own -- `onblocked` in `_open` only
 * fires for a version-change conflict, never for a generic stall -- and the
 * runtime already refuses to let a boot or an execution hang forever
 * (osa-runtime.js, `DEFAULT_BOOT_TIMEOUT_MS` and `exec_seconds`);
 * persistence gets the same treatment, since a chat turn must never stall
 * waiting on it. Overridable per instance via the constructor's
 * `operationTimeoutMs`, the same way `PyodideRuntime` takes `bootTimeoutMs`,
 * so a test can prove the real race against a deadline measured in
 * milliseconds rather than the 10s a person actually waits.
 */
export const OPERATION_TIMEOUT_MS = 10_000;

/** Distinguishes a deadline expiring from every other kind of failure. */
class WorkspaceTimeoutError extends Error {}

/**
 * Race `promise` against `timeoutMs`. Settles exactly as `promise` would,
 * UNLESS it is still pending when the deadline fires, in which case this
 * rejects with a `WorkspaceTimeoutError` instead -- turning a hang into
 * something every caller below can report or convert to a value, while a
 * promise that rejects quickly for its own reason (a real bug, not a stall)
 * still propagates that reason unchanged.
 *
 * @param {Promise<T>} promise
 * @param {string} label - Named in the timeout message.
 * @param {number} timeoutMs
 * @returns {Promise<T>}
 * @template T
 */
function raceDeadline(promise, label, timeoutMs) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new WorkspaceTimeoutError(`${label} did not finish within ${Math.round(timeoutMs / 1000)}s`));
    }, timeoutMs);
    promise.then(
      (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    );
  });
}

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
 * so a save failure has to be reported IN THAT RESULT, not lost to an
 * unhandled rejection. `sizeUsed`/`manifests`/`exportZip` keep their
 * existing throw-on-failure contract (their callers already handle a
 * rejection); every method is bounded by `OPERATION_TIMEOUT_MS` regardless.
 */
export class WorkspaceStore {
  /**
   * @param {object} options
   * @param {string} options.community
   * @param {IDBFactory|null} [options.dbFactory] - Defaults to the real
   *   `indexedDB` global when present, and to `null` (meaning "unavailable")
   *   otherwise -- which is exactly what running under Bun, or in a browser
   *   with storage disabled, looks like. Injectable so a test can simulate a
   *   quota failure, or a request that never settles, through a real,
   *   differently-behaved implementation rather than a stubbed method.
   * @param {number} [options.operationTimeoutMs] - Defaults to
   *   `OPERATION_TIMEOUT_MS`. Overridable the same way `PyodideRuntime`
   *   takes `bootTimeoutMs`, so a test can prove the deadline race for real
   *   without waiting the full default.
   */
  constructor({
    community,
    dbFactory = typeof indexedDB !== 'undefined' ? indexedDB : null,
    operationTimeoutMs = OPERATION_TIMEOUT_MS,
  } = {}) {
    if (typeof community !== 'string' || community === '') {
      throw new TypeError('WorkspaceStore requires a non-empty community id');
    }
    if (typeof operationTimeoutMs !== 'number' || !Number.isFinite(operationTimeoutMs) || operationTimeoutMs <= 0) {
      throw new TypeError(`operationTimeoutMs must be a positive finite number, got ${operationTimeoutMs}`);
    }
    this.community = community;
    this._dbFactory = dbFactory;
    this._dbPromise = null;
    this._operationTimeoutMs = operationTimeoutMs;
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
        // A version-change BLOCK: another tab holds an older connection open
        // to this database and neither side closes it. This never resolves
        // and onerror never fires for it either, so without this handler the
        // whole call would depend on the operation-level deadline alone to
        // ever settle, 10 full seconds for something the browser already
        // knows will never proceed on its own.
        request.onblocked = () =>
          reject(new Error('the workspace database is blocked by another open connection or tab; try closing other tabs'));
      }).catch((err) => {
        // A rejection here must not poison every later call: closing the
        // blocking tab, or the browser recovering from a transient failure,
        // should let the NEXT open succeed. Only the promise object itself
        // is cached, so it is cleared as soon as it settles badly.
        this._dbPromise = null;
        throw err;
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
    return raceDeadline(this._sizeUsed(), 'sizeUsed', this._operationTimeoutMs);
  }

  async _sizeUsed() {
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

  /**
   * Reserve the next ordinal for a session's run, ATOMICALLY, so two runs
   * recorded at about the same time -- two tabs sharing one session id
   * restored from localStorage is the real case this guards -- can never be
   * assigned the same one and silently overwrite each other's files and run
   * record.
   *
   * The read (this session's highest ordinal so far) and the write (a
   * PLACEHOLDER run record at the next one, status `RESERVED_RUN_STATUS`)
   * happen inside ONE readwrite transaction on the runs store. IndexedDB
   * serializes readwrite transactions with overlapping scope in the order
   * they were CREATED, across every connection and every tab open to this
   * database, so a second reservation for this session cannot run its own
   * read until this transaction has committed and this placeholder is
   * already visible to it: the second reservation is guaranteed to see a
   * higher ordinal. That is what makes the assignment atomic without
   * `navigator.locks`, which does not reach every browser this runtime runs
   * in.
   *
   * A reservation a crash leaves pending (the tab closes before
   * `recordRun` ever completes it) is not cleaned up: it simply stays
   * RESERVED forever, and `deriveManifest` leaves any run in that status out
   * of what it derives, so it can never be listed as present. The ordinal
   * itself is spent either way, which is the price of never reusing one.
   *
   * @param {string} session
   * @returns {Promise<number>}
   */
  async _reserveOrdinal(session) {
    const db = await this._open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(RUNS_STORE, 'readwrite');
      const store = tx.objectStore(RUNS_STORE);
      const request = store.getAll(rangeFor(`${this.community}${KEY_SEP}${session}${KEY_SEP}`));
      let ordinal = null;
      request.onsuccess = () => {
        const rows = request.result || [];
        ordinal = rows.reduce((max, row) => Math.max(max, row.ordinal), 0) + 1;
        // Written INSIDE this same transaction, before it can commit: see
        // the method doc for why that is what makes this atomic.
        store.put({
          key: this._runKey(session, ordinal),
          community: this.community,
          session,
          ordinal,
          callId: null,
          status: RESERVED_RUN_STATUS,
          description: '',
          files: [],
          timestamp: new Date().toISOString(),
        });
      };
      request.onerror = () => reject(request.error || new Error("could not read the session's runs"));
      tx.oncomplete = () => resolve(ordinal);
      tx.onerror = () => reject(tx.error || new Error('reserving the run ordinal failed'));
      tx.onabort = () => reject(tx.error || new Error('reserving the run ordinal was aborted, most likely a storage quota'));
    });
  }

  /**
   * Write one file's bytes, validated, size-capped and reported rather than
   * thrown.
   *
   * @param {string} session
   * @param {string} path - Workspace-relative; validated again here even
   *   though the Python side already checked it, since a check that runs
   *   only in the language the model's code is written in is a check a
   *   caller who is not that model's code -- a bug in this module itself,
   *   or a future direct caller of `recordRun` -- can still skip.
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
    const outcome = await raceDeadline(
      (async () => {
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
      })(),
      `putFile(${path})`,
      this._operationTimeoutMs
    ).catch((err) => ({ ok: false, reason: describeError(err) }));
    return outcome;
  }

  /**
   * Persist one run: its automatic files (the code, stdout, stderr, summary
   * and any figures) always, plus whatever `explicitFiles` (from
   * `osa.save_script`/`osa.save_artifact`, base64-decoded) the run saved.
   *
   * Automatic files are NEVER subject to the run's 25 MB explicit-save
   * budget below: Python's own 25 MB check (`_record_saved_file`,
   * osa-output.js) counts only explicit saves, since it has no visibility
   * into files the host generates after the run ends, and the two layers
   * must never be able to disagree about a path Python already accepted.
   * Automatic files are bounded a different way, by their own existing
   * caps: text is clipped to `_FULL_CHARS` (256 KiB) per stream, and a
   * figure is capped by `image_bytes`/`image_px` before it is ever attached
   * -- four small text files plus at most a handful of images, always well
   * under either the 10 MB per-file or 25 MB per-run limit. Only the
   * per-file and community caps apply to them (via `putFile`).
   *
   * Explicit files ARE subject to the run's 25 MB budget, one candidate at
   * a time so "quota ran out partway through" reports exactly which files
   * that left out, and capped at `MAX_EXPLICIT_FILES` distinct paths ("saved
   * equals announced": `ClientToolResult.artifacts` holds at most
   * that many names) -- both re-checks of what osa.save_script/
   * osa.save_artifact already enforced in Python, for a caller that reaches
   * this method some other way. A run record is written whether or not
   * every file succeeded, naming only the files that actually made it in --
   * that record is `deriveManifest`'s and `buildNotebook`'s only input, so
   * a failed save can never be listed as present in either.
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
  async recordRun(run) {
    const explicitPaths = new Set((run.explicitFiles || []).map((f) => f.path));
    try {
      return await raceDeadline(this._recordRunInner(run), 'the workspace write', this._operationTimeoutMs);
    } catch (err) {
      if (err instanceof WorkspaceTimeoutError) {
        // A deadline is reported as a normal failure, never a hang: the
        // caller (ClientToolController#persist) gets a value it can turn
        // into the model's result, exactly as it would for any other
        // recordRun failure.
        return this._wholeRunFailure(describeError(err), explicitPaths);
      }
      // Anything else is a genuinely unexpected error (recordRun's OWN
      // failure paths below always resolve, never throw), and is allowed to
      // propagate: ClientToolController#persist logs it and still answers
      // the model correctly. See that method's own comment.
      throw err;
    }
  }

  _wholeRunFailure(reason, explicitPaths) {
    const failures = [];
    for (const path of explicitPaths) failures.push({ path, reason });
    failures.push({ path: "scripts/run-NNN.py (and this run's other automatic files)", reason });
    return { ordinal: null, savedArtifacts: [], failures };
  }

  async _recordRunInner({
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
      return this._wholeRunFailure('IndexedDB is not available in this browser', explicitPaths);
    }

    let ordinal;
    let used;
    try {
      ordinal = await this._reserveOrdinal(session);
      used = await this._sizeUsed();
    } catch (err) {
      return this._wholeRunFailure(describeError(err), explicitPaths);
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
      .map((f) => ({ path: f.path, data: base64ToBytes(f.data_base64) }));

    const saved = [];
    const savedArtifacts = [];

    // Automatic files: no run-budget check (see the method doc), only the
    // per-file and community caps `putFile` already applies.
    for (const candidate of candidates) {
      const result = await this.putFile(session, candidate.path, candidate.data, used);
      if (result.ok) {
        used += candidate.data.length;
        saved.push(candidate.path);
      } else {
        failures.push({ path: candidate.path, reason: result.reason });
      }
    }

    // Explicit files: the 25 MB run budget and the 32-distinct-file cap
    // both apply, re-checking what Python already enforced.
    let runBudget = 0;
    for (let index = 0; index < explicitCandidates.length; index++) {
      const candidate = explicitCandidates[index];
      if (index >= WORKSPACE_LIMITS.MAX_EXPLICIT_FILES) {
        failures.push({
          path: candidate.path,
          reason: `this run has already explicitly saved ${WORKSPACE_LIMITS.MAX_EXPLICIT_FILES} files, the most a run may save`,
        });
        continue;
      }
      if (runBudget + candidate.data.length > WORKSPACE_LIMITS.MAX_RUN_BYTES) {
        failures.push({
          path: candidate.path,
          reason: `this run has already explicitly saved ${runBudget} bytes, over the ${WORKSPACE_LIMITS.MAX_RUN_BYTES}-byte per-run limit`,
        });
        continue;
      }
      const result = await this.putFile(session, candidate.path, candidate.data, used);
      if (result.ok) {
        runBudget += candidate.data.length;
        used += candidate.data.length;
        saved.push(candidate.path);
        savedArtifacts.push(candidate.path);
      } else {
        failures.push({ path: candidate.path, reason: result.reason });
      }
    }

    // Complete the reservation: the SAME key _reserveOrdinal placed a
    // placeholder at, overwritten with the real content. Never a fresh
    // record, so there is only ever one row per (session, ordinal).
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
        tx.onabort = () => reject(tx.error || new Error('the run record was aborted, most likely a storage quota'));
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
    return raceDeadline(this._manifests(), 'manifests', this._operationTimeoutMs);
  }

  async _manifests() {
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
    return raceDeadline(this._exportZip(), 'exportZip', this._operationTimeoutMs);
  }

  async _exportZip() {
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
      const notebookRuns = sessionRuns
        .filter((run) => run.status !== RESERVED_RUN_STATUS)
        .map((run) => {
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
    return raceDeadline(this._deleteAll(), 'deleteAll', this._operationTimeoutMs).catch((err) => ({ ok: false, reason: describeError(err) }));
  }

  async _deleteAll() {
    const db = await this._open();
    const tx = db.transaction([FILES_STORE, RUNS_STORE], 'readwrite');
    const prefix = `${this.community}${KEY_SEP}`;
    tx.objectStore(FILES_STORE).delete(rangeFor(prefix));
    tx.objectStore(RUNS_STORE).delete(rangeFor(prefix));
    await new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error || new Error('the delete did not complete'));
      tx.onabort = () => reject(tx.error || new Error('the delete was aborted, most likely a storage quota'));
    });
    return { ok: true };
  }
}
