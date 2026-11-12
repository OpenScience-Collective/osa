/**
 * The plan rail and "Continue in notebook" in the center-stage dialog (E6.1, #701, board
 * C3), run against the real widget and the runtime bundle as built, in happy-dom windows.
 *
 * The rail: in the center dialog an <aside aria-label="Plan"> with an <ol> beside the
 * thread, driven by the plan card's state: it shows for a proposed, approved, stopped and
 * finished plan and not for none, changes requested or unanswered; its rows follow the
 * steps as they run, in words as well as icons; a row with a run card is a button that
 * scrolls to and focuses the card without touching the address, and the others are plain
 * text; the run mode is shown read-only, and the memory budget has a "change" that opens
 * Settings on the memory choices; the strip over the thread is hidden while the rail
 * shows (the paused bar stays), and at 900px and under the rail is the one that goes.
 * Docking removes the rail and puts it back without moving or redrawing the thread; Tab
 * runs header, rail, thread, input and stays in the dialog; Escape docks.
 *
 * The header: "Continue in notebook" shows in the dialog only, when the reply card would
 * offer it too, and goes the card's way (runNotebookImport).
 *
 * What stands in: `fetch`, which answers with the SSE the test writes event by event, and
 * the Python worker behind the runtime (test-support/plan-widget-harness.js); for the
 * header, the notebook's side of the bridge, as in test-widget-notebook-entry.js. Layout,
 * the real Tab key and painted contrast are browser-harness/center-stage-check.mjs's.
 *
 * Run with: bun frontend/test-widget-plan-rail.js
 */

import { normalizeProposal } from './osa-plan.js';
import { NOTEBOOK_CONVERSATIONS, run } from './test-support/notebook-conversations.js';
import {
  PLAN_ARGS, approve, barButtons, click, codeRequest, doneEvent, planRequest, proposeFirst, settled, start, text,
  typeInto, waitUntil,
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

console.log('='.repeat(60));
console.log('Widget: the plan rail and Continue in notebook (E6.1, board C3)');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** The widget with the dialog raised in the middle of the page. */
async function centerPage(options) {
  const page = await start(options);
  page.widget.open({ mode: 'center' });
  assert(page.q('.osa-chat-window').classList.contains('osa-center'), 'set up: the dialog is in the middle');
  return page;
}

const rail = (page) => page.q('aside.osa-plan-rail');
const rows = (page) => page.qa('.osa-plan-rail ol > li');
const rowStates = (page) => rows(page).map((li) => [...li.classList].find((c) => c.startsWith('osa-plan-rail-') && c !== 'osa-plan-rail-row').replace('osa-plan-rail-', ''));
const railButtons = (page) => page.qa('.osa-plan-rail ol button');
const heading = (page) => text(page.q('.osa-plan-rail-heading'));
// happy-dom keeps an element's computed style until the element changes, so touch it first:
// the media queries are then read against the window as it is now.
const display = (page, el) => {
  el.setAttribute('data-style-check', '1');
  el.removeAttribute('data-style-check');
  return page.window.getComputedStyle(el).display;
};

// Run the steps the plan has, one reply each, in pause mode: step `n` is answered in the
// reply that "Continue with step n" starts. Resolves once the reply has ended.
async function runStepsPaused(page, count) {
  let stream = await approve(page);
  for (let n = 1; n <= count; n++) {
    const next = page.queue();
    stream.send(codeRequest(`p${n}`, `print(${n})`, `Step ${n} code`));
    stream.close();
    await waitUntil(() => page.requests.length === 2 + (n - 1) * 2 + 1, `step ${n} is answered`);
    next.send(doneEvent(`Step ${n} ran.`));
    next.close();
    await waitUntil(() => settled(page), `reply ${n} ends`);
    if (n < count) {
      stream = page.queue();
      click(page, page.q('.osa-plan-bar-run'));
      await waitUntil(() => page.requests.length === 2 + n * 2, `Run step ${n + 1} is sent`);
      stream.send({ event: 'session', session_id: 'sess-1' });
    }
  }
}

// ---------------------------------------------------------------------------
// The rail beside a proposed plan
// ---------------------------------------------------------------------------

console.log('\na proposed plan: the rail is an aside with a list, every step pending and plain text');
{
  const page = await centerPage();
  assert(rail(page) === null, 'no plan, no rail');
  assert(!page.q('.osa-chat-window').classList.contains('osa-center-has-rail'), 'and the window is not a grid');
  await proposeFirst(page);
  const aside = rail(page);
  assert(aside !== null && aside.tagName === 'ASIDE' && aside.getAttribute('aria-label') === 'Plan', 'an <aside aria-label="Plan"> appears');
  assert(aside.querySelector('ol') !== null, 'with an <ol>');
  assertEqual(aside.querySelector('ol').getAttribute('role'), 'list', 'that says it is a list (Safari drops it from a list with no bullets)');
  assert(page.q('.osa-chat-window').classList.contains('osa-center-has-rail'), 'and the window has the rail class');
  assertEqual(heading(page), 'Proposed plan', 'headed "Proposed plan"');
  assertEqual(rows(page).map((li) => text(li.querySelector('.osa-plan-rail-title'))), PLAN_ARGS.steps.map((s) => s.title), 'a row for each step, in order');
  assertEqual(rowStates(page), ['pending', 'pending', 'pending'], 'all pending');
  assertEqual(railButtons(page).length, 0, 'none of them a button: nothing has run');
  assertEqual(rows(page).map((li) => text(li.querySelector('.osa-sr-only'))), ['Step 1, not started:', 'Step 2, not started:', 'Step 3, not started:'], 'each says its state in words');
  assert(page.qa('.osa-plan-rail a').length === 0, 'no link anywhere in it');
  assertEqual(text(page.q('.osa-plan-rail-mode')), 'Run mode: Pauses after each step', 'the run mode is shown');
  assert(page.q('.osa-plan-rail input, .osa-plan-rail select') === null, 'as text, with nothing to change it');
  assertEqual(text(page.q('.osa-plan-rail-memory')), 'Memory budget 512 MB · change', 'and the memory budget, with change');
  // The card's mode radio still decides it; the rail follows until it does.
  const radio = page.q('.osa-plan-card input[value="run_all"]');
  radio.checked = true;
  radio.dispatchEvent(new page.window.Event('change', { bubbles: true }));
  assertEqual(text(page.q('.osa-plan-rail-mode')), 'Run mode: Runs all steps', 'choosing Run all steps on the card changes what the rail says');
}

console.log('\nthe rail sits beside the thread, in the order header, rail, thread, input');
{
  const page = await centerPage();
  await proposeFirst(page);
  const parent = rail(page).parentElement;
  const thread = page.q('.osa-chat-messages');
  assert(parent === thread.parentElement && rail(page).nextElementSibling === thread, 'it is inserted right before the thread');
  const win = page.q('.osa-chat-window');
  const order = (el) => [...win.querySelectorAll('*')].indexOf(el);
  assert(order(page.q('.osa-chat-header')) < order(rail(page)) && order(rail(page)) < order(thread) && order(thread) < order(page.q('.osa-chat-input')), 'in the DOM: header, rail, thread, input');
}

// ---------------------------------------------------------------------------
// Corner, and docking
// ---------------------------------------------------------------------------

console.log('\nin the corner there is no rail, and the strip carries the plan');
{
  const page = await start();
  page.widget.open();
  await proposeFirst(page);
  const first = await approve(page);
  first.send(doneEvent('Step 1 is ready.'));
  first.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assert(rail(page) === null, 'no rail');
  assert(!page.q('.osa-chat-window').classList.contains('osa-center-has-rail'), 'and no rail class');
  assert(page.q('.osa-plan-strip') !== null && display(page, page.q('.osa-plan-strip')) !== 'none', 'the strip over the thread shows');
}

console.log('\ndocking removes the rail and raising it puts the rail back, with the thread untouched');
{
  const page = await centerPage();
  await proposeFirst(page);
  const thread = page.q('.osa-chat-messages');
  const nodes = [...thread.children];
  const heard = [];
  page.widget.on('modechange', (event) => heard.push(event.mode));
  page.widget.setMode('corner');
  assert(rail(page) === null && !page.q('.osa-chat-window').classList.contains('osa-center-has-rail'), 'docked: the rail is gone');
  assert(page.q('.osa-chat-messages') === thread && [...thread.children].length === nodes.length && [...thread.children].every((n, i) => n === nodes[i]), 'and the thread is the same node with the same children');
  page.widget.setMode('center');
  assert(rail(page) !== null, 'raised again: the rail is back');
  assert(page.q('.osa-chat-messages') === thread && [...thread.children].every((n, i) => n === nodes[i]) && thread.children.length === nodes.length, 'the thread is still the same node, with the same children: nothing was redrawn or moved');
  assertEqual(heard, ['corner', 'center'], 'two modechange events, and nothing else fired');
}

// ---------------------------------------------------------------------------
// Approved: rows follow the steps
// ---------------------------------------------------------------------------

console.log('\nan approved plan: rows update as steps run; a row with a card goes to it and leaves the address alone');
{
  const page = await centerPage();
  await proposeFirst(page);
  const first = await approve(page);
  first.send(doneEvent('Approved.'));
  first.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(heading(page), 'Plan · 0 of 3 done', 'approved with nothing run: 0 of 3 done');
  assertEqual(rowStates(page), ['current', 'pending', 'pending'], 'step 1 is current');
  assert(rows(page)[0].getAttribute('aria-current') === 'step', 'and says so to a screen reader');
  assertEqual(railButtons(page).length, 0, 'with no run, no row is a button');
  assertEqual(text(page.q('.osa-plan-rail-mode')), `Run mode: ${text(page.q('.osa-plan-strip-mode'))}`, 'the rail says the run mode as the strip does');

  // Step 1 runs, in a reply of its own as a paused plan does.
  const go = page.queue();
  const stepOne = page.queue();
  click(page, page.q('.osa-plan-bar-run'));
  await waitUntil(() => page.requests.length === 3, 'Run step 1 is sent');
  go.send({ event: 'session', session_id: 'sess-1' });
  go.send(codeRequest('p1', 'print(1)', 'Count the onsets'));
  go.close();
  await waitUntil(() => page.requests.length === 4, 'step 1 ran');
  stepOne.send(doneEvent('Step 1 counted the onsets.'));
  stepOne.close();
  await waitUntil(() => settled(page), 'the reply ends');

  assertEqual(heading(page), 'Plan · 1 of 3 done', 'after step 1: 1 of 3 done');
  assertEqual(rowStates(page), ['done', 'current', 'pending'], 'step 1 done, step 2 current');
  assertEqual(railButtons(page).map((b) => b.getAttribute('data-plan-rail-step')), ['1'], 'the step that ran is a button');
  assert(rows(page)[1].querySelector('button') === null && rows(page)[2].querySelector('button') === null, 'the current and pending rows are plain text');
  assertEqual(text(rows(page)[0].querySelector('.osa-sr-only')), 'Step 1, done:', 'the done row says "Step 1, done" in words');
  assert(rows(page)[0].querySelector('svg') !== null && rows(page)[1].querySelector('svg') === null, 'with a check mark where the others have an empty circle');
  assertEqual(rows(page).length, 3, 'one row for each of the three steps');

  // The thread's strip is hidden while the rail shows; the bar stays.
  assert(display(page, page.q('.osa-plan-strip')) === 'none', 'the strip in the thread is hidden');
  assertEqual(barButtons(page), ['Run step 2', 'Change step 2', 'Stop plan'], 'the paused bar stays, with Run step 2 and Change step 2');
  assert(display(page, page.q('.osa-plan-bar')) !== 'none', 'and shows');

  // A row is a button that takes you to the card.
  const scrolled = [];
  page.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView(options) { scrolled.push([this, options]); };
  const before = [page.window.location.href, page.window.location.hash];
  const card = page.q('.osa-run-card[data-plan-step="1"]');
  assert(card !== null, 'the run card carries its step: data-plan-step="1"');
  assert(page.q('.osa-run-card[data-plan-step="2"]') === null, 'and only the steps that ran have one');
  click(page, railButtons(page)[0]);
  assertEqual(scrolled.length, 1, 'clicking the row scrolls once');
  assert(scrolled[0][0] === card, 'to that run card');
  assert(page.window.document.activeElement === card, 'and focuses it');
  assertEqual([page.window.location.href, page.window.location.hash], before, 'the page\'s address and hash are unchanged');
  assertEqual(page.window.location.hash, '', 'no hash was ever set');

  // Another step runs: the rail follows.
  const next = page.queue();
  const after = page.queue();
  click(page, page.q('.osa-plan-bar-run'));
  await waitUntil(() => page.requests.length === 5, 'Run step 2 is sent');
  next.send({ event: 'session', session_id: 'sess-1' });
  next.send(codeRequest('p2', 'print(2)', 'Epoch the EEG'));
  next.close();
  await waitUntil(() => page.requests.length === 6, 'step 2 ran');
  after.send(doneEvent('Step 2 epoched the data.'));
  after.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(heading(page), 'Plan · 2 of 3 done', 'the heading follows: 2 of 3 done');
  assertEqual(rowStates(page), ['done', 'done', 'current'], 'and the rows');
  assertEqual(railButtons(page).map((b) => b.getAttribute('data-plan-rail-step')), ['1', '2'], 'step 2 is a button now');
  assertEqual(text(page.q('.osa-plan-strip-label')), 'Plan · step 3 of 3', 'the strip, hidden, says the same');
}

console.log('\na failed step is marked with its own icon and says "failed"');
{
  const page = await centerPage();
  await proposeFirst(page);
  const first = await approve(page, { runAll: true });
  const second = page.queue();
  first.send(codeRequest('f1', "FAIL:KeyError: 'Scrambled'\nprint(1)", 'Count the onsets'));
  first.close();
  const third = page.queue();
  await waitUntil(() => page.requests.length === 3, 'attempt 1 is answered');
  second.send(codeRequest('f2', "FAIL:KeyError: 'Scrambled'\nprint(1)", 'Count the onsets again'));
  second.close();
  await waitUntil(() => page.requests.length === 4, 'attempt 2 is answered');
  third.send(doneEvent('It failed twice.'));
  third.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(rowStates(page), ['failed', 'pending', 'pending'], 'step 1 failed');
  assertEqual(text(rows(page)[0].querySelector('.osa-sr-only')), 'Step 1, failed:', 'said in words, not only in red');
  assert(rows(page)[0].querySelector('svg') !== null, 'with an icon of its own');
  assert(rows(page)[0].querySelector('svg').innerHTML !== page.q('.osa-run-card-ok')?.innerHTML, 'a different one from a done step\'s check mark');
  assertEqual(railButtons(page).map((b) => b.getAttribute('data-plan-rail-step')), ['1'], 'the failed step has its cards, so it is a button');
  // Two attempts, two cards: the row goes to the newer one.
  const scrolled = [];
  page.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() { scrolled.push(this); };
  click(page, railButtons(page)[0]);
  const cards = page.qa('.osa-run-card[data-plan-step="1"]');
  assertEqual(cards.length, 2, 'two attempts, two cards');
  assert(scrolled[0] === cards[1] && page.window.document.activeElement === cards[1], 'the row goes to the newest');
  assertEqual(barButtons(page), ['Change step 1', 'Stop plan'], 'the bar still offers Change step 1 and Stop plan');
}

console.log('\na stopped plan keeps its rail, marking the step it stopped on');
{
  const page = await centerPage();
  await proposeFirst(page);
  const first = await approve(page);
  first.send(doneEvent('Approved.'));
  first.close();
  await waitUntil(() => settled(page), 'the reply ends');
  click(page, page.q('.osa-plan-bar-stop'));
  assert(rail(page) !== null, 'the rail stays');
  assertEqual(heading(page), 'Plan stopped · 0 of 3 done', 'headed as stopped');
  assertEqual(rowStates(page), ['stopped', 'pending', 'pending'], 'step 1 is the one it stopped on');
  assertEqual(text(rows(page)[0].querySelector('.osa-sr-only')), 'Step 1, stopped:', 'in words');
}

console.log('\na run-all plan on the rail: it follows the bar, Run step N is not offered while it runs, Pause holds the next step');
{
  const page = await centerPage();
  await proposeFirst(page);
  const first = await approve(page, { runAll: true });
  const afterStep1 = page.queue();
  const runButtons = () => page.qa('button').filter((b) => /^Run step/.test(text(b))).map(text);
  first.send(codeRequest('k1', 'DELAY:300\nprint(1)', 'Step 1'));
  first.close();
  await waitUntil(() => page.api.getBrowserTools().busy, 'step 1 is running');
  assertEqual(rowStates(page), ['current', 'pending', 'pending'], 'running: step 1 is the current row');
  assertEqual(text(page.q('.osa-plan-rail-mode')), 'Run mode: Runs all steps', 'the foot says it runs all steps');
  assertEqual(barButtons(page), ['Pause', 'Stop plan'], 'the bar beside the rail offers Pause and Stop plan');
  assertEqual(runButtons(), [], 'and no Run step button is anywhere, the rail included');
  click(page, page.qa('.osa-plan-bar button').find((b) => text(b) === 'Pause'));
  assertEqual(barButtons(page), ['Stop plan'], 'Pause asked: Stop plan only, until the step ends');
  await waitUntil(() => page.requests.length === 3, 'step 1 ends');
  assertEqual(heading(page), 'Plan · 1 of 3 done', 'between the steps the rail counts one done');
  afterStep1.send(codeRequest('k2', 'print(2)', 'Step 2'));
  afterStep1.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(page.requests.length, 3, 'step 2 is held');
  assertEqual(rowStates(page), ['done', 'current', 'pending'], 'paused: step 2 is the current row, waiting');
  assertEqual(text(page.q('.osa-plan-rail-mode')), 'Run mode: Runs all steps', 'the plan is still run-all');
  assertEqual(barButtons(page), ['Run step 2', 'Change step 2', 'Stop plan'], 'the bar waits as a paused plan does');
  click(page, page.q('.osa-plan-bar-stop'));
  assertEqual(heading(page), 'Plan stopped · 1 of 3 done', 'Stop plan: the rail says stopped');
  assertEqual(rowStates(page), ['done', 'stopped', 'pending'], 'on step 2');
  assert(page.q('.osa-plan-bar') === null && runButtons().length === 0, 'with no bar');
}

console.log('\nan unrelated question while a run-all plan waits: the rail and the bar stay the waiting ones');
{
  const page = await centerPage();
  await proposeFirst(page);
  const first = await approve(page, { runAll: true });
  const next = page.queue();
  first.send(codeRequest('j1', 'print(1)', 'Step 1'));
  first.close();
  await waitUntil(() => page.requests.length === 3, 'step 1 ran');
  next.send(doneEvent('Step 1 done.'));
  next.close();
  await waitUntil(() => settled(page), 'the reply ends');
  const answer = page.queue();
  typeInto(page, page.q('.osa-chat-input input'), 'What is BIDS?');
  page.q('.osa-chat-input input').value = 'What is BIDS?';
  click(page, page.q('.osa-send-btn'));
  answer.send({ event: 'session', session_id: 'sess-1' });
  answer.send({ event: 'content', content: 'BIDS is a data standard. ' });
  await waitUntil(() => page.api.getMessages().some((m) => (m.content || '').includes('BIDS is a data standard')), 'the answer shows');
  assert(!settled(page), 'the answer is going');
  assertEqual(rowStates(page), ['done', 'current', 'pending'], 'the rail still shows step 2 as the one waiting');
  assertEqual(barButtons(page), ['Run step 2', 'Change step 2', 'Stop plan'], 'the bar is the waiting one');
  assertEqual(page.q('.osa-plan-bar-run').getAttribute('aria-disabled'), 'true', 'with Run step 2 disabled for the answer');
  answer.send(doneEvent('BIDS is a data standard.'));
  answer.close();
  await waitUntil(() => settled(page), 'the answer ends');
}

console.log('\na finished plan: every step done, every row a button');
{
  const page = await centerPage();
  await proposeFirst(page);
  let stream = await approve(page, { runAll: true });
  for (let n = 1; n <= 3; n++) {
    const next = page.queue();
    stream.send(codeRequest(`a${n}`, `print(${n})`, `Step ${n}`));
    stream.close();
    await waitUntil(() => page.requests.length === 2 + n, `step ${n} is answered`);
    stream = next;
  }
  stream.send(doneEvent('All three steps ran.'));
  stream.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(heading(page), 'Plan · 3 of 3 done', 'all three done');
  assertEqual(rowStates(page), ['done', 'done', 'done'], 'every row done');
  assertEqual(railButtons(page).length, 3, 'and every one a button');
  assert(page.q('.osa-plan-bar') === null, 'no bar when the plan is over');
}

console.log('\nno rail when the reader asked for changes, until the new plan is proposed; none for a plan nobody answered');
{
  const page = await centerPage();
  await proposeFirst(page);
  assert(rail(page) !== null, 'set up: the draft has a rail');
  click(page, page.q('.osa-plan-ask'));
  typeInto(page, page.q('.osa-plan-feedback textarea'), 'Use sub-003 instead.');
  const again = page.queue();
  click(page, page.q('.osa-plan-send-feedback'));
  await waitUntil(() => page.requests.length === 2, 'the request for changes is posted');
  assert(rail(page) === null && !page.q('.osa-chat-window').classList.contains('osa-center-has-rail'), 'changes requested: no rail');
  again.send(planRequest('plan-2', { ...PLAN_ARGS, steps: [PLAN_ARGS.steps[0], { title: 'Use sub-003', detail: '' }] }));
  again.close();
  await waitUntil(() => rail(page) !== null, 'the revised plan brings the rail back');
  assertEqual(heading(page), 'Proposed plan', 'a re-proposed plan replaces the old one: proposed again');
  assertEqual(rows(page).map((li) => text(li.querySelector('.osa-plan-rail-title'))), ['Count face onsets', 'Use sub-003'], 'with its own steps');

  // A draft the page never answered, restored in a new page, is "not answered".
  const seeded = JSON.stringify({
    version: 3,
    activeId: 'chat-1',
    conversations: [{
      id: 'chat-1', context: null, title: 'Compare ERPs', sessionId: 'sess-old', createdAt: 1, updatedAt: Date.now(),
      messages: [
        { role: 'assistant', content: 'Hello' },
        { role: 'user', content: 'Compare ERPs' },
        { role: 'assistant', content: '', citations: [], plan: normalizeProposal(PLAN_ARGS, 'old-plan') },
      ],
    }],
  });
  const restored = await centerPage({ storage: seeded, storageKey: 'osa-test-plan-rail-draft' });
  restored.api.renderMessages(restored.container);
  assertEqual(restored.api.getMessages().filter((m) => m.plan).map((m) => m.plan.status), ['unanswered'], 'set up: the plan reads as not answered');
  assert(rail(restored) === null, 'an unanswered plan has no rail');
}

console.log('\nno runtime, no rail: a restored plan is drawn without one');
{
  const source = await start();
  await proposeFirst(source);
  const first = await approve(source);
  first.send(doneEvent('Approved.'));
  first.close();
  await waitUntil(() => settled(source), 'the reply ends');
  const bare = await centerPage({ storage: source.window.localStorage.getItem(source.storageKey), storageKey: source.storageKey, bundle: 'none' });
  bare.api.renderMessages(bare.container);
  assert(bare.q('.osa-plan-card') !== null, 'set up: the plan card is drawn');
  assert(rail(bare) === null, 'without the bundle there is no rail');
  assert(bare.q('.osa-center-notebook-btn').hidden, 'and no notebook button');
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

console.log('\nthe memory budget line: change opens Settings on the memory choices; Save updates the line');
{
  const page = await centerPage();
  await proposeFirst(page);
  const change = page.q('.osa-plan-rail-change');
  assertEqual(text(change), 'change', 'a "change" button');
  assert(change.tagName === 'BUTTON' && change.getAttribute('type') === 'button', 'a real button');
  click(page, change);
  const overlay = page.q('.osa-settings-overlay.open');
  assert(overlay !== null && !overlay.classList.contains('osa-feedback-overlay'), 'Settings opens');
  assert(page.window.document.activeElement === page.q('.osa-memory-radio:checked'), 'with focus on the memory choice that is on');
  assert(page.q('.osa-chat-window').contains(overlay), 'inside the dialog');
  const bigger = page.qa('.osa-memory-radio').find((radio) => radio.value === '1024');
  bigger.checked = true;
  bigger.dispatchEvent(new page.window.Event('change', { bubbles: true }));
  click(page, page.q('.osa-settings-btn-save'));
  assert(page.q('.osa-settings-overlay.open') === null, 'Save closes it');
  assertEqual(text(page.q('.osa-plan-rail-memory')), 'Memory budget 1 GB · change', 'and the rail names the new budget');
}

// ---------------------------------------------------------------------------
// Keys
// ---------------------------------------------------------------------------

console.log('\nTab runs header, rail, thread, input and stays in the dialog; Escape docks');
{
  const page = await centerPage();
  await proposeFirst(page);
  const first = await approve(page);
  const next = page.queue();
  first.send(codeRequest('p1', 'print(1)', 'Count the onsets'));
  first.close();
  await waitUntil(() => page.requests.length === 3, 'step 1 ran');
  next.send(doneEvent('Step 1 ran.'));
  next.close();
  await waitUntil(() => settled(page), 'the reply ends');

  const win = page.q('.osa-chat-window');
  const stops = page.widget.__center.focusables(win);
  const at = (el) => stops.indexOf(el);
  const close = at(page.q('.osa-close-btn'));
  const railStep = at(page.q('.osa-plan-rail-step[data-plan-rail-step]'));
  const railChange = at(page.q('.osa-plan-rail-change'));
  const threadStop = at(page.q('.osa-run-card button'));
  const input = at(page.q('.osa-chat-input input'));
  assert([close, railStep, railChange, threadStop, input].every((i) => i >= 0), 'set up: each is a tab stop');
  assert(at(page.q('.osa-history-btn')) < railStep, 'History, in the header, comes before the rail');
  assert(close < railStep && railStep < railChange && railChange < threadStop && threadStop < input, 'header, rail (rows, then change), thread, input');

  const doc = page.window.document;
  const key = (name, init = {}) => new page.window.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...init });
  stops[stops.length - 1].focus();
  const wrap = key('Tab');
  doc.dispatchEvent(wrap);
  assert(wrap.defaultPrevented && doc.activeElement === stops[0], 'Tab from the last control wraps to the first');
  const back = key('Tab', { shiftKey: true });
  doc.dispatchEvent(back);
  assert(back.defaultPrevented && doc.activeElement === stops[stops.length - 1], 'and Shift+Tab from the first wraps to the last');
  assert(win.contains(doc.activeElement), 'focus is in the dialog');

  const thread = page.q('.osa-chat-messages');
  doc.dispatchEvent(key('Escape'));
  assert(!win.classList.contains('osa-center') && rail(page) === null, 'Escape docks the dialog, and the rail goes with it');
  assert(page.q('.osa-chat-messages') === thread, 'the same thread');
  assert(win.classList.contains('open'), 'still open');
}

// What the browser does and happy-dom does not: an element in a display:none subtree has no
// boxes, so focus() on it does nothing. Read from the computed display, as the CSS decides.
function emulateLayout(page) {
  page.window.HTMLElement.prototype.getClientRects = function getClientRects() {
    for (let el = this; el && el.nodeType === 1; el = el.parentElement) {
      el.setAttribute('data-style-check', '1');
      el.removeAttribute('data-style-check');
      if (page.window.getComputedStyle(el).display === 'none') return [];
    }
    return [{ width: 10, height: 10 }];
  };
}

console.log('\nRun step N in the bar: focus goes where the reader can see it, never to the page');
{
  const page = await centerPage();
  await proposeFirst(page);
  await runStepsPaused(page, 1);
  emulateLayout(page);
  assert(display(page, page.q('.osa-plan-strip')) === 'none', 'set up: the strip is hidden while the rail shows');
  const go = page.queue();
  click(page, page.q('.osa-plan-bar-run'));
  await waitUntil(() => page.requests.length === 4, 'Run step 2 is sent');
  const active = page.window.document.activeElement;
  assert(active !== page.window.document.body, 'focus did not fall to the page');
  assert(active === rail(page) && page.q('.osa-chat-window').contains(active), 'it went to the rail, which says the same as the hidden strip, inside the dialog');
  assertEqual(rail(page).getAttribute('tabindex'), '-1', 'the rail takes focus without joining the Tab order');
  go.send({ event: 'session', session_id: 'sess-1' });
  go.send(doneEvent('Nothing more.'));
  go.close();
  await waitUntil(() => settled(page), 'the reply ends');
}

console.log('\nRun step N at 900px and under: the strip is drawn, and it takes focus');
{
  const page = await centerPage();
  page.window.happyDOM.setViewport({ width: 900, height: 700 });
  await proposeFirst(page);
  await runStepsPaused(page, 1);
  emulateLayout(page);
  assert(display(page, page.q('.osa-plan-strip')) !== 'none', 'set up: the strip shows');
  const go = page.queue();
  click(page, page.q('.osa-plan-bar-run'));
  await waitUntil(() => page.requests.length === 4, 'Run step 2 is sent');
  assert(page.window.document.activeElement === page.q('.osa-plan-strip'), 'focus is on the strip');
  go.send({ event: 'session', session_id: 'sess-1' });
  go.send(doneEvent('Nothing more.'));
  go.close();
  await waitUntil(() => settled(page), 'the reply ends');
}

console.log('\na row whose run card is not in the thread still does something');
{
  const page = await centerPage();
  await proposeFirst(page);
  await runStepsPaused(page, 1);
  const scrolled = [];
  page.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView(options) { scrolled.push([this, options]); };
  const thread = page.q('.osa-chat-messages');
  page.q('.osa-run-card[data-plan-step="1"]').remove();
  assertEqual(railButtons(page).length, 1, 'set up: step 1 is still drawn as a button');
  click(page, railButtons(page)[0]);
  assertEqual(scrolled.length, 1, 'the click scrolls');
  assert(scrolled[0][0] === thread && page.window.document.activeElement === thread, 'to the thread, which is focused: no dead control');
}

console.log('\nthe rail is not drawn at 900px and under, where the strip carries the plan');
{
  const page = await centerPage();
  await proposeFirst(page);
  const first = await approve(page);
  first.send(doneEvent('Approved.'));
  first.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assert(display(page, rail(page)) !== 'none' && display(page, page.q('.osa-plan-strip')) === 'none', 'wider than 900px: the rail shows and the strip does not');
  page.window.happyDOM.setViewport({ width: 900, height: 700 });
  assert(display(page, rail(page)) === 'none', 'at 900px: the rail is not drawn');
  assert(display(page, page.q('.osa-plan-strip')) !== 'none', 'and the strip in the thread shows');
  page.window.happyDOM.setViewport({ width: 901, height: 700 });
  assert(display(page, rail(page)) !== 'none', 'at 901px: the rail is back');
}

// ---------------------------------------------------------------------------
// Continue in notebook, in the header
// ---------------------------------------------------------------------------

const DATASET = 'on002718';
const OTHER = 'nm000241';
const NOTEBOOK_ORIGIN = 'https://notebook.osc.earth';
const TODAY = Date.now() - 60_000;
const GREETING = 'Hello';

// A stored chat on `context`, with the reply's runs.
function storedChat(runs, context = { type: 'dataset', id: DATASET }) {
  return JSON.stringify({
    version: 3,
    activeId: 'chat-nb',
    conversations: [{
      id: 'chat-nb',
      context,
      title: 'Count the onsets',
      sessionId: 'session-nb',
      createdAt: TODAY - 1000,
      updatedAt: TODAY,
      messages: [
        { role: 'assistant', content: GREETING },
        { role: 'user', content: 'Count the onsets' },
        { role: 'assistant', content: 'Counted.', citations: [], executions: runs },
      ],
    }],
  });
}

const goodRuns = () => NOTEBOOK_CONVERSATIONS.erp().executions.map(({ stepTitle, ...rest }) => rest);

// The dialog on a dataset page whose community has a notebook tab (a capsule launcher), with
// the notebook's side of the bridge: what the widget posts to its frame is recorded.
async function notebookPage({ storage, dataset = { id: DATASET, zarr: true }, capsule = true, frame = true } = {}) {
  const page = await start({ storage, dataset, widgetConfig: capsule ? { launcher: 'capsule' } : {} });
  if (capsule) await waitUntil(() => page.container.classList.contains('osa-capsule'), 'the community config makes it a capsule');
  page.posts = [];
  page.say = (data) => page.window.dispatchEvent(new page.window.MessageEvent('message', {
    data: { source: 'osa-notebook', ...data }, origin: NOTEBOOK_ORIGIN, source: page.q('.osa-notebook-frame').contentWindow,
  }));
  if (capsule && frame && dataset && dataset.zarr) {
    // The notebook tab makes the frame; come back to the chat, as a reader does.
    click(page, page.q('.osa-notebook-btn'));
    page.q('.osa-notebook-frame').contentWindow.postMessage = (message, targetOrigin) => page.posts.push({ message, targetOrigin });
    click(page, page.q('.osa-launcher-capsule .osa-chat-button'));
    page.say({ type: 'ready' });
  }
  page.widget.open({ mode: 'center' });
  page.header = () => page.q('.osa-center-notebook-btn');
  page.shown = () => { const b = page.header(); return !!b && !b.hidden && display(page, b) !== 'none'; };
  page.imports = () => page.posts.filter((p) => p.message.type === 'import-notebook');
  return page;
}

console.log('\nContinue in notebook shows in the dialog when the card would offer it, and nowhere else');
{
  const page = await notebookPage({ storage: storedChat(goodRuns()) });
  assert(page.container.classList.contains('osa-capsule'), 'set up: a community with a notebook tab');
  assert(page.header() !== null && page.header().classList.contains('osa-center-notebook-btn'), 'the button has the osa-center-notebook-btn class');
  assert(page.header().tagName === 'BUTTON' && page.header().getAttribute('type') === 'button', 'and is a button');
  assert(page.shown(), 'with a notebook, the dataset\'s page and a run that succeeded: shown');
  assertEqual(text(page.header()), 'Continue in notebook', 'with the words "Continue in notebook"');
  assert(page.header().parentElement.classList.contains('osa-header-actions'), 'in the header\'s actions');
  assert(page.header().nextElementSibling === page.q('.osa-center-dock-btn'), 'right before Move to corner');
  assert(page.header().querySelector('svg') !== null && page.header().querySelector('.osa-center-label') !== null, 'with an icon and a label that goes at 600px and under, as the others do');
  assert(page.header().getAttribute('aria-busy') === null && !page.header().disabled, 'ready, not busy');
  assert(page.q('.osa-nb-entry') !== null, 'the card under the reply offers it too');
  page.widget.setMode('corner');
  assert(!page.shown(), 'docked in the corner: not shown');
  page.widget.setMode('center');
  assert(page.shown(), 'raised again: shown');
}

console.log('\n... and not without a successful run, a notebook tab, or the dataset\'s page');
{
  const noRun = await notebookPage({ storage: storedChat([run({ code: 'boom()', status: 'error', stderr: 'NameError: boom' })]) });
  assert(!noRun.shown(), 'no successful run: not shown');
  const none = await notebookPage({ storage: storedChat([]) });
  assert(!none.shown(), 'no run at all: not shown');
  const plain = await notebookPage({ storage: storedChat(goodRuns()), capsule: false });
  assert(!plain.container.classList.contains('osa-capsule') && !plain.shown(), 'no notebook tab: not shown');
  const elsewhere = await notebookPage({ storage: storedChat(goodRuns()), dataset: { id: OTHER, zarr: true } });
  assert(!elsewhere.shown(), 'on another dataset\'s page: not shown');
  const noZarr = await notebookPage({ storage: storedChat(goodRuns()), dataset: { id: DATASET, zarr: false } });
  assert(!noZarr.shown(), 'a dataset with no notebook yet: not shown');
  const search = await notebookPage({ storage: storedChat(goodRuns(), { type: 'search' }) });
  assert(!search.shown(), 'a chat that is not about a dataset: not shown');
  // The page learns the dataset, and the button follows without a new message.
  const late = await notebookPage({ storage: storedChat(goodRuns()), dataset: { id: DATASET } });
  assert(!late.shown(), 'a dataset not yet known to have a notebook: not shown');
  late.widget.setDataset({ id: DATASET, zarr: true });
  assert(late.shown(), 'setDataset with a notebook: shown, in place');
  late.widget.setDataset({ id: OTHER, zarr: true });
  assert(!late.shown(), 'and gone when the page is another dataset');
}

console.log('\nthe click goes the card\'s way: the same import, a busy button, and the notebook opens as a tab of the dialog, which stays up');
{
  const page = await notebookPage({ storage: storedChat(goodRuns()) });
  const heard = [];
  page.widget.on('modechange', (event) => heard.push(event.mode));
  click(page, page.header());
  assertEqual(page.imports().length, 1, 'one import is posted');
  const [sent] = page.imports();
  assertEqual([sent.message.datasetId, sent.message.title, sent.targetOrigin], [DATASET, 'Count the onsets', NOTEBOOK_ORIGIN], 'with the chat\'s dataset and title, to the notebook\'s origin');
  const card = [...page.container.querySelectorAll('.osa-nb-entry-continue')];
  assertEqual(heard, [], 'the dialog did not dock: no modechange event');
  assert(page.container.classList.contains('osa-tab-notebook') && page.q('.osa-chat-window').classList.contains('osa-center'), 'the notebook tab opened, in the dialog');
  assert(card.length === 0 || card.every((c) => c.disabled), 'and the card\'s own button, if drawn, is waiting too');

  // Back on the chat tab (its tab clicked) with the import in flight: the button is busy, and a click does nothing.
  page.q('.osa-center-tab-chat').dispatchEvent(new page.window.Event('click', { bubbles: true }));
  assert(page.shown() && page.header().disabled && page.header().getAttribute('aria-busy') === 'true', 'back on the chat tab mid-import: the button is disabled with aria-busy');
  click(page, page.header());
  assertEqual(page.imports().length, 1, 'a click while it waits imports nothing more');
  page.say({ type: 'result', requestId: sent.message.requestId, ok: true, path: `nemar/${DATASET} · Count the onsets.ipynb` });
  await waitUntil(() => !page.header().disabled, 'the import ends');
  assert(page.header().getAttribute('aria-busy') === null, 'when it ends the button is ready again, not busy');

  // The failure: nothing is left waiting, and the button is there to try again.
  click(page, page.header());
  page.q('.osa-center-tab-chat').dispatchEvent(new page.window.Event('click', { bubbles: true }));
  assertEqual(page.imports().length, 2, 'a second click imports again');
  page.say({ type: 'result', requestId: page.imports()[1].message.requestId, ok: false, error: 'The notebook is busy.' });
  await waitUntil(() => !page.header().disabled, 'the failed import ends');
  assert(page.shown() && !page.header().disabled, 'after a failed import the button is ready to try again');
}

console.log('\nthe button is disabled while a reply loads, and a click then does nothing');
{
  const page = await notebookPage({ storage: storedChat(goodRuns()) });
  page.api.setLoading(true);
  page.api.renderMessages(page.container);
  assert(page.header().disabled, 'disabled while a reply is loading');
  click(page, page.header());
  assertEqual(page.imports().length, 0, 'a click imports nothing');
  page.api.setLoading(false);
  page.api.renderMessages(page.container);
  assert(!page.header().disabled, 'and ready when it is done');
}

clearTimeout(watchdog);
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
