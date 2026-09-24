/**
 * Browser Python runtime: worker lifecycle and boot (epic #429, phase #431, step 1).
 *
 * Phase 1 taught the server to emit a tool call it does not execute. This is the
 * host half of the executor: it owns one Pyodide worker per community per tab,
 * boots it lazily or eagerly per config, keeps it warm across turns, and tears it
 * down for cancellation.
 *
 * Execution itself is step 3 and is deliberately not here. This file is the
 * lifecycle, and the lifecycle is where the measured hazards live.
 *
 * WHY THE BOOT DEADLINE EXISTS
 *
 * Measured in Chrome on 2026-09-22 (recorded on #431): under a Content-Security-
 * Policy with no `wasm-unsafe-eval`, `loadPyodide` never resolves, never rejects,
 * and logs nothing. A CSP refusal is indistinguishable from a slow download. So a
 * spinner with no deadline renders a permanently blocked runtime as a loading one,
 * and nobody ever learns the policy is wrong. Every boot is therefore bounded, and
 * expiry is a FAILED state with an actionable message, never a silent retry.
 *
 * WHY THE WORKER IS A BLOB
 *
 * nemar.org grants `worker-src 'self' blob:`. `'self'` is nemar.org, so a worker
 * served from widget.osc.earth would be refused outright; blob is the only form
 * that works on the embedder we care most about. A blob worker also inherits the
 * page's CSP (measured), which is what makes `connect-src` a real egress ceiling.
 * It is only a ceiling: see the network boundary in step 2, which is what enforces
 * a community's own `fetch_allow` inside it.
 */

import { buildDataClientSource, buildEgressGuardSource, buildNamespaceSealSource } from './osa-egress.js';
import { buildHelpersSource, buildOutputCaptureSource, resolveLimits } from './osa-output.js';
import { createWorkerRuntime } from './osa-worker-core.js';

/** Lifecycle states. A runtime is in exactly one at a time. */
export const RUNTIME_STATE = Object.freeze({
  IDLE: 'idle',
  BOOTING: 'booting',
  READY: 'ready',
  FAILED: 'failed',
  TERMINATED: 'terminated',
});

/**
 * How long a boot may take before it is declared failed.
 *
 * Generous on purpose: a cold Pyodide download over a slow connection is legitimately
 * tens of seconds, and killing a real boot is worse than waiting. The number exists to
 * bound the pathological case (a silent CSP refusal, which never completes at all),
 * not to police normal latency.
 */
export const DEFAULT_BOOT_TIMEOUT_MS = 120_000;

/** Distinguishes why a boot failed, so the UI can say something useful. */
export const BOOT_FAILURE = Object.freeze({
  TIMEOUT: 'timeout',
  WORKER_ERROR: 'worker_error',
  RUNTIME_ERROR: 'runtime_error',
});

/**
 * A wheel file name as the server's lock overlay may list one: bare, no path, and
 * pure Python. The same pattern as `_WHEEL_FILE_NAME` in
 * `src/core/config/runtime_lock.py`, which the server enforces on every entry;
 * checked again here because a name that is not bare would resolve outside the
 * wheel base.
 */
const WHEEL_FILE_NAME = /^[A-Za-z0-9_.+-]+-py3-none-any\.whl$/;

/**
 * The community's lock entries, each wheel's file name made the URL it is served at.
 *
 * The server sends bare names and serves the wheels itself, so the host that
 * sent the hashes is the host the bytes come from. A name that is not bare is
 * refused rather than resolved, since resolving it could point outside that host.
 *
 * @param {{packages?: Object<string, object>, baseUrl?: string}|null} lock
 *   `runtime_lock` from /config, and where its wheels are served, ending in `/`.
 * @returns {Object<string, object>}
 */
export function resolveLockPackages(lock) {
  if (!lock || !lock.packages) return {};
  if (typeof lock.baseUrl !== 'string' || !lock.baseUrl.endsWith('/')) {
    throw new TypeError(`the lock's wheel base must be a URL ending in "/", got ${lock.baseUrl}`);
  }
  const resolved = {};
  for (const [key, entry] of Object.entries(lock.packages)) {
    const name = entry && entry.file_name;
    if (typeof name !== 'string' || !WHEEL_FILE_NAME.test(name)) {
      throw new TypeError(`lock entry ${key} does not name a wheel: ${name}`);
    }
    resolved[key] = { ...entry, file_name: new URL(name, lock.baseUrl).href };
  }
  return resolved;
}

/**
 * The worker's configuration, as plain data: what createWorkerRuntime receives.
 *
 * Separate from buildWorkerSource so a test can hand the SAME configuration to
 * the core without parsing it back out of generated source.
 *
 * @param {object} runtime - A community's `runtime.python` config.
 * @param {{packages?: Object<string, object>, baseUrl?: string}|null} [lock] - See resolveLockPackages.
 * @returns {object}
 */
export function buildWorkerConfig(runtime, lock = null) {
  return {
    indexURL: `https://cdn.jsdelivr.net/pyodide/v${runtime.pyodide_version}/full/`,
    preload: runtime.preload || [],
    allowInstall: runtime.allow_install || [],
    indexUrls: runtime.index_urls || [],
    fetchAllow: runtime.fetch_allow || [],
    lockPackages: resolveLockPackages(lock),
    prelude: typeof runtime.prelude === 'string' ? runtime.prelude : '',
    python: {
      helpers: buildHelpersSource(),
      outputCapture: buildOutputCaptureSource(resolveLimits(runtime.limits)),
      dataClient: buildDataClientSource(),
      namespaceSeal: buildNamespaceSealSource(),
    },
  };
}

/**
 * The worker's side of the platform: loading Pyodide, reading the distribution's
 * lock, sealing egress and reporting to the page.
 *
 * Embedded by value like createWorkerRuntime, so it must be self-contained too:
 * every name it uses is a worker global or its argument. Exported so a test can
 * run the same text against a real server.
 *
 * @param {(fetchAllow: string[]) => void} seal - The egress guard's one-shot seal.
 * @returns {{load: Function, stockLock: Function, seal: Function, send: Function}}
 */
export function createWorkerEnv(seal) {
  return {
    load(indexURL, options) {
      importScripts(`${indexURL}pyodide.js`);
      return loadPyodide({ indexURL, stdout: () => {}, stderr: () => {}, ...options });
    },
    async stockLock(indexURL) {
      const response = await fetch(`${indexURL}pyodide-lock.json`);
      if (!response.ok) throw new Error(`HTTP ${response.status} for ${response.url || indexURL}`);
      return response.json();
    },
    seal,
    send(message) {
      self.postMessage(message);
    },
  };
}

/**
 * Build the worker's source as a string, for a blob URL.
 *
 * Returned as source rather than shipped as a file because embedders pin this
 * widget by SRI hash; a second fetched file would be a second thing to pin and
 * could drift from the pinned one.
 *
 * @param {object} runtime - A community's `runtime.python` config.
 * @param {{packages?: Object<string, object>, baseUrl?: string}|null} [lock] - See resolveLockPackages.
 * @returns {string} Worker source.
 */
export function buildWorkerSource(runtime, lock = null) {
  // Everything the worker needs, decided here and baked in as data. A worker
  // that has to ask for its own configuration has a window where it is alive
  // but unconfigured, and that is exactly where a half-initialized runtime
  // would be reachable.
  const config = buildWorkerConfig(runtime, lock);

  // The egress guard is installed as the worker's FIRST statement, before the
  // loader is even fetched, because importScripts is one of the transports it
  // shims and the boot itself goes through it. A guard installed after boot
  // would leave the whole download window unguarded.
  //
  // Its boot allowlist is the Pyodide CDN, any configured wheel index and each
  // lock-overlay wheel by its exact URL, and NOT the community's fetch_allow:
  // those are what the runtime needs to assemble itself, a strictly different
  // set from what executed code may reach. The wheels are listed one by one so
  // that booting does not open the whole API host.
  const lockWheels = Object.values(config.lockPackages).map((entry) => entry.file_name);
  const guard = buildEgressGuardSource({ bootAllow: [config.indexURL].concat(config.indexUrls, lockWheels) });

  // The template is only glue now. The logic is createWorkerRuntime, embedded
  // by value: an interpolated value is inserted verbatim, so escapes inside it
  // are not reinterpreted the way escapes written in this template would be.
  //
  // One function scope holds the guard and the runtime together. The guard must
  // not reach global scope (a top-level function in a classic worker becomes a
  // property of the global object, which made the sealing function publicly
  // callable), and the runtime must be able to call __seal, so they share a
  // scope rather than communicate through one.
  return `
    (function () {
    ${guard}

    const runtime = (${createWorkerRuntime.toString()})(
      ${JSON.stringify(config)},
      (${createWorkerEnv.toString()})(__seal)
    );

    self.onmessage = function (event) {
      runtime.handle(event.data || {});
    };
    })();
  `;
}

/**
 * Default worker factory: a blob worker, which is what the browser needs.
 *
 * Injectable so tests can supply a real worker over the same message protocol
 * without downloading Pyodide. The seam is the platform boundary, not the logic:
 * everything this module decides still runs for real on either side of it.
 *
 * @param {string} source - Worker source from `buildWorkerSource`.
 * @returns {Worker}
 */
export function defaultWorkerFactory(source) {
  const blob = new Blob([source], { type: 'application/javascript' });
  const url = URL.createObjectURL(blob);
  const worker = new Worker(url);
  // Safe to revoke immediately: the worker holds its own reference to the script
  // once constructed, and leaving it un-revoked leaks the blob for the life of
  // the document, which matters because a widget can reboot the worker many times
  // in one session.
  URL.revokeObjectURL(url);
  return worker;
}

/**
 * `ClientToolResult.artifacts` is at most 32 names of at most 512 characters.
 * Not in `src/core/limits.py`, so mirrored here from the model's own Field
 * declarations, which `test-runtime-lifecycle.js` reads back.
 */
export const MAX_ARTIFACTS = 32;
export const MAX_ARTIFACT_NAME_CHARS = 512;

/**
 * The fields of phase 1's `ClientToolResult`, and nothing else.
 *
 * That model is `extra="forbid"`, so a result carrying one extra key, such as
 * the worker protocol's own `type`, is refused WHOLE with a 422. Every result
 * this runtime hands a caller is cut to exactly this list by
 * `toClientToolResult`, and `test-runtime-lifecycle.js` reads the Python model
 * and fails if the two drift.
 */
export const CLIENT_TOOL_RESULT_FIELDS = Object.freeze([
  'call_id',
  'status',
  'stdout',
  'stderr',
  'summary',
  'images',
  'artifacts',
  'elapsed_ms',
]);

/**
 * Every status a `ClientToolResult` may report, by name, so the host code below
 * writes `RESULT_STATUS.CANCELLED` rather than a bare string it could misspell.
 *
 * Equals `ResultStatus` in `src/api/tool_results.py` (a `Literal`, and
 * `ClientToolResult` is `extra="forbid"`, so a status outside this set is
 * refused whole). `test-runtime-lifecycle.js` reads that Literal back and
 * fails if the two drift, the same way it already does for
 * `CLIENT_TOOL_RESULT_FIELDS` above.
 *
 * Not used inside `createWorkerRuntime` (osa-worker-core.js): that function is
 * serialized with `Function.prototype.toString` and loses its closure, so it
 * keeps its own status literals inline rather than referencing this constant.
 */
export const RESULT_STATUS = Object.freeze({
  OK: 'ok',
  ERROR: 'error',
  DENIED: 'denied',
  TIMEOUT: 'timeout',
  CANCELLED: 'cancelled',
  OOM: 'oom',
});

/** `RESULT_STATUS`'s values, in the order the Python `Literal` declares them. */
export const RESULT_STATUSES = Object.freeze(Object.values(RESULT_STATUS));

/**
 * Bound a string the same way the Python harness does: both ends kept, and the
 * gap says how much was dropped. Mirrors `_clip` in osa-output.js.
 *
 * @param {unknown} text
 * @param {number} limit
 * @returns {string}
 */
export function clipText(text, limit) {
  const value = typeof text === 'string' ? text : text == null ? '' : String(text);
  if (limit <= 0) return '';
  if (value.length <= limit) return value;
  const markerBudget = 64;
  if (limit <= markerBudget) return value.slice(0, limit);
  const head = Math.floor(((limit - markerBudget) * 3) / 5);
  const tail = limit - markerBudget - head;
  const dropped = value.length - head - tail;
  return `${value.slice(0, head)}\n... ${dropped} characters omitted ...\n${value.slice(value.length - tail)}`;
}

/**
 * Cut a worker message or a synthesized result to exactly the server's shape,
 * with every field inside the size the server enforces on it.
 *
 * The ONE choke point every result passes through before a caller sees it. The
 * worker's harness sizes its own output, but several results are built here on
 * the host from values the model chose: a get_full_output call_id or stream
 * name, the names in a denied import, a worker's error text. Each of those was
 * once reflected unbounded, and ClientToolResult refuses a result WHOLE on one
 * oversized field, so sizing them at each call site was one forgotten site away
 * from a 422. Enforcing it here makes that impossible by construction.
 *
 * @param {object} message
 * @param {ReturnType<typeof resolveLimits>} limits
 * @returns {object}
 */
export function toClientToolResult(message, limits) {
  const result = {};
  for (const field of CLIENT_TOOL_RESULT_FIELDS) {
    if (message[field] !== undefined) result[field] = message[field];
  }
  if ('stdout' in result) result.stdout = clipText(result.stdout, limits.stdout_chars);
  if ('stderr' in result) result.stderr = clipText(result.stderr, limits.stderr_chars);
  if ('summary' in result) result.summary = clipText(result.summary, limits.summary_chars);
  if ('images' in result) result.images = Array.isArray(result.images) ? result.images.slice(0, limits.images) : [];
  if ('artifacts' in result) {
    result.artifacts = Array.isArray(result.artifacts)
      ? result.artifacts.slice(0, MAX_ARTIFACTS).map((name) => clipText(name, MAX_ARTIFACT_NAME_CHARS))
      : [];
  }
  if ('elapsed_ms' in result) {
    const ms = Number(result.elapsed_ms);
    result.elapsed_ms = Number.isFinite(ms) && ms > 0 ? Math.round(ms) : 0;
  }
  return result;
}

/** How many runs' full output the browser keeps. */
export const FULL_OUTPUT_MAX_CALLS = 16;

/**
 * Total characters kept across all of them, text and base64 images together.
 * JavaScript strings are UTF-16, so this is about 32 MB of a tab's memory at
 * worst. The worker already bounds each stream, so no single run can exceed it.
 */
export const FULL_OUTPUT_MAX_CHARS = 16_000_000;

/**
 * The untruncated output of recent runs, kept in the browser for get_full_output.
 *
 * The conversation carries a bounded copy and a summary; this is where the rest
 * lives. It is per tab and in memory by design: raw output never goes to the
 * server, so it is gone after a reload, and get_full_output says so rather than
 * returning nothing.
 *
 * Evicts the least recently used run first, by count and by total size, so one
 * enormous run cannot push out everything else and a long session cannot grow
 * without bound.
 */
export class FullOutputStore {
  // Private, because the size accounting is only correct if every change goes
  // through remember() and forget(). With plain fields, one direct write to the
  // map or the counter would desynchronize them with nothing to notice.
  #entries = new Map(); // insertion order is the LRU order
  #chars = 0;
  #maxCalls;
  #maxChars;

  constructor({ maxCalls = FULL_OUTPUT_MAX_CALLS, maxChars = FULL_OUTPUT_MAX_CHARS } = {}) {
    this.#maxCalls = maxCalls;
    this.#maxChars = maxChars;
  }

  static #sizeOf(entry) {
    let n = entry.stdout.length + entry.stderr.length + entry.traceback.length + entry.summary.length;
    for (const image of entry.images) n += image.data_base64.length;
    return n;
  }

  /**
   * @param {string} callId
   * @param {{stdout?: string, stderr?: string, traceback?: string, summary?: string}} full -
   *   `summary` is the UNCLIPPED summary text (osa-output.js's `_summary`,
   *   clipped only to `_FULL_CHARS`, unlike the copy sent to the model),
   *   kept for the workspace's results/run-NNN/summary.txt (#433); it is not
   *   one of `FULL_OUTPUT_STREAMS`, since the model's own `summary` field is
   *   already usually complete and get_full_output never reads this copy.
   * @param {object[]} images - The images the run returned.
   * @param {{local?: boolean}} [options] - `local: true` marks this entry as
   *   the reader's own run, so getFullOutput refuses to read it back to the
   *   model: "the assistant has not seen it" (the widget's own label) has to
   *   hold from the model's side too, and this is the flag that enforces it.
   */
  remember(callId, full, images, { local = false } = {}) {
    const entry = {
      stdout: String((full && full.stdout) || ''),
      stderr: String((full && full.stderr) || ''),
      traceback: String((full && full.traceback) || ''),
      summary: String((full && full.summary) || ''),
      images: Array.isArray(images) ? images : [],
      local: local === true,
    };
    this.forget(callId);
    this.#entries.set(callId, entry);
    this.#chars += FullOutputStore.#sizeOf(entry);
    // The newest entry is never evicted, even alone over budget: the worker
    // bounds each stream, so one run cannot exceed the budget by itself, and a
    // store that drops what was just produced would answer nothing.
    while (this.#entries.size > 1 && (this.#entries.size > this.#maxCalls || this.#chars > this.#maxChars)) {
      this.forget(this.#entries.keys().next().value);
    }
  }

  /** The entry, refreshed as most recently used, or undefined. */
  get(callId) {
    const entry = this.#entries.get(callId);
    if (entry !== undefined) {
      this.#entries.delete(callId);
      this.#entries.set(callId, entry);
    }
    return entry;
  }

  forget(callId) {
    const entry = this.#entries.get(callId);
    if (entry !== undefined) {
      this.#chars -= FullOutputStore.#sizeOf(entry);
      this.#entries.delete(callId);
    }
  }

  get size() {
    return this.#entries.size;
  }

  get chars() {
    return this.#chars;
  }

  get maxCalls() {
    return this.#maxCalls;
  }
}

/** The streams get_full_output can read. */
export const FULL_OUTPUT_STREAMS = Object.freeze(['stdout', 'stderr', 'traceback', 'figures']);

/** What the model is told when the person stopped a run. Fixed text, so it caches. */
export const CANCELLED_STDERR = '[runtime] the reader stopped this run before it finished.';

/**
 * Owns one Pyodide worker: its boot, its warm lifetime, and its teardown.
 */
export class PyodideRuntime {
  /**
   * @param {object} options
   * @param {object} options.runtime - A community's `runtime.python` config.
   * @param {{packages?: Object<string, object>, baseUrl?: string}|null} [options.lock] - The
   *   community's lock overlay and where its wheels are served; see resolveLockPackages.
   * @param {(event: object) => void} [options.onProgress] - Boot progress events.
   * @param {(state: string, detail?: object) => void} [options.onStateChange]
   * @param {(source: string) => Worker} [options.workerFactory]
   * @param {number} [options.bootTimeoutMs]
   */
  constructor({
    runtime,
    lock = null,
    onProgress = () => {},
    onStateChange = () => {},
    workerFactory = defaultWorkerFactory,
    bootTimeoutMs = DEFAULT_BOOT_TIMEOUT_MS,
  }) {
    if (!runtime || typeof runtime.pyodide_version !== 'string') {
      throw new TypeError('PyodideRuntime requires a runtime config with pyodide_version');
    }
    if (typeof bootTimeoutMs !== 'number' || !Number.isFinite(bootTimeoutMs) || bootTimeoutMs <= 0) {
      // Left unvalidated, a negative value is a coin flip: the host clamps it to
      // about a millisecond, so the boot either fails spuriously or wins the race
      // and is silently ignored, with no error in either case.
      throw new TypeError(`bootTimeoutMs must be a positive finite number, got ${bootTimeoutMs}`);
    }
    // Resolved once, here, so a malformed lock fails the setup that can report
    // it rather than a boot that would retry it.
    resolveLockPackages(lock);
    this.runtime = runtime;
    this.lock = lock;
    this.onProgress = onProgress;
    this.onStateChange = onStateChange;
    this.workerFactory = workerFactory;
    this.bootTimeoutMs = bootTimeoutMs;

    this.state = RUNTIME_STATE.IDLE;
    this.failure = null;
    this.version = null;

    this._worker = null;
    this._bootPromise = null;
    this._bootTimer = null;
    this._settle = null;
    // call_id -> {resolve, reject, timer, started}. Executions are correlated
    // by the call_id the server parked, which is the provider-assigned id of the
    // model's own tool_use block (phase 1 never mints one), so a result can
    // never be attributed to the wrong call.
    this._pending = new Map();
    // call_id -> {cancelled, cancel}, for executions still waiting on the boot. They are
    // not in _pending yet because no worker has been asked to run them.
    this._starting = new Map();
    this._callSeq = 0;
    // Clamped the same way the worker clamps them, so the host's deadline and
    // the worker's output caps cannot come from two different readings of one
    // config.
    this.limits = resolveLimits(runtime.limits);
    // Deliberately NOT cleared by _recycle or terminate: output a run produced
    // is still the person's after the instance that produced it is gone, and a
    // timeout is exactly when someone wants to read what was printed first.
    this.outputs = new FullOutputStore();
    // call_id -> files, for a completed execution whose caller has not yet
    // called takeFiles(). Unlike `outputs`, this is not a store meant to be
    // read repeatedly or to outlive the run: it exists only for the short
    // window between a result settling and the workspace write that consumes
    // it, so takeFiles() deletes what it returns.
    this._files = new Map();
  }

  /** True when the runtime can accept work. */
  get isReady() {
    return this.state === RUNTIME_STATE.READY;
  }

  /**
   * Whether this community wants the runtime booted as soon as the widget opens
   * rather than on the first execution.
   */
  get preloadsOnOpen() {
    return this.runtime.preload_on === 'widget_open';
  }

  /**
   * Whether this community wants the runtime booted as soon as the reader sends
   * their first message, rather than waiting for the first execution or the widget
   * opening. Overlaps the download with the model's first turn.
   */
  get preloadsOnFirstMessage() {
    return this.runtime.preload_on === 'first_message';
  }

  _setState(state, detail) {
    this.state = state;
    this.onStateChange(state, detail);
  }

  /**
   * Boot the worker, or return the in-flight boot.
   *
   * Idempotent by design. `widget_open` preloading and a first `tool_request` can
   * both reach this, and booting twice would leave an orphaned worker holding a
   * Pyodide heap that nothing ever terminates.
   *
   * @returns {Promise<{version: string}>}
   */
  boot() {
    if (this.state === RUNTIME_STATE.READY) {
      return Promise.resolve({ version: this.version });
    }
    if (this._bootPromise) {
      return this._bootPromise;
    }
    if (this.state === RUNTIME_STATE.TERMINATED) {
      // A terminated runtime is deliberately not self-healing. terminate() is
      // "stop the runtime", and a silent reboot here would turn that into "stop,
      // then quietly start again", the opposite of what was asked. Call reboot()
      // to explicitly start a new one. Stopping one RUN is cancel(), which does
      // recycle, because the runtime itself was never asked to go.
      return Promise.reject(new Error('runtime is terminated; call reboot() to start a new one'));
    }

    this.failure = null;
    this._setState(RUNTIME_STATE.BOOTING);

    this._bootPromise = new Promise((resolve, reject) => {
      this._settle = { resolve, reject };

      let worker;
      try {
        worker = this.workerFactory(buildWorkerSource(this.runtime, this.lock));
      } catch (err) {
        this._failBoot(BOOT_FAILURE.WORKER_ERROR, `worker could not be constructed: ${err && err.message}`);
        return;
      }
      this._worker = worker;

      worker.onmessage = (event) => this._onWorkerMessage(event.data || {});
      worker.onerror = (event) => {
        const message = (event && event.message) || '';
        // After a successful boot this is no longer a boot failure. The usual
        // cause is the wasm instance aborting, which takes the whole worker with
        // it and would otherwise leave the execution pending forever: a promise
        // nobody settles is indistinguishable from code still running.
        if (this.state === RUNTIME_STATE.READY) {
          this._failRunningExecutions(message);
          return;
        }
        // A CSP refusal of the worker script itself surfaces here with an empty
        // message, which is why the text below does not rely on one.
        this._failBoot(
          BOOT_FAILURE.WORKER_ERROR,
          (event && event.message) || 'the worker failed to start, which a Content-Security-Policy refusal also looks like'
        );
      };

      this._bootTimer = setTimeout(() => {
        this._failBoot(
          BOOT_FAILURE.TIMEOUT,
          `the Python runtime did not finish starting within ${Math.round(this.bootTimeoutMs / 1000)}s. ` +
            'A blocked runtime looks exactly like a slow one: if this page sets a Content-Security-Policy, ' +
            "check that script-src includes 'wasm-unsafe-eval' and worker-src includes blob:."
        );
      }, this.bootTimeoutMs);

      worker.postMessage({ type: 'boot' });
    });

    return this._bootPromise;
  }

  _onWorkerMessage(msg) {
    // Once a boot has settled, protocol messages from that worker are ignored.
    // A second `ready` previously overwrote `version` and refired onStateChange
    // after boot() had already resolved and the caller had moved on, which is
    // silent: no error, and the UI simply sees a version change out from under
    // it. `progress` is exempt because it is advisory and harmless either way.
    if (this._settle === null && (msg.type === 'ready' || msg.type === 'error')) {
      return;
    }
    if (msg.type === 'result') {
      // The full streams stay in this tab. They are split off HERE, before the
      // result reaches any caller, so nothing downstream can forward them to the
      // server by accident: ClientToolResult is extra="forbid" and would refuse
      // the whole result, and the point is that bulk output never leaves.
      const full = msg.full;
      // Explicitly saved files, split off the same way and for the same
      // reason (#433): `files` is not a ClientToolResult field, so
      // toClientToolResult already drops it, but the bytes still have to
      // reach whoever persists them. Held briefly in `_files`, keyed by
      // call_id, and handed out exactly once by takeFiles(); never folded
      // into `outputs`, which is long-lived and sized for text, not for up
      // to 25 MB of file bytes per run.
      const files = Array.isArray(msg.files) ? msg.files : [];
      const result = toClientToolResult(msg, this.limits);
      // A result for a call nobody is waiting on is dropped rather than thrown:
      // the execution was abandoned, or its deadline already settled it. Never
      // silently attributed to another call, and not kept either, since nobody
      // was told its call_id resolved to this output.
      const pendingEntry = this._pending.get(msg.call_id);
      if (pendingEntry && full) {
        // `local` travels with the pending entry from execute()'s own options,
        // never guessed from the call_id's shape, so get_full_output can later
        // refuse a local run's output without trusting a naming convention.
        this.outputs.remember(msg.call_id, full, result.images, { local: pendingEntry.local === true });
      }
      if (pendingEntry && files.length > 0) {
        this._files.set(msg.call_id, files);
      }
      const settled = this._settleExecution(msg.call_id, result);
      // An instance that reported its own out-of-memory is still the instance
      // that ran out. Keeping it would let the next execution start against a
      // heap that is already exhausted, and fail for a reason belonging to the
      // previous run.
      if (settled && msg.status === RESULT_STATUS.OOM) {
        this._recycle();
      }
      return;
    }
    if (msg.type === 'progress') {
      this.onProgress(msg);
      return;
    }
    if (msg.type === 'ready') {
      this._clearBootTimer();
      this.version = msg.version || null;
      this._setState(RUNTIME_STATE.READY, { version: this.version });
      const settle = this._settle;
      this._settle = null;
      this._bootPromise = null;
      if (settle) settle.resolve({ version: this.version });
      return;
    }
    if (msg.type === 'error') {
      this._failBoot(BOOT_FAILURE.RUNTIME_ERROR, msg.message || 'unknown runtime error');
    }
  }

  _clearBootTimer() {
    if (this._bootTimer !== null) {
      clearTimeout(this._bootTimer);
      this._bootTimer = null;
    }
  }

  _failBoot(kind, message) {
    this._clearBootTimer();
    this.failure = { kind, message };

    // Tear the worker down rather than leaving it. A worker that failed to boot
    // may still be mid-download, and leaving it alive keeps a Pyodide heap and an
    // open connection for a runtime nothing will ever use.
    this._disposeWorker();
    this._setState(RUNTIME_STATE.FAILED, this.failure);

    const settle = this._settle;
    this._settle = null;
    this._bootPromise = null;
    if (settle) {
      const err = new Error(message);
      err.kind = kind;
      settle.reject(err);
    }
  }

  /**
   * Fail every outstanding execution.
   *
   * A worker that goes away takes its in-flight executions with it, and a
   * promise nobody ever settles is indistinguishable in the UI from code that
   * is still running. Cancellation and boot failure both reach here.
   *
   * @param {string} reason
   */
  _failPending(reason) {
    const waiting = Array.from(this._pending.values());
    this._pending.clear();
    for (const one of waiting) {
      one.reject(new Error(reason));
    }
  }

  _disposeWorker() {
    if (this._worker) {
      this._worker.onmessage = null;
      this._worker.onerror = null;
      try {
        this._worker.terminate();
      } catch {
        // terminate() on an already-dead worker is not an error worth surfacing.
      }
      this._worker = null;
    }
    this._failPending('the runtime was torn down before this execution finished');
  }

  /**
   * The result of a call that produced no output of its own: it was stopped
   * (timeout, cancellation) or its instance died (oom). Only the status, the
   * explanation and the elapsed time differ between them.
   *
   * @param {string} callId
   * @param {string} status
   * @param {string} stderr
   * @param {number} elapsedMs
   * @returns {object}
   */
  _emptyResult(callId, status, stderr, elapsedMs) {
    return toClientToolResult(
      { call_id: callId, status, stdout: '', stderr, summary: '', images: [], artifacts: [], elapsed_ms: elapsedMs },
      this.limits
    );
  }

  /**
   * Run one block of Python and resolve with the result envelope.
   *
   * Boots on demand, because `preload_on: first_run` means the first execution
   * is what triggers the boot at all, and a caller should not have to know which
   * mode a community configured.
   *
   * The returned object is phase 1's `ClientToolResult` shape minus `call_id`
   * handling: `{status, stdout, stderr, summary, images, artifacts, elapsed_ms}`.
   * It resolves for a failed, timed-out or cancelled run as much as for a
   * successful one; the status says which. It rejects only when no result can
   * exist, such as the boot failing or the runtime being torn down mid-execution.
   *
   * @param {string} code - Python to run.
   * @param {{callId?: string, local?: boolean}} [options] - `callId` is the
   *   call_id from the server's tool_request, which is the provider-assigned
   *   id of the model's tool_use block; the result is reported against it. A
   *   generated one (`local-N`) is used when absent. `local: true` marks this
   *   execution as the READER's own (ClientToolController#runLocal), which
   *   travels with the pending call so the remembered full output is marked
   *   the same way: get_full_output must never be able to read it back for
   *   the model, and this is the one place that fact is recorded (osa-controller.js
   *   never relies on the call_id's `person-run-` shape for this).
   * @returns {Promise<object>} The result envelope.
   */
  async execute(code, options = {}) {
    if (typeof code !== 'string') {
      throw new TypeError(`code must be a string, got ${typeof code}`);
    }
    const callId = options.callId || `local-${++this._callSeq}`;
    if (this._pending.has(callId) || this._starting.has(callId)) {
      throw new Error(`an execution is already in flight for call_id ${callId}`);
    }

    // Registered BEFORE the boot, because a cold boot takes seconds and Stop is
    // most likely to be pressed during exactly those seconds. Without this a
    // cancel() would find nothing to cancel, and the code would run anyway
    // once the boot finished.
    //
    // The boot is raced rather than awaited, so Stop answers at once instead of
    // when the boot ends. The boot itself carries on: it is shared, and the
    // person stopped a run, not the runtime.
    const requested = Date.now();
    // The flag as well as the promise: a cancel() that lands after the boot won
    // the race but before this function resumes must still stop the run.
    const starting = { cancelled: false, cancel: null };
    const cancelled = new Promise((resolve) => {
      starting.cancel = () => {
        starting.cancelled = true;
        resolve(true);
      };
    });
    this._starting.set(callId, starting);
    let wasCancelled;
    try {
      wasCancelled = await Promise.race([this.boot().then(() => false), cancelled]);
    } finally {
      this._starting.delete(callId);
    }
    if (wasCancelled || starting.cancelled) {
      return this._emptyResult(callId, RESULT_STATUS.CANCELLED, CANCELLED_STDERR, Date.now() - requested);
    }

    // Re-checked AFTER the await, not before it. `boot()` yields, so a
    // terminate() can land between the call and the registration below; without
    // this the execution would be registered against a worker that no longer
    // exists and surface as a TypeError about null instead of as cancellation.
    if (this.state !== RUNTIME_STATE.READY || this._worker === null) {
      throw new Error('the runtime is not running; call reboot() to start a new one');
    }

    // The deadline is enforced HERE, not in the worker, because the worker cannot
    // interrupt its own Python. Cooperative interruption needs setInterruptBuffer,
    // which needs a SharedArrayBuffer, which needs cross-origin isolation on every
    // embedding page. #431 rules that out, so terminating is the only way to stop
    // a runaway loop, and only the host can terminate.
    const deadlineMs = Math.max(1, this.limits.exec_seconds) * 1000;
    const started = Date.now();

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        // Resolved, not rejected: the server expects a ClientToolResult for every
        // call it parked, and a timeout is a result with a status. Rejecting
        // would leave the model with no tool_result for its tool_use, which the
        // provider refuses outright.
        this._settleExecution(
          callId,
          this._emptyResult(
            callId,
            RESULT_STATUS.TIMEOUT,
            `[runtime] the code ran longer than ${this.limits.exec_seconds}s and was stopped.`,
            Date.now() - started
          )
        );
        // Recycled rather than terminated: this RUN is over, not the runtime, and
        // the next execution boots a fresh worker. cancel() does the same for a
        // run the person stops. terminate() stays reserved for stopping the
        // runtime itself, which does not self-heal.
        this._recycle();
      }, deadlineMs);

      // A call waiting on the boot and a call running are never the same
      // call; if they ever were, cancel() would stop the wrong one.
      if (this._starting.has(callId)) {
        clearTimeout(timer);
        reject(new Error(`call_id ${callId} is both starting and running`));
        return;
      }
      this._pending.set(callId, { resolve, reject, timer, started, local: options.local === true });
      try {
        this._worker.postMessage({ type: 'execute', call_id: callId, code });
      } catch (err) {
        this._pending.delete(callId);
        clearTimeout(timer);
        reject(err);
      }
    });
  }

  /**
   * Settle every in-flight execution after the worker itself died.
   *
   * Reported as `oom` rather than `error`: a worker that dies mid-execution in
   * this runtime is overwhelmingly a wasm memory abort, which takes the instance
   * down with no exception to catch and no deadline fired. Calling that a
   * generic error would send the model off rewriting correct code, when the
   * useful advice is to work on less data at a time.
   *
   * @param {string} message - Whatever the worker managed to report, often empty.
   */
  _failRunningExecutions(message) {
    const entries = Array.from(this._pending.entries());
    for (const [callId, waiting] of entries) {
      this._pending.delete(callId);
      clearTimeout(waiting.timer);
      waiting.resolve(
        this._emptyResult(
          callId,
          RESULT_STATUS.OOM,
          '[runtime] the Python runtime ran out of memory and was restarted. ' +
            'Try working on less data at a time.' + (message ? ' (' + message + ')' : ''),
          Date.now() - waiting.started
        )
      );
    }
    this._recycle();
  }

  /**
   * Stop one execution because the person asked to.
   *
   * Symmetric with a timeout: the call resolves with a result, status
   * `cancelled`, because the server parked it and the model's tool_use needs a
   * tool_result either way. A running call takes its instance with it, since
   * terminating is the only way to stop Python here (see execute), and the next
   * execution boots a fresh one. A call still waiting on the boot never starts,
   * and the boot carries on, since the person stopped a run and not the runtime.
   *
   * Unlike terminate(), this self-heals: terminate() stops the runtime and
   * refuses to boot again until reboot(), while this stops one call and leaves
   * the runtime able to serve the next.
   *
   * @param {string} callId
   * @returns {boolean} Whether there was anything to cancel.
   */
  cancel(callId) {
    const starting = this._starting.get(callId);
    if (starting) {
      starting.cancel();
      return true;
    }
    const waiting = this._pending.get(callId);
    if (!waiting) {
      return false;
    }
    this._settleExecution(callId, this._emptyResult(callId, RESULT_STATUS.CANCELLED, CANCELLED_STDERR, Date.now() - waiting.started));
    this._recycle();
    return true;
  }

  /**
   * Settle one outstanding execution and stop its deadline.
   *
   * One place, so a result arriving and a deadline firing cannot both settle the
   * same call: whichever gets here first removes it from `_pending`.
   *
   * @param {string} callId
   * @param {object} result
   * @returns {boolean} Whether anything was waiting.
   */
  _settleExecution(callId, result) {
    const waiting = this._pending.get(callId);
    if (!waiting) {
      return false;
    }
    this._pending.delete(callId);
    clearTimeout(waiting.timer);
    waiting.resolve(result);
    return true;
  }

  /**
   * Discard the worker and return to IDLE, so the next execution boots a fresh one.
   *
   * Distinct from terminate(), which is a person saying stop and deliberately does
   * NOT self-heal. This is the runtime discarding an instance it can no longer
   * trust: a timed-out run whose Python is still spinning, or an out-of-memory
   * abort. Both leave the instance unusable while the runtime itself is fine.
   */
  _recycle() {
    this._clearBootTimer();
    this._disposeWorker();
    this.version = null;
    this._settle = null;
    this._bootPromise = null;
    this._setState(RUNTIME_STATE.IDLE);
  }

  /**
   * Answer a get_full_output call from what this tab kept.
   *
   * Runs on the host, not in the worker: it reads stored text, needs no Python,
   * and must work after the instance that produced the output was recycled.
   *
   * @param {{call_id: string, stream?: string, offset?: number}} args - The tool
   *   call's arguments. `call_id` names the EARLIER run to read.
   * @param {{callId?: string}} [options] - `callId` is this get_full_output
   *   call's own id, which the result is reported against.
   * @returns {object} A result envelope, never a rejection: the model gets an
   *   answer it can act on for every call, including the ones that cannot be
   *   served.
   */
  getFullOutput(args, options = {}) {
    const requested = args && typeof args.call_id === 'string' ? args.call_id : '';
    const stream = (args && args.stream) || 'stdout';
    const rawOffset = args && args.offset;
    const offset = Number.isInteger(rawOffset) && rawOffset > 0 ? rawOffset : 0;
    // Model-chosen values, echoed back in messages. Bounded for readability here;
    // toClientToolResult bounds the fields they land in regardless.
    const quoted = JSON.stringify(requested.length > 256 ? `${requested.slice(0, 256)}...` : requested);
    const answer = (fields) =>
      toClientToolResult(
        {
          call_id: options.callId || `local-${++this._callSeq}`,
          status: RESULT_STATUS.OK,
          stdout: '',
          stderr: '',
          summary: '',
          images: [],
          artifacts: [],
          // Zero rather than a measured duration: nothing ran, and a varying
          // number would be the one nondeterministic byte in a stable result.
          elapsed_ms: 0,
          ...fields,
        },
        this.limits
      );

    if (!FULL_OUTPUT_STREAMS.includes(stream)) {
      return answer({
        status: RESULT_STATUS.ERROR,
        stderr: `[runtime] unknown stream ${JSON.stringify(String(stream).slice(0, 64))}; use one of ${FULL_OUTPUT_STREAMS.join(', ')}.`,
      });
    }

    const entry = this.outputs.get(requested);
    // A local run's entry is refused with EXACTLY the unknown-id answer
    // below, not a distinct "that one is private" message: the model must
    // not learn the run even exists, only that this call_id has nothing for
    // it, the same as a call_id that was never recorded or already evicted.
    if (entry === undefined || entry.local === true) {
      return answer({
        status: RESULT_STATUS.ERROR,
        stderr:
          `[runtime] no output is kept for call_id ${quoted} in this browser. Output stays in the ` +
          `tab that ran the code: it is gone after a reload, and only the last ${this.outputs.maxCalls} ` +
          'runs are kept.',
      });
    }

    if (stream === 'figures') {
      // Every image the run returned. They are re-attached to THIS result, so
      // the model sees them again even though stored history carries only a
      // placeholder for the original.
      const images = entry.images.slice(0, this.limits.images);
      return answer({
        images,
        summary:
          images.length === 0
            ? `get_full_output: call_id ${quoted} returned no figures.`
            : `get_full_output: ${images.length} figure(s) from call_id ${quoted}, attached.`,
      });
    }

    // A stream is returned in the field it came from, so the fence labels it
    // the way the original result did: stdout as stdout, and stderr and the
    // traceback (which has no field of its own) as stderr. Each page is sized by
    // THAT field's cap, since the server enforces the cap per field.
    const field = stream === 'stdout' ? 'stdout' : 'stderr';
    const pageSize = field === 'stdout' ? this.limits.stdout_chars : this.limits.stderr_chars;
    const text = entry[stream];
    if (offset >= text.length && text.length > 0) {
      return answer({
        summary: `get_full_output: offset ${offset} is past the end of ${stream} for call_id ${quoted}, which has ${text.length} characters.`,
      });
    }
    const end = Math.min(text.length, offset + pageSize);
    const more = end < text.length ? ` More remains: call again with offset=${end}.` : ' This is the end of the stream.';
    return answer({
      [field]: text.slice(offset, end),
      summary:
        text.length === 0
          ? `get_full_output: ${stream} of call_id ${quoted} is empty.`
          : `get_full_output: ${stream} of call_id ${quoted}, characters ${offset} to ${end} of ${text.length}.${more}`,
    });
  }

  /**
   * The files an execution explicitly saved (osa.save_script/save_artifact),
   * as `{path, data_base64}`, and forget them.
   *
   * A one-shot read, not a lookup: whoever persists a run's files is meant to
   * call this exactly once, right after `execute()` resolves for that
   * call_id, and the workspace write is the only reason this exists. Unlike
   * `getFullOutput`, there is no "read it again later": the bytes are not
   * kept once taken, or once the call they belonged to is superseded.
   *
   * @param {string} callId
   * @returns {Array<{path: string, data_base64: string}>} Possibly empty.
   */
  takeFiles(callId) {
    const files = this._files.get(callId);
    this._files.delete(callId);
    return files || [];
  }

  /**
   * Terminate the worker.
   *
   * This is also cancellation. Cooperative interruption through
   * `setInterruptBuffer` needs a SharedArrayBuffer, which needs cross-origin
   * isolation on the embedding page, and requiring that would constrain every page
   * that embeds the widget. Terminating is decisive, needs nothing from the page,
   * and the measured cost is one cold boot.
   */
  terminate() {
    this._clearBootTimer();
    this._disposeWorker();

    const settle = this._settle;
    this._settle = null;
    this._bootPromise = null;
    this._setState(RUNTIME_STATE.TERMINATED);
    if (settle) {
      settle.reject(new Error('runtime terminated before it finished starting'));
    }
  }

  /**
   * Terminate whatever is running and start a fresh worker.
   *
   * @returns {Promise<{version: string}>}
   */
  reboot() {
    this.terminate();
    this.state = RUNTIME_STATE.IDLE;
    this.failure = null;
    this.version = null;
    return this.boot();
  }
}
