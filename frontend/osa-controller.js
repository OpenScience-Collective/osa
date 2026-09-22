/**
 * Answering the server's tool_request (epic #429, phase #431, step 7).
 *
 * The server ends a run on a browser tool call and parks it. Something in the
 * page must turn that request into exactly one ClientToolResult for the same
 * call_id, whatever happens in between: the person declines, the runtime cannot
 * start, the code runs out of time, the person presses Stop. A parked call with
 * no result leaves the model's tool_use unanswered, which the provider refuses
 * on every later turn, so this module's one promise is that answer() always
 * resolves with a result for the call it was given.
 *
 * It holds no UI. The widget supplies `gate`, which asks the person and
 * resolves with a decision, and renders the code with highlightPython.
 */

import { toClientToolResult } from './osa-runtime.js';

/** Bound by the server beside any python tool; see src/core/config/community.py. */
export const FULL_OUTPUT_TOOL_NAME = 'get_full_output';

/** The client tool runtimes this bundle can execute. */
export const RUNNABLE_RUNTIMES = Object.freeze(['python']);

/** What `gate` resolves with. Anything else is treated as a refusal. */
export const GATE_DECISION = Object.freeze({ RUN: 'run', DENY: 'deny' });

// Fixed text, so a refusal is the same bytes every time and the cached prefix
// of the conversation survives it.
export const DENIED_STDERR = '[runtime] the reader declined to run this code.';
export const DENIED_SUMMARY =
  'Not run. The reader declined this code. Do not call it again unless they ask; ' +
  'answer without running code, or say what the code would have done.';

const describe = (err) => String((err && err.message) || err || 'unknown error');

export class ClientToolController {
  #runtime;
  #tools;
  #gate;
  #autoRun = false;
  // {callId, cancelled, stop} while a request is being answered, else null.
  #current = null;

  /**
   * @param {object} options
   * @param {import('./osa-runtime.js').PyodideRuntime} options.runtime
   * @param {Array<{name: string, runtime: string, requires_permission: boolean}>} options.tools -
   *   The community config's `client_tools`.
   * @param {(prompt: {callId: string, tool: string, code: string, description: string}) => Promise<string>} options.gate -
   *   Asks the person whether to run the code; resolves with a GATE_DECISION.
   */
  constructor({ runtime, tools, gate }) {
    for (const method of ['execute', 'getFullOutput', 'cancel']) {
      if (!runtime || typeof runtime[method] !== 'function') {
        throw new TypeError(`ClientToolController needs a runtime with ${method}()`);
      }
    }
    if (!Array.isArray(tools)) {
      throw new TypeError('ClientToolController needs the configured client tools as a list');
    }
    if (typeof gate !== 'function') {
      // There is no safe default. Running without asking is the one thing the
      // gate exists to prevent, and refusing everything would look like a bug.
      throw new TypeError('ClientToolController needs a gate to ask the person with');
    }
    this.#runtime = runtime;
    this.#gate = gate;
    this.#tools = new Map(
      tools
        .filter((tool) => tool && typeof tool.name === 'string' && RUNNABLE_RUNTIMES.includes(tool.runtime))
        .map((tool) => [tool.name, tool])
    );
  }

  /**
   * The names to declare on /chat and /chat/resume: what this page can run.
   *
   * A tool of a runtime this bundle does not have is left out, so the server
   * never binds it here and never parks a call this page would have to refuse.
   *
   * @returns {string[]}
   */
  get declared() {
    const names = Array.from(this.#tools.keys());
    return names.length > 0 ? names.concat(FULL_OUTPUT_TOOL_NAME) : [];
  }

  /**
   * Run code without asking, for the rest of this page's life. Deliberately not
   * persisted anywhere (#431 decision 4): it is off again after a reload.
   */
  get autoRun() {
    return this.#autoRun;
  }

  set autoRun(value) {
    this.#autoRun = value === true;
  }

  /** Whether a request is being answered right now. */
  get busy() {
    return this.#current !== null;
  }

  /**
   * Answer one tool_request.
   *
   * @param {{call_id: string, tool: string, args: object, requires_permission?: boolean}} request -
   *   The `tool_request` SSE event.
   * @returns {Promise<object>} A ClientToolResult for `request.call_id`. Never
   *   rejects for anything that can happen while answering; it throws only when
   *   the request has no usable call_id, since no result could name its call.
   */
  async answer(request) {
    const callId = request && request.call_id;
    if (typeof callId !== 'string' || callId === '' || callId.length > 256) {
      throw new TypeError('a tool_request needs a call_id of 1 to 256 characters to be answered');
    }
    if (this.#current !== null) {
      // The server parks one call per session, so this is a second session in
      // the same page or a bug. Either way it gets a result, and the first
      // call is left alone.
      return this.#result(callId, 'error', '[runtime] another browser task is still running in this page.');
    }
    const current = { callId, cancelled: false, stop: null };
    this.#current = current;
    try {
      return await this.#dispatch(request, current);
    } finally {
      this.#current = null;
    }
  }

  /**
   * Stop the request being answered.
   *
   * While the person is still being asked, this counts as declining: nothing
   * ran. Once the code is running, the runtime stops it and the result says
   * `cancelled`.
   *
   * @returns {boolean} Whether there was anything to stop.
   */
  cancel() {
    const current = this.#current;
    if (current === null) {
      return false;
    }
    current.cancelled = true;
    if (current.stop !== null) {
      current.stop();
      return true;
    }
    return this.#runtime.cancel(current.callId);
  }

  async #dispatch(request, current) {
    const { callId } = current;
    const tool = request.tool;
    const args = request.args !== null && typeof request.args === 'object' ? request.args : {};

    if (tool === FULL_OUTPUT_TOOL_NAME) {
      // No gate: it reads output this tab already holds and runs nothing.
      return this.#runtime.getFullOutput(args, { callId });
    }

    const configured = typeof tool === 'string' ? this.#tools.get(tool) : undefined;
    if (configured === undefined) {
      const name = JSON.stringify(String(tool).slice(0, 64));
      return this.#result(callId, 'error', `[runtime] this page cannot run a tool named ${name}.`);
    }
    if (typeof args.code !== 'string' || args.code.trim() === '') {
      return this.#result(callId, 'error', '[runtime] the call carried no code to run.');
    }

    // Asked unless BOTH the request and the page's own config say the tool
    // needs no permission. A missing or malformed flag on either side asks.
    const skipGate = request.requires_permission === false && configured.requires_permission === false;
    if (!skipGate && !this.#autoRun) {
      const decision = await this.#ask(current, {
        callId,
        tool,
        code: args.code,
        description: typeof args.description === 'string' ? args.description : '',
      });
      if (decision.failed) {
        return this.#result(
          callId,
          'error',
          `[runtime] the permission prompt could not be shown, so the code was not run: ${decision.failed}`
        );
      }
      if (decision.value !== GATE_DECISION.RUN || current.cancelled) {
        return this.#result(callId, 'denied', DENIED_STDERR, DENIED_SUMMARY);
      }
    }

    try {
      return await this.#runtime.execute(args.code, { callId });
    } catch (err) {
      // No result can come from the runtime: it could not boot, or it was torn
      // down. The call still needs one.
      return this.#result(callId, 'error', `[runtime] the code could not be run: ${describe(err)}`);
    }
  }

  /** Ask the person, and let cancel() answer for them. */
  #ask(current, prompt) {
    return new Promise((resolve) => {
      current.stop = () => resolve({ value: GATE_DECISION.DENY });
      let asking;
      try {
        asking = Promise.resolve(this.#gate(prompt));
      } catch (err) {
        asking = Promise.reject(err);
      }
      asking.then(
        (value) => resolve({ value }),
        (err) => resolve({ failed: describe(err) })
      );
    }).finally(() => {
      current.stop = null;
    });
  }

  #result(callId, status, stderr, summary = '') {
    return toClientToolResult(
      {
        call_id: callId,
        status,
        stdout: '',
        stderr,
        summary,
        images: [],
        artifacts: [],
        // Nothing ran.
        elapsed_ms: 0,
      },
      this.#runtime.limits
    );
  }
}
