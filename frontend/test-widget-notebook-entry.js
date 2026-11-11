/**
 * The notebook entry points (E4.3, #675), run against the real widget source and
 * the real runtime bundle in a happy-dom window.
 *
 * What it holds the widget to: the card under the last reply appears once the
 * active chat has one successful run (not get_full_output), not while a reply is
 * loading, not when every run failed or was denied, and again after a reload from
 * stored runs; it offers Continue in notebook only when the notebook is available
 * for the chat's dataset; Continue builds with the bundle's builder, hands it to
 * importNotebookToTab, and is disabled while it waits; Download code saves the
 * builder's exact notebook and script through saveFile; the History row menu
 * offers the same for any saved chat with a dataset and a successful run; and
 * nothing in either is built from unescaped conversation text.
 *
 * What stands in: `fetch` (HTTP fixtures for the community config), and the
 * notebook's side of the bridge (messages dispatched as if from the frame, in the
 * shapes notebook/osa-bridge.js posts, and the frame's postMessage wrapped to
 * record what the widget sends it). The files saved are recorded on their way to
 * the link click, as test-widget-tools.js records downloads. The notebooks and
 * scripts come from the real builder over test-support/notebook-conversations.js.
 *
 * Run with: bun frontend/test-widget-notebook-entry.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { buildConversationNotebook } from './osa-notebook-builder.js';
import { NOTEBOOK_CONVERSATIONS, run } from './test-support/notebook-conversations.js';

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
// The real runtime bundle, evaluated once as the browser evaluates the file; each
// window is handed a copy of the OSARuntime it defines, as its script element's load would.
// eslint-disable-next-line no-new-func
new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
const OSARuntime = globalThis.OSARuntime;

const API = 'http://localhost/api';
const KEY = 'osa-test-notebook-entry';
const GREETING = 'Hello from the notebook entry tests';
const TITLE = 'NEMAR Assistant';
const NOTEBOOK_ORIGIN = 'https://notebook.osc.earth';
const DATASET = 'on002718';
const OTHER = 'nm000241';
const TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];
const PYTHON = { pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 60 } };
const HOSTILE = '<img src=x onerror=alert(1)></code></pre><script>alert(1)</script>';
const TODAY = Date.now() - 60_000;

// A stored chat: a greeting, what the reader asked, and the reply with its runs.
function chat(id, { title, context = { type: 'dataset', id: DATASET }, runs, updatedAt = TODAY }) {
  return {
    id,
    context,
    title,
    sessionId: `session-${id}`,
    createdAt: updatedAt - 1000,
    updatedAt,
    messages: [
      { role: 'assistant', content: GREETING },
      { role: 'user', content: title },
      { role: 'assistant', content: `Answer to ${title}`, citations: [], executions: runs },
    ],
  };
}

// As the widget stores them: it does not keep a plan step's title yet (E2.4 will).
const erpRuns = () => NOTEBOOK_CONVERSATIONS.erp().executions.map(({ stepTitle, ...rest }) => rest);
const failedRuns = () => NOTEBOOK_CONVERSATIONS.onlyFailures().executions;

// A v3 record with `conversations` and the one named open.
function record(activeId, conversations) {
  return JSON.stringify({
    version: 3,
    activeId,
    conversations: [
      { id: 'chat-empty', context: null, title: '', sessionId: null, createdAt: TODAY + 5000, updatedAt: TODAY + 5000, messages: [{ role: 'assistant', content: GREETING }] },
      ...conversations,
    ],
  });
}

// What the builder makes of a stored chat, with the widget's inputs: the real builder, no stand-in.
function expectedCode(conversation) {
  return buildConversationNotebook({
    datasetId: conversation.context.id,
    title: conversation.title,
    createdAt: conversation.createdAt,
    communityName: TITLE,
    executions: conversation.messages.flatMap((m) => m.executions || []).filter((r) => r.tool !== 'get_full_output'),
  });
}

function serverFetch(tools) {
  return async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    return new Response(JSON.stringify({
      default_model: 'm',
      offered_models: [],
      widget: { title: TITLE, launcher: 'capsule' },
      client_tools: tools ? TOOLS : [],
      runtime: tools ? { python: PYTHON } : null,
    }), { headers: { 'content-type': 'application/json' } });
  };
}

// Every file the widget hands the reader, as a browser would save it (see recordDownloads
// in test-widget-tools.js): the Blob behind each address it makes, and each link it clicks
// with `download`.
function recordDownloads(window) {
  const made = [];
  const blobs = new Map();
  const realCreate = URL.createObjectURL;
  URL.createObjectURL = (blob) => {
    const url = realCreate.call(URL, blob);
    blobs.set(url, blob);
    return url;
  };
  window.document.addEventListener('click', (event) => {
    const link = event.target;
    if (!link || link.tagName !== 'A' || !link.hasAttribute('download')) return;
    event.preventDefault();
    made.push({ filename: link.download, blob: blobs.get(link.href) });
  }, true);
  return made;
}

// A capsule widget as a page load: the chats in `storage`, the dataset the page is on,
// and the bridge's side of the notebook. `tools` gives the community its runtime.
async function start({ storage, dataset = { id: DATASET, zarr: true }, tools = true, openNotebookFrame = true } = {}) {
  const window = new Window({
    url: 'http://localhost/dataset/x',
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      handleDisabledFileLoadingAsSuccess: true,
      navigation: { disableChildFrameNavigation: true },
    },
  });
  window.__OSA_TEST__ = true;
  window.sessionStorage.setItem(`${KEY}-session`, '1');
  window.localStorage.setItem(KEY, storage);
  if (tools) window.OSARuntime = { ...OSARuntime };
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const fetch = serverFetch(tools);
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const load = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  load(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  if (dataset) api.setDataset(dataset);
  api.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING });
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const q = (s) => container.querySelector(s);
  const qa = (s) => [...container.querySelectorAll(s)];
  const hooks = api.__browser;
  await waitUntil(() => container.classList.contains('osa-capsule'), 'the community config makes it a capsule');
  if (tools) await hooks.declaredClientTools();
  const click = (el) => el.dispatchEvent(new window.Event('click', { bubbles: true }));
  const chatButton = () => q('.osa-launcher-capsule .osa-chat-button');
  click(chatButton());
  const ctx = { window, api, hooks, container, q, qa, click, downloads: recordDownloads(window), posts: [], frame: null };
  if (openNotebookFrame && dataset && dataset.zarr) {
    // The notebook tab makes the frame; come back to the chat, as a reader does.
    click(q('.osa-notebook-btn'));
    ctx.frame = q('.osa-notebook-frame');
    ctx.frame.contentWindow.postMessage = (message, targetOrigin) => ctx.posts.push({ message, targetOrigin });
    click(chatButton());
  }
  ctx.say = (data) => window.dispatchEvent(new window.MessageEvent('message', {
    data: { source: 'osa-notebook', ...data }, origin: NOTEBOOK_ORIGIN, source: ctx.frame.contentWindow,
  }));
  ctx.importPosts = () => ctx.posts.filter((p) => p.message.type === 'import-notebook');
  ctx.card = () => q('.osa-nb-entry');
  ctx.button = (focus) => ctx.card()?.querySelector(`[data-focus="${focus}"]`) || null;
  ctx.labels = () => [...ctx.card().querySelectorAll('.osa-nb-entry-actions button')].map((b) => b.textContent);
  ctx.activeTab = () => (container.classList.contains('osa-tab-notebook') ? 'notebook' : 'chat');
  return ctx;
}

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const keydown = (ctx, el, key) => el.dispatchEvent(new ctx.window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
const IMPORT_PATH = `nemar/${DATASET} · Famous vs scrambled ERPs in ${DATASET}.ipynb`;
const ERP_TITLE = 'Famous vs scrambled ERPs in on002718';
const erpChat = (extra = {}) => chat('chat-erp', { title: ERP_TITLE, runs: erpRuns(), ...extra });

console.log('='.repeat(60));
console.log('Widget: Continue in notebook and Download code (E4.3, #675)');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// The pure helpers' reading of a conversation, through the card
// ---------------------------------------------------------------------------

console.log('\nthe card appears under the last reply once a run has succeeded, and after a reload');
{
  const stored = erpChat();
  const ctx = await start({ storage: record('chat-erp', [stored]) });
  assert(ctx.card() !== null, 'a chat reloaded from stored runs has its card');
  const messages = ctx.qa('.osa-chat-messages > *');
  assert(messages[messages.length - 1] === ctx.card(), 'it is the last thing in the chat, under the last reply');
  assert(messages[messages.length - 2].classList.contains('osa-message'), 'right after the reply');
  const { steps } = expectedCode(stored).stats;
  assert(steps > 1, 'set up: the chat has several steps');
  assert(text(ctx.card().querySelector('p')) === `${steps} steps ran in this chat. Keep going in a notebook with all of their code.`,
    `it says "${steps} steps ran in this chat. Keep going in a notebook with all of their code." (steps from the builder)`);
  assertEqual(ctx.labels(), ['Continue in notebook', 'Download code'], 'Continue in notebook and Download code');
  assert(ctx.card().getAttribute('role') === 'group' && ctx.card().getAttribute('aria-label') === 'Keep going in a notebook', 'the card is a labeled group');
  for (const el of ctx.card().querySelectorAll('button')) {
    assert(el.textContent.trim() !== '' && el.getAttribute('type') === 'button', `"${el.textContent}" is a labeled button`);
  }
  assert(ctx.card().querySelector('svg') === null, 'no icon in the card');

  const one = chat('chat-one', { title: 'One step', runs: [run({ code: 'print(1)', description: 'One' })] });
  const single = await start({ storage: record('chat-one', [one]) });
  assert(text(single.card().querySelector('p')).startsWith('1 step ran in this chat.'), 'one step reads "1 step ran", not "1 steps"');
}

console.log('\nnot while a reply is loading, not for failed, denied or output-reading runs');
{
  const ctx = await start({ storage: record('chat-erp', [erpChat()]) });
  ctx.hooks.setLoading(true);
  ctx.hooks.renderMessages(ctx.container);
  assert(ctx.card() === null, 'no card while a reply is loading');
  ctx.hooks.setLoading(false);
  ctx.hooks.renderMessages(ctx.container);
  assert(ctx.card() !== null, 'the card is back when it is done');

  const failed = await start({ storage: record('chat-failed', [chat('chat-failed', { title: 'All failed', runs: failedRuns() })]) });
  assert(failed.card() === null, 'no card when every run failed, was denied, timed out, was cancelled or ran out of memory');
  const reading = await start({
    storage: record('chat-read', [chat('chat-read', { title: 'Reading output', runs: [run({ tool: 'get_full_output', code: 'print(1)' })] })]),
  });
  assert(reading.card() === null, 'no card when the only run read an output back (get_full_output)');
  const empty = await start({ storage: record('chat-blank', [chat('chat-blank', { title: 'Empty code', runs: [run({ code: '   ' })] })]) });
  assert(empty.card() === null, 'no card for a run that succeeded with no code');
  const none = await start({ storage: record('chat-none', [chat('chat-none', { title: 'No runs', runs: [] })]) });
  assert(none.card() === null, 'no card for a chat with no runs');
  const search = await start({ storage: record('chat-search', [chat('chat-search', { title: 'Search chat', context: { type: 'search' }, runs: erpRuns() })]) });
  assert(search.card() === null, 'no card for a chat that is not about a dataset: nothing to name the notebook from');

  // A reply that is the last assistant message only: runs in an earlier reply still count.
  const earlier = erpChat();
  earlier.messages.push({ role: 'user', content: 'And?' }, { role: 'assistant', content: 'Nothing more to run.', citations: [] });
  const later = await start({ storage: record('chat-erp', [earlier]) });
  const replies = later.qa('.osa-chat-messages > *');
  assert(later.card() !== null && replies[replies.length - 1] === later.card(), 'runs in an earlier reply put the card under the last one');
  assertEqual(later.qa('.osa-nb-entry').length, 1, 'and there is one card');
}

console.log('\na community with no runtime has no card, and one that loads its runtime late gets it');
{
  const ctx = await start({ storage: record('chat-erp', [erpChat()]), tools: false });
  assert(ctx.card() === null, 'no runtime: no card');
  assert(ctx.container.querySelector('.osa-nb-entry') === null, 'and none anywhere');
  ctx.window.OSARuntime = { ...OSARuntime };
  ctx.hooks.setUpBrowserTools({ client_tools: TOOLS, runtime: { python: PYTHON } });
  await ctx.hooks.declaredClientTools();
  await waitUntil(() => ctx.card() !== null, 'the card is drawn once the bundle has loaded');
  assert(ctx.card() !== null, 'the bundle loaded late: the card is drawn without another message');
}

// ---------------------------------------------------------------------------
// Continue in notebook: when it is offered
// ---------------------------------------------------------------------------

console.log('\nContinue in notebook is offered only for the page\'s own dataset, with a notebook');
{
  const stored = erpChat();
  const elsewhere = await start({ storage: record('chat-erp', [stored]), dataset: { id: OTHER, zarr: true }, openNotebookFrame: false });
  assertEqual(elsewhere.labels(), ['Download code'], 'another dataset on the page: only Download code');
  assert(/^\d+ steps ran in this chat\. Download all of their code/.test(text(elsewhere.card().querySelector('p'))),
    'and the sentence does not promise a notebook it cannot open');
  const noZarr = await start({ storage: record('chat-erp', [stored]), dataset: { id: DATASET, zarr: false }, openNotebookFrame: false });
  assertEqual(noZarr.labels(), ['Download code'], 'a dataset with no Zarr copy: only Download code');
  const unknown = await start({ storage: record('chat-erp', [stored]), dataset: { id: DATASET }, openNotebookFrame: false });
  assertEqual(unknown.labels(), ['Download code'], 'a dataset not yet known to have one: only Download code');
  const noPage = await start({ storage: record('chat-erp', [stored]), dataset: null, openNotebookFrame: false });
  assertEqual(noPage.labels(), ['Download code'], 'a page with no dataset: only Download code');

  // The page learns it, and the card follows without a new message.
  unknown.api.setDataset({ id: DATASET, zarr: true });
  assertEqual(unknown.labels(), ['Continue in notebook', 'Download code'], 'setDataset with a Zarr copy: Continue appears in place');
  unknown.api.setDataset({ id: OTHER, zarr: true });
  assertEqual(unknown.labels(), ['Download code'], 'and goes when the page is another dataset');
}

// ---------------------------------------------------------------------------
// Continue in notebook: the import
// ---------------------------------------------------------------------------

console.log('\nContinue builds with the bundle, imports, waits disabled, and becomes Open notebook');
{
  const stored = erpChat();
  const ctx = await start({ storage: record('chat-erp', [stored]) });
  const expected = expectedCode(stored);
  ctx.say({ type: 'ready' });
  ctx.click(ctx.button('continue'));
  assert(ctx.activeTab() === 'notebook', 'it opens the notebook tab');
  assertEqual(ctx.importPosts().length, 1, 'the import is posted once');
  const [sent] = ctx.importPosts();
  assertEqual(sent.targetOrigin, NOTEBOOK_ORIGIN, 'to the notebook\'s origin');
  assertEqual(sent.message.datasetId, DATASET, 'with the chat\'s dataset');
  assertEqual(sent.message.title, ERP_TITLE, 'the chat\'s title');
  assertEqual(sent.message.notebook, expected.notebook, 'and the notebook exactly as the real builder makes it from the chat\'s runs');
  assert(ctx.button('continue').disabled, 'the button is disabled while it waits');
  assert(ctx.button('continue').getAttribute('aria-busy') === 'true', 'and says it is busy');
  assert(/Sending the code to the notebook/.test(text(ctx.card())), 'with a status line');
  ctx.click(ctx.button('continue'));
  assertEqual(ctx.importPosts().length, 1, 'a second click while it waits imports nothing more');

  ctx.say({ type: 'result', requestId: sent.message.requestId, ok: true, path: IMPORT_PATH });
  await waitUntil(() => ctx.button('open') !== null, 'the card becomes Open notebook');
  assertEqual(ctx.labels(), ['Open notebook', 'Download code'], 'after success: Open notebook, and Download code');
  assert(ctx.card().querySelector('[role="alert"]') === null, 'with nothing to alert');
  // Back to the chat, where the card is, and open the notebook from it.
  ctx.click(ctx.q('.osa-launcher-capsule .osa-chat-button'));
  assert(ctx.activeTab() === 'chat', 'set up: back on the chat tab');
  ctx.click(ctx.button('open'));
  assert(ctx.activeTab() === 'notebook', 'Open notebook switches to the notebook tab');
  assertEqual(ctx.importPosts().length, 1, 'and does not import a second time');
  const opens = ctx.posts.filter((p) => p.message.type === 'open-notebook');
  assertEqual(opens.map((p) => p.message.path), [IMPORT_PATH], 'it asks the notebook to show the imported one');

  // A new run changes the step count: Continue again.
  ctx.click(ctx.q('.osa-launcher-capsule .osa-chat-button'));
  const messages = ctx.hooks.getMessages();
  messages[messages.length - 1].executions.push(run({ description: 'A new run', code: 'print("new")', stdout: 'new\n' }));
  ctx.hooks.renderMessages(ctx.container);
  assertEqual(ctx.labels(), ['Continue in notebook', 'Download code'], 'a new run changes the step count: Continue in notebook is back');
  assert(/^\d+ steps ran/.test(text(ctx.card().querySelector('p'))) && text(ctx.card().querySelector('p')).startsWith(`${expected.stats.steps + 1} steps`),
    'with the new count');
  // A run that did not succeed leaves the count, and the card as it was.
  ctx.click(ctx.button('continue'));
  ctx.say({ type: 'result', requestId: ctx.importPosts()[1].message.requestId, ok: true, path: IMPORT_PATH });
  await waitUntil(() => ctx.button('open') !== null, 'the second import is done');
  messages[messages.length - 1].executions.push(run({ code: 'boom()', status: 'error', stderr: 'NameError: boom' }));
  ctx.hooks.renderMessages(ctx.container);
  assertEqual(ctx.labels(), ['Open notebook', 'Download code'], 'a failed run does not change the step count: still Open notebook');
}

console.log('\nUndo in the notebook puts Continue back, and a refusal or error is said inline');
{
  const ctx = await start({ storage: record('chat-erp', [erpChat()]) });
  ctx.say({ type: 'ready' });
  ctx.click(ctx.button('continue'));
  ctx.say({ type: 'result', requestId: ctx.importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  await waitUntil(() => ctx.button('open') !== null, 'imported');
  ctx.say({ type: 'notebooks', active: IMPORT_PATH, items: [{ path: IMPORT_PATH, title: 'Famous', kind: 'chat', open: true, undoable: true }] });
  ctx.click(ctx.q('.osa-notebook-undo'));
  ctx.say({ type: 'result', requestId: ctx.posts.find((p) => p.message.type === 'undo-import').message.requestId, ok: true });
  await waitUntil(() => ctx.window.OSAChatWidget.__notebook.state().import.state === 'undone', 'undone');
  ctx.click(ctx.q('.osa-launcher-capsule .osa-chat-button'));
  assertEqual(ctx.labels(), ['Continue in notebook', 'Download code'], 'after Undo the card offers Continue in notebook again');

  ctx.click(ctx.button('continue'));
  ctx.say({ type: 'result', requestId: ctx.importPosts()[1].message.requestId, ok: false, error: HOSTILE });
  await waitUntil(() => ctx.window.OSAChatWidget.__notebook.state().import.state === 'error', 'the import failed');
  ctx.click(ctx.q('.osa-launcher-capsule .osa-chat-button'));
  const alert = ctx.card().querySelector('[role="alert"]');
  assert(alert !== null, 'a failed import is said inline, with role=alert');
  assertEqual(alert.textContent, HOSTILE, 'with the notebook\'s words, as text');
  assert(alert.querySelector('*') === null && ctx.card().querySelector('img, script') === null, 'and nothing in it is markup');
  assert(!ctx.button('continue').disabled, 'Continue is available to try again');
  assert(ctx.card().querySelector('.osa-nb-entry-status') === null, 'and no longer says it is sending');
}

console.log('\na notebook that never answers is said in the card, and the card can try again');
{
  const ctx = await start({ storage: record('chat-erp', [erpChat()]) });
  ctx.window.OSAChatWidget.__notebook.setRequestTimeouts(40, 5000);
  ctx.click(ctx.button('continue'));
  assert(ctx.button('continue').disabled, 'set up: it is waiting');
  await waitUntil(() => ctx.window.OSAChatWidget.__notebook.state().import.state === 'error', 'the request times out');
  ctx.click(ctx.q('.osa-launcher-capsule .osa-chat-button'));
  const alert = ctx.card().querySelector('[role="alert"]');
  assert(alert !== null && /did not answer/.test(alert.textContent), 'the card says the notebook did not answer, with role=alert');
  assert(!ctx.button('continue').disabled, 'and Continue is available again');
  // A change in the step count makes the old failure stale.
  const messages = ctx.hooks.getMessages();
  messages[messages.length - 1].executions.push(run({ code: 'print(1)' }));
  ctx.hooks.renderMessages(ctx.container);
  assert(ctx.card().querySelector('[role="alert"]') === null, 'a new run clears a failure that was about the old code');
}

// ---------------------------------------------------------------------------
// Download code
// ---------------------------------------------------------------------------

console.log('\nDownload code: a menu of two files, saved from the builder\'s exact output');
{
  const stored = erpChat();
  const ctx = await start({ storage: record('chat-erp', [stored]) });
  const expected = expectedCode(stored);
  const menu = () => ctx.card().querySelector('.osa-nb-entry-menu');
  const download = ctx.button('download');
  assert(menu().hidden && download.getAttribute('aria-expanded') === 'false' && download.getAttribute('aria-haspopup') === 'menu', 'the menu is closed, and the button says it opens one');
  ctx.click(download);
  assert(!menu().hidden && ctx.button('download').getAttribute('aria-expanded') === 'true', 'a click opens it');
  assertEqual([...menu().querySelectorAll('[role="menuitem"]')].map((b) => b.textContent), ['Notebook (.ipynb)', 'Python script with cells (.py)'], 'with the two files');
  assert(text(menu()).includes('Opens as cells in VS Code, Spyder and Jupytext.'), 'and the note about cells');
  assert(ctx.window.document.activeElement === ctx.button('ipynb'), 'focus moves to the first item');
  keydown(ctx, ctx.window.document.activeElement, 'ArrowDown');
  assert(ctx.window.document.activeElement === ctx.button('py'), 'ArrowDown moves to the next item');
  keydown(ctx, ctx.window.document.activeElement, 'ArrowDown');
  assert(ctx.window.document.activeElement === ctx.button('ipynb'), 'and wraps');

  keydown(ctx, ctx.window.document.activeElement, 'Escape');
  assert(menu().hidden, 'Escape closes the menu');
  assert(ctx.window.document.activeElement === ctx.button('download'), 'and returns focus to Download code');
  assert(ctx.q('.osa-chat-window').classList.contains('open'), 'without closing the panel');

  ctx.click(ctx.button('download'));
  ctx.click(ctx.button('ipynb'));
  assertEqual(ctx.downloads.length, 1, 'choosing the notebook saves one file');
  assertEqual(ctx.downloads[0].filename, 'on002718-chat.ipynb', 'named from the dataset');
  const saved = JSON.parse(await ctx.downloads[0].blob.text());
  assertEqual(saved, expected.notebook, 'the notebook JSON is the builder\'s exactly');
  assert(menu().hidden && ctx.window.document.activeElement === ctx.button('download'), 'the menu closes and focus returns');

  ctx.click(ctx.button('download'));
  ctx.click(ctx.button('py'));
  assertEqual(ctx.downloads[1].filename, 'on002718-chat.py', 'the script is named the same way');
  assertEqual(await ctx.downloads[1].blob.text(), expected.script, 'and holds the builder\'s script text exactly');
  assert(ctx.downloads[1].blob.type.startsWith('text/'), 'as text');

  // Tabbing away closes it.
  ctx.click(ctx.button('download'));
  ctx.button('ipynb').dispatchEvent(new ctx.window.FocusEvent('focusout', { bubbles: true, relatedTarget: ctx.q('.osa-chat-input input') }));
  assert(menu().hidden, 'focus moving out of the card closes the menu');

  // Download code is there, and the same, when Continue is not.
  const elsewhere = await start({ storage: record('chat-erp', [stored]), dataset: { id: OTHER, zarr: true }, openNotebookFrame: false });
  elsewhere.click(elsewhere.button('download'));
  elsewhere.click(elsewhere.button('py'));
  assertEqual(elsewhere.downloads[0].filename, 'on002718-chat.py', 'on another dataset\'s page the file is still named for the chat\'s dataset');
  assertEqual(await elsewhere.downloads[0].blob.text(), expected.script, 'with the same script');
}

// ---------------------------------------------------------------------------
// A builder that fails, and text that is not the widget's
// ---------------------------------------------------------------------------

console.log('\na builder that throws, or finds no step, never breaks rendering');
{
  const ctx = await start({ storage: record('chat-erp', [erpChat()]) });
  const warn = console.warn;
  console.warn = () => {};
  try {
    ctx.window.OSARuntime.buildConversationNotebook = () => { throw new Error('the builder broke'); };
    ctx.hooks.renderMessages(ctx.container);
    assert(ctx.card() === null, 'a builder that throws: the card is hidden');
    assert(ctx.qa('.osa-message').length === 3, 'and the conversation is drawn');
    ctx.window.OSARuntime.buildConversationNotebook = () => ({ notebook: {}, script: '', stats: { steps: 0, leftOut: [] } });
    ctx.hooks.renderMessages(ctx.container);
    assert(ctx.card() === null, 'a builder that finds no step: the card is hidden');
  } finally {
    console.warn = warn;
  }
  assert(ctx.qa('.osa-message').length === 3, 'and the conversation is drawn');

  // A builder that works when the card is drawn and fails at the click says so in the card.
  const shown = await start({ storage: record('chat-erp', [erpChat()]) });
  shown.click(shown.button('download'));
  const realBuilder = shown.window.OSARuntime.buildConversationNotebook;
  let calls = 0;
  shown.window.OSARuntime.buildConversationNotebook = (input) => {
    calls += 1;
    if (calls === 1) throw new Error('broke at the click');
    return realBuilder(input);
  };
  const error = console.error;
  const warn2 = console.warn;
  console.error = () => {};
  console.warn = () => {};
  try {
    shown.click(shown.button('ipynb'));
  } finally {
    console.error = error;
    console.warn = warn2;
  }
  assertEqual(shown.downloads.length, 0, 'a builder that throws at the click saves nothing');
  const alert = shown.card() && shown.card().querySelector('[role="alert"]');
  assert(alert && alert.textContent.includes('broke at the click'), 'and the card says why, with role=alert');
}

console.log('\nconversation text reaches the card and the menu only as text');
{
  const stored = chat('chat-hostile', { title: HOSTILE, runs: [run({ description: HOSTILE, code: `print(${JSON.stringify(HOSTILE)})`, stdout: HOSTILE })] });
  const ctx = await start({ storage: record('chat-hostile', [stored]) });
  assert(ctx.card() !== null, 'a chat with a hostile title and run still gets its card');
  assert(!ctx.card().innerHTML.includes('<img') && ctx.card().querySelector('img, script, iframe') === null, 'nothing from the chat is markup in the card');
  assert(!ctx.card().innerHTML.includes('alert(1)'), 'and none of its text is in the card');
  ctx.say({ type: 'ready' });
  ctx.click(ctx.button('continue'));
  assertEqual(ctx.importPosts()[0].message.title, ctx.hooks.conversationStore.conversationTitle(stored.messages), 'the title still goes to the notebook as data, as the chat\'s own (cut) title');

  ctx.click(ctx.q('.osa-launcher-capsule .osa-chat-button'));
  ctx.hooks.history.open(ctx.container);
  assert(ctx.q('.osa-history-sheet').classList.contains('open'), 'set up: the sheet is open');
  const menus = ctx.qa('.osa-history-menu');
  for (const menu of menus) {
    assert(!menu.innerHTML.includes('onerror') && menu.querySelector('img, script') === null, 'no row menu holds the chat\'s text as markup');
  }
}

// ---------------------------------------------------------------------------
// The History row menu
// ---------------------------------------------------------------------------

function historyChats() {
  return [
    chat('chat-here', { title: 'Compare ERPs', runs: erpRuns(), updatedAt: TODAY }),
    chat('chat-other', { title: 'Spectra on another dataset', context: { type: 'dataset', id: OTHER }, runs: [run({ code: 'print(2)', description: 'Spectrum' })], updatedAt: TODAY - 1000 }),
    chat('chat-failed', { title: 'Everything failed', runs: failedRuns(), updatedAt: TODAY - 2000 }),
    chat('chat-search', { title: 'Search with runs', context: { type: 'search' }, runs: erpRuns(), updatedAt: TODAY - 3000 }),
    chat('chat-norun', { title: 'Only talk', runs: [], updatedAt: TODAY - 4000 }),
  ];
}

const rowFor = (ctx, id) => ctx.qa('.osa-history-row').find((row) => row.getAttribute('data-id') === id);
const menuLabels = (row) => [...row.querySelectorAll('.osa-history-menu > button')].map((b) => b.textContent);

console.log('\nthe History row menu gains the two items for a dataset chat with a successful run');
{
  // The open chat is the empty one: every row here is a non-active conversation.
  const ctx = await start({ storage: record('chat-empty', historyChats()) });
  ctx.hooks.history.open(ctx.container);
  assertEqual(menuLabels(rowFor(ctx, 'chat-here')), ['Open', 'Continue in notebook', 'Download code', 'Delete'], 'a chat on the page\'s dataset: Open, Continue, Download code, Delete');
  const here = rowFor(ctx, 'chat-here');
  const continueItem = here.querySelector('.osa-history-menu-continue');
  assert(!continueItem.disabled, 'Continue is enabled for the page\'s own dataset');
  assert(here.querySelector('.osa-history-menu-reason') === null || here.querySelector('.osa-history-menu-reason').closest('.osa-history-save-choices') !== null,
    'and there is no reason line for it');

  const other = rowFor(ctx, 'chat-other');
  assertEqual(menuLabels(other), ['Open', 'Continue in notebook', 'Download code', 'Delete'], 'a chat on another dataset has the same items');
  const disabled = other.querySelector('.osa-history-menu-continue');
  assert(disabled.disabled, 'Continue is disabled when the dataset is not the page\'s');
  const reasonId = disabled.getAttribute('aria-describedby');
  const reason = other.querySelector(`#${reasonId}`);
  assert(reason !== null && text(reason).includes(OTHER) && !reason.hidden, 'with a visible reason that names the dataset');
  assertEqual(menuLabels(rowFor(ctx, 'chat-failed')), ['Open', 'Delete'], 'a chat whose runs all failed has neither');
  assertEqual(menuLabels(rowFor(ctx, 'chat-search')), ['Open', 'Delete'], 'a dataset search has neither');
  assertEqual(menuLabels(rowFor(ctx, 'chat-norun')), ['Open', 'Delete'], 'a chat with no runs has neither');

  // The notebook not being available is a reason too.
  ctx.api.setDataset({ id: DATASET, zarr: false });
  ctx.hooks.history.close(ctx.container);
  ctx.hooks.history.open(ctx.container);
  const noNotebook = rowFor(ctx, 'chat-here').querySelector('.osa-history-menu-continue');
  assert(noNotebook.disabled && text(rowFor(ctx, 'chat-here')).includes('not available'), 'a dataset with no notebook: disabled, and says it is not available');
  ctx.api.setDataset({ id: DATASET, zarr: true });

  const noRuntime = await start({ storage: record('chat-empty', historyChats()), tools: false, openNotebookFrame: false });
  noRuntime.hooks.history.open(noRuntime.container);
  assertEqual(menuLabels(rowFor(noRuntime, 'chat-here')), ['Open', 'Delete'], 'a community with no runtime has neither item');
}

console.log('\nContinue works on a non-active chat, and closes the sheet');
{
  const chats = historyChats();
  const ctx = await start({ storage: record('chat-empty', chats) });
  ctx.say({ type: 'ready' });
  ctx.hooks.history.open(ctx.container);
  const activeBefore = ctx.hooks.getActiveConversation().id;
  const row = rowFor(ctx, 'chat-here');
  ctx.click(row.querySelector('.osa-history-more'));
  assert(!row.querySelector('.osa-history-menu').hidden, 'set up: the row menu is open');
  ctx.click(row.querySelector('.osa-history-menu-continue'));
  assert(!ctx.q('.osa-history-sheet').classList.contains('open') && !ctx.hooks.history.isOpen(), 'the sheet closes');
  assertEqual(ctx.importPosts().length, 1, 'one import is posted');
  assertEqual(ctx.importPosts()[0].message.notebook, expectedCode(chats[0]).notebook, 'for that chat\'s runs, from the real builder');
  assertEqual(ctx.importPosts()[0].message.title, 'Compare ERPs', 'under its title');
  assertEqual(ctx.hooks.getActiveConversation().id, activeBefore, 'the open chat did not change');
  assert(ctx.activeTab() === 'notebook', 'the notebook tab is showing');
  ctx.say({ type: 'result', requestId: ctx.importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  await waitUntil(() => ctx.window.OSAChatWidget.__notebook.state().import.state === 'imported', 'the import finished');
  assert(ctx.card() === null, 'the chat the reader has open has no run, so no card: the other chat\'s import is not its');
}

console.log('\nContinue on the open chat, from History, is the same import the card makes');
{
  const chats = historyChats();
  const ctx = await start({ storage: record('chat-here', chats) });
  ctx.say({ type: 'ready' });
  assert(ctx.card() !== null, 'set up: the open chat has its card');
  ctx.hooks.history.open(ctx.container);
  ctx.click(rowFor(ctx, 'chat-here').querySelector('.osa-history-more'));
  ctx.click(rowFor(ctx, 'chat-here').querySelector('.osa-history-menu-continue'));
  assertEqual(ctx.importPosts().length, 1, 'one import is posted');
  ctx.say({ type: 'result', requestId: ctx.importPosts()[0].message.requestId, ok: true, path: IMPORT_PATH });
  await waitUntil(() => ctx.window.OSAChatWidget.__notebook.state().import.state === 'imported', 'the import finished');
  ctx.click(ctx.q('.osa-launcher-capsule .osa-chat-button'));
  assertEqual(ctx.labels(), ['Open notebook', 'Download code'], 'the card knows, and offers Open notebook, not a second import');
}

console.log('\nDownload code from History saves any saved chat\'s files');
{
  const chats = historyChats();
  const ctx = await start({ storage: record('chat-empty', chats) });
  ctx.hooks.history.open(ctx.container);
  const row = rowFor(ctx, 'chat-other');
  ctx.click(row.querySelector('.osa-history-more'));
  const choices = row.querySelector('.osa-history-save-choices');
  const toggle = row.querySelector('.osa-history-menu-download');
  assert(choices.hidden && toggle.getAttribute('aria-expanded') === 'false', 'the two files are folded away');
  ctx.click(toggle);
  assert(!choices.hidden && toggle.getAttribute('aria-expanded') === 'true', 'Download code opens them');
  assertEqual([...choices.querySelectorAll('button')].map((b) => b.textContent), ['Notebook (.ipynb)', 'Python script with cells (.py)'], 'the same two');
  assert(text(choices).includes('Opens as cells in VS Code, Spyder and Jupytext.'), 'and the note');
  ctx.click(choices.querySelectorAll('button')[1]);
  assertEqual(ctx.downloads[0].filename, `${OTHER}-chat.py`, 'named from that chat\'s own dataset, though the page is on another');
  assertEqual(await ctx.downloads[0].blob.text(), expectedCode(chats[1]).script, 'with the builder\'s script for it');
  assert(ctx.hooks.history.isOpen(), 'the sheet stays open');

  const here = rowFor(ctx, 'chat-here');
  ctx.click(here.querySelector('.osa-history-more'));
  ctx.click(here.querySelector('.osa-history-menu-download'));
  ctx.click(here.querySelector('.osa-history-menu-save[data-format="ipynb"]'));
  assertEqual(ctx.downloads[1].filename, `${DATASET}-chat.ipynb`, 'the notebook is named the same way');
  assertEqual(JSON.parse(await ctx.downloads[1].blob.text()), expectedCode(chats[0]).notebook, 'with the builder\'s notebook exactly');
}

console.log('\nthe existing menu still closes on Escape and keeps focus in the sheet with the new items');
{
  const ctx = await start({ storage: record('chat-empty', historyChats()) });
  ctx.hooks.history.open(ctx.container);
  const row = rowFor(ctx, 'chat-here');
  ctx.click(row.querySelector('.osa-history-more'));
  assert(ctx.window.document.activeElement === row.querySelector('.osa-history-menu-open'), 'opening the menu focuses Open, as before');
  ctx.click(row.querySelector('.osa-history-menu-download'));
  const escape = () => ctx.container.dispatchEvent(new ctx.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  escape();
  assert(row.querySelector('.osa-history-menu').hidden && ctx.hooks.history.isOpen(), 'Escape closes the menu and leaves the sheet');
  assert(ctx.window.document.activeElement === row.querySelector('.osa-history-more'), 'with focus back on the row\'s button');
  escape();
  assert(!ctx.hooks.history.isOpen(), 'a second Escape closes the sheet');
}

console.log(`\n${'='.repeat(60)}`);
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
// Each window leaves its notebook load timers behind; the suite is done.
process.exit(failed > 0 ? 1 : 0);
