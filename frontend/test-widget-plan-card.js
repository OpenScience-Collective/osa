/**
 * The plan card and the step progress (E2.3 #662, E2.4 #671), run against the real widget.
 *
 * The server answers a plan-first analysis with a `propose_plan` tool_request; the widget
 * shows an editable card, answers /chat/resume with the reader's decision, runs each step
 * of an approved plan without asking again, and says where the plan is: a step header on
 * each run, a progress strip, a bar with Run step N, Change step N and Stop plan.
 *
 * What stands in: `fetch`, which answers with the SSE the test writes event by event, in
 * the shape the server sends (frontend/test-widget-handover-events.js does the same); and
 * the Python worker behind the runtime, the test worker the other widget suites use. The
 * widget, the runtime bundle as built (the real controller, the real plan logic), the
 * stream handler and the gate are the real ones, in a happy-dom window. The first part
 * tests the pure plan logic directly.
 *
 * Run with: bun frontend/test-widget-plan-card.js
 */

import * as plan from './osa-plan.js';
import { cssNumber } from './test-support/css-px.js';
import {
  PLAN_ARGS, SOURCE, TITLE, approve, barButtons, click, codeRequest, doneEvent, lastReply, messagesOf,
  planRequest, proposeFirst, send, sessionEvent, settled, start, stepTitles, text, typeInto, waitUntil,
} from './test-support/plan-widget-harness.js';

let passed = 0;
let failed = 0;

const SUITE_TIMEOUT_MS = 120_000;
const watchdog = setTimeout(() => {
  console.error(`\n  x FAIL: the suite did not finish within ${SUITE_TIMEOUT_MS / 1000}s.`);
  process.exit(1);
}, SUITE_TIMEOUT_MS);
watchdog.unref?.();

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

// The page and the server's side of it: frontend/test-support/plan-widget-harness.js.

console.log('='.repeat(60));
console.log('Widget: the plan card and the step progress (E2.3, E2.4)');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// The pure logic
// ---------------------------------------------------------------------------

console.log('\nnormalizeProposal: the card is built from the tool_request, or not at all');
{
  const proposal = plan.normalizeProposal(PLAN_ARGS, 'c1');
  assertEqual(
    proposal,
    { callId: 'c1', findings: PLAN_ARGS.findings, estPeakMb: 300, steps: PLAN_ARGS.steps, runMode: 'pause', status: 'draft' },
    'a draft in pause mode, with the steps as proposed'
  );
  assertEqual(plan.normalizeProposal({ ...PLAN_ARGS, steps: [] }, 'c1'), null, 'no steps: nothing to show');
  assertEqual(plan.normalizeProposal({ ...PLAN_ARGS, steps: [{ title: ' \n ', detail: 'x' }] }, 'c1'), null, 'a step with no title: nothing to show');
  assertEqual(plan.normalizeProposal(PLAN_ARGS, ''), null, 'no call id: the decision could not name its call');
  assertEqual(plan.normalizeProposal(null, 'c1'), null, 'no arguments');
  const messy = plan.normalizeProposal({
    findings: 'f'.repeat(5000),
    est_peak_mb: 99999,
    steps: Array.from({ length: 12 }, (_, i) => ({ title: `a\n\t  b ${'t'.repeat(300)}${i}`, detail: 'd'.repeat(900) })),
  }, 'c1');
  assertEqual(
    [messy.findings.length, messy.estPeakMb, messy.steps.length, messy.steps[0].title.length, messy.steps[0].detail.length],
    [plan.PLAN_LIMITS.findings, plan.PLAN_LIMITS.peakMb, plan.PLAN_LIMITS.steps, plan.PLAN_LIMITS.title, plan.PLAN_LIMITS.detail],
    'every field is held to the server\'s bounds'
  );
  assert(!/\s{2}|[\n\t]/.test(messy.steps[0].title), 'a title is one line');
}

console.log('\nthe step reducers: edit, add, remove, with the server\'s limits, and nothing is changed in place');
{
  const steps = plan.normalizeProposal(PLAN_ARGS, 'c1').steps;
  const frozen = JSON.stringify(steps);
  const edited = plan.editStep(steps, 1, { title: '  Epoch\n the EEG  ', detail: 'new detail' });
  assertEqual(edited[1], { title: 'Epoch the EEG', detail: 'new detail' }, 'edit replaces one step, its title one line');
  assertEqual([edited[0], edited[2]], [steps[0], steps[2]], 'and no other');
  assertEqual(plan.editStep(steps, 9, { title: 'x', detail: '' }), steps, 'an index out of range changes nothing');
  let grown = steps;
  while (plan.canAddStep(grown)) grown = plan.addStep(grown);
  assertEqual(grown.length, plan.PLAN_LIMITS.steps, 'add stops at 8 steps');
  assertEqual(plan.addStep(grown), grown, 'and a ninth is refused');
  assertEqual(plan.addStep(steps)[3], { title: '', detail: '' }, 'an added step is empty, at the end');
  let shrunk = grown;
  while (plan.canRemoveStep(shrunk)) shrunk = plan.removeStep(shrunk, 0);
  assertEqual(shrunk.length, 1, 'remove stops at 1 step');
  assertEqual(plan.removeStep(shrunk, 0), shrunk, 'and the last step stays');
  assertEqual(plan.removeStep(steps, 1).map((s) => s.title), ['Count face onsets', 'Average per condition'], 'remove takes out the named step');
  assertEqual(JSON.stringify(steps), frozen, 'the list passed in is never changed');
  assertEqual(plan.firstUntitledStep(plan.addStep(steps)), 4, 'the first step with no title is found');
  assertEqual(plan.firstUntitledStep(steps), 0, 'none when every step has one');
}

console.log('\nbuildDecision: exactly the server\'s PlanDecision');
{
  const p = { ...plan.normalizeProposal(PLAN_ARGS, 'c9'), runMode: 'run_all' };
  assertEqual(
    plan.buildDecision(p, 'approved'),
    { call_id: 'c9', decision: 'approved', steps: PLAN_ARGS.steps, run_mode: 'run_all' },
    'an approval has no feedback key'
  );
  assertEqual(
    plan.buildDecision(p, 'changes_requested', '  make it shorter  ').feedback,
    'make it shorter',
    'feedback is trimmed'
  );
  assertEqual(plan.buildDecision(p, 'changes_requested', 'x'.repeat(2000)).feedback.length, plan.PLAN_LIMITS.feedback, 'and held to 1000 characters');
  assert(!('feedback' in plan.buildDecision(p, 'changes_requested', '   ')), 'blank feedback is left out');
  const refused = (fn) => { try { fn(); return null; } catch (err) { return err.message; } };
  assert(refused(() => plan.buildDecision({ ...p, steps: plan.addStep(p.steps) }, 'approved')) === 'step 4 needs a title', 'a step with no title is refused, by number');
  assert(refused(() => plan.buildDecision({ ...p, steps: [] }, 'approved')) !== null, 'no steps is refused');
  assert(refused(() => plan.buildDecision(p, 'maybe')) !== null, 'an unknown decision is refused');
  assert(refused(() => plan.buildDecision({ ...p, callId: '' }, 'approved')) !== null, 'a decision with no call id is refused');
  assertEqual(plan.buildDecision({ ...p, runMode: 'sideways' }, 'approved').run_mode, 'pause', 'an unknown run mode is pause, the default');
}

console.log('\nstep attribution: ok advances, a failure is the next attempt, two attempts at most');
{
  const approved = { ...plan.normalizeProposal(PLAN_ARGS, 'c1'), status: 'approved' };
  const run = (step, attempt, status) => ({ step, attempt, status });
  assertEqual(plan.nextRun(approved, []), { step: 1, attempt: 1, title: 'Count face onsets' }, 'the first run is step 1');
  assertEqual(plan.nextRun(approved, [run(1, 1, 'ok')]), { step: 2, attempt: 1, title: 'Epoch the EEG' }, 'after an ok run, the next step');
  assertEqual(plan.nextRun(approved, [run(1, 1, 'error')]), { step: 1, attempt: 2, title: 'Count face onsets' }, 'after a failure, the same step again');
  assertEqual(plan.nextRun(approved, [run(1, 1, 'error'), run(1, 2, 'ok')]), { step: 2, attempt: 1, title: 'Epoch the EEG' }, 'a retry that works advances');
  assertEqual(plan.nextRun(approved, [run(1, 1, 'error'), run(1, 2, 'timeout')]), null, 'two failed attempts: the next run is not covered');
  assertEqual(plan.nextRun(approved, [run(1, 1, 'cancelled')]), { step: 1, attempt: 2, title: 'Count face onsets' }, 'a stopped run used its attempt');
  assertEqual(plan.nextRun(approved, [run(1, 1, 'ok'), run(2, 1, 'ok'), run(3, 1, 'ok')]), null, 'every step done: not covered');
  for (const status of ['draft', 'changes_requested', 'stopped', 'unanswered']) {
    assertEqual(plan.nextRun({ ...approved, status }, []), null, `a plan that is ${status} covers nothing`);
  }
  assertEqual(plan.nextRun(null, []), null, 'no plan covers nothing');
}

console.log('\ncurrentPlan and progressOf: read back from the conversation');
{
  const approved = { ...plan.normalizeProposal(PLAN_ARGS, 'c1'), status: 'approved' };
  const ok = (step, attempt = 1) => ({ step, attempt, status: 'ok', code: 'x' });
  const messages = [
    { role: 'assistant', content: 'hi' },
    { role: 'user', content: 'q' },
    { role: 'assistant', content: 'plan', plan: approved, executions: [ok(1)] },
    { role: 'user', content: 'Continue with step 2.' },
    { role: 'assistant', content: 'two', executions: [ok(2), { status: 'ok', local: true, step: 7 }, { status: 'ok' }] },
  ];
  const current = plan.currentPlan(messages);
  assertEqual([current.index, current.runs.map((r) => r.step)], [2, [1, 2]], 'the plan covers its own reply and the ones after it; a reader\'s own run and an unattributed run are not steps');
  const progress = plan.progressOfConversation(messages, { active: false });
  assertEqual(
    [progress.state, progress.step, progress.total, progress.mode, progress.segments],
    ['paused', 3, 3, 'pause', ['done', 'done', 'current']],
    'two steps done, step 3 next, paused'
  );
  assertEqual(plan.progressOfConversation(messages, { active: true }).state, 'running', 'running while the reply is going');
  const stuck = plan.progressOf(approved, [ok(1), { step: 2, attempt: 2, status: 'error' }, ], { active: true });
  assertEqual([stuck.state, stuck.step, stuck.segments], ['failed', 2, ['done', 'failed', 'pending']], 'a step out of attempts is failed, even while the reply goes on');
  const retrying = plan.progressOf(approved, [{ step: 1, attempt: 1, status: 'error' }], { active: true });
  assertEqual([retrying.state, retrying.attempt], ['retrying', 1], 'a failed first attempt is being retried while the reply goes on');
  assertEqual(plan.progressOf(approved, [ok(1), ok(2), ok(3)]).state, 'done', 'every step ok is done');
  assertEqual(plan.progressOf({ ...approved, status: 'stopped' }, [ok(1)]).segments, ['done', 'stopped', 'pending'], 'a plan stopped between steps marks the step that did not start');
  assertEqual(plan.progressOf({ ...approved, status: 'stopped' }, [{ step: 1, attempt: 1, status: 'cancelled' }]).segments, ['stopped', 'pending', 'pending'], 'and one stopped mid-run marks the step that was running');
  assertEqual(plan.progressOfConversation([...messages, { role: 'assistant', content: 'new', plan: plan.normalizeProposal(PLAN_ARGS, 'c2') }]), null, 'a newer plan that is still a draft has no strip');
  const replanned = plan.currentPlan([...messages, { role: 'assistant', content: 'new', plan: { ...approved, callId: 'c2' }, executions: [] }]);
  assertEqual(replanned.runs, [], 'a newer plan does not inherit the runs of the old one');
}

// ---------------------------------------------------------------------------
// client_tools
// ---------------------------------------------------------------------------

console.log('\nthe bodies of /chat and /chat/resume declare propose_plan once the runtime bundle is loaded');
{
  const page = await start();
  const first = page.queue();
  send(page, 'plot the ERP');
  first.send(sessionEvent());
  first.send(codeRequest('c1', 'print(1)'));
  first.close();
  // The code needs the reader's consent: this is not a plan.
  await waitUntil(() => page.api.getToolActivity() && page.api.getToolActivity().phase === 'asking', 'the gate asks');
  assertEqual(page.requests[0].body.client_tools, ['execute_code', 'get_full_output', 'propose_plan'], '/chat declares propose_plan');
  const second = page.queue();
  click(page, page.q('.osa-tool-run'));
  await waitUntil(() => page.requests.length === 2, 'the result is posted');
  assertEqual(page.requests[1].kind, 'resume', 'the second request is /chat/resume');
  assertEqual(page.requests[1].body.client_tools, ['execute_code', 'get_full_output', 'propose_plan'], '/chat/resume declares it too');
  second.send(doneEvent('Ran.'));
  second.close();
  await waitUntil(() => settled(page), 'the reply ends');
}

console.log('\nand not otherwise: a bundle with no plan logic, or no bundle at all');
{
  const old = await start({ bundle: 'old' });
  const first = old.queue();
  send(old, 'plot the ERP');
  first.send(sessionEvent());
  first.send(codeRequest('c1', 'print(1)'));
  first.close();
  await waitUntil(() => old.api.getToolActivity() && old.api.getToolActivity().phase === 'asking', 'the gate asks');
  const second = old.queue();
  click(old, old.q('.osa-tool-run'));
  await waitUntil(() => old.requests.length === 2, 'the result is posted');
  assertEqual(old.requests[0].body.client_tools, ['execute_code', 'get_full_output'], 'a bundle with no plan logic: /chat does not declare it');
  assertEqual(old.requests[1].body.client_tools, ['execute_code', 'get_full_output'], 'nor does /chat/resume');
  second.send(doneEvent('Ran.'));
  second.close();
  await waitUntil(() => settled(old), 'the reply ends');

  const none = await start({ bundle: 'none' });
  const only = none.queue();
  send(none, 'what is this dataset?');
  only.send(sessionEvent());
  only.send(doneEvent('A dataset.'));
  only.close();
  await waitUntil(() => settled(none), 'the reply ends');
  assert(!('client_tools' in none.requests[0].body), 'no bundle at all: no client_tools in /chat');
}

// ---------------------------------------------------------------------------
// The card
// ---------------------------------------------------------------------------

console.log('\nthe card shows the findings, the plan, the memory line, the steps and the controls');
{
  const page = await start();
  await proposeFirst(page);
  const card = page.q('.osa-plan-card');
  assert(text(card.querySelector('.osa-plan-findings')).includes(PLAN_ARGS.findings), 'the findings');
  assertEqual(text(card.querySelector('.osa-plan-title')), 'Proposed plan · 3 steps', 'the title counts the steps');
  assertEqual(text(card.querySelector('.osa-plan-badge')), 'Draft', 'a draft');
  assertEqual(text(card.querySelector('.osa-plan-top .osa-plan-meta')), 'Largest step needs about 300 MB', 'the memory line says what the largest step needs, and names no budget');
  assertEqual(stepTitles(page), PLAN_ARGS.steps.map((s) => s.title), 'the steps, in order');
  assertEqual(page.qa('.osa-plan-step-detail').map(text), PLAN_ARGS.steps.map((s) => s.detail), 'with their details');
  assertEqual(page.qa('.osa-plan-edit-btn').map((b) => b.getAttribute('aria-label')), ['Edit step 1', 'Edit step 2', 'Edit step 3'], 'each step has an Edit');
  assertEqual(page.qa('.osa-plan-remove-btn').map((b) => b.getAttribute('aria-label')), ['Remove step 1', 'Remove step 2', 'Remove step 3'], 'and a Remove');
  assertEqual(text(card.querySelector('.osa-plan-add')), '+ Add a step', 'and a way to add one');
  const radios = [...card.querySelectorAll('input[type="radio"]')];
  assertEqual(radios.map((r) => [r.value, r.checked]), [['pause', true], ['run_all', false]], 'two run modes, Pause the default');
  assertEqual(radios.map((r) => text(r.closest('label'))), ['Pause so I can look', 'Run all steps'], 'each with its label');
  assertEqual(text(card.querySelector('legend')), 'When a step finishes', 'in a group with a name');
  assertEqual(text(card.querySelector('.osa-plan-approve')), 'Approve and run step 1', 'Approve says what it runs');
  assertEqual(text(card.querySelector('.osa-plan-ask')), 'Ask for changes', 'and Ask for changes');
  assert(!/budget/i.test(text(card)), 'the card never mentions a memory budget');
  assert(page.q('.osa-plan-waiting'), 'the conversation says it is waiting for the decision');
  assert(!page.q('.osa-loading'), 'and shows no loading dots');
  assert(!page.q('.osa-plan-strip') && !page.q('.osa-plan-bar'), 'there is no strip or bar before the plan is approved');
  assert(page.q('.osa-chat-input input').disabled, 'the input waits too');

  // Keyboard reach and names: every control is a real button or input with a name.
  const controls = [...card.querySelectorAll('button, input, textarea')];
  assert(controls.length > 0 && controls.every((el) => el.tagName !== 'BUTTON' || el.getAttribute('type') === 'button'), 'every button is a plain button');
  assert(controls.every((el) => el.getAttribute('tabindex') !== '-1'), 'none is taken out of the tab order');
  assert(controls.filter((el) => el.tagName === 'BUTTON').every((el) => text(el) !== '' || el.getAttribute('aria-label')), 'every button has a name');
  assert(card.querySelector('textarea') === null, 'no text box is open until a step is edited');
}

console.log('\nthe 44 px targets and the dark scheme are in the stylesheet');
{
  const page = await start();
  await proposeFirst(page);
  const style = (selector) => page.window.getComputedStyle(page.q(selector));
  assert(cssNumber(style('.osa-plan-approve').minHeight) >= 44, 'Approve is at least 44 px tall');
  assert(cssNumber(style('.osa-plan-ask').minHeight) >= 44, 'Ask for changes is at least 44 px tall');
  assert(/\.osa-chat-widget\.osa-dark \.osa-plan-card[^{]*\{[^}]*--osa-plan-danger/.test(SOURCE), 'the dark panel redefines the card\'s own colors');
  assert(/\.osa-plan-strip \{[^}]*position: sticky/.test(SOURCE) && /\.osa-plan-bar \{[^}]*flex-wrap: wrap/.test(SOURCE), 'the strip and the bar wrap at a narrow width');
}

console.log('\nthe model\'s text is escaped on the card');
{
  const page = await start();
  const hostile = [
    '<img src=x onerror=alert(1)>',
    '</li></ol><script>alert(1)</script>',
    '"><svg onload=alert(1)>',
  ];
  await proposeFirst(page, {
    findings: '</div><iframe srcdoc="x"></iframe><b>bold</b>',
    est_peak_mb: 10,
    steps: hostile.map((h) => ({ title: h, detail: h })),
  });
  const card = page.q('.osa-plan-card');
  const allowed = ['section', 'div', 'strong', 'h4', 'span', 'ol', 'li', 'button', 'svg', 'path', 'line', 'fieldset', 'legend', 'label', 'input'];
  const foreign = [...card.querySelectorAll('*')].map((el) => el.tagName.toLowerCase()).filter((tag) => !allowed.includes(tag));
  assertEqual(foreign, [], 'no element the widget did not write');
  assertEqual(stepTitles(page), hostile, 'the titles read back as written');
  assert(text(card.querySelector('.osa-plan-findings')).includes('<iframe srcdoc="x"></iframe><b>bold</b>'), 'and the findings');
  // The editor, too: the text goes into a field, never into the markup around it.
  click(page, page.q('.osa-plan-edit-btn'));
  const field = page.q('.osa-plan-input');
  assertEqual(field.value, hostile[0], 'the title field holds the text, not its markup');
  assertEqual([...page.q('.osa-plan-card').querySelectorAll('img, script, iframe')].length, 0, 'nothing in the card ran or loaded');
}

console.log('\nediting: Edit, Save and Cancel, with a label on every field');
{
  const page = await start();
  await proposeFirst(page);
  click(page, page.qa('.osa-plan-edit-btn')[1]);
  const title = page.q('.osa-plan-input');
  const detail = page.q('.osa-plan-textarea');
  assertEqual([title.value, detail.value], [PLAN_ARGS.steps[1].title, PLAN_ARGS.steps[1].detail], 'the editor opens with the step');
  const labelFor = (el) => page.q(`label[for="${el.id}"]`);
  assertEqual([text(labelFor(title)), text(labelFor(detail))], ['Step 2 title', 'Step 2 detail'], 'both fields have a label');
  assertEqual([title.getAttribute('maxlength'), detail.getAttribute('maxlength')], ['120', '600'], 'held to the server\'s limits');
  assert(page.window.document.activeElement === title, 'the title has focus');
  assert(page.q('.osa-plan-approve').disabled && page.q('.osa-plan-ask').disabled && page.q('.osa-plan-add').disabled, 'while editing, the decision and the other edits wait');
  assert(text(page.q('.osa-plan-card')).includes('Save or cancel the step you are editing first.'), 'and the card says why');

  typeInto(page, title, 'Epoch  the \t EEG once');
  typeInto(page, detail, 'Baseline -200 to 0 ms.');
  // Typing survives a redraw caused by something else.
  page.api.renderMessages(page.container, { follow: false });
  assertEqual(page.q('.osa-plan-input').value, 'Epoch  the \t EEG once', 'a redraw does not lose what was typed');
  click(page, page.q('.osa-plan-save'));
  assertEqual(stepTitles(page)[1], 'Epoch the EEG once', 'Save changes the step, its title one line');
  assertEqual(page.qa('.osa-plan-step-detail').map(text)[1], 'Baseline -200 to 0 ms.', 'and its detail');
  assert(page.window.document.activeElement === page.qa('.osa-plan-edit-btn')[1], 'focus returns to the step\'s Edit');
  assert(!page.q('.osa-plan-approve').disabled, 'the decision is open again');

  click(page, page.qa('.osa-plan-edit-btn')[0]);
  typeInto(page, page.q('.osa-plan-input'), 'Something else');
  click(page, page.q('.osa-plan-cancel'));
  assertEqual(stepTitles(page)[0], 'Count face onsets', 'Cancel keeps the step as it was');

  click(page, page.qa('.osa-plan-edit-btn')[0]);
  typeInto(page, page.q('.osa-plan-input'), '   ');
  click(page, page.q('.osa-plan-save'));
  assert(text(page.q('.osa-plan-error')) === 'Give the step a title.', 'a step with no title is not saved');
  assert(page.q('.osa-plan-input') !== null, 'the editor stays open');
  page.q('.osa-plan-input').dispatchEvent(new page.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  assert(page.q('.osa-plan-input') === null && stepTitles(page)[0] === 'Count face onsets', 'Escape cancels');
}

console.log('\nadding and removing steps, within 1 to 8');
{
  const page = await start();
  await proposeFirst(page);
  click(page, page.q('.osa-plan-add'));
  assertEqual(page.qa('.osa-plan-step').length, 4, 'Add opens a fourth step');
  assert(page.window.document.activeElement === page.q('.osa-plan-input'), 'in its editor, with the title focused');
  click(page, page.q('.osa-plan-cancel'));
  assertEqual(page.qa('.osa-plan-step').length, 3, 'cancelling a step that was never saved takes it away again');

  for (let n = 4; n <= 8; n++) {
    click(page, page.q('.osa-plan-add'));
    typeInto(page, page.q('.osa-plan-input'), `Extra step ${n}`);
    click(page, page.q('.osa-plan-save'));
  }
  assertEqual(page.qa('.osa-plan-step').length, 8, 'eight steps');
  assert(page.q('.osa-plan-add') === null, 'and no way to add a ninth');
  assertEqual(text(page.q('.osa-plan-title')), 'Proposed plan · 8 steps', 'the title counts them');

  click(page, page.qa('.osa-plan-remove-btn')[0]);
  assertEqual(stepTitles(page)[0], 'Epoch the EEG', 'Remove takes out the step it names');
  assertEqual(page.qa('.osa-plan-step').length, 7, 'seven left');
  while (page.qa('.osa-plan-step').length > 1) click(page, page.q('.osa-plan-remove-btn'));
  assertEqual(page.qa('.osa-plan-step').length, 1, 'one step stays');
  assert(page.q('.osa-plan-remove-btn').disabled, 'and cannot be removed');
  assertEqual(text(page.q('.osa-plan-title')), 'Proposed plan · 1 step', 'one step, said in the singular');
}

// ---------------------------------------------------------------------------
// The decision
// ---------------------------------------------------------------------------

console.log('\nApprove posts exactly {session_id, result: {call_id, decision, steps, run_mode}, client_tools}');
{
  const page = await start();
  await proposeFirst(page);
  // Edit, add and remove, so what is sent is the plan as the reader left it.
  click(page, page.qa('.osa-plan-edit-btn')[0]);
  typeInto(page, page.q('.osa-plan-input'), 'Count the face onsets');
  typeInto(page, page.q('.osa-plan-textarea'), 'Per condition.');
  click(page, page.q('.osa-plan-save'));
  click(page, page.q('.osa-plan-add'));
  typeInto(page, page.q('.osa-plan-input'), 'Summarize');
  typeInto(page, page.q('.osa-plan-textarea'), 'Say what the N170 shows.');
  click(page, page.q('.osa-plan-save'));
  click(page, page.qa('.osa-plan-remove-btn')[1]);
  const resumed = await approve(page);
  assertEqual(page.requests.length, 2, 'one /chat and one /chat/resume');
  assertEqual(page.requests[1].kind, 'resume', 'the decision goes to /chat/resume');
  assertEqual(
    page.requests[1].body,
    {
      session_id: 'sess-1',
      result: {
        call_id: 'plan-1',
        decision: 'approved',
        steps: [
          { title: 'Count the face onsets', detail: 'Per condition.' },
          { title: 'Average per condition', detail: 'Average the epochs and find the N170 channels.' },
          { title: 'Summarize', detail: 'Say what the N170 shows.' },
        ],
        run_mode: 'pause',
      },
      client_tools: ['execute_code', 'get_full_output', 'propose_plan'],
    },
    'the body is exactly that, in that order, with the steps as edited, added and removed'
  );
  const reply = lastReply(page);
  assertEqual([reply.plan.status, reply.plan.steps.length], ['approved', 3], 'the reply holds the approved plan');
  resumed.send(doneEvent('Step 1 is ready when you are.'));
  resumed.close();
  await waitUntil(() => settled(page), 'the reply ends');
}

console.log('\nrun all is sent as run_all; Ask for changes sends changes_requested and runs no code');
{
  const page = await start();
  await proposeFirst(page);
  const resumed = await approve(page, { runAll: true });
  assertEqual(page.requests[1].body.result.run_mode, 'run_all', 'the radio chose it');
  assert(!('feedback' in page.requests[1].body.result), 'an approval carries no feedback');
  resumed.send(doneEvent('Done.'));
  resumed.close();
  await waitUntil(() => settled(page), 'the reply ends');

  const changes = await start();
  await proposeFirst(changes);
  click(changes, changes.q('.osa-plan-ask'));
  const note = changes.q('.osa-plan-feedback textarea');
  assertEqual(text(changes.q(`label[for="${note.id}"]`)), 'What should change? (optional)', 'asking for changes opens a labeled note, optional');
  assertEqual(note.getAttribute('maxlength'), '1000', 'held to 1000 characters');
  assert(changes.q('.osa-plan-ask').getAttribute('aria-expanded') === 'true', 'the button says it is open');
  assert(changes.window.document.activeElement === note, 'the note has focus');
  click(changes, changes.q('.osa-plan-cancel'));
  assert(changes.q('.osa-plan-feedback') === null, 'Back closes it');
  click(changes, changes.q('.osa-plan-ask'));
  typeInto(changes, changes.q('.osa-plan-feedback textarea'), 'Use sub-003 instead.');
  const again = changes.queue();
  click(changes, changes.q('.osa-plan-send-feedback'));
  await waitUntil(() => changes.requests.length === 2, 'the request for changes is posted');
  assertEqual(
    changes.requests[1].body.result,
    {
      call_id: 'plan-1',
      decision: 'changes_requested',
      steps: PLAN_ARGS.steps,
      run_mode: 'pause',
      feedback: 'Use sub-003 instead.',
    },
    'changes_requested, with the steps and the note'
  );
  // The assistant revises: a new plan in the same reply, which the card shows as the new draft.
  again.send(planRequest('plan-2', { ...PLAN_ARGS, steps: [PLAN_ARGS.steps[0]] }));
  again.close();
  await waitUntil(() => changes.qa('.osa-plan-card.osa-plan-live').length === 1 && text(changes.q('.osa-plan-title')) === 'Proposed plan · 1 step', 'the revised plan is on the card');
  assertEqual(changes.api.getMessages().filter((m) => m.executions && m.executions.length).length, 0, 'no code ran');
  // Sending with no note is allowed: it is optional.
  const last = changes.queue();
  click(changes, changes.q('.osa-plan-ask'));
  click(changes, changes.q('.osa-plan-send-feedback'));
  await waitUntil(() => changes.requests.length === 3, 'a request for changes without a note is posted');
  assert(!('feedback' in changes.requests[2].body.result), 'with no feedback key');
  last.send(doneEvent('OK.'));
  last.close();
  await waitUntil(() => settled(changes), 'the reply ends');
  assertEqual(messagesOf(changes).filter((m) => m.plan).length, 1, 'one plan stays on the reply: the latest');
}

// ---------------------------------------------------------------------------
// Consent
// ---------------------------------------------------------------------------

const asking = (page) => page.api.getToolActivity() && page.api.getToolActivity().phase === 'asking';

console.log('\nconsent: an approved plan\'s code is not asked about, up to two attempts a step');
{
  const page = await start();
  await proposeFirst(page);
  let stream = await approve(page, { runAll: true });
  // Step 1 raises, and is tried again; the second attempt fails too.
  stream.send(codeRequest('r1', 'FAIL:KeyError: \'Scrambled\'', 'Count the onsets'));
  stream.close();
  const second = page.queue();
  await waitUntil(() => page.requests.length === 3, 'the first run is answered without a question');
  assert(!asking(page), 'step 1, attempt 1 ran with no permission card');
  second.send(codeRequest('r2', 'FAIL:KeyError: \'Scrambled\'', 'Count the onsets again'));
  second.close();
  const third = page.queue();
  await waitUntil(() => page.requests.length === 4, 'the second attempt is answered without a question');
  assert(!asking(page), 'step 1, attempt 2 ran with no permission card');
  assertEqual(page.requests[2].body.result.status, 'error', 'and its result says it raised');
  // A third request for the same step is beyond the plan: asked.
  third.send(codeRequest('r3', 'print(3)', 'Count the onsets a third time'));
  third.close();
  await waitUntil(() => asking(page), 'the third attempt asks');
  assert(page.q('.osa-tool-run') && page.q('.osa-tool-deny'), 'with the usual card, Run and Don\'t run');
  const fourth = page.queue();
  click(page, page.q('.osa-tool-deny'));
  await waitUntil(() => page.requests.length === 5, 'the refusal is posted');
  assertEqual(page.requests[4].body.result.status, 'denied', 'a refusal is a refusal');
  fourth.send(doneEvent('I stopped.'));
  fourth.close();
  await waitUntil(() => settled(page), 'the reply ends');
  const runs = lastReply(page).executions;
  assertEqual(runs.map((r) => [r.step, r.attempt, r.status]), [[1, 1, 'error'], [1, 2, 'error'], [undefined, undefined, 'denied']], 'the first two are attempts 1 and 2 of step 1; the third is not a step');
}

console.log('\nconsent: a plan\'s steps run in turn, and a request past the last step asks');
{
  const page = await start();
  await proposeFirst(page);
  const streams = [await approve(page, { runAll: true })];
  for (let n = 1; n <= 3; n++) {
    const next = page.queue();
    streams[streams.length - 1].send(codeRequest(`s${n}`, `print(${n})`, `Step ${n} code`));
    streams[streams.length - 1].close();
    await waitUntil(() => page.requests.length === 2 + n, `step ${n} is answered`);
    assert(!asking(page), `step ${n} ran with no permission card`);
    streams.push(next);
  }
  streams[3].send(codeRequest('extra', 'print("extra")', 'A fourth step the plan does not have'));
  streams[3].close();
  await waitUntil(() => asking(page), 'a request past the plan asks');
  const last = page.queue();
  click(page, page.q('.osa-tool-deny'));
  await waitUntil(() => page.requests.length === 6, 'the refusal is posted');
  last.send(doneEvent('Done.'));
  last.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(lastReply(page).executions.map((r) => r.step), [1, 2, 3, undefined], 'steps 1 to 3, then a run that is none');
}

console.log('\nconsent: after Ask for changes, code is asked about as before');
{
  const page = await start();
  await proposeFirst(page);
  click(page, page.q('.osa-plan-ask'));
  const next = page.queue();
  click(page, page.q('.osa-plan-send-feedback'));
  await waitUntil(() => page.requests.length === 2, 'the request for changes is posted');
  next.send(codeRequest('sneaky', 'print(1)', 'Run anyway'));
  next.close();
  await waitUntil(() => asking(page), 'code after a request for changes asks');
  const last = page.queue();
  click(page, page.q('.osa-tool-deny'));
  await waitUntil(() => page.requests.length === 3, 'the refusal is posted');
  last.send(doneEvent('OK.'));
  last.close();
  await waitUntil(() => settled(page), 'the reply ends');
}

// ---------------------------------------------------------------------------
// Step headers, the strip, the bar
// ---------------------------------------------------------------------------

console.log('\npause: step headers, elapsed time, the strip and the paused bar; Run step N is a normal message');
{
  const page = await start();
  await proposeFirst(page);
  const first = await approve(page);
  const next = page.queue();
  first.send(codeRequest('p1', 'DELAY:1100\nprint(1)', 'Count the onsets'));
  first.close();
  await waitUntil(() => page.requests.length === 3, 'step 1 ran without a question');
  next.send({ event: 'content', content: 'Step 1 counted the onsets.' });
  next.send(doneEvent('Step 1 counted the onsets.'));
  next.close();
  await waitUntil(() => settled(page), 'the reply ends');

  assertEqual(text(page.q('.osa-run-card .osa-run-card-title')), 'Step 1 · Count face onsets', 'the run card is headed with its step and its title');
  assertEqual(text(page.q('.osa-run-card-elapsed')), '1.1 s', 'and says how long it took');
  const strip = page.q('.osa-plan-strip');
  assertEqual(text(strip.querySelector('.osa-plan-strip-label')), 'Plan · step 2 of 3', 'the strip says where the plan is');
  assertEqual(strip.querySelectorAll('.osa-plan-seg').length, 3, 'with a segment per step');
  assertEqual([...strip.querySelectorAll('.osa-plan-seg')].map((s) => s.className.replace('osa-plan-seg osa-plan-seg-', '')), ['done', 'current', 'pending'], 'the first done, the second next');
  assertEqual(text(strip.querySelector('.osa-plan-strip-mode')), 'Pauses after each step', 'and says it pauses');
  assertEqual(barButtons(page), ['Run step 2', 'Change step 2', 'Stop plan'], 'the paused bar offers Run step 2, Change step 2 and Stop plan');
  assert(text(page.q('.osa-plan-bar-text')).startsWith('Paused after step 1'), 'and says what it is waiting for');
  assertEqual(text(page.q('.osa-plan-badge')), 'Approved', 'the card above is read-only and Approved');
  assert(page.q('.osa-plan-approve') === null && page.q('.osa-plan-edit-btn') === null, 'with no controls');

  // Change step 2: the box is prefilled and focused, nothing is sent.
  click(page, page.q('.osa-plan-bar-change'));
  assertEqual(page.q('.osa-chat-input input').value, 'Change step 2: ', 'Change step N prefills the box');
  assert(page.window.document.activeElement === page.q('.osa-chat-input input'), 'and focuses it');
  assertEqual(page.requests.length, 3, 'nothing was sent');
  page.q('.osa-chat-input input').value = '';

  // Run step 2: an ordinary message, and the plan goes on in the new reply.
  const go = page.queue();
  const after = page.queue();
  click(page, page.q('.osa-plan-bar-run'));
  await waitUntil(() => page.requests.length === 4, 'Run step 2 sends a message');
  assertEqual(page.requests[3].kind, 'chat', 'to /chat');
  assertEqual(page.requests[3].body.message, 'Continue with step 2.', 'saying "Continue with step 2."');
  assertEqual(page.requests[3].body.session_id, 'sess-1', 'in the same session');
  go.send(sessionEvent());
  go.send(codeRequest('p2', 'print(2)', 'Epoch the EEG'));
  go.close();
  await waitUntil(() => page.requests.length === 5, 'step 2 ran in the new reply, without a question');
  after.send(doneEvent('Step 2 epoched the data.'));
  after.close();
  await waitUntil(() => settled(page), 'the reply ends');
  const heads = page.qa('.osa-run-card .osa-run-card-title').map(text);
  assertEqual(heads, ['Step 1 · Count face onsets', 'Step 2 · Epoch the EEG'], 'the second run is step 2 in the second reply');
  assertEqual(text(page.q('.osa-plan-strip-label')), 'Plan · step 3 of 3', 'the strip moved on');
  assertEqual(barButtons(page), ['Run step 3', 'Change step 3', 'Stop plan'], 'the bar offers step 3');
}

console.log('\nrun all: the steps follow one another, and the strip says it runs all');
{
  const page = await start();
  await proposeFirst(page);
  let stream = await approve(page, { runAll: true });
  for (let n = 1; n <= 3; n++) {
    const next = page.queue();
    stream.send(codeRequest(`a${n}`, `print(${n})`, `Step ${n}`));
    stream.close();
    await waitUntil(() => page.requests.length === 2 + n, `step ${n} is answered`);
    if (n === 1) {
      assertEqual(text(page.q('.osa-plan-strip-mode')), 'Runs all steps', 'the strip says it runs all steps');
      assertEqual(text(page.q('.osa-plan-strip-label')), 'Plan · step 2 of 3', 'and that the reply is on step 2 once step 1 is done');
      assertEqual(barButtons(page), ['Stop plan'], 'while the reply goes on, the bar offers only Stop plan');
    }
    stream = next;
  }
  stream.send(doneEvent('All three steps ran.'));
  stream.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(page.qa('.osa-run-card .osa-run-card-title').map(text), ['Step 1 · Count face onsets', 'Step 2 · Epoch the EEG', 'Step 3 · Average per condition'], 'three step headers');
  assertEqual(text(page.q('.osa-plan-strip-label')), 'Plan · all 3 steps done', 'the strip says all three are done');
  assert(page.q('.osa-plan-bar') === null, 'and the bar is gone');
}

console.log('\na failed step: its header, the retry line, what waits, and the second attempt');
{
  const page = await start();
  await proposeFirst(page);
  const first = await approve(page, { runAll: true });
  const afterFail = page.queue();
  first.send(codeRequest('f1', 'FAIL:KeyError: \'Scrambled\'\nprint(1)', 'Count the onsets'));
  first.close();
  await waitUntil(() => page.requests.length === 3, 'step 1 is answered');
  await waitUntil(() => page.q('.osa-run-card-retry'), 'the card says it will be tried again');
  assertEqual(text(page.q('.osa-run-card .osa-run-card-title')), "Step 1 failed · KeyError: 'Scrambled'", 'the head names the step, the type and the message');
  assert(page.q('.osa-run-card-failed'), 'it is a failed card');
  assert(text(page.q('.osa-run-card-retry')).includes('attempt 2 of 2'), 'the retry line says which attempt is next');
  assertEqual(text(page.q('.osa-plan-bar-text')), 'Step 2 waits until step 1 works.', 'the bar says step 2 waits');
  assertEqual(barButtons(page), ['Stop plan'], 'and offers Stop plan');
  assert(page.q('.osa-plan-seg-failed') !== null, 'the strip marks the step red');
  assertEqual(text(page.q('.osa-plan-strip-label')), 'Plan · step 1 of 3', 'on step 1');

  const afterRetry = page.queue();
  afterFail.send(codeRequest('f2', 'print(1)', 'Count the onsets, fixed'));
  afterFail.close();
  await waitUntil(() => page.requests.length === 4, 'the retry is answered');
  afterRetry.send(doneEvent('Step 1 works now.'));
  afterRetry.close();
  await waitUntil(() => settled(page), 'the reply ends');
  const runs = lastReply(page).executions;
  assertEqual(runs.map((r) => [r.step, r.attempt, r.status]), [[1, 1, 'error'], [1, 2, 'ok']], 'the second run is attempt 2 of step 1');
  assertEqual(page.qa('.osa-run-card .osa-run-card-title').map(text), ["Step 1 failed · KeyError: 'Scrambled'", 'Step 1 · Count face onsets'], 'both are headed with the step');
  assert(page.q('.osa-run-card-retry') === null, 'no retry line once the reply is done');
}

console.log('\na step that fails twice: the plan stops waiting, and Change step N and Stop plan are offered');
{
  const page = await start();
  await proposeFirst(page);
  const first = await approve(page);
  const second = page.queue();
  first.send(codeRequest('g1', 'FAIL:ValueError: bad\nx', 'Count'));
  first.close();
  const third = page.queue();
  await waitUntil(() => page.requests.length === 3, 'attempt 1 is answered');
  second.send(codeRequest('g2', 'FAIL:ValueError: bad\nx', 'Count again'));
  second.close();
  await waitUntil(() => page.requests.length === 4, 'attempt 2 is answered');
  third.send(doneEvent('It failed twice; tell me what to change.'));
  third.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(text(page.q('.osa-plan-bar-text')), 'Step 1 did not work after 2 attempts.', 'the bar says so');
  assertEqual(barButtons(page), ['Change step 1', 'Stop plan'], 'and offers to change it, or to stop');
  assert(page.q('.osa-run-card-retry') === null, 'the last card promises no further attempt');
}

console.log('\nStop plan: the running step is cancelled, no further request is answered, the strip says stopped');
{
  const page = await start();
  await proposeFirst(page);
  const first = await approve(page);
  const afterStop = page.queue();
  first.send(codeRequest('x1', 'NEVER\nwhile True: pass', 'A step that does not end'));
  first.close();
  await waitUntil(() => page.api.getBrowserTools().busy, 'step 1 is running');
  assertEqual(barButtons(page), ['Stop plan'], 'the bar offers Stop plan');
  click(page, page.q('.osa-plan-bar-stop'));
  await waitUntil(() => page.requests.length === 3, 'the cancelled run is answered');
  assertEqual(page.requests[2].body.result.status, 'cancelled', 'with the status cancelled');
  assertEqual(lastReply(page).plan.status, 'stopped', 'the plan is stopped');
  // The assistant asks for another step anyway: it is left parked.
  afterStop.send(codeRequest('x2', 'print(2)', 'Step 2'));
  afterStop.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(page.requests.length, 3, 'the next request for code was not answered: no further /chat/resume');
  assert(!asking(page), 'and nobody was asked about it');
  assertEqual(lastReply(page).executions.length, 1, 'only the cancelled run is on the reply');
  assertEqual(text(page.q('.osa-plan-strip-label')), 'Plan stopped · step 1 of 3', 'the strip says stopped');
  assert(page.q('.osa-plan-seg-stopped') !== null, 'and marks the step');
  assert(page.q('.osa-plan-bar') === null, 'the bar is gone');
  assertEqual(text(page.q('.osa-plan-badge')), 'Stopped', 'the card says Stopped');
  assertEqual(text(page.q('.osa-run-card .osa-run-card-title')), 'Step 1 stopped · Count face onsets', 'and the run says it was stopped');
  assertEqual(page.stored().conversations.find((c) => c.id === page.widget.__browser.getActiveConversation().id).messages.at(-1).plan.status, 'stopped', 'which is stored');

  // The next message starts a reply of its own, and its code is asked about.
  const next = page.queue();
  send(page, 'Run it anyway');
  await waitUntil(() => page.requests.length === 4, 'the next message is sent');
  next.send(codeRequest('x3', 'print(3)', 'Anyway'));
  next.close();
  await waitUntil(() => asking(page), 'code after Stop plan asks');
  const last = page.queue();
  click(page, page.q('.osa-tool-deny'));
  await waitUntil(() => page.requests.length === 5, 'the refusal is posted');
  last.send(doneEvent('OK.'));
  last.close();
  await waitUntil(() => settled(page), 'the reply ends');
}

console.log('\nStop plan while the plan is paused: the plan is stopped, nothing is cancelled');
{
  const page = await start();
  await proposeFirst(page);
  const first = await approve(page);
  const next = page.queue();
  first.send(codeRequest('y1', 'print(1)', 'Count'));
  first.close();
  await waitUntil(() => page.requests.length === 3, 'step 1 ran');
  next.send(doneEvent('Step 1 done.'));
  next.close();
  await waitUntil(() => settled(page), 'the reply ends');
  click(page, page.q('.osa-plan-bar-stop'));
  assertEqual(text(page.q('.osa-plan-strip-label')), 'Plan stopped · step 2 of 3', 'the strip says stopped, before step 2');
  assert(page.q('.osa-plan-bar') === null, 'and the bar is gone');
  assertEqual(page.requests.length, 3, 'nothing was sent');
}

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

console.log('\nthe plan, the step, the attempt, the title and the time are stored, bounded, and survive a reload');
{
  const page = await start();
  await proposeFirst(page);
  const first = await approve(page);
  const failedThenOk = page.queue();
  first.send(codeRequest('k1', 'FAIL:KeyError: \'a\'\nx', 'Count'));
  first.close();
  await waitUntil(() => page.requests.length === 3, 'attempt 1 is answered');
  const last = page.queue();
  failedThenOk.send(codeRequest('k2', 'DELAY:1200\nprint(1)', 'Count, fixed'));
  failedThenOk.close();
  await waitUntil(() => page.requests.length === 4, 'attempt 2 is answered');
  last.send(doneEvent('Step 1 done.'));
  last.close();
  await waitUntil(() => settled(page), 'the reply ends');

  const record = page.stored();
  const stored = record.conversations.find((c) => c.id === record.activeId).messages.find((m) => m.plan);
  assertEqual(
    [stored.plan.status, stored.plan.runMode, stored.plan.callId, stored.plan.steps.length, stored.plan.estPeakMb],
    ['approved', 'pause', 'plan-1', 3, 300],
    'the approved plan is stored on its reply'
  );
  assert(!('_ui' in stored.plan), 'with none of the card\'s transient state');
  assertEqual(
    stored.executions.map((r) => [r.step, r.attempt, r.stepTitle, r.status]),
    [[1, 1, 'Count face onsets', 'error'], [1, 2, 'Count face onsets', 'ok']],
    'each run is stored with its step, attempt and title'
  );
  assertEqual([stored.executions[0].elapsedMs, stored.executions[1].elapsedMs], [0, 1200], 'and its time');

  // A reload in the same browser session: a new page over the same storage.
  const reloaded = await start({ storage: page.window.localStorage.getItem(page.storageKey), storageKey: page.storageKey });
  const restored = messagesOf(reloaded).find((m) => m.plan);
  assertEqual(restored.plan, stored.plan, 'the plan is read back as stored');
  assertEqual(restored.executions.map((r) => [r.step, r.attempt, r.stepTitle, r.elapsedMs]), [[1, 1, 'Count face onsets', 0], [1, 2, 'Count face onsets', 1200]], 'and so are the runs');
  reloaded.api.renderMessages(reloaded.container);
  assertEqual(text(reloaded.q('.osa-plan-badge')), 'Approved', 'the restored card says Approved');
  assert(reloaded.q('.osa-plan-approve') === null && reloaded.q('.osa-plan-edit-btn') === null && reloaded.q('.osa-plan-add') === null, 'and is read-only');
  assertEqual(text(reloaded.q('.osa-plan-mode-line')), 'Pauses after each step', 'with its run mode');
  assertEqual(stepTitles(reloaded), PLAN_ARGS.steps.map((s) => s.title), 'and its steps');
  assertEqual(text(reloaded.q('.osa-plan-strip-label')), 'Plan · step 2 of 3', 'the strip shows where the plan stood');
  assertEqual(barButtons(reloaded), ['Run step 2', 'Change step 2', 'Stop plan'], 'with the paused bar');
  assertEqual(reloaded.qa('.osa-run-card .osa-run-card-title').map(text), ["Step 1 failed · KeyError: 'a'", 'Step 1 · Count face onsets'], 'the run cards keep their step headers');
  assertEqual(text(reloaded.q('.osa-run-card-elapsed')), '1.2 s', 'and the time of the run that has one');
}

console.log('\na stored plan is bounded when it is read back');
{
  const oversized = {
    callId: 'c'.repeat(500),
    findings: 'f'.repeat(5000),
    estPeakMb: 99999999,
    runMode: 'sideways',
    status: 'approved',
    feedback: 'n'.repeat(5000),
    steps: Array.from({ length: 12 }, (_, i) => ({ title: `title ${i}\n${'t'.repeat(500)}`, detail: 'd'.repeat(2000) })),
    _ui: { editing: 1 },
  };
  const page = await start();
  const L = page.api.PLAN_STORAGE_LIMITS;
  assertEqual(
    L,
    { steps: plan.PLAN_LIMITS.steps, title: plan.PLAN_LIMITS.title, detail: plan.PLAN_LIMITS.detail, findings: plan.PLAN_LIMITS.findings, feedback: plan.PLAN_LIMITS.feedback, peakMb: plan.PLAN_LIMITS.peakMb, attempts: plan.MAX_STEP_ATTEMPTS },
    'the widget\'s limits are the plan logic\'s'
  );
  const cut = page.api.normalizePersistedPlan(oversized);
  assertEqual(
    [cut.callId.length, cut.findings.length, cut.estPeakMb, cut.steps.length, cut.steps[0].title.length, cut.steps[0].detail.length, cut.runMode, cut.status, cut.feedback.length, '_ui' in cut],
    [256, L.findings, L.peakMb, L.steps, L.title, L.detail, 'pause', 'approved', L.feedback, false],
    'every field is within its bound, and the card\'s transient state is not read'
  );
  assertEqual(page.api.normalizePersistedPlan({ ...oversized, status: 'draft' }).status, 'unanswered', 'a draft was never answered');
  assertEqual(page.api.normalizePersistedPlan({ ...oversized, status: 'nonsense' }).status, 'unanswered', 'nor was a status nobody knows');
  assertEqual([page.api.normalizePersistedPlan(null), page.api.normalizePersistedPlan({ steps: [] }), page.api.normalizePersistedPlan({ steps: [{ title: '' }] }), page.api.normalizePersistedPlan('x')], [null, null, null, null], 'what is not a plan is not one');
  const runs = page.api.normalizePersistedExecutions([
    { callId: 'a', step: 3, attempt: 2, stepTitle: 'x'.repeat(500), elapsedMs: 1e12, status: 'ok' },
    { callId: 'b', step: 99, attempt: 'x', stepTitle: 7, elapsedMs: -5, status: 'ok' },
    { callId: 'c', status: 'ok' },
  ]);
  assertEqual(
    [runs[0].step, runs[0].attempt, runs[0].stepTitle.length, runs[0].elapsedMs, 'step' in runs[1], 'step' in runs[2]],
    [3, 2, L.title, 86400000, false, false],
    'a run\'s step fields are bounded, and a run with no valid step has none'
  );
}

console.log('\na draft that was never answered comes back as Not answered, read-only');
{
  const seeded = JSON.stringify({
    version: 3,
    activeId: 'chat-1',
    conversations: [{
      id: 'chat-1', context: null, title: 'Compare ERPs', sessionId: 'sess-old', createdAt: 1, updatedAt: Date.now(),
      messages: [
        { role: 'assistant', content: 'Hello' },
        { role: 'user', content: 'Compare ERPs' },
        { role: 'assistant', content: '', citations: [], plan: { ...plan.normalizeProposal(PLAN_ARGS, 'old-plan') } },
      ],
    }],
  });
  const page = await start({ storage: seeded, storageKey: 'osa-test-plan-draft' });
  page.api.renderMessages(page.container);
  assertEqual(text(page.q('.osa-plan-badge')), 'Not answered', 'the badge says so');
  assert(page.q('.osa-plan-approve') === null && page.q('.osa-plan-edit-btn') === null, 'with nothing to press');
  assert(page.q('.osa-plan-strip') === null, 'and no strip');
}

console.log('\nthe stored plan conversation becomes a notebook with the step titles');
{
  const page = await start();
  await proposeFirst(page);
  let stream = await approve(page, { runAll: true });
  for (let n = 1; n <= 2; n++) {
    const next = page.queue();
    stream.send(codeRequest(`n${n}`, `import numpy as np\nprint(${n})`, `Description ${n}`));
    stream.close();
    await waitUntil(() => page.requests.length === 2 + n, `step ${n} is answered`);
    stream = next;
  }
  stream.send(doneEvent('Two steps ran.'));
  stream.close();
  await waitUntil(() => settled(page), 'the reply ends');
  const record = page.stored();
  const messages = record.conversations.find((c) => c.id === record.activeId).messages;
  const executions = messages.flatMap((m) => (Array.isArray(m.executions) ? m.executions : []));
  const built = page.window.OSARuntime.buildConversationNotebook({
    datasetId: 'xx099904', title: 'Compare ERPs', createdAt: Date.now(), communityName: TITLE, executions,
  });
  const headings = built.notebook.cells.filter((cell) => cell.cell_type === 'markdown').map((cell) => cell.source);
  assert(headings.some((h) => h.includes('## Step 1 · Count face onsets')), 'step 1 is named by its plan title in the notebook');
  assert(headings.some((h) => h.includes('## Step 2 · Epoch the EEG')), 'and step 2');
  assert(built.script.includes('Step 1 · Count face onsets'), 'and in the script');
  assert(!headings.some((h) => h.includes('Description 1')), 'rather than by the model\'s description');
}

clearTimeout(watchdog);
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
