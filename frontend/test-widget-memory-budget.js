/**
 * The browser memory budget and the heap report (E5.1 #668), run against the real widget.
 *
 * The reader picks 256 MB, 512 MB (the default), 1 GB or 2 GB per step in Settings. It is
 * stored with their settings, sent as `page_context.memory_budget_mb` on /chat and
 * /chat/resume of a page that can run code, and, after each run, the run card says how large
 * the WebAssembly heap was against it, warns by step when it passed it, and a reload or the
 * History sheet restores the line. The plan card warns when its largest step is expected to
 * pass the budget.
 *
 * What stands in: `fetch`, which answers with the SSE the test writes event by event, in the
 * shape the server sends; and the Python worker behind the runtime, the test worker the other
 * widget suites use, which reports a heap when the code says `HEAP_MB=<n>` (the real worker
 * reads Pyodide's memory: test-worker-core.js holds that half against real Pyodide). The
 * widget, the runtime bundle as built, the controller, the stream handler and the gate are
 * the real ones, in a happy-dom window. The first part tests the pure logic directly.
 *
 * Run with: bun frontend/test-widget-memory-budget.js
 */

import * as memory from './osa-memory.js';
import { CLIENT_TOOL_RESULT_FIELDS } from './osa-runtime.js';
import {
  PLAN_ARGS, SOURCE, approve, click, codeRequest, doneEvent, proposeFirst, send, sessionEvent, settled, start,
  text, waitUntil,
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
console.log('Widget: the browser memory budget (E5.1)');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// The pure logic
// ---------------------------------------------------------------------------

console.log('\nthe budgets: four sizes, 512 MB the default, anything else reads as the default');
{
  assertEqual([...memory.MEMORY_BUDGETS_MB], [256, 512, 1024, 2048], 'the four sizes the server accepts');
  assertEqual(memory.DEFAULT_MEMORY_BUDGET_MB, 512, 'the default is 512 MB');
  for (const size of memory.MEMORY_BUDGETS_MB) assertEqual(memory.normalizeBudgetMb(size), size, `${size} is kept`);
  for (const bad of [0, 100, 300, 511, 4096, -512, 512.5, '512', '1 GB', null, undefined, NaN, Infinity, true, [], {}]) {
    assertEqual(memory.normalizeBudgetMb(bad), 512, `${JSON.stringify(bad)} reads as 512`);
  }
  assertEqual(memory.MEMORY_BUDGETS_MB.map(memory.sizeLabel), ['256 MB', '512 MB', '1 GB', '2 GB'], 'the choices read 256 MB, 512 MB, 1 GB, 2 GB');
}

console.log('\nthe heap: whole MB from bytes, bounded, and nothing when the worker measured nothing');
{
  assertEqual(memory.heapMbFromBytes(312 * 1024 * 1024), 312, 'bytes become whole MB');
  assertEqual(memory.heapMbFromBytes(312.4 * 1024 * 1024), 312, 'rounded');
  assertEqual(memory.heapMbFromBytes(1), 1, 'a heap is at least 1 MB when there is one');
  assertEqual(memory.heapMbFromBytes(10 * 1024 * 1024 * 1024), memory.MAX_HEAP_MB, 'and never more than a 32-bit memory can hold');
  for (const none of [0, -5, NaN, Infinity, '300', null, undefined]) {
    assertEqual(memory.heapMbFromBytes(none), null, `${JSON.stringify(none)} is no heap`);
  }
  assertEqual(memory.normalizeHeapMb(312), 312, 'a stored heap is kept');
  for (const bad of [0, -1, 1.5, '300', null, undefined, NaN]) assertEqual(memory.normalizeHeapMb(bad), null, `${JSON.stringify(bad)} is not a stored heap`);
  assertEqual(memory.normalizeHeapMb(999999), memory.MAX_HEAP_MB, 'one past the limit is held to it');
}

console.log('\nheapReport and the warnings: the words, and who is named');
{
  assertEqual(memory.heapReport(312, 512), { text: 'Heap 312 MB of 512 MB', exceeded: false, heapMb: 312, budgetMb: 512 }, 'under the budget');
  assertEqual(memory.heapReport(512, 512).exceeded, false, 'at the budget is not over it');
  assertEqual(memory.heapReport(513, 512).exceeded, true, 'one MB past it is');
  assertEqual(memory.heapReport(700, 300).text, 'Heap 700 MB of 512 MB', 'a budget that is not one of the four reads as the default');
  assertEqual(memory.heapReport(null, 512), null, 'no heap, no line');
  assertEqual(memory.overBudgetWarning(memory.heapReport(300, 512), { step: 1, title: 'x' }), '', 'no warning under the budget');
  assertEqual(
    memory.overBudgetWarning(memory.heapReport(700, 512), { step: 2, title: 'Epoch the EEG' }),
    'Step 2, Epoch the EEG, used more than the 512 MB budget. Ask for smaller pieces, or raise the budget in Settings.',
    'a planned step is named, with its number and title'
  );
  assert(memory.overBudgetWarning(memory.heapReport(3000, 2048), { step: 1, title: '' }).startsWith('Step 1 used more than the 2 GB budget'), 'a step with no title is named by its number');
  assert(memory.overBudgetWarning(memory.heapReport(700, 512), { step: undefined, title: undefined }).startsWith('This run used more than'), 'a run no plan covers is "This run"');
  assertEqual(memory.planBudgetWarning(300, 512), '', 'an estimate under the budget warns of nothing');
  assertEqual(memory.planBudgetWarning(512, 512), '', 'nor one at it');
  assert(memory.planBudgetWarning(600, 512).startsWith('That is more than your 512 MB memory budget.'), 'one over it does');
  assertEqual(memory.planBudgetWarning(0, 512), '', 'a plan with no estimate has nothing to warn about');
  assertEqual(memory.planBudgetWarning(NaN, 512), '', 'nor an estimate that is not a number');
}

console.log('\nthe widget holds its own copy of the sizes, for a saved setting read before the bundle loads: the copies agree');
{
  const sizes = SOURCE.match(/const MEMORY_BUDGETS_MB = Object\.freeze\((\[[^\]]*\])\)/);
  assert(sizes !== null, 'the widget declares its sizes');
  assertEqual(JSON.parse(sizes[1]), [...memory.MEMORY_BUDGETS_MB], 'the same sizes as osa-memory.js');
  assertEqual(Number(SOURCE.match(/const DEFAULT_MEMORY_BUDGET_MB = (\d+)/)[1]), memory.DEFAULT_MEMORY_BUDGET_MB, 'the same default');
  assertEqual(Number(SOURCE.match(/const MAX_HEAP_MB = (\d+)/)[1]), memory.MAX_HEAP_MB, 'the same heap limit');
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

const SETTINGS_KEY = 'osa-settings-test';
// The chat is opened the first time only: the launcher toggles it.
const open = (page) => {
  if (!page.opened) {
    click(page, page.q('.osa-chat-button'));
    page.opened = true;
  }
  click(page, page.q('.osa-settings-btn-open'));
};
const radios = (page) => page.qa('.osa-memory-radio');
const chosen = (page) => radios(page).filter((r) => r.checked).map((r) => Number(r.value));
const choose = (page, size) => {
  const radio = radios(page).find((r) => Number(r.value) === size);
  radio.checked = true;
  radio.dispatchEvent(new page.window.Event('change', { bubbles: true }));
};
const hidden = (page) => page.q('.osa-memory-field').style.display === 'none';

console.log('\nthe Browser memory field: four 44 px choices after the model fields, 512 MB chosen');
{
  const page = await start();
  open(page);
  const field = page.q('.osa-memory-field');
  assert(!hidden(page), 'shown on a page that can run code');
  assertEqual(text(field.querySelector('legend')), 'Browser memory', 'its legend names it');
  assertEqual(page.qa('.osa-memory-choice').map(text), ['256 MB', '512 MB', '1 GB', '2 GB'], 'four choices, in order');
  assertEqual(chosen(page), [512], '512 MB is chosen for a reader who never chose');
  assert(page.q('.osa-memory-choice-on') && text(page.q('.osa-memory-choice-on')) === '512 MB', 'and marked');
  const fields = [...page.q('.osa-settings-body').children];
  assert(fields.indexOf(page.q('#osa-settings-api-key-field')) < fields.indexOf(field), 'it comes after the model and key fields');
  assert(fields.indexOf(field) < fields.indexOf(page.q('.osa-workspace-field')), 'and before the workspace');
  assert(text(field).includes('One array cannot exceed 2 GB in the browser'), 'it says what the budget is for and the 2 GB array limit');
  assertEqual(radios(page).every((r) => r.getAttribute('name') === 'osa-settings-memory'), true, 'the four are one radio group');
  const css = SOURCE.match(/\.osa-memory-choice \{[^}]*\}/)[0];
  assert(/min-height:\s*44px/.test(css), 'each choice is 44 px high');
  assertEqual(page.q('.osa-settings-footer').textContent.replace(/\s+/g, ' ').trim(), 'Cancel Save', 'Cancel and Save are as before');
}

console.log('\non a page with no browser runtime the field is out of the way');
{
  const page = await start({ bundle: 'none' });
  open(page);
  assert(hidden(page), 'hidden');
}

console.log('\nSave stores the choice; Cancel does not; a reload reads it back');
{
  const page = await start();
  open(page);
  choose(page, 1024);
  assertEqual(text(page.q('.osa-memory-choice-on')), '1 GB', 'the choice is marked as it is made');
  click(page, page.q('.osa-settings-btn-cancel'));
  assertEqual(page.window.localStorage.getItem(SETTINGS_KEY), null, 'Cancel stored nothing');
  open(page);
  assertEqual(chosen(page), [512], 'and the dialog opens on what is saved, not on the cancelled choice');
  choose(page, 1024);
  click(page, page.q('.osa-settings-btn-save'));
  assert(!page.q('.osa-settings-overlay').classList.contains('open'), 'Save closes the dialog');
  assertEqual(page.storedSettings().memoryBudgetMb, 1024, 'the choice is stored beside the other settings');
  assertEqual(page.widget.__settings.get().memoryBudgetMb, 1024, 'and in effect now');
  const again = await start({ settings: page.window.localStorage.getItem(SETTINGS_KEY) });
  open(again);
  assertEqual(chosen(again), [1024], 'a reload reads 1 GB back');
}

console.log('\na stored budget that is not one of the four reads as 512 MB, with no notice');
{
  for (const bad of [300, 0, -1, 4096, '1024', 'big', null, 512.5, [], {}, true]) {
    const page = await start({ settings: { apiKey: null, model: null, memoryBudgetMb: bad } });
    assertEqual(page.widget.__settings.get().memoryBudgetMb, 512, `${JSON.stringify(bad)} reads as 512`);
    open(page);
    assertEqual(chosen(page), [512], `and the dialog shows 512 MB for ${JSON.stringify(bad)}`);
    assert(!/corrupt|invalid/i.test(page.q('.osa-chat-messages').textContent), `with no notice for ${JSON.stringify(bad)}`);
  }
  const older = await start({ settings: { apiKey: null, model: null, keyProvider: null } });
  assertEqual(older.widget.__settings.get().memoryBudgetMb, 512, 'settings saved before the budget existed read as 512');
  for (const size of memory.MEMORY_BUDGETS_MB) {
    const page = await start({ settings: { memoryBudgetMb: size } });
    assertEqual(page.widget.__settings.get().memoryBudgetMb, size, `${size} is kept`);
  }
  const corrupt = await start({ settings: '{not json' });
  assertEqual(corrupt.widget.__settings.get().memoryBudgetMb, 512, 'corrupt settings read as the defaults, 512 MB included');
}

// ---------------------------------------------------------------------------
// The request
// ---------------------------------------------------------------------------

console.log('\n/chat and /chat/resume carry the budget when the page has the runtime');
{
  const page = await start({ settings: { memoryBudgetMb: 2048 } });
  const first = page.queue();
  send(page, 'plot the ERP');
  first.send(sessionEvent());
  first.send(codeRequest('c1', 'print(1)'));
  first.close();
  await waitUntil(() => page.api.getToolActivity() && page.api.getToolActivity().phase === 'asking', 'the gate asks');
  assertEqual(page.requests[0].body.page_context, { memory_budget_mb: 2048 }, '/chat carries it, and only it when no page is shared');
  const second = page.queue();
  click(page, page.q('.osa-tool-run'));
  await waitUntil(() => page.requests.length === 2, 'the result is posted');
  assertEqual(page.requests[1].kind, 'resume', 'the second request is /chat/resume');
  assertEqual(page.requests[1].body.page_context, { memory_budget_mb: 2048 }, '/chat/resume carries it too');
  second.send(doneEvent('Ran.'));
  second.close();
  await waitUntil(() => settled(page), 'the reply ends');
}

console.log('\nit rides beside the page context the reader shares, and follows a change in Settings');
{
  const page = await start({ allowPageContext: true });
  open(page);
  choose(page, 256);
  click(page, page.q('.osa-settings-btn-save'));
  const first = page.queue();
  send(page, 'hello');
  first.send(sessionEvent());
  first.send(doneEvent('Hi.'));
  first.close();
  await waitUntil(() => settled(page), 'the reply ends');
  const context = page.requests[0].body.page_context;
  assertEqual(context.memory_budget_mb, 256, 'the new budget is sent');
  assert(typeof context.url === 'string' && context.url.startsWith('http'), 'beside the page URL');
}

console.log('\nit is not sent from a page with no runtime, with the old bundle rule kept: no page context when nothing else is set');
{
  const page = await start({ bundle: 'none' });
  const first = page.queue();
  send(page, 'hello');
  first.send(sessionEvent());
  first.send(doneEvent('Hi.'));
  first.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assert(!('page_context' in page.requests[0].body), 'the body has no page_context at all');
  assert(!('client_tools' in page.requests[0].body), 'and declares no tools');
}

// ---------------------------------------------------------------------------
// The run card
// ---------------------------------------------------------------------------

const asking = (page) => page.api.getToolActivity() && page.api.getToolActivity().phase === 'asking';
const heapLine = (page) => page.qa('.osa-run-card-heap').map(text);
const warnings = (page) => page.qa('.osa-run-card-memory-warning').map(text);

/** One run the reader approves, with its result posted and the reply ended. */
async function runOnce(page, code, description = 'Run the step') {
  const stream = page.queue();
  const before = page.requests.length;
  send(page, 'do it');
  stream.send(sessionEvent());
  stream.send(codeRequest(`call-${before}`, code, description));
  stream.close();
  await waitUntil(() => asking(page), 'the gate asks');
  const resumed = page.queue();
  click(page, page.q('.osa-tool-run'));
  await waitUntil(() => page.requests.length === before + 2, 'the result is posted');
  resumed.send(doneEvent('Done.'));
  resumed.close();
  await waitUntil(() => settled(page), 'the reply ends');
}

console.log('\nthe run card says "Heap N MB of B MB", and a run under the budget has no warning');
{
  const page = await start();
  await runOnce(page, '# HEAP_MB=312\nprint(1)');
  assertEqual(heapLine(page), ['Heap 312 MB of 512 MB'], 'the heap against the default budget');
  assertEqual(warnings(page), [], 'no warning');
  assert(!page.q('.osa-run-card-heap-over'), 'and no warning style');
  const result = page.requests[1].body.result;
  assert(Object.keys(result).every((key) => CLIENT_TOOL_RESULT_FIELDS.includes(key)), `the result sent to the server has only ClientToolResult fields (${Object.keys(result).join(', ')})`);
  assert(!JSON.stringify(page.requests[1].body).includes('heap'), 'nothing about the heap reaches the server');
  const run = page.api.getMessages().find((m) => m.executions && m.executions.length).executions[0];
  assertEqual([run.heapMb, run.budgetMb], [312, 512], 'the run record keeps the heap and the budget it ran under');
}

console.log('\na run with no reported heap has no line');
{
  const page = await start();
  await runOnce(page, 'print(1)');
  assertEqual(heapLine(page), [], 'no heap, no line');
  assertEqual(warnings(page), [], 'and no warning');
}

console.log('\na run over the budget gets the warning style and a line naming the run');
{
  const page = await start({ settings: { memoryBudgetMb: 256 } });
  await runOnce(page, '# HEAP_MB=300\nprint(1)');
  assertEqual(heapLine(page), ['Heap 300 MB of 256 MB'], 'the heap against the chosen budget');
  assert(page.q('.osa-run-card-heap-over'), 'in the warning style');
  assertEqual(
    warnings(page),
    ['This run used more than the 256 MB budget. Ask for smaller pieces, or raise the budget in Settings.'],
    'and a line under it'
  );
}

console.log('\na planned step is named, by number and title');
{
  const page = await start();
  await proposeFirst(page);
  const stream = await approve(page, { runAll: true });
  const next = page.queue();
  stream.send(codeRequest('s1', '# HEAP_MB=700\nprint(1)', 'Count the onsets'));
  stream.close();
  await waitUntil(() => page.requests.length === 3, 'step 1 is answered');
  next.send(doneEvent('Step 1 is done.'));
  next.close();
  await waitUntil(() => settled(page), 'the reply ends');
  assertEqual(heapLine(page), ['Heap 700 MB of 512 MB'], 'the heap line');
  assertEqual(
    warnings(page),
    [`Step 1, ${PLAN_ARGS.steps[0].title}, used more than the 512 MB budget. Ask for smaller pieces, or raise the budget in Settings.`],
    'the warning names step 1 and its title'
  );
}

console.log('\nreload and History restore the line, as it was, whatever the budget is now');
{
  const page = await start({ settings: { memoryBudgetMb: 1024 } });
  await runOnce(page, '# HEAP_MB=900\nprint(1)');
  assertEqual(heapLine(page), ['Heap 900 MB of 1024 MB'], 'the line reads the budget in MB, as the plan card does');
  const stored = page.stored();
  const storedRun = JSON.stringify(stored).match(/"heapMb":(\d+),"budgetMb":(\d+)/);
  assert(storedRun && storedRun[1] === '900' && storedRun[2] === '1024', 'the record in storage holds the heap and the budget');

  // Reload, with the reader now on a different budget.
  const reloaded = await start({
    storage: page.window.localStorage.getItem(page.storageKey),
    storageKey: page.storageKey,
    settings: { memoryBudgetMb: 256 },
  });
  await waitUntil(() => reloaded.qa('.osa-run-card').length > 0, 'the restored chat shows its run');
  assertEqual(heapLine(reloaded), ['Heap 900 MB of 1024 MB'], 'a reload restores the line, against the budget the run had');
  assertEqual(warnings(reloaded), [], 'and no warning, since it fit that budget');

  // History: start a new chat from the sheet, then open the earlier one from it.
  click(reloaded, reloaded.q('.osa-history-btn'));
  click(reloaded, reloaded.q('.osa-history-new'));
  assertEqual(reloaded.qa('.osa-run-card').length, 0, 'a new chat has no card');
  click(reloaded, reloaded.q('.osa-history-btn'));
  const row = reloaded.qa('.osa-history-open').find((el) => el.getAttribute('aria-current') !== 'true');
  assert(row !== undefined, 'the sheet lists the earlier chat');
  click(reloaded, row);
  await waitUntil(() => reloaded.qa('.osa-run-card').length > 0, 'History opens the chat with its run');
  assertEqual(heapLine(reloaded), ['Heap 900 MB of 1024 MB'], 'History restores the line too');
}

console.log('\na stored heap that is not a whole number of MB is dropped, not drawn');
{
  const page = await start();
  await runOnce(page, '# HEAP_MB=300\nprint(1)');
  const record = JSON.parse(page.window.localStorage.getItem(page.storageKey));
  const text_ = JSON.stringify(record).replace('"heapMb":300', '"heapMb":"300"');
  const reloaded = await start({ storage: text_, storageKey: page.storageKey });
  await waitUntil(() => reloaded.qa('.osa-run-card').length > 0, 'the restored chat shows its run');
  assertEqual(heapLine(reloaded), [], 'no line from a value that is not a number');
}

// ---------------------------------------------------------------------------
// The plan card
// ---------------------------------------------------------------------------

console.log('\nthe plan card keeps "Largest step needs about N MB" and warns when it passes the budget');
{
  const under = await start();
  await proposeFirst(under);
  assertEqual(text(under.q('.osa-plan-top .osa-plan-meta')), 'Largest step needs about 300 MB', 'the line is as it was');
  assertEqual(under.qa('.osa-plan-meta-warn').length, 0, '300 MB under a 512 MB budget: no warning');

  const over = await start({ settings: { memoryBudgetMb: 256 } });
  await proposeFirst(over);
  assertEqual(text(over.q('.osa-plan-top .osa-plan-meta')), 'Largest step needs about 300 MB', 'the line is still there');
  assertEqual(
    over.qa('.osa-plan-meta-warn').map(text),
    ['That is more than your 256 MB memory budget. Edit the steps to read in smaller pieces, or raise the budget in Settings.'],
    'and says it is more than the 256 MB budget'
  );
  const resumed = await approve(over);
  resumed.send(doneEvent('Ready.'));
  resumed.close();
  await waitUntil(() => settled(over), 'the reply ends');
  assertEqual(over.qa('.osa-plan-meta-warn').length, 0, 'once decided, the card no longer warns');
}

console.log(`\nTotal: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
process.exit(failed === 0 ? 0 : 1);
