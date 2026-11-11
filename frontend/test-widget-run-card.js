/**
 * The widget's run card (#664, #672), run for real.
 *
 * One card for the question whether to run code, the run, and what came of it: the
 * widget source runs unmodified inside a happy-dom window, so escaping and every
 * assertion on the page go through a real HTML serializer and parser. What stands in
 * is the platform only (`fetch`, the script loader), and for the run itself the
 * real controller and runtime over the test worker the other suites use.
 *
 * The failures it reads are not written here: frontend/test-support/run-card-fixtures.js
 * holds stderr recorded off real Pyodide (record-run-card-fixtures.js), so the card is
 * held to what the runtime really writes (osa-output.js _error_text and _clip).
 *
 * Run with: bun frontend/test-widget-run-card.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { RUN_CARD_FIXTURES } from './test-support/run-card-fixtures.js';

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

async function waitUntil(predicate, label, timeoutMs = 5000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) throw new Error(`waitUntil timed out after ${timeoutMs}ms: ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');

function noNetwork(url) {
  return Promise.reject(new Error(`unexpected request in a unit test: ${url}`));
}

/** The widget, evaluated in its own window, as test-widget-tools.js loads it. */
function loadWidget({ bundleLoads = false } = {}) {
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
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  window.fetch = noNetwork;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, noNetwork, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  return { window, api: window.OSAChatWidget.__browser, widget: window.OSAChatWidget };
}

/** The same, with the real runtime bundle, so highlighting and "Edit and run" exist. */
function loadWidgetWithRealBundle() {
  const loaded = loadWidget({ bundleLoads: true });
  // eslint-disable-next-line no-new-func
  new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
  loaded.window.OSARuntime = globalThis.OSARuntime;
  return loaded;
}

const LOCAL_RUNTIME_CONFIG = {
  pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 60 },
};
const LOCAL_TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];

/**
 * The widget's browserTools/browserRuntime swapped for the REAL controller over the
 * REAL test worker (test-workers/executing.js). The gate answers Run, so an
 * assistant call goes through without anyone to click.
 */
async function useLocalController({ api, window }) {
  api.setUpBrowserTools({ client_tools: LOCAL_TOOLS, runtime: { python: LOCAL_RUNTIME_CONFIG } });
  await api.declaredClientTools();
  const runtime = new window.OSARuntime.PyodideRuntime({
    runtime: LOCAL_RUNTIME_CONFIG,
    workerFactory: () => new Worker(new URL('./test-workers/executing.js', import.meta.url).href),
  });
  const controller = new window.OSARuntime.ClientToolController({
    runtime,
    tools: LOCAL_TOOLS,
    gate: async () => window.OSARuntime.GATE_DECISION.RUN,
  });
  api.setBrowserTools(controller);
  api.setBrowserRuntime(runtime);
  return { runtime, controller };
}

function mountedContainer(window) {
  const container = window.document.createElement('div');
  container.className = 'osa-chat-widget';
  container.innerHTML = '<div class="osa-chat-messages"></div>';
  window.document.body.appendChild(container);
  return container;
}

function click(window, el) {
  el.dispatchEvent(new window.Event('click', { bubbles: true }));
}

/** Markup that is not the widget's own: any element or attribute outside what it writes. */
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
      if (name === 'href' || name === 'srcdoc') problems.push(`${name} on <${tag}>`);
    }
  }
  return { problems, holder };
}

function holderOf(window, html) {
  const holder = window.document.createElement('div');
  holder.innerHTML = html;
  return holder;
}

const CARD_TAGS = ['div', 'span', 'pre', 'code', 'button', 'img', 'label', 'input', 'textarea', 'svg', 'rect', 'path', 'polyline', 'line', 'circle'];
const HOSTILE = [
  '<img src=x onerror=alert(1)>',
  '</code></pre><script>alert(1)</script>',
  '"><svg onload=alert(1)>',
  "' onmouseover='alert(1)",
  '&lt;already escaped&gt; & raw',
  '</div><iframe srcdoc="x">',
];

const PNG_1PX = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
const RUN_CODE = 'import numpy as np\nprint("<b>&amp;</b>")  # </code></pre>\nx = np.arange(3)\n';

/** A stored run, with whatever a test needs to differ. */
function run(over = {}) {
  return {
    callId: 'c-1', tool: 'execute_code', description: 'Average the epochs', code: RUN_CODE, status: 'ok',
    stdout: '', stderr: '', traceback: '', images: [], local: false, workspaceNote: '', ...over,
  };
}

/** A run built from a recorded fixture, the way executionRecord writes it. */
function recorded(api, name, over = {}) {
  const fixture = RUN_CARD_FIXTURES[name];
  const record = api.executionRecord(
    { call_id: `rec-${name}`, tool: 'execute_code', args: { code: fixture.code, description: 'Average the epochs' } },
    { status: fixture.status, stdout: fixture.stdout, stderr: fixture.stderr, images: [] },
    fixture.traceback
  );
  return { ...record, ...over };
}

const textOf = (el) => (el ? el.textContent : null);
const omittedIn = (text) => [...text.matchAll(/\.\.\. (\d+) characters omitted \.\.\./g)].reduce((n, m) => n + Number(m[1]), 0);

console.log('='.repeat(60));
console.log('Widget: the run card');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// Consent and running: the live card
// ---------------------------------------------------------------------------

console.log('\nconsent: the code in view, Run, Don\'t run, and "until I reload"');
{
  const { window, api } = loadWidget();
  const html = api.toolPanelHtml({ phase: 'asking', prompt: { code: RUN_CODE, description: 'Average the epochs' }, decide() {} });
  const { problems, holder } = markupProblems(window, html, CARD_TAGS);
  assertEqual(problems, [], 'no foreign markup');
  const card = holder.querySelector('.osa-run-card');
  assertEqual(card.getAttribute('role'), 'group', 'a labeled group');
  assertEqual(card.getAttribute('aria-label'), 'Run code in your browser?', 'named for the question');
  assertEqual(textOf(card.querySelector('.osa-run-card-title')), 'Run this Python in your browser?', 'the title asks');
  assertEqual(textOf(card.querySelector('.osa-run-card-description')), 'Average the epochs', 'the assistant\'s own description under it');
  assertEqual(textOf(card.querySelector('pre code')), RUN_CODE, 'the exact code, visible');
  assert(!card.querySelector('[hidden]') && !card.querySelector('[aria-expanded]'), 'nothing to open: the code is not behind a button');
  assertEqual(textOf(card.querySelector('.osa-tool-run')), 'Run', 'Run');
  assertEqual(textOf(card.querySelector('.osa-tool-deny')), 'Don\'t run', 'Don\'t run');
  assertEqual(textOf(card.querySelector('.osa-tool-autorun')).trim(), 'Run without asking until I reload', 'and the reload-long choice');
  assertEqual(card.querySelector('.osa-tool-autorun-input').getAttribute('type'), 'checkbox', 'a checkbox');
  assert(card.querySelector('.osa-code-copy'), 'with Copy over the code');
}

console.log('\nrunning: the status, a bar once the boot reports a step, and Stop');
{
  const { window, api } = loadWidget();
  const running = (progress) => holderOf(window, api.toolPanelHtml({ phase: 'running', prompt: { code: 'x', description: 'd' }, progress }));
  let holder = running(null);
  assertEqual(holder.querySelector('.osa-run-card').getAttribute('role'), 'status', 'announced as a status');
  assertEqual(textOf(holder.querySelector('.osa-run-card-title')), 'Running Python', 'titled');
  assertEqual(textOf(holder.querySelector('.osa-tool-status')), 'Running Python in your browser...', 'says what it is doing');
  assert(!holder.querySelector('.osa-tool-progress'), 'no bar before any step is reported');
  assertEqual(textOf(holder.querySelector('.osa-tool-stop')), 'Stop', 'Stop');
  holder = running({ text: 'Loading numpy (2 of 4)', step: 2, steps: 4 });
  const bar = holder.querySelector('.osa-tool-progress');
  assertEqual([bar.getAttribute('role'), bar.getAttribute('aria-valuenow'), bar.getAttribute('aria-valuemax')], ['progressbar', '2', '4'],
    'a determinate bar, with its numbers');
  assertEqual(bar.querySelector('.osa-tool-progress-fill').style.width, '50%', 'half full');
  assertEqual(api.toolPanelHtml(null), '', 'no activity, no card');
}

// ---------------------------------------------------------------------------
// Done: with and without a figure
// ---------------------------------------------------------------------------

const FIGURE_RUN = () => run({
  stdout: 'epochs: 882\nmean: 1.5\n',
  images: [{ mime: 'image/png', data_base64: PNG_1PX, width: 1, height: 1 }],
});

console.log('\ndone, with a figure: the figure first, then Show code, Copy code, Output, Download');
{
  const { window, api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'user', content: 'plot', dataset: 'nm000132' }, { role: 'assistant', content: 'done', executions: [FIGURE_RUN()] }]);
  const { problems, holder } = markupProblems(window, api.executionsHtml(api.getMessages()[2].executions, 2), CARD_TAGS);
  assertEqual(problems, [], 'no foreign markup');
  const card = holder.querySelector('.osa-run-card');
  assert(card.classList.contains('osa-run-card-ok') && card.classList.contains('osa-execution-run'), 'an ok card');
  assertEqual(textOf(card.querySelector('.osa-run-card-title')), 'Ran Python', 'titled by its status');
  assertEqual(textOf(card.querySelector('.osa-run-card-description')), 'Average the epochs', 'with the description');
  const figure = card.querySelector('.osa-execution-figure');
  const actions = card.querySelector('.osa-run-card-actions');
  assert(figure && !figure.closest('[hidden]'), 'the figure is visible by default');
  assert(figure.compareDocumentPosition(actions) & 4, 'it comes before the buttons');
  const toggle = card.querySelector('.osa-run-card-code-toggle');
  assertEqual(textOf(toggle), 'Show code · 3 lines', 'Show code, with its length');
  assertEqual(toggle.getAttribute('aria-expanded'), 'false', 'closed to begin with');
  const panel = [...card.querySelectorAll('[id]')].find((el) => el.id === toggle.getAttribute('aria-controls'));
  assert(panel && panel.hasAttribute('hidden') && panel.querySelector('pre code'), 'aria-controls names the hidden panel holding the code');
  assertEqual(textOf(card.querySelector('.osa-code-copy')), 'Copy code', 'Copy code');
  assertEqual(textOf(card.querySelector('.osa-run-card-toggle[data-panel="output"]')), 'Output · 2 lines', 'Output, with its length');
  assertEqual(card.querySelector('.osa-figure-download').getAttribute('aria-label'), 'Download figure 1 as nm000132-run-1-figure-1.png', 'the figure\'s Download');
  assert(!card.querySelector('.osa-rerun-open'), 'no Edit and run while the page has no runtime');
  assert(!card.querySelector('.osa-run-card-error') && !card.querySelector('.osa-run-card-retry'), 'no error, no retry line');
  const order = [...actions.querySelectorAll('button')].map((b) => b.className.split(' ').find((c) => /code-toggle|code-copy|toggle/.test(c)));
  assert(order.indexOf('osa-run-card-code-toggle') < order.indexOf('osa-code-copy'), 'Show code leads, Copy code after it');
}

console.log('\ndone, without a figure');
{
  const { window, api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'assistant', content: 'done', executions: [run({ stdout: '3\n' })] }]);
  const card = holderOf(window, api.executionsHtml(api.getMessages()[1].executions, 1)).querySelector('.osa-run-card');
  assert(!card.querySelector('.osa-execution-figure') && !card.querySelector('img'), 'no figure, and no placeholder where one would be');
  assert(card.querySelector('.osa-run-card-code-toggle'), 'Show code is still there');
  const output = card.querySelector('.osa-run-card-toggle[data-panel="output"]');
  assertEqual([textOf(output), output.getAttribute('aria-expanded')], ['Output · 1 line', 'false'], 'the output waits behind its button');
  const silent = holderOf(window, api.executionsHtml([run({ stdout: '' })], 1)).querySelector('.osa-run-card');
  assert(!silent.querySelector('.osa-run-card-toggle[data-panel="output"]'), 'a run that printed nothing has no Output button');
  const codeless = holderOf(window, api.executionsHtml([run({ code: '', status: 'denied' })], 1)).querySelector('.osa-run-card');
  assert(!codeless.querySelector('.osa-run-card-code-toggle') && !codeless.querySelector('.osa-code-copy'), 'a run with no code has no code buttons');
}

console.log('\neach status says what happened, by its own label and its own state');
{
  const { window, api } = loadWidget();
  const labels = api.EXECUTION_LABELS;
  assertEqual(Object.keys(labels).sort(), ['cancelled', 'denied', 'error', 'ok', 'oom', 'timeout'], 'the six statuses the runtime reports');
  const states = { ok: 'ok', error: 'failed', timeout: 'failed', oom: 'failed', denied: 'stopped', cancelled: 'stopped' };
  // Spelled out here, not read from the widget's own table, so a changed or mangled label fails.
  const expectedTitles = { ok: 'Ran Python', error: 'Python raised an error', denied: 'Not run', timeout: 'Stopped: took too long', cancelled: 'Stopped', oom: 'Stopped: out of memory' };
  assertEqual(labels, expectedTitles, 'the table holds exactly these labels');
  for (const [status, label] of Object.entries(expectedTitles)) {
    const card = holderOf(window, api.executionsHtml([run({ status, stderr: status === 'ok' ? '' : '[runtime] it stopped' })], 0)).querySelector('.osa-run-card');
    assertEqual(textOf(card.querySelector('.osa-run-card-title')), label, `${status}: the title is "${label}"`);
    assert(card.classList.contains(`osa-run-card-${states[status]}`), `${status}: a ${states[status]} card`);
    assertEqual(card.getAttribute('data-status'), status, `${status}: named on the card`);
  }
  assertEqual(textOf(holderOf(window, api.executionsHtml([run({ status: 'denied' })], 0)).querySelector('.osa-run-card-title')), 'Not run', 'a declined run reads "Not run"');
  const local = holderOf(window, api.executionsHtml([run({ local: true })], 0)).querySelector('.osa-run-card');
  assertEqual(textOf(local.querySelector('.osa-run-card-title')), 'Your run: Ran Python', 'the reader\'s own run says so');
  assertEqual(textOf(local.querySelector('.osa-execution-local-note')), 'This run is yours. The assistant has not seen it.', 'and the assistant has not seen it');
  const odd = holderOf(window, api.executionsHtml([run({ status: '__proto__' })], 0)).querySelector('.osa-run-card');
  assertEqual(textOf(odd.querySelector('.osa-run-card-title')), 'Python', 'a status nobody named reads "Python", even one Object.prototype has');
  const stopped = holderOf(window, api.executionsHtml([run({ status: 'timeout', stderr: '[runtime] the code ran longer than 60s and was stopped.' })], 0)).querySelector('.osa-run-card');
  assertEqual(textOf(stopped.querySelector('.osa-run-card-error-text')), 'the code ran longer than 60s and was stopped.', 'a run the runtime stopped says why, without its tag');
}

// ---------------------------------------------------------------------------
// Behavior on the page
// ---------------------------------------------------------------------------

console.log('\nShow code opens the code and keeps it open; Copy code puts the exact code on the clipboard');
{
  const { window, api } = loadWidgetWithRealBundle();
  api.setUpBrowserTools({ client_tools: LOCAL_TOOLS, runtime: { python: LOCAL_RUNTIME_CONFIG } });
  await api.declaredClientTools();
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'assistant', content: 'done', executions: [FIGURE_RUN()] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  const toggle = () => container.querySelector('.osa-run-card-code-toggle');
  const panel = () => container.querySelector('.osa-code-panel');
  assertEqual(toggle().getAttribute('aria-expanded'), 'false', 'starts collapsed');
  assert(panel().hasAttribute('hidden'), 'with the code hidden');
  assert(panel().querySelector('pre code .osa-py-kw'), 'and highlighted once the runtime has loaded');
  click(window, toggle());
  assertEqual(toggle().getAttribute('aria-expanded'), 'true', 'a click opens it');
  assert(!panel().hasAttribute('hidden'), 'revealing the code');
  assertEqual(api.getMessages()[1].executions[0]._codeOpen, true, 'and records it on the run');
  assertEqual(textOf(toggle()), 'Hide code · 3 lines', 'the button offers to hide it');
  api.renderMessages(container);
  assertEqual(toggle().getAttribute('aria-expanded'), 'true', 'a re-render keeps it open');

  const copy = container.querySelector('.osa-code-copy');
  click(window, copy);
  await waitUntil(() => copy.textContent === 'Copied', 'the button says Copied');
  assertEqual(await window.navigator.clipboard.readText(), RUN_CODE, 'the clipboard holds the run\'s exact code');
  assertEqual(textOf(container.querySelector('.osa-code-status')), 'Copied', 'and the status line says it for a screen reader');
  await new Promise((resolve) => setTimeout(resolve, 2100));
  assertEqual(copy.textContent, 'Copy code', 'then it is Copy code again');

  const output = container.querySelector('.osa-run-card-toggle[data-panel="output"]');
  click(window, output);
  assertEqual([output.getAttribute('aria-expanded'), api.getMessages()[1].executions[0]._panelsOpen], ['true', { output: true }], 'Output opens the same way');
  assert(!container.querySelector('.osa-execution-output').closest('[hidden]'), 'showing the output');
}

console.log('\nEdit and run puts its editor inside the card');
{
  const { window, api } = loadWidgetWithRealBundle();
  await useLocalController({ api, window });
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'assistant', content: 'done', executions: [FIGURE_RUN()] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  const card = () => container.querySelector('.osa-run-card');
  assert(card().querySelector('.osa-run-card-actions .osa-rerun-open'), 'Edit and run is one of the card\'s buttons');
  assertEqual(container.querySelectorAll('.osa-run-card').length, 1, 'one card');
  click(window, card().querySelector('.osa-rerun-open'));
  assert(card().querySelector('.osa-rerun .osa-rerun-textarea'), 'the editor is inside the card');
  assert(!card().querySelector('.osa-rerun-open'), 'and the button gives way to it');
  assertEqual(card().querySelector('.osa-rerun-textarea').value, RUN_CODE, 'prefilled with the run\'s code');
}

// ---------------------------------------------------------------------------
// After a reload
// ---------------------------------------------------------------------------

console.log('\na restored card is the same card, minus the figure, and stores nothing transient');
{
  const { window, api } = loadWidget();
  const live = run({
    stdout: 'epochs: 882\n', stderr: '<cell>:2: UserWarning: flat\n',
    images: [{ mime: 'image/png', data_base64: PNG_1PX, width: 1, height: 1 }],
    _codeOpen: true, _panelsOpen: { output: true, warnings: true }, _editing: false, _draft: 'x', _runningLocal: false, _localResult: null,
  });
  const failedRun = run({
    callId: 'c-2', status: 'error', code: 'a = {}\nb = a["k"]\nprint(b)\n', stderr: "KeyError: 'k'\n  line 2: b = a[\"k\"]",
    traceback: 'Traceback (most recent call last):\n  File "<cell>", line 2, in <module>\nKeyError: \'k\'\n', _panelsOpen: { traceback: true },
  });
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'user', content: 'q' }, { role: 'assistant', content: 'done', executions: [live, failedRun] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  assert(container.querySelector('img'), 'sanity: the live card shows its figure');

  api.saveHistory();
  const raw = window.localStorage.getItem(api.getConfig().storageKey);
  assert(!/"_[A-Za-z]/.test(raw), `no stored key starts with an underscore (${(raw.match(/"_[A-Za-z]+"/) || [''])[0]})`);
  assert(!raw.includes(PNG_1PX), 'and no figure is stored');
  assert(raw.includes('"traceback"'), 'while the traceback is');

  // What the card looks like with the same runs, no figure and no open state.
  const clean = [{ ...live, images: [] }, failedRun].map((entry) => {
    const copy = { ...entry };
    for (const key of Object.keys(copy)) if (key.startsWith('_')) delete copy[key];
    return copy;
  });
  const expected = api.executionsHtml(clean, 2);
  api.setMessages([]);
  api.loadHistory();
  const restored = mountedContainer(window);
  api.renderMessages(restored);
  const cards = [...restored.querySelectorAll('.osa-run-card')];
  assertEqual(cards.length, 2, 'both cards come back');
  assertEqual(cards.map((card) => card.outerHTML), [...holderOf(window, expected).querySelectorAll('.osa-run-card')].map((card) => card.outerHTML),
    'each exactly as the same record renders with no figure and nothing open');
  assert(!restored.querySelector('img') && !restored.querySelector('.osa-execution-figure'), 'no figure, and no placeholder');
  assert(cards[0].querySelector('.osa-run-card-code-toggle').getAttribute('aria-expanded') === 'false', 'the code is closed again');
  assertEqual(textOf(cards[1].querySelector('.osa-run-card-error-text')), "KeyError: 'k'", 'a restored failure still shows its error');
  assert(cards[1].querySelector('.osa-run-card-toggle[data-panel="traceback"]'), 'and its Full traceback');
}

console.log('\nthe card renders before the runtime has loaded, and escapes plainly');
{
  const { window, api } = loadWidget();
  const html = api.executionsHtml([run({ code: '</code></pre><script>alert(1)</script>' })], 0);
  const { problems, holder } = markupProblems(window, html, CARD_TAGS);
  assertEqual(problems, [], 'no foreign markup');
  assertEqual(textOf(holder.querySelector('.osa-code-panel pre code')), '</code></pre><script>alert(1)</script>', 'the code reads back as text');
}

// ---------------------------------------------------------------------------
// Hostile content
// ---------------------------------------------------------------------------

console.log('\nhostile code, description, output and stderr stay escaped, in every state');
{
  const { window, api } = loadWidget();
  for (const text of HOSTILE) {
    const states = {
      ok: run({ description: text, code: text, stdout: text, stderr: `${text}\n<cell>:1: UserWarning: ${text}`, workspaceNote: text }),
      failed: run({ status: 'error', description: text, code: `${text}\nx`, stdout: text, stderr: `${text}\nKeyError: ${text}\n  line 1: ${text}`, traceback: text }),
      untyped: run({ status: 'oom', description: text, stderr: `[runtime] ${text}` }),
    };
    for (const [label, entry] of Object.entries(states)) {
      const html = api.executionsHtml([entry], 0, { pending: true });
      const { problems, holder } = markupProblems(window, html, CARD_TAGS);
      assert(problems.length === 0, `${label}: ${JSON.stringify(text.slice(0, 22))} leaves no foreign markup${problems.length ? ` (${problems.join('; ')})` : ''}`);
      assertEqual(textOf(holder.querySelector('.osa-run-card-description')), text, `${label}: the description reads back unchanged`);
    }
  }
  const asking = api.toolPanelHtml({ phase: 'asking', prompt: { code: HOSTILE[1], description: HOSTILE[0] }, decide() {} });
  assertEqual(markupProblems(window, asking, CARD_TAGS).problems, [], 'the consent card too');
}

// ---------------------------------------------------------------------------
// E3.2: failures, retries, warnings and cut output, from recorded runs
// ---------------------------------------------------------------------------

console.log('\nthe fixtures are what the runtime wrote');
{
  const f = RUN_CARD_FIXTURES;
  assert(f.keyErrorWithLine.stderr.startsWith("KeyError: 'Scrambled'\n  line 4: "), 'a KeyError, then the line the runtime found');
  assert(!/\n {2}line \d+/.test(f.errorWithoutLine.stderr), 'one error has no line');
  assert(omittedIn(f.cutOutput.stdout) > 0 && omittedIn(f.cutStderr.stderr) > 0, 'and two runs were cut by the worker');
  assert(f.okWithWarnings.status === 'ok' && f.okWithWarnings.stderr.includes('Warning:'), 'a run that succeeded and warned');
}

function failedCard(window, api, name, over = {}, options = {}) {
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'assistant', content: '', executions: [recorded(api, name, over)] }]);
  return holderOf(window, api.executionsHtml(api.getMessages()[1].executions, 1, options)).querySelector('.osa-run-card');
}

console.log('\na KeyError opens with its type and message, and the failing line marked');
{
  const { window, api } = loadWidget();
  const card = failedCard(window, api, 'keyErrorWithLine');
  assert(card.classList.contains('osa-run-card-failed'), 'a failed card');
  assertEqual(textOf(card.querySelector('.osa-run-card-title')), 'Python raised an error', 'titled by the status');
  const error = card.querySelector('.osa-run-card-error-text');
  assertEqual(textOf(error), "KeyError: 'Scrambled'", 'Type: message');
  assertEqual(textOf(error.querySelector('.osa-run-card-error-type')), 'KeyError', 'with the type of its own');
  assert(!error.closest('[hidden]'), 'open by default: nothing to click to see it');
  const marked = card.querySelectorAll('.osa-run-card-line-marked');
  assertEqual(marked.length, 1, 'one line is marked');
  assertEqual(textOf(marked[0].querySelector('.osa-run-card-lineno')), '4', 'line 4');
  assertEqual(textOf(marked[0].querySelector('.osa-run-card-linetext')).replace('Failing line: ', ''), '    erp[name] = sum(events[name]) / len(events[name])', 'the line itself, from the run\'s code');
  assert(marked[0].querySelector('.osa-run-card-marker').textContent === '>' && /Failing line/.test(marked[0].textContent), 'marked by a glyph and a label as well as the color');
  assertEqual([...card.querySelectorAll('.osa-run-card-excerpt .osa-run-card-lineno')].map(textOf), ['3', '4', '5'], 'between its neighbors');
  const toggle = card.querySelector('.osa-run-card-code-toggle');
  assertEqual([textOf(toggle), toggle.getAttribute('aria-expanded')], ['All code · 5 lines', 'false'], 'All code, closed, with its length');
  assert(card.querySelector('.osa-run-card-toggle[data-panel="traceback"]'), 'Full traceback, because the browser kept one');
  assertEqual(textOf(card.querySelector('.osa-run-card-copy-error')), 'Copy error', 'Copy error');
  assert(!card.querySelector('.osa-code-copy.osa-run-card-button'), 'and no Copy code button of its own');
  assert(card.querySelector('.osa-code-panel .osa-code-copy'), 'the code\'s own Copy is in its panel');
}

console.log('\nFull traceback only when there is one');
{
  const { window, api } = loadWidget();
  const without = failedCard(window, api, 'keyErrorWithLine', { traceback: '' });
  assert(!without.querySelector('.osa-run-card-toggle[data-panel="traceback"]'), 'no button, no panel, for a run with none');
  assert(!without.querySelector('.osa-run-card-traceback'), 'and nothing hidden in the page');
  assert(without.querySelector('.osa-run-card-copy-error'), 'Copy error is still there');
  const withTrace = failedCard(window, api, 'keyErrorWithLine');
  const button = withTrace.querySelector('.osa-run-card-toggle[data-panel="traceback"]');
  const panel = [...withTrace.querySelectorAll('[id]')].find((el) => el.id === button.getAttribute('aria-controls'));
  assertEqual([textOf(button), button.getAttribute('aria-expanded'), panel.hasAttribute('hidden')], ['Full traceback', 'false', true], 'closed to begin with');
  assertEqual(textOf(panel.querySelector('pre')), RUN_CARD_FIXTURES.keyErrorWithLine.traceback, 'holding the whole traceback');
}

console.log('\na SyntaxError, and an error the runtime found no line for');
{
  const { window, api } = loadWidget();
  const syntax = failedCard(window, api, 'syntaxError');
  assertEqual(textOf(syntax.querySelector('.osa-run-card-error-text')), "SyntaxError: '[' was never closed", 'the message, without the runtime\'s own "(<cell>, line 1)"');
  assertEqual(textOf(syntax.querySelector('.osa-run-card-line-marked .osa-run-card-lineno')), '1', 'the line is marked');
  const plain = failedCard(window, api, 'errorWithoutLine');
  assertEqual(textOf(plain.querySelector('.osa-run-card-error-text')), 'SyntaxError: source code string cannot contain null bytes', 'Type: message and nothing more');
  assert(!plain.querySelector('.osa-run-card-excerpt') && !plain.querySelector('.osa-run-card-line-marked'), 'no line to mark, so none is invented');
  assert(plain.querySelector('.osa-run-card-code-toggle'), 'the code is still one click away');
}

console.log('\na message of several lines keeps the exception\'s own type');
{
  const { window, api } = loadWidget();
  const card = failedCard(window, api, 'multiLineMessage');
  assertEqual(textOf(card.querySelector('.osa-run-card-error-type')), 'RuntimeError', 'the type is the exception\'s, not a line of its message');
  assertEqual(textOf(card.querySelector('.osa-run-card-error-text')),
    'RuntimeError: could not merge the event files\nOtherError: column onset is missing', 'and the whole message follows it');
  assertEqual(textOf(card.querySelector('.osa-run-card-line-marked .osa-run-card-lineno')), '2', 'the failing line is still marked');
  const noTrace = failedCard(window, api, 'multiLineMessage', { traceback: '' });
  assert(noTrace.querySelector('.osa-run-card-error-text').textContent.includes('column onset is missing'),
    'without a traceback to read the type from, nothing the run said is hidden');
}

console.log('\na custom exception name that does not end in Error');
{
  const { window, api } = loadWidget();
  for (const traceback of [undefined, '']) {
    const card = failedCard(window, api, 'customName', traceback === undefined ? {} : { traceback });
    assertEqual(textOf(card.querySelector('.osa-run-card-error-type')), 'MyFailure', `named${traceback === '' ? ' with no traceback' : ' from its traceback'}`);
    assertEqual(textOf(card.querySelector('.osa-run-card-error-text')), 'MyFailure: nope', 'with its message');
  }
  const bare = holderOf(window, api.executionsHtml([run({ status: 'error', stderr: 'MyFailure: nope' })], 0)).querySelector('.osa-run-card');
  assertEqual(textOf(bare.querySelector('.osa-run-card-error-text')), 'MyFailure: nope', 'with no traceback and no line, shown whole');
  assert(!bare.querySelector('.osa-run-card-error-type'), 'with no type claimed, since nothing says it is one');
}

console.log('\na failed run with no stderr says so, and a line number no code has is not drawn');
{
  const { window, api } = loadWidget();
  const silent = holderOf(window, api.executionsHtml([run({ status: 'error', stderr: '' })], 0)).querySelector('.osa-run-card');
  assertEqual(textOf(silent.querySelector('.osa-run-card-no-message')), 'No error message was recorded.', 'a muted line instead of a bare title');
  assert(!silent.querySelector('.osa-run-card-error'), 'and no error block claiming something');
  const huge = holderOf(window, api.executionsHtml([run({ status: 'error', stderr: 'ValueError: x\n  line 99999999999999999999999: y' })], 0)).querySelector('.osa-run-card');
  assert(!huge.querySelector('.osa-run-card-excerpt') && !/e\+/.test(huge.textContent), 'a huge line number is not drawn (no 1e+23)');
  assertEqual(textOf(huge.querySelector('.osa-run-card-error-text')), 'ValueError: x', 'the message still is');
}

console.log('\nwhat scrolls can be reached by keyboard');
{
  const { window, api } = loadWidget();
  const card = failedCard(window, api, 'keyErrorWithLine');
  assertEqual(card.querySelector('.osa-run-card-excerpt').getAttribute('tabindex'), '0', 'the code excerpt takes focus, so a long line can be scrolled');
  assertEqual(card.querySelector('.osa-run-card-error-text').getAttribute('tabindex'), '0', 'and so does the error text');
}

console.log('\nstderr that does not parse is shown whole rather than hidden');
{
  const { window, api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: '' }]);
  const card = holderOf(window, api.executionsHtml([run({ status: 'error', stderr: 'something unusual happened\nand then more' })], 0)).querySelector('.osa-run-card');
  assertEqual(textOf(card.querySelector('.osa-run-card-error-text')), 'something unusual happened\nand then more', 'as the message');
  assert(!card.querySelector('.osa-run-card-error-type'), 'with no type claimed');
}

console.log('\nthe retry line, only while the reply is still pending after a failure');
{
  const { window, api } = loadWidget();
  const max = api.MAX_BROWSER_RUNS_PER_REPLY;
  const failedRun = (over = {}) => recorded(api, 'keyErrorWithLine', over);
  const lineOf = (html) => textOf(holderOf(window, html).querySelector('.osa-run-card-retry'));
  assertEqual(lineOf(api.executionsHtml([failedRun()], 0, { pending: true })),
    `The assistant is looking at this error and may run the code again (1 of ${max} runs used)`, 'a failed last run on a pending reply says the assistant may run again, not that it will');
  assertEqual(lineOf(api.executionsHtml([run(), failedRun()], 0, { pending: true })),
    `The assistant is looking at this error and may run the code again (2 of ${max} runs used)`, 'counting the runs so far');
  assertEqual(lineOf(api.executionsHtml([failedRun()], 0, { pending: false })), null, 'not once the reply is done');
  assertEqual(lineOf(api.executionsHtml([failedRun()], 0)), null, 'nor by default');
  assertEqual(lineOf(api.executionsHtml([failedRun(), run()], 0, { pending: true })), null, 'not on a run that was followed by another');
  assertEqual(lineOf(api.executionsHtml([run()], 0, { pending: true })), null, 'not after a run that worked');
  assertEqual(lineOf(api.executionsHtml([failedRun({ local: true })], 0, { pending: true })), null, 'not after the reader\'s own run, which the assistant never sees');
  const spent = Array.from({ length: max }, (_, i) => failedRun({ callId: `s${i}` }));
  assertEqual(lineOf(api.executionsHtml(spent, 0, { pending: true })), null, 'not when the reply has no run left to give');
  const denied = recorded(api, 'keyErrorWithLine', { status: 'denied' });
  assertEqual(lineOf(api.executionsHtml([denied], 0, { pending: true })), null, 'not after a run the reader declined');
}

console.log('\nthe retry line follows the page: shown while the assistant answers a failed run, gone after');
{
  const { window, api } = loadWidgetWithRealBundle();
  await useLocalController({ api, window });
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'user', content: 'q' }, { role: 'assistant', content: '' }]);
  const container = mountedContainer(window);
  const result = await api.answerToolRequest(container, { call_id: 'r1', tool: 'execute_code', args: { code: 'ERR:30', description: 'd' } }, 2);
  assertEqual(result.status, 'ok', 'sanity: the test worker ran it');
  const record = api.getMessages()[2].executions[0];
  assert(record.traceback.startsWith('Traceback (most recent call last)') && record.traceback.endsWith('ValueError: boom'),
    'the run\'s record holds the traceback the browser kept for it');
  assertEqual(record.traceback, api.normalizePersistedExecutions([JSON.parse(JSON.stringify(record))])[0].traceback, 'and it reads back after a reload');
  assert(!container.querySelector('.osa-run-card-retry'), 'a run that worked says nothing about retrying');
  record.status = 'error';
  record.stderr = "ValueError: boom\n  line 1: ERR:30";
  api.renderMessages(container);
  assertEqual(textOf(container.querySelector('.osa-run-card-retry')),
    `The assistant is looking at this error and may run the code again (1 of ${api.MAX_BROWSER_RUNS_PER_REPLY} runs used)`, 'a failed run on the reply the assistant is answering');
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'user', content: 'q' }, { role: 'assistant', content: 'earlier', executions: [record] }, { role: 'user', content: 'next' }, { role: 'assistant', content: '' }]);
  await api.answerToolRequest(container, { call_id: 'r2', tool: 'execute_code', args: { code: 'print(2)', description: 'd' } }, 4);
  api.renderMessages(container);
  assert(!container.querySelector('.osa-run-card-retry'), 'but not on an earlier reply, once the wait has moved on to a later one');
}

console.log('\nwarnings from a run that worked: collapsed, counted');
{
  const { window, api } = loadWidget();
  const card = failedCard(window, api, 'okWithWarnings');
  assert(card.classList.contains('osa-run-card-ok') && !card.querySelector('.osa-run-card-error'), 'an ok card, with no error block');
  const toggle = card.querySelector('.osa-run-card-warnings-toggle');
  assertEqual([textOf(toggle), toggle.getAttribute('aria-expanded')], ['2 warnings', 'false'], '"2 warnings", collapsed');
  const panel = [...card.querySelectorAll('[id]')].find((el) => el.id === toggle.getAttribute('aria-controls'));
  assert(panel.hasAttribute('hidden'), 'the lines are behind it');
  const lines = textOf(panel.querySelector('pre')).split('\n').filter((line) => line.includes('Warning:'));
  assertEqual(lines.length, 2, 'both warnings, as written');
  const one = failedCard(window, api, 'okWithWarnings', { stderr: '<cell>:2: UserWarning: flat' });
  assertEqual(textOf(one.querySelector('.osa-run-card-warnings-toggle')), '1 warning', 'one is "1 warning"');
  const none = failedCard(window, api, 'okWithWarnings', { stderr: 'a note with no warning' });
  assert(!none.querySelector('.osa-run-card-warnings-toggle'), 'stderr with no warning in it adds no button, as before');
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'assistant', content: 'x', executions: [recorded(api, 'okWithWarnings')] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  click(window, container.querySelector('.osa-run-card-warnings-toggle'));
  assertEqual(container.querySelector('.osa-run-card-warnings-toggle').getAttribute('aria-expanded'), 'true', 'a click opens them');
  assertEqual(api.getMessages()[1].executions[0]._panelsOpen, { warnings: true }, 'and the card remembers');
}

console.log('\ncut output says how much is not shown');
{
  const { window, api } = loadWidget();
  const fixture = RUN_CARD_FIXTURES.cutOutput;
  const workerCut = omittedIn(fixture.stdout);
  const record = recorded(api, 'cutOutput');
  const L = api.EXECUTION_FIELD_LIMITS;
  assert(record.stdout.length <= L.stdout, 'the widget keeps at most what it stores of the output');
  // What is missing in the end: the output the run wrote, less what is shown of it.
  const markerText = (text) => text.match(/\n\.\.\. \d+ characters omitted \.\.\.\n/g).join('');
  const wrote = fixture.stdout.length - markerText(fixture.stdout).length + workerCut;
  const shown = record.stdout.length - markerText(record.stdout).length;
  assertEqual(omittedIn(record.stdout), wrote - shown, 'the stored output counts every character not in it: the worker\'s cut and the widget\'s');
  assert(record.stdout.startsWith(fixture.stdout.slice(0, 100)) && record.stdout.endsWith(fixture.stdout.slice(-100)), 'keeping both ends of it');
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'assistant', content: '', executions: [record] }]);
  const card = holderOf(window, api.executionsHtml(api.getMessages()[1].executions, 1)).querySelector('.osa-run-card');
  assertEqual(textOf(card.querySelector('.osa-run-card-cut')), `Output was cut: ${wrote - shown} characters not shown.`, 'the card says how many');
  const plain = holderOf(window, api.executionsHtml([run({ stdout: 'short\n' })], 0)).querySelector('.osa-run-card');
  assert(!plain.querySelector('.osa-run-card-cut'), 'and says nothing when nothing was cut');
}

console.log('\nan error at the end of cut stderr is still shown: both ends are kept');
{
  const { window, api } = loadWidget();
  const fixture = RUN_CARD_FIXTURES.cutStderr;
  const record = recorded(api, 'cutStderr');
  assertEqual(record.stderr.length, api.EXECUTION_FIELD_LIMITS.stderr, 'the record is cut to the field limit');
  assert(record.stderr.endsWith(fixture.stderr.slice(-80)), 'with the end, where the error is, kept');
  const card = failedCard(window, api, 'cutStderr');
  assertEqual(textOf(card.querySelector('.osa-run-card-error-text')), 'ValueError: no recording had a usable event file', 'the message');
  assertEqual(textOf(card.querySelector('.osa-run-card-line-marked .osa-run-card-lineno')), '4', 'and the line');
  assert(/Output was cut: \d+ characters not shown\./.test(textOf(card.querySelector('.osa-run-card-cut'))), 'and that the rest was cut');
  assertEqual(card.querySelector('.osa-run-card-cut').textContent.match(/\d+/)[0], String(omittedIn(record.stderr)), 'by the count in the stored text');
}

console.log('\nthe widget cuts what it stores from the middle, to the limit, and never in half a marker');
{
  const { api } = loadWidget();
  const L = api.EXECUTION_FIELD_LIMITS;
  const cut = (text) => api.executionRecord({ call_id: 'k' }, { status: 'ok', stdout: text, stderr: '' }).stdout;
  const long = 'a'.repeat(5000) + 'b'.repeat(5000);
  const out = cut(long);
  assertEqual(out.length, L.stdout, 'exactly the limit');
  assert(out.startsWith('aaaa') && out.endsWith('bbbb'), 'both ends');
  assertEqual(omittedIn(out), 10000 - (out.length - out.match(/\n\.\.\. \d+ characters omitted \.\.\.\n/)[0].length), 'and a marker with the true count');
  assertEqual(cut('short'), 'short', 'short text is left alone');
  // A worker marker in the dropped middle is added to the count, and one on the edge is not kept in half.
  for (const at of [2300, 2390, 2399, 2400, 2401, 2410, 3000, 3600, 3980, 4010]) {
    const text = `${'x'.repeat(at)}\n... 777 characters omitted ...\n${'y'.repeat(6000)}`;
    const stored = cut(text);
    const markers = stored.match(/\.\.\. \d+ characters omitted \.\.\./g) || [];
    assert(stored.length <= L.stdout && markers.every((m) => /^\.\.\. \d+ characters omitted \.\.\.$/.test(m)), `a marker at ${at} leaves whole markers only, within the limit`);
    const wrote = text.length - '\n... 777 characters omitted ...\n'.length + 777;
    const shown = stored.length - (stored.match(/\n\.\.\. \d+ characters omitted \.\.\.\n/g) || []).join('').length;
    assertEqual(omittedIn(stored), wrote - shown, `a marker at ${at}: the count is of what the run wrote`);
  }
}

console.log('\nCopy error copies the error as written, and says Copied');
{
  const { window, api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'assistant', content: '', executions: [recorded(api, 'keyErrorWithLine')] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  const button = container.querySelector('.osa-run-card-copy-error');
  click(window, button);
  await waitUntil(() => button.textContent === 'Copied', 'the button says Copied');
  assertEqual(await window.navigator.clipboard.readText(), RUN_CARD_FIXTURES.keyErrorWithLine.stderr, 'the clipboard holds Type: message and the failing line');
  const traceback = container.querySelector('.osa-run-card-toggle[data-panel="traceback"]');
  click(window, traceback);
  assert(!container.querySelector('.osa-run-card-traceback').closest('[hidden]'), 'Full traceback opens its panel');
  assertEqual(api.getMessages()[1].executions[0]._panelsOpen, { traceback: true }, 'and the card remembers');
}

console.log('\nwhere the browser refuses the clipboard, Copy error selects exactly the text it promised');
{
  const { window, api } = loadWidget();
  (await window.navigator.permissions.query({ name: 'clipboard-write' })).state = 'denied';
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'assistant', content: '', executions: [recorded(api, 'keyErrorWithLine')] }]);
  const container = mountedContainer(window);
  api.renderMessages(container);
  const button = container.querySelector('.osa-run-card-copy-error');
  const status = container.querySelector('.osa-code-status');
  const realWarn = console.warn;
  console.warn = () => {};
  try {
    click(window, button);
    await waitUntil(() => status.textContent !== '', 'the copy gives up');
  } finally {
    console.warn = realWarn;
  }
  assert(/^This page cannot copy for you: the text is selected, so press (Cmd|Ctrl)\+C$/.test(status.textContent), `the bar names the keys (${JSON.stringify(status.textContent)})`);
  assertEqual(window.getSelection().toString(), RUN_CARD_FIXTURES.keyErrorWithLine.stderr, 'what is selected is the type, message and failing line, as the button promised');
  assert(!button.classList.contains('copied') && button.textContent === 'Copy error', 'the button does not claim it copied');
}

console.log('\na card opens its own panel, even when the page holds another element with the same id');
{
  const { window, api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: 'Hi' }, { role: 'assistant', content: '', executions: [recorded(api, 'keyErrorWithLine')] }]);
  const container = mountedContainer(window);
  const stray = window.document.createElement('div');
  stray.id = 'osa-run-1-0-code';
  window.document.body.insertBefore(stray, window.document.body.firstChild);
  api.renderMessages(container);
  const toggle = container.querySelector('.osa-run-card-code-toggle');
  click(window, toggle);
  assertEqual(toggle.getAttribute('aria-expanded'), 'true', 'the button opens');
  const panel = container.querySelector('.osa-run-card .osa-code-panel');
  assert(!panel.hidden, 'the card\'s own panel is shown, not the other element');
  assertEqual(api.getMessages()[1].executions[0]._codeOpen, true, 'and the record remembers');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed ? 1 : 0);
