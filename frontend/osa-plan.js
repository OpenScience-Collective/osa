/**
 * The plan a browser reply works through (E2.3, E2.4): the pure logic behind the plan
 * card and the step progress, with no DOM and no widget state.
 *
 * The server's `propose_plan` call (src/tools/client_tools.py ProposePlanArgs) arrives as
 * a tool_request; the reader edits the steps, picks a run mode and decides; the answer
 * goes back on /chat/resume as a PlanDecision (src/api/tool_results.py). After approval
 * the model runs one `execute_code` per step, and each run is attributed to a step here:
 * a run after an ok run belongs to the next step, a run after a failed one is the same
 * step's next attempt, and a step has at most MAX_STEP_ATTEMPTS attempts.
 *
 * The limits below mirror the server's (MAX_PLAN_STEPS and the others in
 * src/tools/client_tools.py, MAX_PLAN_FEEDBACK_CHARS in src/api/tool_results.py);
 * tests/test_frontend/test_plan_resume_payload.py feeds this module's decisions to the
 * server's own models, so a drift fails there.
 *
 * Text from the model is untrusted here as everywhere: this module only bounds and
 * shapes it. Escaping is the renderer's job.
 */

/** The server's bounds on a plan and a decision. */
export const PLAN_LIMITS = Object.freeze({
  steps: 8,
  title: 120,
  detail: 600,
  findings: 2000,
  feedback: 1000,
  peakMb: 8192,
});

/** The most attempts one step gets: a run, and one more after it failed. */
export const MAX_STEP_ATTEMPTS = 2;

export const RUN_MODE = Object.freeze({ PAUSE: 'pause', RUN_ALL: 'run_all' });

export const PLAN_DECISION = Object.freeze({ APPROVED: 'approved', CHANGES: 'changes_requested' });

/**
 * Where a plan is in its life. `draft` is on the card awaiting the reader; `unanswered` is
 * what a draft becomes when a chat is restored (its parked call is long gone), which the
 * widget does on its own, before this module has loaded.
 */
export const PLAN_STATUS = Object.freeze({
  DRAFT: 'draft',
  APPROVED: 'approved',
  CHANGES: 'changes_requested',
  STOPPED: 'stopped',
  UNANSWERED: 'unanswered',
});

const RUN_MODES = Object.freeze(Object.values(RUN_MODE));

/** A line of text: every run of whitespace, line breaks included, becomes one space. */
export function oneLine(value) {
  return typeof value === 'string' ? value.split(/\s+/).filter(Boolean).join(' ') : '';
}

/**
 * The first `limit` code points of `value`. The server's limits count code points (pydantic
 * max_length), where String.slice counts UTF-16 code units and could cut an emoji in half,
 * leaving a lone surrogate the server's JSON parser refuses.
 */
export function clip(value, limit) {
  if (typeof value !== 'string') return '';
  return value.length <= limit ? value : Array.from(value).slice(0, limit).join('');
}

const text = clip;

/** A whole number from 0 to `max`, else 0. */
function wholeNumber(value, max) {
  return Number.isInteger(value) && value > 0 ? Math.min(value, max) : 0;
}

/**
 * One step as stored and shown: a one-line title and a detail, both bounded.
 * @param {unknown} step
 * @returns {{title: string, detail: string}}
 */
export function cleanStep(step) {
  const raw = step && typeof step === 'object' ? step : {};
  return {
    title: clip(oneLine(text(raw.title, PLAN_LIMITS.title * 4)), PLAN_LIMITS.title),
    detail: text(raw.detail, PLAN_LIMITS.detail),
  };
}

/**
 * A plan, from the arguments of a `propose_plan` tool_request, ready for the card.
 * Null when there is nothing to show: no steps, or a step with no title (the server
 * refuses both, so this is a malformed request rather than a plan).
 *
 * @param {unknown} args `{findings, steps: [{title, detail}], est_peak_mb}`
 * @param {string} callId the request's call_id, which the decision must name
 * @returns {object | null}
 */
export function normalizeProposal(args, callId) {
  const source = args && typeof args === 'object' ? args : {};
  if (typeof callId !== 'string' || callId === '' || callId.length > 256) return null;
  if (!Array.isArray(source.steps) || source.steps.length === 0) return null;
  const steps = source.steps.slice(0, PLAN_LIMITS.steps).map(cleanStep);
  if (steps.some((step) => step.title === '')) return null;
  return {
    callId,
    findings: text(source.findings, PLAN_LIMITS.findings),
    estPeakMb: wholeNumber(source.est_peak_mb, PLAN_LIMITS.peakMb),
    steps,
    runMode: RUN_MODE.PAUSE,
    status: PLAN_STATUS.DRAFT,
  };
}

// ---------------------------------------------------------------------------
// Editing the steps: each returns a new list and never changes its argument.
// ---------------------------------------------------------------------------

export function canAddStep(steps) {
  return Array.isArray(steps) && steps.length < PLAN_LIMITS.steps;
}

export function canRemoveStep(steps) {
  return Array.isArray(steps) && steps.length > 1;
}

/** `steps` with step `index` (0-based) replaced by the edited title and detail. */
export function editStep(steps, index, edit) {
  if (!Array.isArray(steps) || !Number.isInteger(index) || index < 0 || index >= steps.length) return steps;
  const next = steps.slice();
  next[index] = cleanStep(edit);
  return next;
}

/** `steps` with an empty step at the end, unless the plan is already at its limit. */
export function addStep(steps) {
  return canAddStep(steps) ? [...steps, { title: '', detail: '' }] : steps;
}

/** `steps` without step `index` (0-based), unless that would leave none. */
export function removeStep(steps, index) {
  if (!canRemoveStep(steps) || !Number.isInteger(index) || index < 0 || index >= steps.length) return steps;
  return steps.filter((_, i) => i !== index);
}

/** The 1-based number of the first step with no title, or 0 when every step has one. */
export function firstUntitledStep(steps) {
  const at = (Array.isArray(steps) ? steps : []).findIndex((step) => oneLine(step && step.title) === '');
  return at + 1;
}

// ---------------------------------------------------------------------------
// The decision
// ---------------------------------------------------------------------------

/**
 * The PlanDecision to send on /chat/resume, as the plan stands on the card.
 * Throws when the steps are not something the server would take, so a decision that
 * would be a 422 is never sent.
 *
 * @param {{callId: string, steps: object[], runMode: string}} plan
 * @param {string} decision PLAN_DECISION.APPROVED or PLAN_DECISION.CHANGES
 * @param {string} [feedback] the reader's note, optional, at most PLAN_LIMITS.feedback
 * @returns {{call_id: string, decision: string, steps: object[], run_mode: string, feedback?: string}}
 */
export function buildDecision(plan, decision, feedback = '') {
  if (!plan || typeof plan.callId !== 'string' || plan.callId === '') {
    throw new TypeError('a plan decision needs the call_id of the plan it answers');
  }
  if (decision !== PLAN_DECISION.APPROVED && decision !== PLAN_DECISION.CHANGES) {
    throw new TypeError(`unknown plan decision ${JSON.stringify(decision)}`);
  }
  const steps = (Array.isArray(plan.steps) ? plan.steps : []).map(cleanStep);
  if (steps.length < 1 || steps.length > PLAN_LIMITS.steps) {
    throw new RangeError(`a plan has 1 to ${PLAN_LIMITS.steps} steps, not ${steps.length}`);
  }
  const untitled = firstUntitledStep(steps);
  if (untitled > 0) throw new RangeError(`step ${untitled} needs a title`);
  const note = clip(typeof feedback === 'string' ? feedback.trim() : '', PLAN_LIMITS.feedback);
  const out = {
    call_id: plan.callId,
    decision,
    steps,
    run_mode: RUN_MODES.includes(plan.runMode) ? plan.runMode : RUN_MODE.PAUSE,
  };
  if (note) out.feedback = note;
  return out;
}

// ---------------------------------------------------------------------------
// Which step a run belongs to
// ---------------------------------------------------------------------------

const isPlanned = (run) => run && typeof run === 'object' && Number.isInteger(run.step) && run.step > 0 && run.local !== true;

/**
 * The newest plan in a conversation and the runs that belong to it, or null when no
 * reply holds one. A plan covers the runs of its own reply and of every reply after it
 * (a paused plan continues in the next message) that were attributed to a step. A
 * newer plan, a re-proposal after "Change step N", is a new plan: the runs of the old
 * one are not its.
 *
 * @param {Array<{role?: string, plan?: object, executions?: object[]}>} messages
 * @returns {{index: number, plan: object, runs: object[]} | null}
 */
export function currentPlan(messages) {
  const list = Array.isArray(messages) ? messages : [];
  let index = -1;
  for (let i = list.length - 1; i >= 0; i--) {
    const message = list[i];
    if (message && message.role === 'assistant' && message.plan && typeof message.plan === 'object') {
      index = i;
      break;
    }
  }
  if (index < 0) return null;
  const runs = [];
  for (let i = index; i < list.length; i++) {
    const message = list[i];
    if (!message || message.role !== 'assistant' || !Array.isArray(message.executions)) continue;
    runs.push(...message.executions.filter(isPlanned));
  }
  return { index, plan: list[index].plan, runs };
}

/**
 * The step and attempt the next code run belongs to, or null when the plan does not
 * cover it: no plan, one not approved (or stopped, or asked to change), every step done,
 * or the step's attempts used up. Null means the run is not part of the plan, so it is
 * asked about like any other code.
 *
 * @param {object | null} plan
 * @param {object[]} runs this plan's attributed runs, in order
 * @returns {{step: number, attempt: number, title: string} | null}
 */
export function nextRun(plan, runs) {
  if (!plan || plan.status !== PLAN_STATUS.APPROVED || !Array.isArray(plan.steps)) return null;
  const last = runs.length > 0 ? runs[runs.length - 1] : null;
  let step = 1;
  let attempt = 1;
  if (last) {
    if (last.status === 'ok') {
      step = last.step + 1;
    } else if ((Number.isInteger(last.attempt) ? last.attempt : 1) < MAX_STEP_ATTEMPTS) {
      step = last.step;
      attempt = (Number.isInteger(last.attempt) ? last.attempt : 1) + 1;
    } else {
      return null;
    }
  }
  if (step > plan.steps.length) return null;
  return { step, attempt, title: plan.steps[step - 1].title };
}

/** What the next run of the conversation is, as nextRun gives it, from its messages. */
export function nextRunOf(messages) {
  const current = currentPlan(messages);
  return current ? nextRun(current.plan, current.runs) : null;
}

// ---------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------

export const PROGRESS_STATE = Object.freeze({
  RUNNING: 'running',
  RETRYING: 'retrying',
  PAUSED: 'paused',
  FAILED: 'failed',
  DONE: 'done',
  STOPPED: 'stopped',
});

/**
 * Where an approved or stopped plan stands, for the strip and the bar.
 *
 * @param {object} plan
 * @param {object[]} runs the plan's attributed runs, in order
 * @param {{active?: boolean}} [options] `active`: the reply is still going (a run, or
 *   the assistant working out what comes next)
 * @returns {{total: number, step: number, mode: string, state: string, attempt: number,
 *   title: string, segments: string[], doneCount: number}}
 */
export function progressOf(plan, runs, { active = false } = {}) {
  const total = plan.steps.length;
  const latest = new Map();
  for (const run of runs) latest.set(run.step, run);
  const isDone = (n) => latest.has(n) && latest.get(n).status === 'ok';
  let doneCount = 0;
  for (let n = 1; n <= total; n++) if (isDone(n)) doneCount += 1;
  const last = runs.length > 0 ? runs[runs.length - 1] : null;
  const attempts = (run) => (Number.isInteger(run.attempt) ? run.attempt : 1);

  let state;
  let step;
  if (plan.status === PLAN_STATUS.STOPPED) {
    // Stopped on the step that was running, or before the next one when it was between steps.
    state = PROGRESS_STATE.STOPPED;
    step = last ? (last.status === 'ok' ? last.step + 1 : last.step) : 1;
  } else if (doneCount === total) {
    state = PROGRESS_STATE.DONE;
    step = total;
  } else if (last && last.status !== 'ok') {
    step = last.step;
    state = active && attempts(last) < MAX_STEP_ATTEMPTS ? PROGRESS_STATE.RETRYING : PROGRESS_STATE.FAILED;
  } else {
    step = last ? last.step + 1 : 1;
    state = active ? PROGRESS_STATE.RUNNING : PROGRESS_STATE.PAUSED;
  }
  step = Math.min(Math.max(step, 1), total);

  const failing = state === PROGRESS_STATE.FAILED || state === PROGRESS_STATE.RETRYING;
  const segments = [];
  for (let n = 1; n <= total; n++) {
    if (n === step && state === PROGRESS_STATE.STOPPED) segments.push('stopped');
    else if (isDone(n)) segments.push('done');
    else if (n === step && failing) segments.push('failed');
    else if (n === step && state !== PROGRESS_STATE.DONE) segments.push('current');
    else segments.push('pending');
  }
  return {
    total,
    step,
    mode: plan.runMode === RUN_MODE.RUN_ALL ? RUN_MODE.RUN_ALL : RUN_MODE.PAUSE,
    state,
    attempt: last && last.step === step ? attempts(last) : 1,
    title: plan.steps[step - 1].title,
    segments,
    doneCount,
  };
}

/**
 * Whether the bar offers Pause: a run-all plan whose reply is going on, a step running
 * or being tried again. A plan that pauses after each step has nothing to add, and one
 * waiting, failed, done or stopped is not running.
 *
 * @param {{mode: string, state: string} | null} progress as progressOf gives it
 */
export function canPause(progress) {
  return Boolean(progress) && progress.mode === RUN_MODE.RUN_ALL
    && (progress.state === PROGRESS_STATE.RUNNING || progress.state === PROGRESS_STATE.RETRYING);
}

/**
 * Whether the reply, asked to Pause, must hold the request for code that has just
 * arrived: the plan is approved and the request is the first attempt of a step, so
 * every step before it is done. Pressed while a step runs, that is the step after it; a
 * step that failed is tried again first, since it has not finished.
 *
 * @param {Array<{role?: string, plan?: object, executions?: object[]}>} messages
 */
export function pauseDue(messages) {
  const next = nextRunOf(messages);
  return Boolean(next) && next.attempt === 1;
}

/** The progress of a conversation's newest plan, or null: see currentPlan and progressOf. */
export function progressOfConversation(messages, options) {
  const current = currentPlan(messages);
  if (!current) return null;
  const { plan } = current;
  if (plan.status !== PLAN_STATUS.APPROVED && plan.status !== PLAN_STATUS.STOPPED) return null;
  return progressOf(plan, current.runs, options);
}
