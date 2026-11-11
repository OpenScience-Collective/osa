/**
 * The plan rail beside the conversation in the center-stage dialog (E6.1, board C3): the
 * pure logic that turns a conversation into the rows the rail draws, with no DOM and no
 * widget state.
 *
 * It adds no progress model of its own. Which plan is current and which runs belong to it
 * is `currentPlan`, and where each step stands is `progressOf`, both from osa-plan.js, the
 * same pair the progress strip and the bar read, so the three always agree.
 *
 * A draft plan (the card is waiting for the reader) shows as a "Proposed plan" with every
 * step pending. An approved, stopped or finished plan shows its steps as they stand. A plan
 * the reader asked to change, and one nobody answered (a chat restored with its card still
 * open), have no rail: nothing is being followed.
 *
 * Text from the model (the step titles) is only bounded by osa-plan.js; escaping is the
 * renderer's job.
 */
import { currentPlan, oneLine, PLAN_STATUS, PROGRESS_STATE, progressOf, RUN_MODE } from './osa-plan.js';

/** The states a rail row can be in. */
export const RAIL_STATE = Object.freeze({
  DONE: 'done',
  CURRENT: 'current',
  FAILED: 'failed',
  STOPPED: 'stopped',
  PENDING: 'pending',
});

/**
 * The run mode in words, as the plan card says it. The widget holds the same two strings
 * (PLAN_MODE_LABELS); test-widget-plan-rail.js compares them on screen.
 */
export const RAIL_MODE_LABELS = Object.freeze({
  [RUN_MODE.PAUSE]: 'Pauses after each step',
  [RUN_MODE.RUN_ALL]: 'Runs all steps',
});

// What a screen reader hears after "Step 3,": the state, since the icon alone is not a cue
// that reaches it and color is not one at all.
const STATE_WORDS = Object.freeze({
  [RAIL_STATE.DONE]: 'done',
  [RAIL_STATE.CURRENT]: 'current step',
  [RAIL_STATE.FAILED]: 'failed',
  [RAIL_STATE.STOPPED]: 'stopped',
  [RAIL_STATE.PENDING]: 'not started',
});

const KNOWN_STATES = Object.freeze(Object.values(RAIL_STATE));

function rowLabel(n, state) {
  return `Step ${n}, ${STATE_WORDS[state]}`;
}

function headingOf(plan, progress) {
  if (plan.status === PLAN_STATUS.DRAFT) return 'Proposed plan';
  const count = `${progress.doneCount} of ${progress.total} done`;
  return progress.state === PROGRESS_STATE.STOPPED ? `Plan stopped · ${count}` : `Plan · ${count}`;
}

/**
 * What the rail shows for a conversation, or null when there is no rail.
 *
 * @param {Array<{role?: string, plan?: object, executions?: object[]}>} messages
 * @param {{active?: boolean}} [options] `active`: the reply is still going, as progressOf
 *   takes it (a step running tells "in progress" from "paused" only through this)
 * @returns {null | {
 *   heading: string, doneCount: number, total: number, mode: string, modeLabel: string,
 *   rows: Array<{n: number, title: string, state: string, hasCard: boolean, label: string}>
 * }}
 */
export function planRailOf(messages, { active = false } = {}) {
  const current = currentPlan(messages);
  if (!current) return null;
  const { plan, runs } = current;
  if (!Array.isArray(plan.steps) || plan.steps.length === 0) return null;
  const mode = plan.runMode === RUN_MODE.RUN_ALL ? RUN_MODE.RUN_ALL : RUN_MODE.PAUSE;
  const modeLabel = RAIL_MODE_LABELS[mode];

  if (plan.status === PLAN_STATUS.DRAFT) {
    const rows = plan.steps.map((step, i) => ({
      n: i + 1,
      title: oneLine(step && step.title),
      state: RAIL_STATE.PENDING,
      hasCard: false,
      label: rowLabel(i + 1, RAIL_STATE.PENDING),
    }));
    const progress = { doneCount: 0, total: rows.length };
    return { heading: headingOf(plan, progress), doneCount: 0, total: rows.length, mode, modeLabel, rows };
  }
  if (plan.status !== PLAN_STATUS.APPROVED && plan.status !== PLAN_STATUS.STOPPED) return null;

  const progress = progressOf(plan, runs, { active });
  const ran = new Set(runs.map((run) => run.step));
  const rows = progress.segments.map((segment, i) => {
    const state = KNOWN_STATES.includes(segment) ? segment : RAIL_STATE.PENDING;
    const n = i + 1;
    return {
      n,
      title: oneLine(plan.steps[i] && plan.steps[i].title),
      state,
      hasCard: ran.has(n),
      label: rowLabel(n, state),
    };
  });
  return {
    heading: headingOf(plan, progress),
    doneCount: progress.doneCount,
    total: progress.total,
    mode,
    modeLabel,
    rows,
  };
}
