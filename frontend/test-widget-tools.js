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

/**
 * Poll `predicate` until it is true, for real DOM click handlers that kick
 * off an async runLocal() a test cannot otherwise await directly (the click
 * event handler itself is fire-and-forget). Throws with `label` on timeout,
 * rather than leaving a test hung against the suite's own watchdog.
 */
async function waitUntil(predicate, label, timeoutMs = 5000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) {
      throw new Error(`waitUntil timed out after ${timeoutMs}ms: ${label}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
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

function loadWidgetWithRealBundle({ fetch = noNetwork } = {}) {
  const loaded = loadWidget({ bundleLoads: true, fetch });
  // eslint-disable-next-line no-new-func
  new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
  loaded.window.OSARuntime = globalThis.OSARuntime;
  return loaded;
}

const LOCAL_RUNTIME_CONFIG = {
  pyodide_version: '0.29.5',
  preload: [],
  preload_on: 'first_run',
  fetch_allow: [],
  limits: { exec_seconds: 60 },
};
const LOCAL_TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];

/**
 * Swap the widget's browserTools/browserRuntime for a REAL
 * ClientToolController over the REAL test worker (test-workers/*.js, the
 * same one test-controller.js drives directly), instead of the real-Pyodide
 * instance setUpBrowserTools() itself builds. runtimeApi (set by
 * declaredClientTools() below) is untouched, so highlightPython and
 * RUNTIME_STATE keep coming from the real bundle.
 *
 * @param {{api: object, window: object}} loaded - loadWidgetWithRealBundle()'s return.
 * @param {{worker?: string, gate?: Function}} [options]
 * @returns {Promise<{runtime: object, controller: object}>}
 */
async function useLocalController({ api, window }, { worker = 'executing', gate, runtimeConfig = LOCAL_RUNTIME_CONFIG } = {}) {
  api.setUpBrowserTools({ client_tools: LOCAL_TOOLS, runtime: { python: LOCAL_RUNTIME_CONFIG } });
  await api.declaredClientTools();
  const runtime = new window.OSARuntime.PyodideRuntime({
    runtime: runtimeConfig,
    workerFactory: () => new Worker(new URL(`./test-workers/${worker}.js`, import.meta.url).href),
  });
  const controller = new window.OSARuntime.ClientToolController({
    runtime,
    tools: LOCAL_TOOLS,
    // No assistant call is answered in these tests unless a test overrides
    // this, so a gate that is ever actually asked something is a bug: it
    // would mean runLocal reached the gate, which it must never do.
    gate: gate || (async () => { throw new Error('the gate must never be asked for a local run'); }),
  });
  api.setBrowserTools(controller);
  api.setBrowserRuntime(runtime);
  return { runtime, controller };
}

/** A minimal `.osa-chat-widget` skeleton renderMessages() can render into. */
function mountedContainer(window) {
  const container = window.document.createElement('div');
  container.className = 'osa-chat-widget';
  container.innerHTML = '<div class="osa-chat-messages"></div>';
  window.document.body.appendChild(container);
  return container;
}

/** Dispatch a real click event, the same way every existing rerun test does. */
function click(window, el) {
  el.dispatchEvent(new window.Event('click', { bubbles: true }));
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

// ---------------------------------------------------------------------------
// Clicking Run for real, over the real controller and the real test worker
// (test-workers/*.js, the same seam test-controller.js drives directly).
// ---------------------------------------------------------------------------

console.log('\nclicking Run executes the reader\'s edit for real and lands it in the reply\'s runs, saved');
{
  const { window, api } = loadWidgetWithRealBundle();
  await useLocalController({ api, window });

  const run = {
    callId: 'call-1', tool: 'execute_code', description: 'load it', code: 'print(1)',
    status: 'ok', stdout: '1\n', stderr: '', images: [], local: false,
  };
  api.setMessages([{ role: 'assistant', content: 'hi' }, { role: 'assistant', content: 'ok', executions: [run] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);

  click(window, container.querySelector('.osa-rerun-open[data-msg-index="1"][data-run-index="0"]'));
  const textarea = container.querySelector('.osa-rerun-textarea');
  textarea.value = 'print("edited")';
  textarea.dispatchEvent(new window.Event('input', { bubbles: true }));
  click(window, container.querySelector('.osa-rerun-run'));

  await waitUntil(() => api.getMessages()[1].executions.length === 2, 'the new run to land');
  const executions = api.getMessages()[1].executions;
  assertEqual(executions.length, 2, 'a new run entry was appended, the original left alone');
  const record = executions[1];
  assertEqual(record.local, true, "marked as the reader's own");
  assertEqual(record.code, 'print("edited")', 'carrying the EDITED code, not the original run\'s');
  assertEqual(record.status, 'ok', 'the test worker answers ok');
  assertEqual(record.stdout, 'stdout of print("edited")', "the run's real stdout (executing.js's own `stdout of ${code}`)");

  const bodyText = container.querySelector('.osa-chat-messages').textContent;
  assert(bodyText.includes('stdout of print("edited")'), "the rendered DOM shows the run's real stdout, not just the in-memory record");

  const raw = JSON.parse(window.localStorage.getItem(api.getConfig().storageKey));
  assertEqual(raw.messages[1].executions.length, 2, 'the conversation was saved with both runs, not just rendered');
  assertEqual(raw.messages[1].executions[1].local, true, 'saved marked local too');
}

console.log('\na reader\'s run that crashes still lands local: true, its status and stderr rendered');
{
  // executing.js cannot produce a genuine Python-exception "error" status at
  // the top level (ERR: only populates get_full_output's OWN store, which a
  // local run never reaches anyway, since that call never goes to the
  // model); CRASH (worker dies, -> oom) and a real timeout below are what it
  // can actually produce as a non-ok top-level status with real stderr.
  const { window, api } = loadWidgetWithRealBundle();
  await useLocalController({ api, window });
  const run = { callId: 'call-2', tool: 'execute_code', description: 'x', code: 'print(1)', status: 'ok', stdout: '', stderr: '', images: [] };
  api.setMessages([{ role: 'assistant', content: 'ok', executions: [run] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  click(window, container.querySelector('.osa-rerun-open[data-msg-index="0"][data-run-index="0"]'));
  const textarea = container.querySelector('.osa-rerun-textarea');
  textarea.value = 'CRASH the instance';
  textarea.dispatchEvent(new window.Event('input', { bubbles: true }));
  click(window, container.querySelector('.osa-rerun-run'));

  await waitUntil(() => api.getMessages()[0].executions.length === 2, 'the crashed run to land');
  const record = api.getMessages()[0].executions[1];
  assertEqual(record.local, true, 'still marked local even though the run failed');
  assertEqual(record.status, 'oom', 'reported as oom, the same as any other run whose instance dies');
  assert(/out of memory/.test(record.stderr), 'with the fixed explanation in stderr');
  const bodyText = container.querySelector('.osa-chat-messages').textContent;
  assert(bodyText.includes('out of memory'), 'the crash is rendered in the DOM');
}

console.log("\na reader's run that times out still lands local: true, its status and stderr rendered");
{
  const { window, api } = loadWidgetWithRealBundle();
  await useLocalController({ api, window }, { runtimeConfig: { ...LOCAL_RUNTIME_CONFIG, limits: { exec_seconds: 1 } } });
  const run = { callId: 'call-3', tool: 'execute_code', description: 'x', code: 'print(1)', status: 'ok', stdout: '', stderr: '', images: [] };
  api.setMessages([{ role: 'assistant', content: 'ok', executions: [run] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  click(window, container.querySelector('.osa-rerun-open[data-msg-index="0"][data-run-index="0"]'));
  const textarea = container.querySelector('.osa-rerun-textarea');
  textarea.value = 'NEVER answers';
  textarea.dispatchEvent(new window.Event('input', { bubbles: true }));
  click(window, container.querySelector('.osa-rerun-run'));

  await waitUntil(() => api.getMessages()[0].executions.length === 2, 'the timed-out run to land', 6000);
  const record = api.getMessages()[0].executions[1];
  assertEqual(record.local, true, 'still marked local even though the run timed out');
  assertEqual(record.status, 'timeout', 'reported as a timeout, the same as any other run over its deadline');
  assert(/ran longer than 1s/.test(record.stderr), 'with the fixed explanation, naming the configured deadline');
  const bodyText = container.querySelector('.osa-chat-messages').textContent;
  assert(bodyText.includes('ran longer than 1s'), 'the timeout is rendered in the DOM');
}

console.log('\nCancel after a completed run: the record shows exactly once, matching what was persisted');
{
  // Intended behavior: the run HAPPENED and stays part of the reply's runs;
  // Cancel only closes the editor, it does not undo or hide the run.
  const { window, api } = loadWidgetWithRealBundle();
  await useLocalController({ api, window });
  const run = { callId: 'call-4', tool: 'execute_code', description: 'x', code: 'print(1)', status: 'ok', stdout: '', stderr: '', images: [] };
  api.setMessages([{ role: 'assistant', content: 'ok', executions: [run] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  click(window, container.querySelector('.osa-rerun-open[data-msg-index="0"][data-run-index="0"]'));
  const textarea = container.querySelector('.osa-rerun-textarea');
  textarea.value = 'print("cancel-test")';
  textarea.dispatchEvent(new window.Event('input', { bubbles: true }));
  click(window, container.querySelector('.osa-rerun-run'));
  await waitUntil(() => api.getMessages()[0].executions.length === 2, 'the run to land');

  const stdoutText = 'stdout of print("cancel-test")';
  const during = container.querySelector('.osa-chat-messages').textContent;
  const occurrencesDuring = during.split(stdoutText).length - 1;
  assertEqual(occurrencesDuring, 1, 'shown exactly once while the editor is still open (live, under the editor)');

  click(window, container.querySelector('.osa-rerun-cancel'));
  const executions = api.getMessages()[0].executions;
  assertEqual(executions.length, 2, "the run happened: it stays in the reply's runs, Cancel does not remove it");
  const record = executions[1];
  assertEqual(record.stdout, stdoutText, 'matching exactly what was persisted when it ran');

  const after = container.querySelector('.osa-chat-messages').textContent;
  const occurrencesAfter = after.split(stdoutText).length - 1;
  assertEqual(occurrencesAfter, 1, 'still shown exactly once after Cancel: now as its own entry, never duplicated, never dropped');
}

console.log("\nStop from the widget cancels a reader's run mid-flight, and the runtime is free after");
{
  const { window, api } = loadWidgetWithRealBundle();
  const { controller, runtime } = await useLocalController({ api, window });
  // Warm the runtime with a throwaway run first, fully awaited, so the run
  // this test stops is genuinely EXECUTING (state READY, the call handed to
  // the worker) rather than still racing its own cold boot. Cancelling a
  // call that is still booting does not recycle the instance -- the boot
  // carries on and the runtime reaches READY on its own timeline, since the
  // person stopped a run and not the runtime (osa-runtime.js execute()/
  // cancel(), and see test-runtime-lifecycle.js's "Stop during a cold boot"
  // case) -- so without this the widget would correctly, not incorrectly,
  // still say Python is starting, and this test would be exercising that
  // other, already-covered case instead of the one it names.
  await runtime.execute('print("warm")', { callId: 'warmup' });
  const run = { callId: 'call-5', tool: 'execute_code', description: 'x', code: 'print(1)', status: 'ok', stdout: '', stderr: '', images: [] };
  api.setMessages([{ role: 'assistant', content: 'ok', executions: [run] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  click(window, container.querySelector('.osa-rerun-open[data-msg-index="0"][data-run-index="0"]'));
  const textarea = container.querySelector('.osa-rerun-textarea');
  textarea.value = 'NEVER answers';
  textarea.dispatchEvent(new window.Event('input', { bubbles: true }));
  click(window, container.querySelector('.osa-rerun-run'));

  await waitUntil(() => container.querySelector('.osa-rerun-stop') !== null, 'the Stop control to appear');
  // The Stop button existing in the DOM only proves _runningLocal was set;
  // execute() still races its own (now-warm) boot check before the call
  // reaches the worker. Wait for it to actually land in the worker's pending
  // map -- a real tick, not a synchronous check -- so Stop below targets a
  // running call, not one still inside that race.
  await waitUntil(() => runtime._pending.size > 0, 'the run to reach the worker, past its own boot race');
  assertEqual(controller.busy, true, 'the controller is genuinely busy while it runs');
  click(window, container.querySelector('.osa-rerun-stop'));

  await waitUntil(() => api.getMessages()[0].executions.length === 2, 'the cancelled run to land');
  const record = api.getMessages()[0].executions[1];
  assertEqual(record.status, 'cancelled', 'Stop cancels it');
  assertEqual(controller.busy, false, 'the runtime is free again, not stuck busy');

  // Controls reset AND the runtime genuinely accepts a new run: not merely
  // re-enabled in the DOM, but actually usable.
  const runBtnAfter = container.querySelector('.osa-rerun-run');
  assert(runBtnAfter && !runBtnAfter.hasAttribute('disabled'), 'Run is available again, not stuck disabled');
  textarea.value = 'print("after stop")';
  textarea.dispatchEvent(new window.Event('input', { bubbles: true }));
  click(window, container.querySelector('.osa-rerun-run'));
  await waitUntil(() => api.getMessages()[0].executions.length === 3, 'a genuinely new run to complete after Stop');
  assertEqual(api.getMessages()[0].executions[2].status, 'ok', 'and it succeeds normally');
}

console.log("\nboth Stop buttons visible at once: each one stops only its own run");
{
  const { window, api } = loadWidgetWithRealBundle();
  const { controller, runtime } = await useLocalController({ api, window });
  // Warm the runtime first, same reason as the previous test: the reader's
  // run below needs to be genuinely executing (past its own boot race) for
  // the assistant's call to queue behind it with #current already set --
  // the exact shape #12 found broken, where the reader's own Stop reached
  // the assistant's queued call instead of the run it was pressed on.
  await runtime.execute('print("warm")', { callId: 'warmup' });

  const run = { callId: 'call-7', tool: 'execute_code', description: 'x', code: 'print(1)', status: 'ok', stdout: '', stderr: '', images: [] };
  api.setMessages([
    { role: 'assistant', content: 'ok', executions: [run] },
    { role: 'assistant', content: '', executions: [] },
  ]);
  const container = mountedContainer(window);
  api.renderMessages(container);

  // The reader opens and runs their own edit first.
  click(window, container.querySelector('.osa-rerun-open[data-msg-index="0"][data-run-index="0"]'));
  const textarea = container.querySelector('.osa-rerun-textarea');
  textarea.value = 'NEVER answers';
  textarea.dispatchEvent(new window.Event('input', { bubbles: true }));
  click(window, container.querySelector('.osa-rerun-run'));
  await waitUntil(() => container.querySelector('.osa-rerun-stop') !== null, "the reader's Stop to appear");
  await waitUntil(() => runtime._pending.size > 0, "the reader's run to reach the worker, past its own boot race");

  // The assistant's own call is asked while the reader's run is still going,
  // so it queues behind it (answer() sets #current, then awaits
  // #localRun.done, per osa-controller.js). Not awaited here: it only
  // settles once the reader's run is stopped or finishes.
  const assistantRequest = { call_id: 'call-assistant-1', tool: 'execute_code', args: { code: 'print(2)', description: 'assistant run' } };
  const assistantAnswered = api.answerToolRequest(container, assistantRequest, 1);
  await waitUntil(() => container.querySelector('.osa-tool-stop') !== null, "the assistant's tool panel Stop to appear");

  // Both panels are visible at once. Stop the assistant's call through ITS
  // OWN button first. It is still only queued (answer() awaits
  // #localRun.done before it can even resolve), so this marks it cancelled
  // without settling anything yet -- and, critically, must not touch the
  // reader's run, which is proven right here, before either promise
  // resolves: still busy, its own Stop still standing.
  assertEqual(controller.busy, true, "still busy: the reader's run has not been touched");
  click(window, container.querySelector('.osa-tool-stop'));
  // Give any wrongly-targeted cancellation a real chance to land (a wrong
  // cancel() would settle runtime.execute() and, a few microtasks later,
  // runLocal()'s own record) before checking it did not: an assertion made
  // in the very same synchronous tick as the click would pass either way,
  // since neither path's consequences have propagated yet.
  await new Promise((resolve) => setTimeout(resolve, 50));
  assertEqual(api.getMessages()[0].executions.length, 1, "the reader's run has not landed: the assistant's Stop never reached it");
  assert(container.querySelector('.osa-rerun-stop') !== null, "the reader's own Stop is still there: their run was never touched");
  assertEqual(controller.busy, true, "the reader's run is still genuinely running, untouched by the assistant's Stop");

  // Now stop the reader's own run through ITS OWN button. That frees the
  // runtime, which is what finally lets the assistant's already-cancelled
  // call resolve.
  click(window, container.querySelector('.osa-rerun-stop'));
  await waitUntil(() => api.getMessages()[0].executions.length === 2, "the reader's cancelled run to land");
  assertEqual(api.getMessages()[0].executions[1].status, 'cancelled', "the reader's own run stopped, through its own Stop");

  const assistantResult = await assistantAnswered;
  assertEqual(assistantResult.status, 'denied', "the assistant's call is the one its own Stop marked -- declined once its turn came, never run");
  assertEqual(controller.busy, false, 'the runtime is free again');
}

console.log('\na synchronous double click on Run executes exactly once');
{
  const { window, api } = loadWidgetWithRealBundle();
  await useLocalController({ api, window });
  const run = { callId: 'call-6', tool: 'execute_code', description: 'x', code: 'print(1)', status: 'ok', stdout: '', stderr: '', images: [] };
  api.setMessages([{ role: 'assistant', content: 'ok', executions: [run] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  click(window, container.querySelector('.osa-rerun-open[data-msg-index="0"][data-run-index="0"]'));
  const textarea = container.querySelector('.osa-rerun-textarea');
  textarea.value = 'print("double")';
  textarea.dispatchEvent(new window.Event('input', { bubbles: true }));

  const runBtn = container.querySelector('.osa-rerun-run');
  // Two clicks, back to back, on the SAME node reference, before either
  // handler yields to the event loop: the worst-case shape of a real
  // double-click, and the one a naive re-query after the first render would
  // not even reproduce.
  click(window, runBtn);
  click(window, runBtn);

  await waitUntil(() => api.getMessages()[0].executions.length >= 2, 'the run to land');
  await new Promise((resolve) => setTimeout(resolve, 80)); // let a wrongly-started second run finish landing too
  const executions = api.getMessages()[0].executions;
  assertEqual(executions.length, 2, 'exactly one new run landed, never two');
}

console.log('\nRun is disabled, and says why, while the runtime is still booting (a real BOOTING state)');
{
  const { window, api } = loadWidgetWithRealBundle();
  const { runtime } = await useLocalController({ api, window }, { worker: 'slow-boot' });
  const run = {
    callId: 'call-7', tool: 'execute_code', description: 'x', code: 'print(1)',
    status: 'ok', stdout: '', stderr: '', images: [], _editing: true,
  };
  // slow-boot.js answers `ready` after a real 300ms delay: booted directly
  // here (not through Run) so the BOOTING window is observed without racing
  // a click against it.
  const booting = runtime.boot();
  assertEqual(runtime.state, window.OSARuntime.RUNTIME_STATE.BOOTING, 'genuinely booting, not simulated');
  const html = api.executionsHtml([run], 0);
  const holder = window.document.createElement('div');
  holder.innerHTML = html;
  const runBtn = holder.querySelector('.osa-rerun-run');
  assert(runBtn && runBtn.hasAttribute('disabled'), 'Run is disabled while the runtime is booting');
  const note = holder.querySelector('.osa-rerun-note');
  assert(note && /starting/i.test(note.textContent), 'and says why');
  await booting;
  assertEqual(runtime.state, window.OSARuntime.RUNTIME_STATE.READY, 'and it does finish booting, proving the window was real');
  runtime.terminate();
}

// ---------------------------------------------------------------------------
// #7: a workspace save failure renders for BOTH kinds of run, regardless of
// the run's own status (unlike ordinary stderr, which is status-gated).
// ---------------------------------------------------------------------------

console.log('\na workspace save-failure note renders regardless of status, for an assistant run and for a local run');
{
  const { window, api } = loadWidget();
  const assistantRun = {
    callId: 'call-a', tool: 'execute_code', description: 'x', code: 'print(1)',
    status: 'ok', stdout: 'fine\n', stderr: '', images: [], local: false,
    workspaceNote: "[workspace] could not save artifacts/x.txt: quota exceeded",
  };
  const localRun = {
    callId: 'person-run-9', tool: 'execute_code', description: 'x', code: 'print(1)',
    status: 'ok', stdout: 'fine\n', stderr: '', images: [], local: true,
    workspaceNote: "[workspace] could not save artifacts/y.txt: quota exceeded",
  };
  const html = api.executionsHtml([assistantRun, localRun], 0);
  const holder = window.document.createElement('div');
  holder.innerHTML = html;
  const notes = Array.from(holder.querySelectorAll('.osa-execution-workspace-note')).map((n) => n.textContent);
  assertEqual(notes.length, 2, 'one note per run, even though BOTH runs succeeded (status ok)');
  assert(notes[0].includes('artifacts/x.txt'), "the assistant run's own failure is shown");
  assert(notes[1].includes('artifacts/y.txt'), "the reader's own run's failure is shown too, its only visible surface");
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

console.log('\na freshly-opened run panel shows the boot progress a first_message preload already made, not a blank bar');
{
  // preload_on: first_message boots on the reader's first message, well before the
  // model answers and asks to run code, so the boot can advance through several
  // steps before any tool_request (and so any 'running' toolActivity) exists to
  // receive them via onRuntimeProgress. Without a fix, the panel that opens once
  // Run is finally clicked starts at progress: null and stays that way until the
  // NEXT event arrives, which reads as a frozen or missing bar for however much of
  // the boot already happened silently.
  const { window, api } = loadWidgetWithRealBundle();
  const { runtime } = await useLocalController({ api, window }, { worker: 'happy' });

  const bootPromise = runtime.boot();
  assertEqual(runtime.state, 'booting', 'boot() takes effect synchronously, before any worker message can arrive');
  assertEqual(api.getToolActivity(), null, 'no panel exists yet to receive a progress event');

  // Capture the shape onRuntimeProgress computes, the same way every other test
  // above does: seed a running activity, feed it the event, and read what it
  // wrote. This is the panel state the reader would have seen, had one existed.
  api.setToolActivity(api.runningActivity({ code: 'x', description: '' }));
  api.onRuntimeProgress({ phase: 'loading_runtime', step: 1, steps: 2 });
  const expected = api.getToolActivity().progress;
  api.setToolActivity(null);

  // The model now asks to run code and the reader clicks Run: a FRESH running
  // activity is created, well after the boot (and its progress) began.
  const activity = api.runningActivity({ code: 'x', description: '' });
  assertEqual(activity.progress, expected, 'the panel opens already showing the step under way, not a blank bar');

  await bootPromise;
  api.onRuntimeStateChange('ready');
  const after = api.runningActivity({ code: 'x', description: '' });
  assertEqual(after.progress, null, 'once the runtime is no longer booting, a later run seeds no stale step');
}

console.log('\nsending the first message boots the runtime under preload_on: first_message, and not under first_run');
{
  async function stateRightAfterFirstMessage(preloadOn) {
    const config = {
      default_model: 'm',
      offered_models: [],
      widget: {},
      client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
      runtime: { python: { ...LOCAL_RUNTIME_CONFIG, preload_on: preloadOn } },
    };
    const fetch = async (url) => {
      const s = String(url);
      if (s.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
      if (s.endsWith('/chat')) {
        return new Response(JSON.stringify({ message: { content: 'hi' }, session_id: 's1' }),
          { headers: { 'content-type': 'application/json' } });
      }
      return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
    };
    const { window, api, widget } = loadWidgetWithRealBundle({ fetch });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: `osa-test-first-msg-${preloadOn}` });
    widget.init();
    // Swap in a runtime over the real test worker, with the preload_on under test.
    // Its own two setter calls at the end unconditionally replace whatever
    // setUpBrowserTools's own (never-booted, so harmless) real construction built.
    await useLocalController({ api, window }, { worker: 'happy', runtimeConfig: { ...LOCAL_RUNTIME_CONFIG, preload_on: preloadOn } });

    const container = window.document.querySelector('.osa-chat-widget');
    const input = container.querySelector('.osa-chat-input input');
    input.value = 'hello';
    click(window, container.querySelector('.osa-send-btn'));

    // sendMessage is async but calls boot() (fire-and-forget) in its synchronous
    // prefix, before its first await, so the state change (if any) has already
    // happened by the time dispatchEvent returns control here.
    const state = api.getBrowserRuntime().state;
    // Let the rest of the send (the fetch, the reply, saveHistory) finish before
    // this window is abandoned for the next case, so nothing dangles across it.
    await waitUntil(() => !container.querySelector('.osa-send-btn').disabled, 'the send settles', 3000);
    return state;
  }

  assertEqual(await stateRightAfterFirstMessage('first_message'), 'booting',
    'preload_on: first_message boots the moment the first message is sent');
  assertEqual(await stateRightAfterFirstMessage('first_run'), RUNTIME_STATE.IDLE,
    'preload_on: first_run does not boot on send; only an actual execution does');
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

console.log('\ngetBrowserRuntimeStatus() carries Pyodide\'s own boot state separately from the wiring state');
{
  // state describes the bundle/controller wiring; runtime describes Pyodide's
  // OWN boot, which first_message and widget_open can start well before any
  // Run gate exists. Proven against a REAL boot, over the real test worker.
  const { window, api, widget } = loadWidgetWithRealBundle();
  const { runtime } = await useLocalController({ api, window }, { worker: 'happy' });
  assertEqual(widget.getBrowserRuntimeStatus().state, 'ready', 'the wiring is ready (bundle loaded, controller built)');
  assertEqual(widget.getBrowserRuntimeStatus().runtime, 'idle', 'but Pyodide itself has not booted yet');

  const bootPromise = runtime.boot();
  assertEqual(widget.getBrowserRuntimeStatus().runtime, 'booting', 'a real boot already under way is reflected at once');

  await bootPromise;
  assertEqual(widget.getBrowserRuntimeStatus().runtime, 'ready', 'and once it settles, the field reflects that too');
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

console.log('\nthe reader\'s bubbles take a community color only when its config names one');
{
  // Its own key, never derived from theme_color: a community that sets only a
  // theme keeps the platform-blue bubbles it has always had.
  assert(SOURCE.includes('--osa-user-bg: #2563eb;'), 'the stylesheet default for the bubbles stays the platform blue');
  const cases = [
    { label: 'both colors', widget: { theme_color: '#257a92', user_bubble_color: '#257a92' }, bubble: '#257a92' },
    { label: 'theme only', widget: { theme_color: '#008a79' }, bubble: '' },
    { label: 'a malformed bubble color', widget: { theme_color: '#257a92', user_bubble_color: 'red;x:y' }, bubble: '' },
  ];
  for (const [index, { label, widget: widgetConfig, bubble }] of cases.entries()) {
    const config = { default_model: 'm', offered_models: [], widget: widgetConfig, client_tools: [], runtime: null };
    const fetch = async (url) => {
      if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
      return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
    };
    const { window, widget } = loadWidget({ fetch });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: `osa-test-bubble-${index}` });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    await waitUntil(() => container.style.getPropertyValue('--osa-primary') === widgetConfig.theme_color, `the theme is applied (${label})`);
    assertEqual(container.style.getPropertyValue('--osa-user-bg'), bubble, `${label}: the bubbles are ${bubble || 'left at the default'}`);
  }
}

console.log('\nthree more widget colors: theme_text_color, accent_color and user_bubble_text_color');
{
  // Stylesheet defaults: white text on a theme_color surface, and the accent
  // tracks theme_color itself, both exactly today's behavior.
  assert(SOURCE.includes('--osa-on-primary: #ffffff;'), 'the stylesheet default for on-primary text stays white');
  assert(SOURCE.includes('--osa-accent: var(--osa-primary);'), 'the stylesheet default for the accent tracks theme_color');
  const cases = [
    {
      label: 'all three set',
      widget: {
        theme_color: '#5bbad5', theme_text_color: '#04121f',
        user_bubble_color: '#5bbad5', user_bubble_text_color: '#04121f',
        accent_color: '#257a92',
      },
      onPrimary: '#04121f', userText: '#04121f', accent: '#257a92',
    },
    {
      label: 'theme_color only, the three new fields unset',
      widget: { theme_color: '#008a79' },
      onPrimary: '', userText: '', accent: '',
    },
    {
      label: 'malformed values for all three',
      widget: {
        theme_color: '#257a92', theme_text_color: 'red;x:y',
        user_bubble_color: '#257a92', user_bubble_text_color: 'not-a-color',
        accent_color: '12345',
      },
      onPrimary: '', userText: '', accent: '',
    },
  ];
  for (const [index, { label, widget: widgetConfig, onPrimary, userText, accent }] of cases.entries()) {
    const config = { default_model: 'm', offered_models: [], widget: widgetConfig, client_tools: [], runtime: null };
    const fetch = async (url) => {
      if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
      return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
    };
    const { window, widget } = loadWidget({ fetch });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: `osa-test-textcolors-${index}` });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    await waitUntil(() => container.style.getPropertyValue('--osa-primary') === widgetConfig.theme_color, `the theme is applied (${label})`);
    assertEqual(container.style.getPropertyValue('--osa-on-primary'), onPrimary, `${label}: on-primary text is ${onPrimary || 'left at the default'}`);
    assertEqual(container.style.getPropertyValue('--osa-user-text'), userText, `${label}: bubble text is ${userText || 'left at the default'}`);
    assertEqual(container.style.getPropertyValue('--osa-accent'), accent, `${label}: accent is ${accent || 'left at the default (tracks theme_color)'}`);
  }
}

console.log('\nevery classified surface and foreground resolves to the color this PR assigned it, against the REAL stylesheet');
{
  // Against the real, unmodified <style> block (injectStyles(), called by init()):
  // a probe element carries the exact class/selector structure a mutation to the
  // CSS text would break, inside a throwaway .osa-chat-widget container whose
  // custom properties this test sets directly -- the same properties
  // applyWidgetConfig() would have set from a resolved community config, without
  // needing a config round trip for every one of the ~20 cases below.
  //
  // happy-dom's getComputedStyle does NOT match :hover or :focus (confirmed
  // empirically: a real :focus() call is reflected in element.matches(':focus')
  // but never changes getComputedStyle's result), so the seven hover/focus-gated
  // foregrounds are checked against the stylesheet text instead, immediately
  // below the computed-style table.
  const { window, widget } = loadWidget();
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-css-audit' });
  widget.init(); // injects the real STYLES block; nothing here awaits its (unused) fetch

  function probe(customProps, html, selector) {
    const container = window.document.createElement('div');
    container.className = 'osa-chat-widget';
    for (const [prop, value] of Object.entries(customProps)) {
      container.style.setProperty(prop, value);
    }
    container.innerHTML = html;
    window.document.body.appendChild(container);
    return container.querySelector(selector);
  }

  const NEMAR_PROPS = {
    '--osa-primary': '#5bbad5',
    '--osa-primary-dark': '#42a1bc',
    '--osa-on-primary': '#04121f',
    '--osa-accent': '#257a92',
    '--osa-user-bg': '#5bbad5',
    '--osa-user-text': '#04121f',
  };
  const UNSET_PROPS = {};

  // Surfaces: background painted with theme_color, text/icons on it. NEMAR sets
  // theme_text_color (#04121f); left unset, the stylesheet's own white applies.
  const SURFACES = [
    { label: 'launcher button (.osa-chat-button)', html: '<button class="osa-chat-button">x</button>', selector: '.osa-chat-button' },
    { label: 'header (.osa-chat-header)', html: '<div class="osa-chat-header">x</div>', selector: '.osa-chat-header' },
    { label: 'header icon buttons (.osa-header-btn)', html: '<button class="osa-header-btn">x</button>', selector: '.osa-header-btn' },
    { label: 'feedback Send (.osa-feedback-send)', html: '<button class="osa-feedback-send">x</button>', selector: '.osa-feedback-send' },
    { label: 'chat Send (.osa-send-btn)', html: '<button class="osa-send-btn">x</button>', selector: '.osa-send-btn' },
    { label: 'Settings Save (.osa-settings-btn-save)', html: '<button class="osa-settings-btn-save">x</button>', selector: '.osa-settings-btn-save' },
    { label: 'the Run button (.osa-tool-actions button.osa-tool-run)', html: '<div class="osa-tool-actions"><button class="osa-tool-run">Run</button></div>', selector: '.osa-tool-run' },
    { label: 'the re-run Run button (.osa-rerun-buttons button.osa-rerun-run)', html: '<div class="osa-rerun-buttons"><button class="osa-rerun-run">Run</button></div>', selector: '.osa-rerun-run' },
  ];
  for (const { label, html, selector } of SURFACES) {
    const nemar = probe(NEMAR_PROPS, html, selector);
    const unset = probe(UNSET_PROPS, html, selector);
    assertEqual(window.getComputedStyle(nemar).color, '#04121f', `${label}: NEMAR-style text is theme_text_color`);
    assertEqual(window.getComputedStyle(unset).color, '#ffffff', `${label}: unset falls back to white`);
  }

  // Foregrounds: theme_color used AS a foreground on the widget's white panel.
  // NEMAR sets accent_color (#257a92, deliberately different from theme_color
  // #5bbad5, so a mutation reverting one of these to --osa-primary directly is
  // caught); left unset, --osa-accent's own default (var(--osa-primary)) applies.
  const FOREGROUNDS = [
    { label: 'message links (.osa-message-content a)', html: '<div class="osa-message-content"><a href="#">x</a></div>', selector: 'a', property: 'color' },
    { label: 'citation links (.osa-citation a)', html: '<span class="osa-citation"><a href="#">x</a></span>', selector: 'a', property: 'color' },
    { label: "the reader's own page-context checkbox (.osa-combined-footer input[type=checkbox])", html: '<div class="osa-combined-footer"><input type="checkbox"></div>', selector: 'input', property: 'accentColor' },
    { label: 'the local-run note (.osa-execution-local-note)', html: '<div class="osa-execution-local-note">x</div>', selector: '.osa-execution-local-note', property: 'color' },
    // The literal inline style attribute the widget's own settings template
    // writes for the OpenRouter link (custom-model-field, applyWidgetConfig's
    // sibling markup), copied verbatim so a change to either drifts this test.
    { label: 'the OpenRouter link (inline style)', html: '<a href="#" style="color: var(--osa-accent); text-decoration: underline;">OpenRouter</a>', selector: 'a', property: 'color' },
  ];
  for (const { label, html, selector, property } of FOREGROUNDS) {
    const nemar = probe(NEMAR_PROPS, html, selector);
    const unset = probe(UNSET_PROPS, html, selector);
    assertEqual(window.getComputedStyle(nemar)[property], '#257a92', `${label}: NEMAR-style is accent_color`);
    assertEqual(window.getComputedStyle(unset)[property], '#2563eb', `${label}: unset tracks theme_color's own default`);
  }
  assert(SOURCE.includes('style="color: var(--osa-accent); text-decoration: underline;">OpenRouter</a>'),
    'the OpenRouter link\'s inline style is exactly what the probe above copied');

  // Hover/focus-gated foregrounds: not reachable through getComputedStyle under
  // happy-dom (see the note above the probe() helper), so checked as source text.
  const HOVER_AND_FOCUS_FOREGROUNDS = [
    ['sources hover (.osa-message-sources a:hover)', '.osa-message-sources a:hover {\n      color: var(--osa-accent);'],
    ["copy button hover (.osa-message-copy-btn:hover)", '.osa-message-copy-btn:hover {\n      color: var(--osa-accent);'],
    ['feedback comment focus border (.osa-feedback-comment-input:focus)', '.osa-feedback-comment-input:focus {\n      outline: none;\n      border-color: var(--osa-accent);'],
    ['chat input focus border (.osa-chat-input input:focus)', '.osa-chat-input input:focus {\n      border-color: var(--osa-accent);'],
    ['footer-powered link hover (.osa-footer-powered a:hover)', '.osa-combined-footer .osa-footer-powered a:hover {\n      color: var(--osa-accent);'],
    ['settings input focus border (.osa-settings-input:focus)', '.osa-settings-input:focus {\n      border-color: var(--osa-accent);'],
    ['settings select focus border (.osa-settings-select:focus)', '.osa-settings-select:focus {\n      border-color: var(--osa-accent);'],
  ];
  for (const [label, needle] of HOVER_AND_FOCUS_FOREGROUNDS) {
    assert(SOURCE.includes(needle), `${label}: the rule reads var(--osa-accent), not var(--osa-primary) or a fixed color`);
  }
}

console.log('\napplyWidgetConfig() warns on a malformed color instead of dropping it in silence, for every color field');
{
  // console.warn is the real widget's own real call: the widget script runs with the
  // REAL global console injected (see loadWidget's run(...) call), so intercepting it
  // here observes exactly what a browser's devtools console would show.
  const BAD = 'not-a-color';
  const originalWarn = console.warn;
  const warnings = [];
  console.warn = (...args) => { warnings.push(args.join(' ')); };
  try {
    const config = {
      default_model: 'm', offered_models: [], client_tools: [], runtime: null,
      widget: {
        theme_color: BAD,
        user_bubble_color: BAD,
        theme_text_color: BAD,
        accent_color: BAD,
        user_bubble_text_color: BAD,
      },
    };
    const fetch = async (url) => {
      if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
      return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
    };
    const { window, widget } = loadWidget({ fetch });
    widget.setConfig({
      apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-warn-colors',
      disclaimerColor: BAD, disclaimerBackground: BAD,
    });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    // applyWidgetConfig() only runs once the server config resolves and reports a
    // change; every field here is malformed, so nothing should ever land inline.
    await waitUntil(() => warnings.length >= 7, 'all seven malformed colors are warned about', 3000);

    const fields = [
      'themeColor', 'userBubbleColor', 'themeTextColor', 'accentColor', 'userBubbleTextColor',
      'disclaimerColor', 'disclaimerBackground',
    ];
    for (const field of fields) {
      assert(warnings.some((w) => w.includes(field) && w.includes(BAD)), `a warning names ${field} and the rejected value`);
    }

    const properties = [
      '--osa-primary', '--osa-primary-dark', '--osa-user-bg', '--osa-on-primary', '--osa-accent',
      '--osa-user-text', '--osa-disclaimer-color', '--osa-disclaimer-bg',
    ];
    for (const property of properties) {
      assertEqual(container.style.getPropertyValue(property), '', `${property} still falls back to the stylesheet default`);
    }
  } finally {
    console.warn = originalWarn;
  }
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed > 0 ? 1 : 0);
