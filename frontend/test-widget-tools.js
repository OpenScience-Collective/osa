/**
 * The widget's browser-execution logic (#431 step 7), run for real.
 *
 * The widget source runs unmodified inside a happy-dom window, so escaping goes
 * through a real HTML serializer and parser rather than a stand-in written for
 * the test: a stand-in that escaped would make every escaping test pass by
 * construction. What stands in is the platform only: `fetch` answers with HTTP
 * fixtures, and the steps of a browser turn are passed to the loop that owns
 * the budget, which is the logic under test there.
 *
 * Run with: bun frontend/test-widget-tools.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { RUNTIME_STATE } from './osa-runtime.js';
import { WorkspaceStore } from './osa-workspace.js';

let passed = 0;
let failed = 0;

const SUITE_TIMEOUT_MS = 60_000;
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

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');

function noNetwork(url) {
  return Promise.reject(new Error(`unexpected request in a unit test: ${url}`));
}

/**
 * The widget, evaluated in its own window. Nothing initializes on its own:
 * the script tag carries data-no-auto-init, as an embedder's may.
 */
function loadWidget({
  scriptSrc = 'http://localhost/static/osa-chat-widget.js',
  fetch = noNetwork,
  bundleLoads = false,
} = {}) {
  // Nothing is ever fetched. A script element the widget adds fires `error`,
  // as a refused bundle does, or `load` when a test needs the bundle to arrive.
  const window = new Window({
    url: 'http://localhost/page',
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      handleDisabledFileLoadingAsSuccess: bundleLoads,
    },
  });
  window.__OSA_TEST__ = true;
  const script = window.document.createElement('script');
  if (scriptSrc) script.setAttribute('src', scriptSrc);
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  return { window, api: window.OSAChatWidget.__browser, widget: window.OSAChatWidget };
}

/**
 * Parse generated HTML with the real parser and report anything that is not
 * markup the widget itself writes. This is the property that matters for
 * innerHTML: whatever the content, the only elements and attributes that exist
 * afterwards are ours.
 */
function markupProblems(window, html, allowedTags) {
  const holder = window.document.createElement('div');
  holder.innerHTML = html;
  const problems = [];
  for (const element of holder.querySelectorAll('*')) {
    const tag = element.tagName.toLowerCase();
    if (!allowedTags.includes(tag)) problems.push(`unexpected <${tag}>`);
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith('on')) problems.push(`event handler ${name} on <${tag}>`);
      if (name === 'src' && !/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(attribute.value)) {
        problems.push(`src ${JSON.stringify(attribute.value.slice(0, 60))}`);
      }
      if ((name === 'href' || name === 'style' || name === 'srcdoc')) problems.push(`${name} on <${tag}>`);
    }
  }
  return { problems, holder };
}

const HOSTILE = [
  '<img src=x onerror=alert(1)>',
  '</code></pre><script>alert(1)</script>',
  '"><svg onload=alert(1)>',
  "' onmouseover='alert(1)",
  '&lt;already escaped&gt; & raw',
  '</summary></details><iframe srcdoc="x">',
];

console.log('='.repeat(60));
console.log('Widget: browser execution');
console.log('='.repeat(60));

console.log('\nthe widget stops at the run count the server enforces');
{
  const { api } = loadWidget();
  const limits = readFileSync(new URL('../src/core/limits.py', import.meta.url), 'utf8');
  const server = Number((limits.match(/^MAX_BROWSER_RUNS_PER_REPLY = (\d+)/m) || [])[1]);
  assertEqual(api.MAX_BROWSER_RUNS_PER_REPLY, server, 'MAX_BROWSER_RUNS_PER_REPLY matches src/core/limits.py');
}

console.log('\na run record puts only our markup on the page, whatever it holds');
{
  const { window, api } = loadWidget();
  const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
  const runs = HOSTILE.map((text, i) => ({
    callId: `c${i}`,
    tool: 'execute_code',
    description: text,
    code: `print(${JSON.stringify(text)})`,
    status: i === 0 ? '__proto__' : 'error',
    stdout: text,
    stderr: text,
    images: [
      { mime: 'image/png', data_base64: png, width: 1, height: 1 },
      { mime: 'image/svg+xml', data_base64: png, width: 1, height: 1 },
      { mime: 'image/png', data_base64: `${png}" onerror="alert(1)`, width: 1, height: 1 },
      { mime: 'image/png', data_base64: 'javascript:alert(1)', width: 1, height: 1 },
      null,
    ],
  }));
  const html = api.executionsHtml(runs);
  const { problems, holder } = markupProblems(window, html, ['details', 'summary', 'pre', 'code', 'img', 'span']);
  assertEqual(problems, [], 'no foreign element, handler or source survives parsing');
  const summaries = Array.from(holder.querySelectorAll('summary')).map((s) => s.textContent);
  assertEqual(summaries[1], `Python raised an error: ${HOSTILE[1]}`, 'hostile text comes back as the same text');
  assert(summaries[0].startsWith('Python: '), 'a status that names an Object.prototype key gets the plain label');
  assertEqual(holder.querySelectorAll('img').length, HOSTILE.length, 'exactly one image per run survives: the real PNG');
  const outputs = Array.from(holder.querySelectorAll('.osa-execution-output')).map((p) => p.textContent);
  assert(outputs.includes(HOSTILE[0]), 'printed output is shown as text');
}

console.log('\nthe permission gate puts only our markup on the page, whatever it asks');
{
  const { window, api } = loadWidget();
  for (const text of HOSTILE) {
    const asking = api.toolPanelHtml({ phase: 'asking', prompt: { code: text, description: text }, decide() {} });
    const running = api.toolPanelHtml({ phase: 'running', prompt: { code: text, description: text }, progress: { text, step: null, steps: null } });
    for (const [label, html] of [['asking', asking], ['running', running]]) {
      const { problems, holder } = markupProblems(window, html,
        ['div', 'pre', 'code', 'button', 'label', 'input', 'span']);
      assert(problems.length === 0, `${label}: ${JSON.stringify(text.slice(0, 24))} leaves no foreign markup${problems.length ? ` (${problems.join('; ')})` : ''}`);
      const description = holder.querySelector('.osa-tool-panel-description');
      assert(description && description.textContent === text, `${label}: the description reads back unchanged`);
    }
  }
  assertEqual(api.toolPanelHtml(null), '', 'no activity, no panel');
}

// ---------------------------------------------------------------------------
// The editable re-run panel: "Edit and run" on a recorded run.
// ---------------------------------------------------------------------------

function loadWidgetWithRealBundle() {
  const loaded = loadWidget({ bundleLoads: true });
  // eslint-disable-next-line no-new-func
  new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
  loaded.window.OSARuntime = globalThis.OSARuntime;
  return loaded;
}

console.log('\n"Edit and run" only appears once the runtime exists on the page');
{
  const { window, api } = loadWidget();
  const run = {
    callId: 'call-1', tool: 'execute_code', description: 'load the file', code: 'print(1)',
    status: 'ok', stdout: '', stderr: '', images: [],
  };
  const html = api.executionsHtml([run], 0);
  const holder = window.document.createElement('div');
  holder.innerHTML = html;
  assert(!holder.querySelector('.osa-rerun-open'), 'no client tools declared: no "Edit and run" affordance at all');
}

console.log('\nthe editor opens prefilled with the run\'s code, and escapes hostile content');
{
  const { window, api } = loadWidgetWithRealBundle();
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.29.5', preload: [], fetch_allow: [], limits: {} } },
  });
  await api.declaredClientTools();
  assert(api.canRunLocalCode(), 'the runtime exists on this page now');

  const hostileCode = '</textarea><script>alert(1)</script>';
  const run = {
    callId: 'call-2', tool: 'execute_code', description: 'load the file', code: hostileCode,
    status: 'ok', stdout: '', stderr: '', images: [],
  };
  const openHtml = api.executionsHtml([run], 2);
  const { problems: openProblems, holder: openHolder } = markupProblems(window, openHtml,
    ['details', 'summary', 'pre', 'code', 'div', 'button', 'span']);
  assertEqual(openProblems, [], 'the closed record leaves no foreign markup');
  const openBtn = openHolder.querySelector('.osa-rerun-open');
  assert(openBtn, 'the "Edit and run" button is offered');
  assertEqual(openBtn.getAttribute('data-msg-index'), '2', 'naming the reply it belongs to');
  assertEqual(openBtn.getAttribute('data-run-index'), '0', 'and the run within it');

  const editingRun = { ...run, _editing: true };
  const editorHtml = api.executionsHtml([editingRun], 2);
  const { problems: editorProblems, holder: editorHolder } = markupProblems(window, editorHtml,
    ['details', 'summary', 'pre', 'code', 'div', 'label', 'textarea', 'button', 'span']);
  assertEqual(editorProblems, [], 'the open editor leaves no foreign markup either, for hostile code');
  const textarea = editorHolder.querySelector('.osa-rerun-textarea');
  assert(textarea, 'the editor has a textarea');
  assertEqual(textarea.value, hostileCode, 'prefilled with the run\'s code, read back exactly as it was, unescaped');
  assert(!editorHolder.querySelector('.osa-rerun-open'), 'no "Edit and run" button while it is already open');
  assert(editorHolder.querySelector('.osa-rerun-run'), 'a Run control is present');
  assert(editorHolder.querySelector('.osa-rerun-cancel'), 'and a Cancel control');
}

console.log('\nRun is disabled, and says why, while the runtime is already answering the assistant');
{
  const { window, api } = loadWidgetWithRealBundle();
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.29.5', preload: [], fetch_allow: [], limits: {} } },
  });
  await api.declaredClientTools();

  const container = window.document.createElement('div');
  container.className = 'osa-chat-widget';
  container.innerHTML = '<div class="osa-chat-messages"></div>';
  window.document.body.appendChild(container);

  const answering = api.getBrowserTools().answer({
    call_id: 'busy-1', tool: 'execute_code', args: { code: 'print(1)', description: 'd' }, requires_permission: true,
  });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assertEqual(api.getBrowserTools().busy, true, 'the controller reports busy while the gate is showing');

  const run = {
    callId: 'call-3', tool: 'execute_code', description: 'x', code: 'print(2)',
    status: 'ok', stdout: '', stderr: '', images: [], _editing: true,
  };
  const html = api.executionsHtml([run], 0);
  const holder = window.document.createElement('div');
  holder.innerHTML = html;
  const runBtn = holder.querySelector('.osa-rerun-run');
  assert(runBtn && runBtn.hasAttribute('disabled'), 'Run is disabled while the runtime is busy');
  const note = holder.querySelector('.osa-rerun-note');
  assert(note && /busy/i.test(note.textContent), 'and says why');

  const asking = api.getToolActivity();
  asking.decide(window.OSARuntime.GATE_DECISION.DENY, false);
  await answering;
  assertEqual(api.getBrowserTools().busy, false, 'free again once the assistant\'s call is answered');

  const htmlAfter = api.executionsHtml([run], 0);
  const holderAfter = window.document.createElement('div');
  holderAfter.innerHTML = htmlAfter;
  const runBtnAfter = holderAfter.querySelector('.osa-rerun-run');
  assert(runBtnAfter && !runBtnAfter.hasAttribute('disabled'), 'and Run is enabled again once the runtime is free');
}

console.log('\na finished local run renders labeled plainly as the reader\'s own, open by default');
{
  const { window, api } = loadWidget();
  const run = {
    callId: 'person-run-1', tool: 'execute_code', description: 'tweak the seed', code: 'print("mine")',
    status: 'ok', stdout: 'mine\n', stderr: '', images: [], local: true,
  };
  const html = api.executionsHtml([run], 0);
  const holder = window.document.createElement('div');
  holder.innerHTML = html;
  const details = holder.querySelector('details.osa-execution');
  assert(details && details.hasAttribute('open'), 'a local run is expanded by default, not collapsed');
  const summary = holder.querySelector('summary');
  assert(summary && summary.textContent.startsWith('Your run: '), 'labeled as the reader\'s own in the summary');
  const note = holder.querySelector('.osa-execution-local-note');
  assert(note && note.textContent === 'This run is yours. The assistant has not seen it.', 'and said plainly in the body too');
  const output = holder.querySelector('.osa-execution-output');
  assert(output && output.textContent === 'mine\n', 'its stdout is shown, bounded the way any recorded run\'s already is');
}

console.log('\na local run is kept in the reply\'s runs and survives reloading the stored conversation');
{
  const { window, api } = loadWidget();
  const localRun = {
    callId: 'person-run-7', tool: 'execute_code', description: 'edited', code: 'print(1)',
    status: 'ok', stdout: '1\n', stderr: '', images: [{ mime: 'image/png', data_base64: 'iVBORw0KGgo=' }],
    local: true, _editing: true, _draft: 'print(1)  # draft', _runningLocal: false,
    _localResult: { callId: 'person-run-7', status: 'ok', stdout: '1\n', stderr: '', images: [] },
  };
  api.setMessages([{ role: 'assistant', content: 'here you go', executions: [localRun] }]);
  api.saveHistory();

  const raw = JSON.parse(window.localStorage.getItem(api.getConfig().storageKey));
  const savedRun = raw.messages[0].executions[0];
  assert(
    !('_editing' in savedRun) && !('_draft' in savedRun) && !('_runningLocal' in savedRun) && !('_localResult' in savedRun),
    'every transient editing field is stripped before saving'
  );
  assertEqual(savedRun.local, true, 'local survives the save');
  assertEqual(savedRun.images, [], 'images are not persisted, the same as any other run');

  api.setMessages([]);
  const needsSave = api.loadHistory();
  const reloaded = api.getMessages();
  assertEqual(reloaded[0].executions[0].local, true, 'local survives being read back too');
  assert(!('_editing' in reloaded[0].executions[0]), 'and no transient field comes back either');
  assert(!needsSave, 'a clean, current-version save needs no immediate re-save');
}

console.log('\nCancel closes the editor and the run renders exactly as it did before editing began');
{
  const { window, api } = loadWidgetWithRealBundle();
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.29.5', preload: [], fetch_allow: [], limits: {} } },
  });
  await api.declaredClientTools();

  const run = {
    callId: 'call-9', tool: 'execute_code', description: 'x', code: 'print(9)',
    status: 'ok', stdout: '9\n', stderr: '', images: [],
  };
  api.setMessages([{ role: 'assistant', content: 'hi' }, { role: 'assistant', content: 'ok', executions: [run] }]);

  const container = window.document.createElement('div');
  container.className = 'osa-chat-widget';
  container.innerHTML = '<div class="osa-chat-messages"></div>';
  window.document.body.appendChild(container);

  api.renderMessages(container);
  const before = container.querySelector('.osa-chat-messages').innerHTML;

  const openBtn = container.querySelector('.osa-rerun-open');
  assert(openBtn, 'the "Edit and run" button is rendered');
  openBtn.dispatchEvent(new window.Event('click', { bubbles: true }));
  assertEqual(api.getMessages()[1].executions[0]._editing, true, 'clicking it opens the editor');

  const textarea = container.querySelector('.osa-rerun-textarea');
  textarea.value = 'print("something else")';
  textarea.dispatchEvent(new window.Event('input', { bubbles: true }));
  assertEqual(api.getMessages()[1].executions[0]._draft, 'print("something else")', 'typing updates the draft');

  const cancelBtn = container.querySelector('.osa-rerun-cancel');
  assert(cancelBtn, 'a Cancel control is present while editing');
  cancelBtn.dispatchEvent(new window.Event('click', { bubbles: true }));

  const runAfter = api.getMessages()[1].executions[0];
  assert(!('_editing' in runAfter) && !('_draft' in runAfter), 'Cancel discards the edit');
  const after = container.querySelector('.osa-chat-messages').innerHTML;
  assertEqual(after, before, 'and the record renders exactly as it did before editing began');
}

console.log('\na determinate progress bar tracks a real boot sequence, and never jumps');
{
  const { window, api } = loadWidget();

  // Nothing to show yet: runningActivity() starts with progress: null, and
  // the panel falls back to a plain label with no bar at all.
  api.setToolActivity(api.runningActivity({ code: 'x = 1', description: 'set x' }));
  {
    const holder = window.document.createElement('div');
    holder.innerHTML = api.toolPanelHtml(api.getToolActivity());
    assertEqual(holder.querySelector('.osa-tool-progress'), null, 'no bar before any progress event arrives');
    assertEqual(holder.querySelector('.osa-tool-status').textContent, 'Running Python in your browser...',
      'a generic label stands in until the worker reports something');
  }

  // The real sequence a preload-only boot sends (frontend/test-worker-core.js
  // proves the worker emits exactly this): loading_runtime and runtime_loaded
  // share step 1 (the interpreter), then each preload name advances by one,
  // ending at step === steps.
  const sequence = [
    { phase: 'loading_runtime', step: 1, steps: 3 },
    { phase: 'runtime_loaded', step: 1, steps: 3 },
    { phase: 'loading_package', package: 'numpy', step: 2, steps: 3 },
    { phase: 'loading_package', package: 'pandas', step: 3, steps: 3 },
  ];
  const seen = [];
  for (const event of sequence) {
    api.onRuntimeProgress(event);
    const activity = api.getToolActivity();
    const holder = window.document.createElement('div');
    holder.innerHTML = api.toolPanelHtml(activity);
    const bar = holder.querySelector('.osa-tool-progress');
    seen.push({
      phase: event.phase,
      step: activity.progress.step,
      steps: activity.progress.steps,
      role: bar && bar.getAttribute('role'),
      valuemin: bar && bar.getAttribute('aria-valuemin'),
      valuemax: bar && bar.getAttribute('aria-valuemax'),
      valuenow: bar && bar.getAttribute('aria-valuenow'),
      hasName: Boolean(bar && bar.getAttribute('aria-label')),
      fillWidth: bar && bar.querySelector('.osa-tool-progress-fill').style.width,
    });
  }
  assertEqual(seen, [
    { phase: 'loading_runtime', step: 1, steps: 3, role: 'progressbar', valuemin: '0', valuemax: '3', valuenow: '1', hasName: true, fillWidth: '33%' },
    { phase: 'runtime_loaded', step: 1, steps: 3, role: 'progressbar', valuemin: '0', valuemax: '3', valuenow: '1', hasName: true, fillWidth: '33%' },
    { phase: 'loading_package', step: 2, steps: 3, role: 'progressbar', valuemin: '0', valuemax: '3', valuenow: '2', hasName: true, fillWidth: '67%' },
    { phase: 'loading_package', step: 3, steps: 3, role: 'progressbar', valuemin: '0', valuemax: '3', valuenow: '3', hasName: true, fillWidth: '100%' },
  ], 'steps stays 3 throughout, step never resets, and the bar ends full');

  // runtime_loaded, which used to render nothing at all, now carries its own label.
  api.setToolActivity(api.runningActivity({ code: 'x', description: '' }));
  api.onRuntimeProgress({ phase: 'runtime_loaded', step: 1, steps: 2 });
  const loadedStatus = api.getToolActivity().progress.text;
  assert(/downloads once/.test(loadedStatus) && !/\d+\s*(MB|kB|bytes)/i.test(loadedStatus),
    `runtime_loaded gets a reassuring label with no size figure, got ${JSON.stringify(loadedStatus)}`);

  // A progress event reaching the widget without step/steps (an older worker,
  // or a message this code does not otherwise recognize) degrades to the
  // label alone, never to a broken or stale bar.
  api.setToolActivity(api.runningActivity({ code: 'x', description: '' }));
  api.onRuntimeProgress({ phase: 'loading_runtime' });
  {
    const activity = api.getToolActivity();
    assertEqual([activity.progress.step, activity.progress.steps], [null, null], 'no step/steps means no bar, not a guess');
    const holder = window.document.createElement('div');
    holder.innerHTML = api.toolPanelHtml(activity);
    assertEqual(holder.querySelector('.osa-tool-progress'), null, 'and none is rendered');
  }

  // The gate: onRuntimeProgress only ever touches a RUNNING activity. Asking
  // and no-activity are both left alone, so a progress event that arrives
  // late (after the person already answered, or before) cannot resurrect or
  // corrupt a panel nobody is looking at.
  api.setToolActivity(null);
  api.onRuntimeProgress({ phase: 'loading_runtime', step: 1, steps: 1 });
  assertEqual(api.getToolActivity(), null, 'a progress event with no running activity is a no-op');

  api.setToolActivity({ phase: 'asking', prompt: { code: '', description: '' }, decide() {} });
  api.onRuntimeProgress({ phase: 'loading_runtime', step: 1, steps: 1 });
  assertEqual(api.getToolActivity().phase, 'asking', 'and never overwrites the asking panel either');

  // Once the runtime leaves `booting` the boot is over, whether it finished,
  // failed, was stopped or was recycled, so its last label and bar stop
  // describing anything. Every state the runtime declares is walked, so a new
  // one cannot arrive unconsidered.
  assertEqual(Object.values(RUNTIME_STATE).sort(), ['booting', 'failed', 'idle', 'ready', 'terminated'],
    'the states this test walks are the ones the runtime declares');
  api.setToolActivity(api.runningActivity({ code: 'x', description: '' }));
  api.onRuntimeProgress({ phase: 'prelude', step: 3, steps: 3 });
  api.onRuntimeStateChange('booting');
  assert(api.getToolActivity().progress !== null, 'booting leaves the boot progress in place');
  for (const state of ['ready', 'failed', 'terminated', 'idle']) {
    api.setToolActivity(api.runningActivity({ code: 'x', description: '' }));
    api.onRuntimeProgress({ phase: 'prelude', step: 3, steps: 3 });
    api.onRuntimeStateChange(state);
    const holder = window.document.createElement('div');
    holder.innerHTML = api.toolPanelHtml(api.getToolActivity());
    assertEqual(holder.querySelector('.osa-tool-progress'), null, `${state} clears the bar`);
    assertEqual(holder.querySelector('.osa-tool-status').textContent, 'Running Python in your browser...',
      `and after ${state} the panel says the code is running`);
  }
  api.setToolActivity({ phase: 'asking', prompt: { code: '', description: '' }, decide() {} });
  api.onRuntimeStateChange('ready');
  api.onRuntimeStateChange('failed');
  assertEqual(api.getToolActivity().phase, 'asking', 'a state change never touches the asking panel');

  // A step or step count that is not a positive integer, or a step past the
  // count, draws no bar: the label still shows, and a wrong bar is worse than
  // none.
  const malformed = [
    { step: 5, steps: 3 },
    { step: '2', steps: 3 },
    { step: 2, steps: '3' },
    { step: 0, steps: 3 },
    { step: -1, steps: 3 },
    { step: 1.5, steps: 3 },
    { step: 1, steps: 0 },
    { step: null, steps: 3 },
  ];
  for (const fields of malformed) {
    api.setToolActivity(api.runningActivity({ code: 'x', description: '' }));
    api.onRuntimeProgress({ phase: 'loading_package', package: 'zarr', ...fields });
    const activity = api.getToolActivity();
    const holder = window.document.createElement('div');
    holder.innerHTML = api.toolPanelHtml(activity);
    assertEqual(
      [activity.progress.text, activity.progress.step, activity.progress.steps, holder.querySelector('.osa-tool-progress')],
      ['Loading zarr...', null, null, null],
      `step ${JSON.stringify(fields.step)} of ${JSON.stringify(fields.steps)} keeps the label and draws no bar`
    );
  }
}

console.log('\nwhat is stored is what is read back, within the same bounds');
{
  const { api } = loadWidget();
  const L = api.EXECUTION_FIELD_LIMITS;
  const record = api.executionRecord(
    { call_id: 'c'.repeat(L.callId + 10), tool: 'execute_code', args: { code: 'x'.repeat(L.code + 10), description: 'd'.repeat(L.description + 10) } },
    { status: 'ok', stdout: 'o'.repeat(L.stdout + 10), stderr: 'e'.repeat(L.stderr + 10), images: [{ mime: 'image/png' }] }
  );
  assertEqual(
    [record.callId.length, record.code.length, record.description.length, record.stdout.length, record.stderr.length],
    [L.callId, L.code, L.description, L.stdout, L.stderr],
    'a record is written within the limits'
  );
  const stored = JSON.parse(JSON.stringify({ ...record, images: [] }));
  assertEqual(api.normalizePersistedExecutions([stored])[0], stored, 'and reads back identically after a reload');

  assertEqual(api.normalizePersistedExecutions('not a list'), [], 'a non-list reads back as no runs');
  const junk = api.normalizePersistedExecutions([null, 'str', 42, [1], { stdout: 123, code: { a: 1 }, images: ['keep?'] }]);
  assertEqual(junk.length, 1, 'only objects survive');
  assertEqual([junk[0].stdout, junk[0].code, junk[0].images], ['', '', []], 'non-string fields become empty, and images are never restored');
  const many = api.normalizePersistedExecutions(Array.from({ length: 25 }, () => ({ status: 'ok' })));
  assertEqual(many.length, api.MAX_BROWSER_RUNS_PER_REPLY, 'at most one reply\'s worth of runs is restored');
  // Every field, from the one table both sides read: a limit changed on one
  // side only shows up here as a length that differs.
  const oversized = Object.fromEntries(Object.entries(L).map(([field, limit]) => [field, 'z'.repeat(limit * 3)]));
  const cut = api.normalizePersistedExecutions([oversized])[0];
  assertEqual(
    Object.fromEntries(Object.keys(L).map((field) => [field, cut[field].length])),
    { ...L },
    'every oversized stored field is cut to exactly its limit'
  );
}

console.log('\na continuation composes each run after the one before');
{
  const { api } = loadWidget();
  assertEqual(api.composeReply('', 'b'), 'b', 'first run: its own text');
  assertEqual(api.composeReply('a', ''), 'a', 'a run that adds nothing keeps what was there');
  assertEqual(api.composeReply('a', 'b'), 'a\n\nb', 'a later run follows after a blank line');
}

console.log('\nthe loop stops at the budget and says so in the reply');
{
  const { api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: 'Working on it.', executions: [{ status: 'ok' }] }]);
  let answered = 0;
  let error = null;
  try {
    await api.continueBrowserReply({ toolRequest: { call_id: 'r0' }, messageIndex: 0 }, {
      answer: async () => { answered += 1; return { status: 'ok' }; },
      resume: async () => 'stream',
      // A model that asks again every time.
      stream: async () => ({ toolRequest: { call_id: `r${answered}` }, messageIndex: 0 }),
    });
  } catch (err) {
    error = err;
  }
  assert(error !== null, 'it throws rather than looping');
  assertEqual(answered, api.MAX_BROWSER_RUNS_PER_REPLY, 'after answering exactly the budget');
  const content = api.getMessages()[0].content;
  assert(content.startsWith('Working on it.') && /_\[The reply stopped: .*most one reply may\]_$/.test(content),
    `the reply keeps its text and records why it stopped (got ${JSON.stringify(content)})`);
  assertEqual(api.getMessages()[0].executions.length, 1, 'and keeps the record of what ran');
}

console.log('\na reply that finishes needs no note');
{
  const { api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: '' }]);
  const seen = [];
  await api.continueBrowserReply({ toolRequest: { call_id: 'a' }, messageIndex: 0 }, {
    answer: async (request, index) => { seen.push([request.call_id, index]); return { status: 'ok' }; },
    resume: async (request) => `stream-${request.call_id}`,
    stream: async (resumed) => (resumed === 'stream-a' ? { toolRequest: { call_id: 'b' }, messageIndex: 0 } : null),
  });
  assertEqual(seen, [['a', 0], ['b', 0]], 'each request is answered against the same reply');
  assertEqual(api.getMessages()[0].content, '', 'and nothing is added to it');
  await api.continueBrowserReply(null, { answer() { throw new Error('no'); } });
  assert(true, 'a run that ended normally is not a browser turn at all');
}

console.log('\na refused resume is recorded once, by the loop; a failed stream is left to the stream');
{
  const { api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: '', executions: [{ status: 'ok' }] }]);
  let error = null;
  try {
    await api.continueBrowserReply({ toolRequest: { call_id: 'a' }, messageIndex: 0 }, {
      answer: async () => ({ status: 'ok' }),
      resume: async () => { throw new Error('No outstanding browser call with that id for this session.'); },
      stream: async () => null,
    });
  } catch (err) {
    error = err;
  }
  assert(error && /No outstanding/.test(error.message), 'the resume error reaches the caller');
  assertEqual(api.getMessages()[0].content, '_[The reply stopped: No outstanding browser call with that id for this session.]_',
    'and a reply that only ran code says it stopped');

  api.setMessages([{ role: 'assistant', content: 'kept' }]);
  try {
    await api.continueBrowserReply({ toolRequest: { call_id: 'a' }, messageIndex: 0 }, {
      answer: async () => ({ status: 'ok' }),
      resume: async () => 'stream',
      stream: async () => { throw new Error('Stream interrupted'); },
    });
  } catch {
    // Expected.
  }
  assertEqual(api.getMessages()[0].content, 'kept', 'a stream failure is not annotated twice');
}

console.log('\na result refused by the per-minute limit is waited out; an hourly refusal is not');
{
  const sent = [];
  const replies = [];
  const fetch = async (url, init) => {
    sent.push({ url: String(url), body: init.body });
    return replies.shift()();
  };
  const { api, widget } = loadWidget({ fetch });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test' });
  const limited = (details) => () => new Response(JSON.stringify({ error: 'Rate limit exceeded', details }), {
    status: 429,
    headers: { 'content-type': 'application/json' },
  });
  const streaming = () => new Response('data: {}\n\n', { headers: { 'content-type': 'text/event-stream' } });
  const request = { session_id: 's', call_id: 'c1' };
  const result = { call_id: 'c1', status: 'ok', stdout: 'computed' };

  replies.push(limited('Too many requests per minute'), limited('Too many requests per minute'), streaming);
  const response = await api.postResume(request, result, [5, 5]);
  assertEqual(response.status, 200, 'after two per-minute refusals the third attempt continues the reply');
  assertEqual(sent.length, 3, 'sending the result three times');
  assert(sent.every((s) => s.url.endsWith('/test/chat/resume') && s.body === sent[0].body),
    'the same result each time, to the resume route');
  assertEqual(JSON.parse(sent[0].body).result, result, 'which is the result the browser produced');

  sent.length = 0;
  replies.push(limited('Too many resume requests per hour'));
  let error = null;
  try {
    await api.postResume(request, result, [5, 5]);
  } catch (err) {
    error = err;
  }
  assertEqual(sent.length, 1, 'an hourly refusal is not retried');
  assertEqual(error && error.message, 'Rate limit exceeded: Too many resume requests per hour', 'and says which limit');

  sent.length = 0;
  replies.push(...Array.from({ length: 3 }, () => limited('Too many requests per minute')));
  error = null;
  try {
    await api.postResume(request, result, [5, 5]);
  } catch (err) {
    error = err;
  }
  assertEqual(sent.length, 3, 'a per-minute limit that outlasts the waits stops after them');
  assert(error && /per minute/.test(error.message), 'and fails with the limit named');
}

console.log('\nthe first message waits one shared deadline, not one per wait');
{
  const { api } = loadWidget();
  const never = new Promise(() => {});
  let started = Date.now();
  let declared = await api.declaredClientToolsWithin(never, () => never, 200);
  const took = Date.now() - started;
  assertEqual(declared, [], 'nothing arrives: nothing is declared');
  assert(took >= 190 && took < 350, `in one deadline, not two (took ${took}ms)`);

  started = Date.now();
  declared = await api.declaredClientToolsWithin(Promise.resolve(), () => Promise.resolve({ declared: ['execute_code', 'get_full_output'] }), 5000);
  assertEqual(declared, ['execute_code', 'get_full_output'], 'loaded tools are declared');
  assert(Date.now() - started < 100, 'at once, without waiting out the deadline');
  assertEqual(await api.declaredClientToolsWithin(Promise.resolve(), () => null, 5000), [], 'no tools configured: nothing');
  assertEqual(await api.declaredClientToolsWithin(Promise.resolve(), () => Promise.resolve(null), 5000), [], 'a runtime that failed to load: nothing');
  assertEqual(await api.declaredClientToolsWithin(null, () => Promise.resolve({ declared: ['x'] }), 5000), ['x'], 'no config fetch in flight: the tools alone decide');
}

console.log('\nthe runtime bundle is found beside the script, or not at all');
{
  let { api, widget } = loadWidget({ scriptSrc: 'http://localhost/static/osa-chat-widget.js' });
  assertEqual(api.runtimeBundleUrl(), 'http://localhost/static/osa-runtime.bundle.js', 'next to the widget script');
  ({ api, widget } = loadWidget({ scriptSrc: null }));
  assertEqual(api.runtimeBundleUrl(), null, 'an inline copy with no URL finds nothing');
  widget.setConfig({ widgetScriptUrl: 'https://cdn.example/osa@v1/frontend/osa-chat-widget.js' });
  assertEqual(api.runtimeBundleUrl(), 'https://cdn.example/osa@v1/frontend/osa-runtime.bundle.js',
    'the pop-out, which runs inline, finds it through the URL it is handed');
  ({ api, widget } = loadWidget({ scriptSrc: null }));
  assertEqual(await api.loadRuntimeBundle(), null, 'no URL at all: resolves null rather than throwing');
  assertEqual(widget.getBrowserRuntimeStatus().state, 'off', 'a community without tools reports off');
}

console.log('\na bundle the browser refuses turns code execution off, and says why');
{
  const { window, api } = loadWidget();
  const loading = api.loadRuntimeBundle();
  const script = window.document.head.querySelector('script[src$="osa-runtime.bundle.js"]');
  assert(script !== null, 'the bundle is requested with a script element');
  const integrity = (SOURCE.match(/const RUNTIME_BUNDLE_INTEGRITY = '([^']+)'/) || [])[1];
  assertEqual(script && script.getAttribute('integrity'), integrity, 'carrying the pinned integrity hash');
  assertEqual(script && script.getAttribute('crossorigin'), 'anonymous', 'and crossorigin, without which SRI does not apply');
  script.onerror(new window.Event('error'));
  assertEqual(await loading, null, 'a refused bundle resolves null rather than rejecting');
}

console.log('\nthe real bundle, loaded into the page, answers through the widget\'s own gate');
{
  const { window, api, widget } = loadWidget({ bundleLoads: true });
  // The bundle as built, evaluated as the browser evaluates the file before
  // firing `load`.
  // eslint-disable-next-line no-new-func
  new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
  window.OSARuntime = globalThis.OSARuntime;
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.29.5', preload: [], fetch_allow: [], limits: {} } },
  });
  assertEqual(widget.getBrowserRuntimeStatus().state, 'loading', 'loading while the bundle is fetched');
  const tools = await api.declaredClientTools();
  assertEqual(tools, ['execute_code', 'get_full_output'], 'once loaded, the tools are declared');
  assertEqual(widget.getBrowserRuntimeStatus().state, 'ready', 'and the status says ready');

  // A container whose render throws: the gate must still settle, or the
  // server's call stays parked and the widget freezes.
  const broken = window.document.createElement('div');
  broken.className = 'osa-chat-widget';
  window.document.body.appendChild(broken);
  const answering = api.getBrowserTools().answer({
    call_id: 'g1', tool: 'execute_code', args: { code: 'print(1)', description: 'd' }, requires_permission: true,
  });
  await new Promise((resolve) => setTimeout(resolve, 10));
  const asking = api.getToolActivity();
  assert(asking && asking.phase === 'asking', 'the gate is showing');
  asking.decide(window.OSARuntime.GATE_DECISION.DENY, false);
  const result = await answering;
  assertEqual([result.call_id, result.status], ['g1', 'denied'], 'Don\'t run is answered as denied, although rendering failed');
  assertEqual(api.getToolActivity(), null, 'and the panel is cleared');
}

console.log('\na community\'s lock overlay reaches the runtime, with its wheels served by the API that sent it');
{
  const { window, api } = loadWidget({ bundleLoads: true });
  // eslint-disable-next-line no-new-func
  new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
  window.OSARuntime = globalThis.OSARuntime;
  const packages = {
    zarr: {
      name: 'zarr', version: '3.4.0', file_name: 'zarr-3.4.0-py3-none-any.whl', package_type: 'package',
      install_dir: 'site', sha256: 'a'.repeat(64), imports: ['zarr'], depends: ['numpy'],
    },
  };
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.29.5', preload: ['zarr'], fetch_allow: [], limits: {} } },
    runtime_lock: { packages },
  });
  await api.declaredClientTools();
  const runtime = api.getBrowserRuntime();
  const config = api.getConfig();
  assertEqual(runtime && runtime.lock && runtime.lock.baseUrl, `${config.apiEndpoint}/${config.communityId}/runtime/`,
    'the wheels are fetched from this community\'s runtime route on the API');
  assertEqual(runtime && runtime.lock && runtime.lock.packages, packages, 'and the entries arrive as the server sent them');
  assert(runtime && runtime.onProgress === api.onRuntimeProgress && runtime.onStateChange === api.onRuntimeStateChange,
    'the runtime reports its progress and its state to the widget\'s own handlers');
}

console.log('\nwithout a lock overlay, the runtime gets none');
{
  const { window, api } = loadWidget({ bundleLoads: true });
  // eslint-disable-next-line no-new-func
  new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
  window.OSARuntime = globalThis.OSARuntime;
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.29.5', preload: [], fetch_allow: [], limits: {} } },
    runtime_lock: null,
  });
  await api.declaredClientTools();
  assertEqual(api.getBrowserRuntime() && api.getBrowserRuntime().lock, null, 'lock is null, and the stock lock is used as it is');
}

console.log('\na lock overlay the runtime cannot use is reported, and nothing is declared');
{
  const { window, api, widget } = loadWidget({ bundleLoads: true });
  // eslint-disable-next-line no-new-func
  new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
  window.OSARuntime = globalThis.OSARuntime;
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.29.5', preload: ['zarr'], fetch_allow: [], limits: {} } },
    runtime_lock: {
      packages: {
        zarr: {
          name: 'zarr', version: '3.4.0', file_name: '../zarr-3.4.0-py3-none-any.whl', package_type: 'package',
          install_dir: 'site', sha256: 'a'.repeat(64), imports: ['zarr'], depends: ['numpy'],
        },
      },
    },
  });
  assertEqual(await api.declaredClientTools(), [], 'nothing is declared, so the model is never offered a tool that cannot start');
  const status = widget.getBrowserRuntimeStatus();
  assertEqual([status.state, status.reason], ['unavailable', 'setup-failed'], 'and the status says why');
}

console.log('\na bundle that loads but defines nothing is reported, not ignored');
{
  const { api, widget } = loadWidget({ bundleLoads: true });
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.29.5' } },
  });
  assertEqual(await api.declaredClientTools(), [], 'nothing is declared');
  const status = widget.getBrowserRuntimeStatus();
  assertEqual([status.state, status.reason], ['unavailable', 'bad-bundle'], 'and the status says why');
}

console.log('\nwithout a runtime, a request to run code fails with words a reader can act on');
{
  const { api } = loadWidget();
  let error = null;
  try {
    await api.answerToolRequest(null, { call_id: 'x', tool: 'execute_code', args: {} }, 0);
  } catch (err) {
    error = err;
  }
  assert(error && /not available on this page/.test(error.message) && /Send your message again/.test(error.message),
    `it explains and says what to do (got ${error && JSON.stringify(error.message)})`);
}

console.log('\nthe stream handler continues the same reply across runs');
{
  const encoder = new TextEncoder();
  const sse = (events) => new Response(new ReadableStream({
    start(controller) {
      for (const event of events) controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      controller.close();
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
  const config = { default_model: 'm', offered_models: [], widget: {}, client_tools: [], runtime: null };
  const fetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
  const { window, api, widget } = loadWidget({ fetch });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const stream = api.handleStreamingResponse;
  assert(container !== null && typeof stream === 'function', 'the widget renders and exposes its stream handler');

  const before = api.getMessages().length;
  const first = await stream(sse([
    { event: 'session', session_id: 's' },
    { event: 'content', content: 'Let me ' },
    { event: 'content', content: 'check.' },
    { event: 'tool_request', session_id: 's', call_id: 'c1', tool: 'execute_code', args: {},
      content: 'Let me check.[1]', citations: [{ marker: 1, source: 'https://a.example', title: 'A', cited_text: '' }] },
  ]), container);
  assert(first && first.toolRequest && first.toolRequest.call_id === 'c1', 'a run ending on tool_request hands the request back');
  const index = first.messageIndex;
  assertEqual(api.getMessages().length, before + 1, 'one reply was started');
  assertEqual(api.getMessages()[index].content, 'Let me check.[1]', 'holding the canonical text the request carried');

  const second = await stream(sse([
    { event: 'session', session_id: 's' },
    { event: 'content', content: 'The peak is 10 Hz.' },
    { event: 'done', session_id: 's', content: 'The peak is 10 Hz.[2]',
      citations: [{ marker: 1, source: 'https://a.example' }, { marker: 2, source: 'https://b.example' }] },
  ]), container, { messageIndex: index });
  assertEqual(second, null, 'a run ending on done hands nothing back');
  assertEqual(api.getMessages().length, before + 1, 'the continuation wrote into the same reply');
  assertEqual(api.getMessages()[index].content, 'Let me check.[1]\n\nThe peak is 10 Hz.[2]', 'after the earlier run\'s text');
  assertEqual(api.getMessages()[index].citations.map((c) => c.marker), [1, 2], 'with the whole reply\'s citations');

  // A continuation that fails before writing anything keeps what was there.
  let error = null;
  try {
    await stream(sse([{ event: 'session', session_id: 's' }]), container, { messageIndex: index });
  } catch (err) {
    error = err;
  }
  assert(error === null, 'a stream that ends early with earlier text is reported in the reply, not thrown');
  assert(api.getMessages()[index].content.startsWith('Let me check.[1]\n\nThe peak is 10 Hz.[2]'),
    'and the earlier text survives it');
}

console.log('\nthe Settings workspace panel: size formatting, visibility, and delete arm/disarm (#433)');
{
  const config = { default_model: 'm', offered_models: [], widget: {}, client_tools: [], runtime: null };
  const fetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
  const { window, api, widget } = loadWidget({ fetch });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-settings' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  assert(container !== null, 'the widget renders a container to test the panel against');

  // Size formatting: exact unit boundaries, not merely "a number and a unit".
  assertEqual(api.formatWorkspaceBytes(0), '0 B', '0 bytes');
  assertEqual(api.formatWorkspaceBytes(500), '500 B', 'under 1 KB stays in bytes');
  assertEqual(api.formatWorkspaceBytes(1023), '1023 B', 'just under the KB boundary');
  assertEqual(api.formatWorkspaceBytes(1024), '1.0 KB', 'exactly 1024 bytes is 1.0 KB');
  assertEqual(api.formatWorkspaceBytes(1536), '1.5 KB', 'a KB value with a fraction');
  assertEqual(api.formatWorkspaceBytes(1024 * 1024), '1.0 MB', 'exactly 1 MiB');
  assertEqual(api.formatWorkspaceBytes(2.5 * 1024 * 1024), '2.5 MB', 'an MB value with a fraction');
  assertEqual(api.formatWorkspaceBytes(1024 * 1024 * 1024), '1.0 GB', 'exactly 1 GiB');
  assertEqual(api.formatWorkspaceBytes(3 * 1024 * 1024 * 1024), '3.0 GB', 'GB is the top unit: it never rolls over further');

  // Hidden when there is no store at all (the default state, before any
  // runtime bundle has set one up).
  api.setWorkspaceStore(null);
  const field = container.querySelector('.osa-workspace-field');
  await api.refreshWorkspacePanel(container);
  assertEqual(field.style.display, 'none', 'the panel is hidden when getWorkspaceStore() is null');

  // A REAL WorkspaceStore, genuinely unavailable under happy-dom (no
  // `indexedDB` global here, the same real absence test-controller.js
  // relies on under Bun) -- not a stand-in for one.
  const store = new WorkspaceStore({ community: 'widget-test' });
  assertEqual(store.available, false, 'happy-dom has no real IndexedDB either, so this store genuinely cannot reach one');
  api.setWorkspaceStore(store);

  await api.refreshWorkspacePanel(container);
  assertEqual(field.style.display, '', 'the panel is shown once a store exists');
  const usage = container.querySelector('.osa-workspace-usage');
  assert(/not available/.test(usage.textContent) && /IndexedDB is not available/.test(usage.textContent),
    `a failed size read shows an error, not silence (got ${JSON.stringify(usage.textContent)})`);

  const deleteBtn = container.querySelector('.osa-workspace-delete-btn');
  assertEqual(deleteBtn.textContent.trim(), 'Delete workspace', 'starts unarmed');

  await api.handleWorkspaceDeleteClick(container);
  assertEqual(deleteBtn.textContent.trim(), 'Confirm delete', 'the first click only arms it');
  assert(deleteBtn.classList.contains('osa-workspace-confirm'), 'and marks it visually');

  await api.handleWorkspaceDeleteClick(container);
  assertEqual(deleteBtn.textContent.trim(), 'Delete workspace', 'the second click attempts the delete, resetting the label either way');
  const errorEl = container.querySelector('.osa-error');
  assert(errorEl && errorEl.style.display === 'block' && /IndexedDB is not available/.test(errorEl.textContent),
    `a failed delete against a genuinely unavailable store shows an error (got ${JSON.stringify(errorEl && errorEl.textContent)})`);

  // Reopening Settings disarms a pending confirm: refreshWorkspacePanel is
  // exactly what openSettings calls, so this is the real disarm path, not a
  // stand-in for it.
  await api.handleWorkspaceDeleteClick(container);
  assertEqual(deleteBtn.textContent.trim(), 'Confirm delete', 'armed again, to prove the next step disarms it');
  await api.refreshWorkspacePanel(container);
  assertEqual(deleteBtn.textContent.trim(), 'Delete workspace', 'reopening settings disarms a pending confirm');
  assert(!deleteBtn.classList.contains('osa-workspace-confirm'), 'and clears the visual mark too');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed > 0 ? 1 : 0);
