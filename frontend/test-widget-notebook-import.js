/**
 * Importing a chat's notebook into the notebook tab (#673), run against the real
 * widget source in a happy-dom window, the way test-widget-notebook-tab.js runs
 * the tab.
 *
 * What it holds the widget to: importNotebookToTab opens the notebook tab and
 * the panel, queues the import until the notebook reports ready, posts it once
 * and only to the notebook site's origin, and answers with what the notebook
 * reports; the banner counts the steps and the runs left out from the stats the
 * builder gave; Run all and Undo each post their message; messages from another
 * origin, or not from the widget's own frame, are not heard; and an import that
 * cannot happen says why instead of posting.
 *
 * What stands in: `fetch` (HTTP fixtures for the community config), and the
 * notebook's side of the bridge: messages dispatched as if from the frame, in
 * the shapes notebook/osa-bridge.js posts, and the frame's postMessage wrapped
 * to record what the widget sends it. The notebooks come from the real builder,
 * over the recorded conversations in test-support/notebook-conversations.js.
 * What the page does with a message is notebook/e2e-check.js's, in real Chrome.
 *
 * Run with: bun frontend/test-widget-notebook-import.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { buildConversationNotebook } from './osa-notebook-builder.js';
import { NOTEBOOK_CONVERSATIONS } from './test-support/notebook-conversations.js';

let passed = 0;
let failed = 0;

const SUITE_TIMEOUT_MS = 30_000;
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
    if (Date.now() - started > timeoutMs) {
      throw new Error(`waitUntil timed out after ${timeoutMs}ms: ${label}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');
const NOTEBOOK_ORIGIN = 'https://notebook.osc.earth';

let configCounter = 0;
function configResponse(widgetOverrides = {}) {
  configCounter += 1;
  return {
    default_model: 'm',
    offered_models: [],
    widget: { title: 'NEMAR Assistant', placeholder: `loaded ${configCounter}`, launcher: 'capsule', ...widgetOverrides },
    client_tools: [],
    runtime: null,
  };
}

function fetchReturning(config) {
  return async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
}

function loadWidget({ fetch, reducedMotion = false }) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      navigation: { disableChildFrameNavigation: true },
      device: { prefersReducedMotion: reducedMotion ? 'reduce' : 'no-preference' },
    },
  });
  window.__OSA_TEST__ = true;
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
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
  return { window, widget: window.OSAChatWidget };
}

// A NEMAR-like capsule widget with a dataset that has a Zarr copy, its config loaded.
async function startCapsule({ dataset = { id: 'nm000103', zarr: true }, widget: widgetOverrides, before, reducedMotion } = {}) {
  const config = configResponse(widgetOverrides);
  const { window, widget } = loadWidget({ fetch: fetchReturning(config), reducedMotion });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'nemar', storageKey: `osa-test-nbimport-${configCounter}` });
  if (dataset) widget.setDataset(dataset);
  if (before) before(widget, window);
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => container.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder, 'config loaded');
  const q = (selector) => container.querySelector(selector);
  const click = (selector) => q(selector).dispatchEvent(new window.Event('click', { bubbles: true }));
  return { window, widget, container, q, click };
}

// A message as notebook/osa-bridge.js posts it, from `source` and `origin`.
function bridgeMessage(window, data, { source, origin = NOTEBOOK_ORIGIN }) {
  window.dispatchEvent(new window.MessageEvent('message', { data: { source: 'osa-notebook', ...data }, origin, source }));
}

// Record what the widget sends the notebook's frame.
function recordPosts(frame) {
  const posts = [];
  frame.contentWindow.postMessage = (message, targetOrigin) => posts.push({ message, targetOrigin });
  return posts;
}

const isOn = (view) => view.classList.contains('osa-view-on') && !view.hasAttribute('inert');
const isOff = (view) => view.classList.contains('osa-view-off') && view.hasAttribute('inert');

// What a chat hands over, from the real builder over a recorded conversation.
function conversationInput(name = 'erp') {
  const { notebook, stats } = buildConversationNotebook(NOTEBOOK_CONVERSATIONS[name]());
  const { datasetId, title } = NOTEBOOK_CONVERSATIONS[name]();
  return { datasetId, title, notebook, stats };
}

const DATASET = 'on002718';
const IMPORT_PATH = `nemar/${DATASET} · Famous vs scrambled ERPs in ${DATASET}.ipynb`;

// A capsule on `on002718`, its notebook tab opened, and the notebook's side of the bridge.
async function startNotebook(options = {}) {
  const started = await startCapsule({ dataset: { id: DATASET, zarr: true }, ...options });
  started.click('.osa-notebook-btn');
  const frame = started.q('.osa-notebook-frame');
  const posts = recordPosts(frame);
  const source = frame.contentWindow;
  const say = (data, extra = {}) => bridgeMessage(started.window, data, { source, ...extra });
  const importPosts = () => posts.filter((p) => p.message.type === 'import-notebook');
  return { ...started, frame, posts, source, say, importPosts };
}

const banner = (q) => q('.osa-notebook-banner');
const bannerText = (q) => banner(q).textContent;
const bannerButtons = (q) => [...banner(q).querySelectorAll('button')].map((b) => b.textContent);
const state = (window) => window.OSAChatWidget.__notebook.state();

console.log('='.repeat(60));
console.log('Widget: import a chat notebook into the notebook tab (#673)');
console.log('='.repeat(60));

console.log('\nan import waits for the notebook to be ready, and is posted once, to its origin only');
{
  const { window, widget, q, container, posts, importPosts, say, source } = await startNotebook();
  // The notebook is on the chat tab and the panel is closed, as when "Continue in notebook" runs from a menu.
  container.querySelector('.osa-launcher-capsule .osa-chat-button').dispatchEvent(new window.Event('click', { bubbles: true }));
  container.querySelector('.osa-close-btn').dispatchEvent(new window.Event('click', { bubbles: true }));
  assert(!q('.osa-chat-window').classList.contains('open') && container.classList.contains('osa-tab-chat'), 'set up: the panel is closed, on chat');

  const input = conversationInput();
  const result = widget.__notebook.importNotebookToTab(input);
  assert(result instanceof window.Promise || typeof result.then === 'function', 'it returns a promise');
  assert(q('.osa-chat-window').classList.contains('open'), 'it opens the panel');
  assert(container.classList.contains('osa-tab-notebook'), 'on the notebook tab');
  assertEqual(importPosts().length, 0, 'nothing is posted while the notebook has not said it is ready');
  assertEqual(state(window).outbox.length, 1, 'the import waits in the outbox');
  assertEqual(state(window).requests, 1, 'and its request is pending');
  assert(!banner(q).hidden && /^Importing /.test(bannerText(q)), 'the banner says it is importing');
  assertEqual(bannerButtons(q), [], 'with no buttons yet');

  say({ type: 'ready' });
  assertEqual(importPosts().length, 1, 'ready: the import is posted');
  const [sent] = importPosts();
  assertEqual(sent.targetOrigin, NOTEBOOK_ORIGIN, 'addressed to the notebook\'s origin only');
  assertEqual(sent.message.target, 'osa-notebook', 'for the notebook');
  assertEqual(sent.message.datasetId, DATASET, 'with the dataset');
  assertEqual(sent.message.title, input.title, 'the title');
  assertEqual(sent.message.notebook, input.notebook, 'the notebook exactly as the builder made it');
  assert(!('stats' in sent.message), 'and not the stats: the page has no use for them, and the widget keeps them for its banner');
  assertEqual(Object.keys(sent.message).sort(), ['datasetId', 'notebook', 'requestId', 'target', 'title', 'type'], 'only the fields the page reads');
  assert(typeof sent.message.requestId === 'string' && sent.message.requestId.length > 0, 'under a request id');
  assertEqual(state(window).outbox.length, 0, 'the outbox is empty');

  say({ type: 'ready' });
  say({ type: 'setup', status: 'done' });
  assertEqual(importPosts().length, 1, 'a second ready, and setup, post nothing more');

  say({ type: 'result', requestId: 'not-ours', ok: true, path: IMPORT_PATH });
  assertEqual(state(window).requests, 1, 'a result for a request this widget did not make settles nothing');
  assert(/^Importing /.test(bannerText(q)), 'and the banner is as it was');

  const settled = [];
  result.then((r) => settled.push(r));
  const other = (data) => window.dispatchEvent(new window.MessageEvent('message', {
    data: { source: 'osa-notebook', ...data }, origin: 'https://evil.example', source
  }));
  other({ type: 'result', requestId: sent.message.requestId, ok: true, path: IMPORT_PATH });
  window.dispatchEvent(new window.MessageEvent('message', {
    data: { source: 'osa-notebook', type: 'result', requestId: sent.message.requestId, ok: true, path: IMPORT_PATH },
    origin: NOTEBOOK_ORIGIN, source: window
  }));
  await Promise.resolve();
  assertEqual(settled.length, 0, 'a result from another origin, or not from the frame, is ignored');

  say({ type: 'result', requestId: sent.message.requestId, ok: true, path: IMPORT_PATH });
  await result;
  assertEqual(settled, [{ ok: true, path: IMPORT_PATH, error: undefined }], 'the notebook\'s result settles the promise with its path');
  assertEqual(state(window).requests, 0, 'nothing is pending');
}

console.log('\nan import while the notebook is already ready is posted at once');
{
  const { window, widget, importPosts, say } = await startNotebook();
  say({ type: 'ready' });
  assertEqual(importPosts().length, 0, 'set up: ready, nothing to post');
  widget.__notebook.importNotebookToTab(conversationInput());
  assertEqual(importPosts().length, 1, 'posted straight away');
  assertEqual(state(window).outbox.length, 0, 'and not queued as well');
}

console.log('\nthe banner counts the steps and what was left out, from the stats');
{
  for (const name of ['erp', 'notOk']) {
    const { window, widget, q, importPosts, say } = await startNotebook();
    say({ type: 'ready' });
    const input = conversationInput(name);
    widget.__notebook.importNotebookToTab(input);
    const [sent] = importPosts();
    say({ type: 'result', requestId: sent.message.requestId, ok: true, path: IMPORT_PATH });
    const { steps, leftOut } = input.stats;
    const text = bannerText(q);
    assert(text.includes(`Imported ${steps} ${steps === 1 ? 'step' : 'steps'} from your chat`), `${name}: "Imported ${steps} steps" from the stats`);
    if (leftOut.length > 0) {
      assert(text.includes(`${leftOut.length} ${leftOut.length === 1 ? 'run was' : 'runs were'} left out`), `${name}: ${leftOut.length} left out, from the stats`);
    } else {
      assert(!/left out/.test(text), `${name}: nothing left out, nothing said about it`);
    }
    assertEqual(bannerButtons(q), ['Run all', 'Undo'], `${name}: Run all and Undo`);
    assert(!window.document.querySelector('.osa-notebook-banner').hidden, `${name}: the banner is showing`);
  }
  const { q, widget, importPosts, say } = await startNotebook();
  say({ type: 'ready' });
  widget.__notebook.importNotebookToTab({ ...conversationInput(), stats: { steps: 1, leftOut: [] } });
  assert(/^Importing 1 step from/.test(bannerText(q)), 'one step is "1 step", not "1 steps"');
  say({ type: 'result', requestId: importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  assert(/Imported 1 step from your chat\./.test(bannerText(q)), 'imported: the same');
}

console.log('\nRun all and Undo post their messages, for the imported notebook, once');
{
  const { window, widget, q, posts, importPosts, say } = await startNotebook();
  say({ type: 'ready' });
  widget.__notebook.importNotebookToTab(conversationInput());
  say({ type: 'result', requestId: importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  const notebooks = (undoable) => say({
    type: 'notebooks', active: IMPORT_PATH,
    items: [
      { path: `nemar/${DATASET}.ipynb`, title: 'Playground', kind: 'playground', open: true },
      { path: IMPORT_PATH, title: 'Famous', kind: 'chat', open: true, ...(undoable === undefined ? {} : { undoable }) }
    ]
  });
  notebooks(true);

  const click = (selector) => q(selector).dispatchEvent(new window.Event('click', { bubbles: true }));
  const runPosts = () => posts.filter((p) => p.message.type === 'run-all');
  click('.osa-notebook-run-all');
  assertEqual(runPosts().length, 1, 'Run all posts run-all');
  assertEqual(runPosts()[0].message.path, IMPORT_PATH, 'for the imported notebook\'s path');
  assertEqual(runPosts()[0].targetOrigin, NOTEBOOK_ORIGIN, 'to the notebook\'s origin');
  assert(/^Running /.test(bannerText(q)), 'the banner says the cells are running');
  assert(q('.osa-notebook-run-all').disabled, 'and Run all is off meanwhile');
  click('.osa-notebook-run-all');
  assertEqual(runPosts().length, 1, 'a second click while it runs posts nothing');
  say({ type: 'result', requestId: runPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  assert(/^All cells ran/.test(bannerText(q)), 'a finished run says so');
  assertEqual(bannerButtons(q), ['Run all', 'Undo', 'Dismiss'], 'with Run all, Undo and Dismiss');

  click('.osa-notebook-run-all');
  say({ type: 'result', requestId: runPosts()[1].message.requestId, ok: false, path: IMPORT_PATH, error: 'cell 3 did not finish without an error' });
  assert(bannerText(q).includes('cell 3 did not finish without an error'), 'a run that stopped says where');
}

console.log('\nUndo posts undo-import, and the banner follows the answer');
{
  const { window, widget, q, posts, importPosts, say } = await startNotebook();
  say({ type: 'ready' });
  widget.__notebook.importNotebookToTab(conversationInput());
  say({ type: 'result', requestId: importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  const click = (selector) => q(selector).dispatchEvent(new window.Event('click', { bubbles: true }));
  const undoPosts = () => posts.filter((p) => p.message.type === 'undo-import');
  click('.osa-notebook-undo');
  assertEqual(undoPosts().length, 1, 'Undo posts undo-import');
  assertEqual(undoPosts()[0].message.path, IMPORT_PATH, 'for the imported notebook\'s path');
  assertEqual(undoPosts()[0].targetOrigin, NOTEBOOK_ORIGIN, 'to the notebook\'s origin');
  say({ type: 'result', requestId: undoPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  assert(/removed/.test(bannerText(q)), 'removed: the banner says so');
  assertEqual(bannerButtons(q), ['Dismiss'], 'with nothing left to undo or run');
  click('.osa-notebook-dismiss');
  assert(banner(q).hidden, 'Dismiss hides the banner');
  assertEqual(state(window).import, null, 'and forgets the import');
}

console.log('\nUndo is not offered while the cells run, and a late result cannot overwrite what Undo did');
{
  const { window, widget, q, posts, importPosts, say } = await startNotebook();
  say({ type: 'ready' });
  widget.__notebook.importNotebookToTab(conversationInput());
  say({ type: 'result', requestId: importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  const notebooks = () => say({
    type: 'notebooks', active: IMPORT_PATH,
    items: [
      { path: `nemar/${DATASET}.ipynb`, title: 'Playground', kind: 'playground', open: true },
      { path: IMPORT_PATH, title: 'Famous', kind: 'chat', open: true, undoable: true }
    ]
  });
  notebooks();
  const click = (selector) => q(selector).dispatchEvent(new window.Event('click', { bubbles: true }));
  const sentOf = (type) => posts.filter((p) => p.message.type === type);

  click('.osa-notebook-run-all');
  assertEqual(bannerButtons(q), ['Run all'], 'while the cells run: Undo is not offered');
  notebooks();
  assertEqual(bannerButtons(q), ['Run all'], 'and a fresh list that says it is undoable does not bring it back');
  assertEqual(q('.osa-notebook-undo'), null, 'there is no Undo button to click');
  say({ type: 'result', requestId: sentOf('run-all')[0].message.requestId, ok: true, path: IMPORT_PATH });
  assertEqual(bannerButtons(q), ['Run all', 'Undo', 'Dismiss'], 'once the run is over: Undo is back');

  // Out of order: the page answers a run after the import was undone (a run that
  // outlived its notebook, which the page now prevents but the widget must not trust).
  click('.osa-notebook-run-all');
  const lateRun = sentOf('run-all')[1].message.requestId;
  const record = state(window).import;
  record.state = 'undone';
  record.undoable = false;
  say({ type: 'result', requestId: lateRun, ok: false, path: IMPORT_PATH, error: 'the notebook was closed before every cell ran' });
  assertEqual(state(window).import.state, 'undone', 'a failed run-all result after the removal leaves it removed');
  notebooks();
  assert(/removed/.test(bannerText(q)), 'and the banner, drawn again, still says so');
  assertEqual(bannerButtons(q), ['Dismiss'], 'with no Run all for a file that is gone');
}

console.log('\nan import the reader has edited is no longer offered for Undo');
{
  const { widget, q, importPosts, say } = await startNotebook();
  say({ type: 'ready' });
  widget.__notebook.importNotebookToTab(conversationInput());
  say({ type: 'result', requestId: importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  const items = (undoable) => [
    { path: `nemar/${DATASET}.ipynb`, title: 'Playground', kind: 'playground', open: true },
    { path: IMPORT_PATH, title: 'Famous', kind: 'chat', open: true, undoable }
  ];
  say({ type: 'notebooks', active: IMPORT_PATH, items: items(true) });
  assertEqual(bannerButtons(q), ['Run all', 'Undo'], 'untouched: Undo is offered');
  say({ type: 'notebooks', active: IMPORT_PATH, items: items(false) });
  assertEqual(bannerButtons(q), ['Run all'], 'edited: the notebook says so, and Undo goes');

  // The notebook can still refuse: the edit may have come after its last list.
  const second = await startNotebook();
  second.say({ type: 'ready' });
  second.widget.__notebook.importNotebookToTab(conversationInput());
  second.say({ type: 'result', requestId: second.importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  second.q('.osa-notebook-undo').dispatchEvent(new second.window.Event('click', { bubbles: true }));
  const undo = second.posts.find((p) => p.message.type === 'undo-import');
  second.say({ type: 'result', requestId: undo.message.requestId, ok: false, error: 'it has been edited, so it was kept' });
  assert(bannerText(second.q).includes('it has been edited, so it was kept'), 'a refused Undo says why');
  assertEqual(bannerButtons(second.q), ['Run all'], 'and is not offered again');
}

console.log('\nthe banner shows only on the imported notebook\'s tab');
{
  const { widget, q, importPosts, say } = await startNotebook();
  say({ type: 'ready' });
  widget.__notebook.importNotebookToTab(conversationInput());
  say({ type: 'result', requestId: importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  const items = [
    { path: `nemar/${DATASET}.ipynb`, title: 'Playground', kind: 'playground', open: true },
    { path: IMPORT_PATH, title: 'Famous', kind: 'chat', open: true, undoable: true }
  ];
  say({ type: 'notebooks', active: IMPORT_PATH, items });
  assert(!banner(q).hidden, 'on the imported notebook: shown');
  say({ type: 'notebooks', active: items[0].path, items });
  assert(banner(q).hidden, 'on the Playground: hidden');
  say({ type: 'notebooks', active: IMPORT_PATH, items });
  assert(!banner(q).hidden, 'and back: shown again');
}

console.log('\na refused import says why, and posts nothing');
{
  const { widget, q, importPosts, say } = await startNotebook();
  say({ type: 'ready' });
  widget.__notebook.importNotebookToTab(conversationInput());
  say({ type: 'result', requestId: importPosts()[0].message.requestId, ok: false, error: 'dataset: does not look like a valid dataset id for this community' });
  assert(/does not look like a valid dataset id/.test(bannerText(q)), 'the notebook\'s reason is on the banner');
  assertEqual(bannerButtons(q), ['Dismiss'], 'with Dismiss alone');

  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => { warnings.push(args.map(String).join(' ')); };
  try {
    const fresh = await startNotebook();
    fresh.say({ type: 'ready' });
    const attempts = [
      [{ ...conversationInput(), datasetId: 'on000001' }, 'a dataset that is not the one on screen'],
      [{ ...conversationInput(), datasetId: '' }, 'no dataset id'],
      [{ ...conversationInput(), title: 7 }, 'a title that is not text'],
      [{ ...conversationInput(), notebook: null }, 'no notebook'],
      [{ ...conversationInput(), notebook: [] }, 'a notebook that is a list'],
      [null, 'no input'],
      ['x', 'a string'],
    ];
    for (const [input, what] of attempts) {
      warnings.length = 0;
      const outcome = await fresh.widget.__notebook.importNotebookToTab(input);
      assert(outcome.ok === false && typeof outcome.error === 'string', `${what}: refused with a reason`);
      assert(warnings.some((w) => w.includes('importNotebookToTab')), `${what}: and the console hears of it`);
    }
    assertEqual(fresh.importPosts().length, 0, 'none of them posted anything');
    assertEqual(state(fresh.window).requests, 0, 'or left a request pending');
    assert(banner(fresh.q).hidden, 'or showed a banner');
  } finally {
    console.warn = originalWarn;
  }
}

console.log('\nnothing to import into: no dataset, a dataset with no Zarr copy, a bubble, a failed notebook');
{
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => { warnings.push(args.map(String).join(' ')); };
  try {
    const none = await startCapsule({ dataset: null });
    assertEqual((await none.widget.__notebook.importNotebookToTab(conversationInput())).ok, false, 'no dataset on screen: refused');
    const noZarr = await startCapsule({ dataset: { id: DATASET, zarr: false } });
    assertEqual((await noZarr.widget.__notebook.importNotebookToTab(conversationInput())).ok, false, 'a dataset with no Zarr copy: refused');

    const config = configResponse({ launcher: undefined });
    delete config.widget.launcher;
    const { window, widget } = loadWidget({ fetch: fetchReturning(config) });
    widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-nbimport-bubble' });
    widget.setDataset({ id: DATASET, zarr: true });
    widget.init();
    const container = window.document.querySelector('.osa-chat-widget');
    await waitUntil(() => container.querySelector('.osa-chat-input input').placeholder === config.widget.placeholder, 'config loaded');
    assertEqual((await widget.__notebook.importNotebookToTab(conversationInput())).ok, false, 'a bubble widget has no notebook tab: refused');
    assert(!container.querySelector('.osa-notebook-frame'), 'and makes no frame');

    const failed = await startCapsule({ dataset: { id: DATASET, zarr: true } });
    failed.widget.__notebook.setTimeouts(30, 60);
    failed.click('.osa-notebook-btn');
    await waitUntil(() => state(failed.window).state === 'failed', 'the notebook never answers');
    assertEqual((await failed.widget.__notebook.importNotebookToTab(conversationInput())).ok, false, 'a notebook that failed to open: refused');
  } finally {
    console.warn = originalWarn;
  }
}

console.log('\na request that is waiting when the frame goes is answered, not left hanging');
{
  const { window, widget, q, importPosts } = await startNotebook();
  const result = widget.__notebook.importNotebookToTab(conversationInput());
  widget.setDataset({ id: 'on000001', zarr: true });
  const outcome = await result;
  assertEqual(outcome.ok, false, 'the dataset changed before the notebook was ready: the import fails');
  assert(/replaced/.test(outcome.error), 'saying the notebook was replaced');
  assertEqual(state(window).outbox.length, 0, 'the outbox is empty');
  assertEqual(state(window).requests, 0, 'nothing is pending');
  assert(banner(q).hidden, 'and the banner is gone');
  assertEqual(importPosts().length, 0, 'the import never reached the old frame');
}

console.log('\nan import for the dataset on screen when the notebook for it is not open yet opens it');
{
  const started = await startCapsule({ dataset: { id: DATASET, zarr: true } });
  assert(!started.q('.osa-notebook-frame'), 'set up: no frame yet');
  const result = started.widget.__notebook.importNotebookToTab(conversationInput());
  const frame = started.q('.osa-notebook-frame');
  assert(!!frame, 'the import makes the frame');
  assertEqual(frame.getAttribute('src'), `https://notebook.osc.earth/osa/open.html?community=nemar&dataset=${DATASET}`, 'at the notebook site\'s contract address');
  const posts = recordPosts(frame);
  bridgeMessage(started.window, { type: 'ready' }, { source: frame.contentWindow });
  const sent = posts.filter((p) => p.message.type === 'import-notebook');
  assertEqual(sent.length, 1, 'and posts the import once it is ready');
  bridgeMessage(started.window, { type: 'result', requestId: sent[0].message.requestId, ok: true, path: IMPORT_PATH }, { source: frame.contentWindow });
  assertEqual((await result).path, IMPORT_PATH, 'resolved with the path');
}

console.log(`\n${'='.repeat(60)}`);
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
