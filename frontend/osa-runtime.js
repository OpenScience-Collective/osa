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

import { buildEgressGuardSource } from './osa-egress.js';

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
 * Build the worker's source as a string, for a blob URL.
 *
 * Returned as source rather than shipped as a file because embedders pin this
 * widget by SRI hash; a second fetched file would be a second thing to pin and
 * could drift from the pinned one.
 *
 * @param {object} runtime - A community's `runtime.python` config.
 * @returns {string} Worker source.
 */
export function buildWorkerSource(runtime) {
  const version = runtime.pyodide_version;
  const indexURL = `https://cdn.jsdelivr.net/pyodide/v${version}/full/`;
  const preload = JSON.stringify(runtime.preload || []);

  // The egress guard is installed as the worker's FIRST statement, before the
  // loader is even fetched, because `importScripts` is one of the transports it
  // shims and the boot itself goes through it. A guard installed after boot
  // would leave the whole download window unguarded.
  //
  // Its boot allowlist is the Pyodide CDN plus any configured wheel index, and
  // NOT the community's `fetch_allow`: those are the origins the runtime needs
  // to assemble itself, which is a strictly different set from the origins
  // executed code may reach.
  const bootAllow = [indexURL].concat(runtime.index_urls || []);
  const guard = buildEgressGuardSource({ bootAllow });
  const fetchAllow = JSON.stringify(runtime.fetch_allow || []);

  // Assembled as a template so the config is baked in at build time rather than
  // posted after boot. A worker that has to ask for its own configuration has a
  // window where it is alive but unconfigured, and that window is exactly where a
  // half-initialized runtime would be reachable.
  // One function scope for the guard and the worker body together. The guard is
  // a fragment that must not reach global scope (a top-level `function` in a
  // classic worker becomes a property of the global object, which made the
  // sealing function publicly callable), and the boot handler has to be able to
  // call `__seal`, so they share a scope rather than communicate through one.
  return `
    (function () {
    ${guard}

    let pyodide = null;

    const send = (msg) => self.postMessage(msg);

    self.onmessage = async (event) => {
      const data = event.data || {};
      if (data.type !== 'boot') {
        send({ type: 'error', kind: 'protocol', message: 'unexpected message before boot: ' + data.type });
        return;
      }
      try {
        send({ type: 'progress', phase: 'loading_runtime' });
        importScripts(${JSON.stringify(indexURL)} + 'pyodide.js');

        pyodide = await loadPyodide({
          indexURL: ${JSON.stringify(indexURL)},
          stdout: () => {},
          stderr: () => {},
        });
        send({ type: 'progress', phase: 'runtime_loaded' });

        const preload = ${preload};
        for (let i = 0; i < preload.length; i++) {
          send({ type: 'progress', phase: 'loading_package', package: preload[i], index: i, total: preload.length });
          await pyodide.loadPackage(preload[i]);
        }

        // Install-time capability ends HERE, before anything executable exists.
        // Every package this runtime will ever have is installed during boot:
        // \`preload\` plus, in step 3, the community's \`allow_install\` list, which
        // is static config and so has no reason to be deferred. An import of
        // anything else then yields \`denied_import\` rather than a silent
        // install, which is the behavior #431 asks for, and it is what lets the
        // allowlist narrow to \`fetch_allow\` before any user code runs.
        //
        // The seal is ONE-SHOT. Moving this call later is therefore a deliberate
        // decision that widens what executed code can reach, not a refactor.
        __seal(${fetchAllow});

        send({ type: 'ready', version: pyodide.version });
      } catch (err) {
        send({
          type: 'error',
          kind: 'runtime',
          message: String((err && err.message) || err),
        });
      }
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
 * Owns one Pyodide worker: its boot, its warm lifetime, and its teardown.
 */
export class PyodideRuntime {
  /**
   * @param {object} options
   * @param {object} options.runtime - A community's `runtime.python` config.
   * @param {(event: object) => void} [options.onProgress] - Boot progress events.
   * @param {(state: string, detail?: object) => void} [options.onStateChange]
   * @param {(source: string) => Worker} [options.workerFactory]
   * @param {number} [options.bootTimeoutMs]
   */
  constructor({
    runtime,
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
    this.runtime = runtime;
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
      // A terminated runtime is deliberately not self-healing. Cancellation
      // terminates, and a silent reboot here would turn "stop" into "stop, then
      // quietly start again", which is the opposite of what the person asked for.
      // Call reboot() to explicitly start a new one.
      return Promise.reject(new Error('runtime is terminated; call reboot() to start a new one'));
    }

    this.failure = null;
    this._setState(RUNTIME_STATE.BOOTING);

    this._bootPromise = new Promise((resolve, reject) => {
      this._settle = { resolve, reject };

      let worker;
      try {
        worker = this.workerFactory(buildWorkerSource(this.runtime));
      } catch (err) {
        this._failBoot(BOOT_FAILURE.WORKER_ERROR, `worker could not be constructed: ${err && err.message}`);
        return;
      }
      this._worker = worker;

      worker.onmessage = (event) => this._onWorkerMessage(event.data || {});
      worker.onerror = (event) => {
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
