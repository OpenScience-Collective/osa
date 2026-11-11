/**
 * The plan rail's logic (E6.1, board C3): the rows beside the center-stage conversation,
 * from the same plan state the progress strip reads.
 *
 * It holds planRailOf to: no rail without a plan, for a plan the reader asked to change,
 * and for one nobody answered; a draft is a "Proposed plan" with every step pending; an
 * approved plan's rows follow progressOf step by step (not started, mid-plan, paused,
 * running, a failed step and its retry); a stopped plan marks the step it stopped on; a
 * finished plan is all done; a re-proposed plan replaces the old one; the run mode is
 * said in words; the heading counts done steps. Every row names its state in words, so
 * the icon and the color are never the only cue.
 *
 * Pure: no DOM, no widget. The rail on screen is test-widget-plan-rail.js.
 *
 * Run with: bun frontend/test-plan-rail.js
 */

import { PLAN_STATUS, normalizeProposal, progressOf } from './osa-plan.js';
import { RAIL_MODE_LABELS, RAIL_STATE, planRailOf } from './osa-plan-rail.js';

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

const ARGS = {
  findings: 'Three conditions.',
  est_peak_mb: 300,
  steps: ['Count face onsets', 'Epoch the EEG', 'Average per condition', 'Plot the ERPs', 'Summarize'].map((title) => ({ title, detail: '' })),
};

const planOf = (status, extra = {}) => ({ ...normalizeProposal(ARGS, 'plan-1'), status, ...extra });
const proposal = (status, extra) => ({ role: 'assistant', content: 'Here is a plan.', plan: planOf(status, extra) });
const ok = (step) => ({ step, attempt: 1, status: 'ok', callId: `c${step}` });
const failedRun = (step, attempt = 1) => ({ step, attempt, status: 'error', callId: `f${step}-${attempt}` });
const reply = (...executions) => ({ role: 'assistant', content: 'Ran.', executions });
const states = (rail) => rail.rows.map((row) => row.state);
const D = RAIL_STATE.DONE;
const C = RAIL_STATE.CURRENT;
const P = RAIL_STATE.PENDING;

console.log('='.repeat(60));
console.log('Plan rail logic (E6.1, board C3)');
console.log('='.repeat(60));

console.log('\nno plan, no rail');
{
  assertEqual(planRailOf([]), null, 'an empty conversation');
  assertEqual(planRailOf(undefined), null, 'no conversation at all');
  assertEqual(planRailOf([{ role: 'assistant', content: 'Hello' }, { role: 'user', content: 'Hi' }, reply(ok(1))]), null, 'runs with no plan are not a plan');
  assertEqual(planRailOf([proposal(PLAN_STATUS.APPROVED, { steps: [] })]), null, 'a plan with no steps');
}

console.log('\na draft is a proposed plan with every step pending');
{
  const rail = planRailOf([{ role: 'user', content: 'Compare ERPs' }, proposal(PLAN_STATUS.DRAFT)]);
  assertEqual(rail.heading, 'Proposed plan', 'the heading says it is proposed');
  assertEqual(states(rail), [P, P, P, P, P], 'all five steps are pending');
  assertEqual([rail.doneCount, rail.total], [0, 5], 'none done of five');
  assert(rail.rows.every((row) => row.hasCard === false), 'no step has a run card');
  assertEqual(rail.rows.map((row) => row.title), ARGS.steps.map((s) => s.title), 'the titles are the proposed steps');
  assertEqual(rail.rows.map((row) => row.n), [1, 2, 3, 4, 5], 'numbered from 1');
}

console.log('\nthe reader asked to change it, or never answered: no rail');
{
  assertEqual(planRailOf([proposal(PLAN_STATUS.CHANGES)]), null, 'changes requested');
  assertEqual(planRailOf([proposal(PLAN_STATUS.UNANSWERED)]), null, 'unanswered (a restored chat whose card was open)');
}

console.log('\napproved, before step 1 runs');
{
  const rail = planRailOf([proposal(PLAN_STATUS.APPROVED)]);
  assertEqual(states(rail), [C, P, P, P, P], 'step 1 is current, the rest pending');
  assertEqual(rail.heading, 'Plan · 0 of 5 done', 'the heading counts none done');
  assert(rail.rows.every((row) => row.hasCard === false), 'and no step has a card yet');
}

console.log('\napproved, mid-plan: paused between steps, then running');
{
  const messages = [proposal(PLAN_STATUS.APPROVED), reply(ok(1), ok(2))];
  const paused = planRailOf(messages);
  assertEqual(states(paused), [D, D, C, P, P], 'paused: two done, step 3 is next and current');
  assertEqual(paused.heading, 'Plan · 2 of 5 done', 'the heading counts two done');
  assertEqual(paused.rows.map((row) => row.hasCard), [true, true, false, false, false], 'the steps that ran have a card');
  const running = planRailOf(messages, { active: true });
  assertEqual(states(running), [D, D, C, P, P], 'running: step 3 is current as well');
  assertEqual(running.doneCount, 2, 'still two done');
  assertEqual(states(planRailOf([proposal(PLAN_STATUS.APPROVED, { runMode: 'run_all' }), reply(ok(1), ok(2), ok(3))])), [D, D, D, C, P], 'run_all, three done');
  const progress = progressOf(messages[0].plan, messages[1].executions);
  assertEqual(states(paused), progress.segments.map((s) => (s === 'current' ? C : s === 'done' ? D : P)), 'the rows are what progressOf says: no second model');
}

console.log('\na failed step, and its retry');
{
  const messages = [proposal(PLAN_STATUS.APPROVED), reply(ok(1), failedRun(2))];
  const retrying = planRailOf(messages, { active: true });
  assertEqual(states(retrying), [D, RAIL_STATE.FAILED, P, P, P], 'a failed step with an attempt left, the reply still going: failed on step 2');
  assertEqual(retrying.rows[1].label, 'Step 2, failed', 'and its label says so in words');
  assert(retrying.rows[1].hasCard, 'the failed step has its card');
  const exhausted = planRailOf([proposal(PLAN_STATUS.APPROVED), reply(ok(1), failedRun(2), failedRun(2, 2))]);
  assertEqual(states(exhausted), [D, RAIL_STATE.FAILED, P, P, P], 'out of attempts: failed');
  const fixed = planRailOf([proposal(PLAN_STATUS.APPROVED), reply(ok(1), failedRun(2), { ...ok(2), attempt: 2 })]);
  assertEqual(states(fixed), [D, D, C, P, P], 'the retry worked: step 2 is done, step 3 is next');
  assertEqual(fixed.doneCount, 2, 'and counted once');
}

console.log('\nstopped');
{
  const between = planRailOf([proposal(PLAN_STATUS.STOPPED), reply(ok(1), ok(2))]);
  assertEqual(states(between), [D, D, RAIL_STATE.STOPPED, P, P], 'stopped between steps: marked on the step that would have run next');
  assertEqual(between.heading, 'Plan stopped · 2 of 5 done', 'the heading says stopped and counts what finished');
  assertEqual(between.rows[2].label, 'Step 3, stopped', 'the label says it in words');
  const during = planRailOf([proposal(PLAN_STATUS.STOPPED), reply(ok(1), { step: 2, attempt: 1, status: 'cancelled', callId: 'x' })]);
  assertEqual(states(during), [D, RAIL_STATE.STOPPED, P, P, P], 'stopped while a step ran: marked on that step');
  assert(during.rows[1].hasCard, 'which has its card');
}

console.log('\ndone');
{
  const rail = planRailOf([proposal(PLAN_STATUS.APPROVED), reply(ok(1), ok(2), ok(3), ok(4), ok(5))]);
  assertEqual(states(rail), [D, D, D, D, D], 'every step is done');
  assertEqual([rail.heading, rail.doneCount, rail.total], ['Plan · 5 of 5 done', 5, 5], 'the heading says all five');
  assert(rail.rows.every((row) => row.hasCard), 'each has its card');
}

console.log('\nthe heading counts done steps');
{
  const rail = planRailOf([proposal(PLAN_STATUS.APPROVED), reply(ok(1), ok(2), ok(3))]);
  assert(/3 of 5 done/i.test(rail.heading), 'three of five done reads "3 of 5 done" (the rail shows it in capitals)');
  assertEqual([rail.doneCount, rail.total], [3, 5], 'with the counts beside it');
}

console.log('\na re-proposed plan replaces the old one');
{
  const second = normalizeProposal({ ...ARGS, steps: ARGS.steps.slice(0, 2).map((s) => ({ ...s, title: `New ${s.title}` })) }, 'plan-2');
  const messages = [
    proposal(PLAN_STATUS.APPROVED),
    reply(ok(1), ok(2)),
    { role: 'assistant', content: 'A new plan.', plan: { ...second, status: PLAN_STATUS.DRAFT } },
  ];
  const draft = planRailOf(messages);
  assertEqual([draft.heading, draft.total], ['Proposed plan', 2], 'the new draft is what shows: two steps, proposed');
  assertEqual(draft.rows.map((row) => row.title), ['New Count face onsets', 'New Epoch the EEG'], 'with its own titles');
  messages[2].plan.status = PLAN_STATUS.APPROVED;
  messages.push(reply(ok(1)));
  const approved = planRailOf(messages);
  assertEqual(states(approved), [D, C], 'once approved, only the new plan\'s own runs count');
  assertEqual(approved.rows.map((row) => row.hasCard), [true, false], 'and its cards');
}

console.log('\nthe run mode, in words');
{
  assertEqual(planRailOf([proposal(PLAN_STATUS.APPROVED)]).modeLabel, 'Pauses after each step', 'pause mode');
  assertEqual(planRailOf([proposal(PLAN_STATUS.APPROVED, { runMode: 'run_all' })]).modeLabel, 'Runs all steps', 'run all mode');
  assertEqual(planRailOf([proposal(PLAN_STATUS.APPROVED, { runMode: 'something else' })]).modeLabel, RAIL_MODE_LABELS.pause, 'an unknown mode reads as pause, as the plan logic takes it');
  assertEqual(planRailOf([proposal(PLAN_STATUS.DRAFT, { runMode: 'run_all' })]).modeLabel, 'Runs all steps', 'a draft says the mode picked on the card so far');
  assertEqual(planRailOf([proposal(PLAN_STATUS.STOPPED)]).modeLabel, 'Pauses after each step', 'and a stopped plan the mode it was approved with');
}

console.log('\nevery row names its state in words');
{
  const rail = planRailOf([proposal(PLAN_STATUS.APPROVED), reply(ok(1), failedRun(2))], { active: false });
  assertEqual(rail.rows.map((row) => row.label), ['Step 1, done', 'Step 2, failed', 'Step 3, not started', 'Step 4, not started', 'Step 5, not started'], 'done, failed and not started');
  assertEqual(planRailOf([proposal(PLAN_STATUS.APPROVED)]).rows[0].label, 'Step 1, current step', 'the current step');
  assert(new Set(Object.values(RAIL_STATE)).size === 5, 'five distinct states');
}

console.log('\ntitles are one line, and the input is not changed');
{
  const messages = [proposal(PLAN_STATUS.APPROVED, { steps: [{ title: 'Two\n  lines', detail: 'd' }, { title: 'Second', detail: '' }] }), reply(ok(1))];
  const before = JSON.stringify(messages);
  const rail = planRailOf(messages);
  assertEqual(rail.rows[0].title, 'Two lines', 'whitespace is folded');
  assertEqual(JSON.stringify(messages), before, 'the conversation passed in is untouched');
  assertEqual(Object.keys(rail).sort(), ['doneCount', 'heading', 'mode', 'modeLabel', 'rows', 'total'], 'the rail has the documented keys');
  assertEqual(Object.keys(rail.rows[0]).sort(), ['hasCard', 'label', 'n', 'state', 'title'], 'and each row');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
