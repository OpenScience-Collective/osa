/**
 * The History sheet and saved figures in restored chats (#657, #658), run against the
 * real widget source in happy-dom windows, the way test-widget-history-store.js runs
 * the conversation store.
 *
 * What it holds the widget to: the sheet lists the saved chats by day (Today,
 * Yesterday, Earlier) with each one's title, dataset id, step and figure counts and
 * time; Open restores a chat's messages and session (the next send carries that
 * chat's session id), and a chat of another dataset than the page's shows the A1
 * notice; Delete removes an inactive and the active chat; Clear all needs two clicks
 * and leaves one empty chat; every control is off while a reply loads; Escape closes
 * the sheet and focus returns to the History button; the button is hidden on the
 * notebook tab; the chat behind the sheet is inert. And a run keeps only how many
 * figures it drew: a restored chat reads them back from the workspace into memory
 * (`run.images`), never into storage, shows a one-line note for a figure the
 * workspace no longer has, and thumbnails are made for the visible rows and revoked
 * on close.
 *
 * What stands in: `fetch` (the community config and a one-line chat reply that can be
 * held), IndexedDB (test-support/memory-indexeddb.js, under the real WorkspaceStore
 * from the real runtime bundle), and IntersectionObserver, which in a browser is
 * layout's. The real IndexedDB across a reload, and the layout at 390 px, are
 * browser-harness/history-check.mjs's.
 *
 * Run with: bun frontend/test-widget-history-panel.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { memoryIndexedDB } from './test-support/memory-indexeddb.js';

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
    if (Date.now() - started > timeoutMs) {
      throw new Error(`waitUntil timed out after ${timeoutMs}ms: ${label}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');
// The real runtime bundle, evaluated once as the browser evaluates the file; each
// window is handed the OSARuntime it defines, as its script element's load would.
// eslint-disable-next-line no-new-func
new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
const OSARuntime = globalThis.OSARuntime;

const API = 'http://localhost/api';
const KEY = 'osa-test-history-panel';
const GREETING = 'Hello from the history tests';
const A = 'on002718';
const B = 'nm000241';
const TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];
const PYTHON = { pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 60 } };
// A real 1x1 PNG: what a figure is, to the byte.
const PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const PNG_BYTES = Uint8Array.from(atob(PNG_BASE64), (c) => c.charCodeAt(0));

const DAY = 24 * 60 * 60 * 1000;
const startOfToday = new Date();
startOfToday.setHours(0, 0, 0, 0);
// Noon of a day, in the local calendar, `daysAgo` days back: far from every midnight.
const noon = (daysAgo) => {
  const date = new Date(startOfToday);
  date.setDate(date.getDate() - daysAgo);
  date.setHours(12, 0, 0, 0);
  return date.getTime();
};
// Just after this morning's midnight, so "today" holds at any time of day.
const TODAY = startOfToday.getTime() + 1000;

function serverFetch(widget, withTools) {
  const sent = [];
  const encoder = new TextEncoder();
  let gate = null;
  const answer = () => new Response(new ReadableStream({
    start(controller) {
      for (const event of [{ event: 'content', content: 'An answer.' }, { event: 'done' }]) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      }
      controller.close();
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
  const fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (u.endsWith('/chat')) {
      sent.push(JSON.parse(init.body));
      if (gate) await gate.promise;
      return answer();
    }
    return new Response(JSON.stringify({
      default_model: 'm',
      offered_models: [],
      widget: { title: 'History Tests', search_paths: ['/discover'], ...widget },
      client_tools: withTools ? TOOLS : [],
      runtime: withTools ? { python: PYTHON } : null,
    }), { headers: { 'content-type': 'application/json' } });
  };
  const hold = () => {
    let open;
    gate = { promise: new Promise((resolve) => { open = resolve; }) };
    return () => { gate = null; open(); };
  };
  return { fetch, sent, hold };
}

const run = (callId, over = {}) => ({
  callId, tool: 'execute_code', description: 'Plot the ERP', code: 'print(1)\n', status: 'ok',
  stdout: '', stderr: '', traceback: '', images: [], local: false, workspaceNote: '', ...over,
});

const chat = (id, { title, context, sessionId, updatedAt, runs = [] }) => ({
  id,
  context,
  title,
  sessionId,
  createdAt: updatedAt - 1000,
  updatedAt,
  messages: [
    { role: 'assistant', content: GREETING },
    { role: 'user', content: title },
    { role: 'assistant', content: `Answer to ${title}`, citations: [], executions: runs },
  ],
});

// A real v3 record: three chats with something said in them, and an empty one (the open chat, unless
// another is named).
//   A, today: three runs, of which two count as steps and drew a figure each; the third is
//     the reader's own run (person-run-1), which is not a step and drew nothing.
//   B, yesterday: one step, one figure.
//   C, five days ago: a dataset search, no runs.
function seededRecord(activeId = null) {
  return JSON.stringify({
    version: 3,
    // Without one named, the open chat is a new, empty one, the one a return starts.
    activeId: activeId || 'chat-empty',
    conversations: [
      { id: 'chat-empty', context: null, title: '', sessionId: null, createdAt: TODAY + 5000, updatedAt: TODAY + 5000, messages: [{ role: 'assistant', content: GREETING }] },
      chat('chat-a', {
        title: 'Compare ERPs for famous vs scrambled faces', context: { type: 'dataset', id: A }, sessionId: 'session-a', updatedAt: TODAY,
        runs: [run('call-a1', { figureCount: 1 }), run('call-a2', { figureCount: 1 }), run('person-run-1', { local: true })],
      }),
      chat('chat-b', {
        title: 'Power spectrum of sub-002', context: { type: 'dataset', id: B }, sessionId: 'session-b', updatedAt: noon(1),
        runs: [run('call-b1', { figureCount: 1 })],
      }),
      chat('chat-c', { title: 'Datasets for bilingual performance studies', context: { type: 'search' }, sessionId: 'session-c', updatedAt: noon(5) }),
    ],
  });
}

// A fresh window, as a page load in a browser session that has loaded the widget
// before. `activeId` is the seeded chat that was open; `tools` gives the community a
// Python runtime, so the figures can be read back; `dataset` is the page's.
async function start({ activeId = null, dataset = { id: A }, tools = false, widget = {}, storage = seededRecord(activeId), path = '/dataset/x', popout = false } = {}) {
  const window = new Window({
    url: `http://localhost${path}`,
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      handleDisabledFileLoadingAsSuccess: tools,
      navigation: { disableChildFrameNavigation: true },
    },
  });
  window.__OSA_TEST__ = true;
  window.sessionStorage.setItem(`${KEY}-session`, '1');
  if (storage) window.localStorage.setItem(KEY, storage);
  if (tools) window.OSARuntime = OSARuntime;
  // A pop-out is told its configuration by its opener, and never starts a chat over.
  if (popout) window.__OSA_CHAT_CONFIG__ = { apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING, fullscreen: true };
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const server = serverFetch(widget, tools);
  window.fetch = server.fetch;
  // eslint-disable-next-line no-new-func
  const load = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  load(window, window.document, window.localStorage, server.fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  if (dataset) api.setDataset(dataset);
  api.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING });
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const q = (s) => container.querySelector(s);
  const qa = (s) => [...container.querySelectorAll(s)];
  const hooks = api.__browser;
  await waitUntil(() => q('.osa-chat-title').firstChild.textContent.trim() === 'History Tests', 'the community config arrives');
  if (tools) await hooks.declaredClientTools();
  q('.osa-chat-button').click();
  return {
    window, api, hooks, container, q, qa, sent: server.sent, hold: server.hold,
    stored: () => JSON.parse(window.localStorage.getItem(KEY)),
    button: () => q('.osa-history-btn'),
    sheet: () => q('.osa-history-sheet'),
    sheetOpen: () => q('.osa-history-sheet').classList.contains('open'),
    rows: () => qa('.osa-history-row'),
  };
}

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const rowFor = (ctx, id) => ctx.rows().find((row) => row.getAttribute('data-id') === id);

async function send(ctx, question) {
  ctx.q('.osa-chat-input input').value = question;
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => !ctx.q('.osa-chat-input input').disabled, `the send of "${question}" ends`);
}

const key = (ctx, name) => new ctx.window.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true });

console.log('='.repeat(60));
console.log('Widget: the History sheet and saved figures (#657, #658)');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// The pure helpers
// ---------------------------------------------------------------------------

console.log('\nconversationSummary: steps are the assistant\'s runs, figures are all the figures');
{
  const ctx = await start();
  const { conversationSummary: summary } = ctx.hooks.history;
  const [a, b, c] = JSON.parse(seededRecord()).conversations.slice(1);
  assertEqual(summary(a), {
    id: 'chat-a', title: a.title, context: { type: 'dataset', id: A }, steps: 2, figures: 2, updatedAt: TODAY,
  }, 'two steps (the reader\'s own run is not one) and two figures');
  assertEqual(summary(b).steps, 1, 'one step');
  assertEqual(summary(b).figures, 1, 'one figure');
  assertEqual([summary(c).steps, summary(c).figures], [0, 0], 'a chat with no runs has neither');
  const reading = { ...a, messages: [{ role: 'assistant', content: 'x', executions: [run('r', { tool: 'get_full_output' }), run('s')] }] };
  assertEqual(summary(reading).steps, 1, 'reading an output back is not a step');
  const justRan = { ...a, messages: [{ role: 'assistant', content: 'x', executions: [run('r', { images: [{ mime: 'image/png', data_base64: PNG_BASE64 }] })] }] };
  assertEqual(summary(justRan).figures, 1, 'a figure still only in memory counts');
  assertEqual(summary({ id: 'x', messages: 'not a list' }).steps, 0, 'a record with no messages does not throw');
}

console.log('\ngroupByDay: Today, Yesterday and Earlier, newest first, empty days left out');
{
  const { groupByDay } = (await start()).hooks.history;
  const now = noon(0) > Date.now() ? Date.now() : noon(0);
  const items = [
    { id: 1, updatedAt: TODAY }, { id: 2, updatedAt: noon(1) }, { id: 3, updatedAt: noon(2) }, { id: 4, updatedAt: noon(30) },
  ];
  const groups = groupByDay(items, Math.max(now, TODAY));
  assertEqual(groups.map((g) => [g.key, g.label, g.items.map((i) => i.id)]), [
    ['today', 'Today', [1]], ['yesterday', 'Yesterday', [2]], ['earlier', 'Earlier', [3, 4]],
  ], 'each in its day');
  assertEqual(groupByDay([items[3]], Math.max(now, TODAY)).map((g) => g.key), ['earlier'], 'a day with none is left out');
  assertEqual(groupByDay([], Date.now()), [], 'nothing, no groups');
  const midnight = new Date(2026, 10, 10, 0, 5).getTime();
  assertEqual(groupByDay([{ id: 1, updatedAt: new Date(2026, 10, 9, 23, 55).getTime() }], midnight).map((g) => g.key), ['yesterday'],
    'ten minutes before midnight is yesterday, in the local calendar');
}

// ---------------------------------------------------------------------------
// The sheet
// ---------------------------------------------------------------------------

console.log('\nthe History button, and the sheet it opens');
{
  const ctx = await start();
  const button = ctx.button();
  assert(!!button && button.closest('.osa-header-actions') !== null, 'the header has a History button');
  assertEqual(button.getAttribute('aria-expanded'), 'false', 'collapsed while the sheet is closed');
  assert(!button.hasAttribute('aria-pressed') && !button.hasAttribute('aria-haspopup'), 'a disclosure, neither a toggle nor a menu button');
  assert(ctx.container.querySelector(`#${button.getAttribute('aria-controls')}`) === ctx.sheet(), 'aria-controls names the sheet');
  assert(!ctx.sheetOpen(), 'the sheet is closed');
  button.click();
  assert(ctx.sheetOpen(), 'a click opens it');
  assertEqual(button.getAttribute('aria-expanded'), 'true', 'the button says it is expanded');
  assertEqual(ctx.sheet().getAttribute('role'), 'dialog', 'the sheet is a dialog');
  assertEqual(ctx.sheet().getAttribute('aria-modal'), 'true', 'a modal one');
  assert(ctx.sheet().contains(ctx.window.document.activeElement), 'focus is in the sheet');
  assert(ctx.q('.osa-chat-messages').closest('[inert]') !== null, 'the messages behind it are inert');
  assert(ctx.q('.osa-chat-input').closest('[inert]') !== null, 'and the input');
  assert(ctx.q('.osa-chat-header').closest('[inert]') === null, 'the header, which has the button that closes it, is not');
  assert(ctx.q('.osa-history-scrim').classList.contains('open'), 'a scrim covers the chat');

  assertEqual(ctx.qa('.osa-history-day-label').map(text), ['Today', 'Yesterday', 'Earlier'], 'the days, in order');
  assertEqual(ctx.rows().map((row) => row.getAttribute('data-id')), ['chat-a', 'chat-b', 'chat-c'], 'one row per chat, newest first');
  const rowA = rowFor(ctx, 'chat-a');
  assertEqual(text(rowA.querySelector('.osa-history-title')), 'Compare ERPs for famous vs scrambled faces', 'a row has the chat\'s title');
  assertEqual(rowA.querySelector('.osa-history-meta code').textContent, A, 'the dataset id as code');
  const factsA = text(rowA.querySelector('.osa-history-meta'));
  assert(/2 steps/.test(factsA) && /2 figures/.test(factsA), `and its steps and figures (${factsA})`);
  // The widget writes the time and the date in the reader's locale; the test asks for them
  // the same way, so it holds in any locale it runs in.
  const timeOf = (ms) => new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  assert(factsA.endsWith(timeOf(TODAY)), `and the time, for a chat of today (${factsA})`);
  const factsB = text(rowFor(ctx, 'chat-b').querySelector('.osa-history-meta'));
  assert(/1 step\b/.test(factsB) && /1 figure\b/.test(factsB) && !/steps|figures/.test(factsB), `singular for one (${factsB})`);
  assert(factsB.startsWith(B), 'yesterday\'s row also names its dataset');
  const factsC = text(rowFor(ctx, 'chat-c').querySelector('.osa-history-meta'));
  assert(!/step|figure/.test(factsC) && /Dataset search/.test(factsC), `a chat with no runs says neither, and a search chat says so (${factsC})`);
  const earlier = new Date(noon(5));
  const dateOf = earlier.toLocaleDateString([], earlier.getFullYear() === new Date().getFullYear()
    ? { month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' });
  assert(factsC.endsWith(dateOf), `an earlier chat has its date (${factsC})`);
  assertEqual(text(ctx.q('.osa-history-footer')), `Saved in this browser only, the last ${ctx.hooks.conversationStore.MAX_CONVERSATIONS} chats. Figures and code stay in this browser's workspace until you delete it in Settings.`,
    'the footer, with the cap from the store and the truth about figures');
  assert(rowFor(ctx, 'chat-a').querySelector('.osa-history-open').getAttribute('aria-current') !== 'true', 'no row is current: the open chat is the empty one');
  assert(ctx.rows().every((row) => {
    const more = row.querySelector('.osa-history-more');
    return more.getAttribute('aria-expanded') === 'false' && !more.hasAttribute('aria-haspopup')
      && row.querySelector(`#${more.getAttribute('aria-controls')}`) === row.querySelector('.osa-history-menu');
  }), 'every row has an actions disclosure: aria-expanded and aria-controls, and no menu role it does not keep');
  assertEqual(ctx.qa('[role=menu], [role=menuitem]').length, 0, 'the sheet declares no ARIA menu, whose arrow-key model it does not implement');
}

console.log('\nEscape closes the sheet and returns focus to the button; so does the scrim');
{
  const ctx = await start();
  ctx.button().click();
  ctx.sheet().dispatchEvent(key(ctx, 'Escape'));
  assert(!ctx.sheetOpen(), 'Escape closes the sheet');
  assert(ctx.window.document.activeElement === ctx.button(), 'focus is back on the History button');
  assertEqual(ctx.button().getAttribute('aria-expanded'), 'false', 'collapsed again');
  assert(ctx.q('[inert]') === null, 'nothing is inert any more');
  ctx.button().click();
  ctx.q('.osa-history-scrim').click();
  assert(!ctx.sheetOpen() && ctx.window.document.activeElement === ctx.button(), 'a click on the scrim closes it, with the same focus');
  ctx.button().click();
  ctx.button().click();
  assert(!ctx.sheetOpen(), 'the button closes it too');
}

console.log('\na row\'s menu: Escape closes the menu first, then the sheet');
{
  const ctx = await start();
  ctx.button().click();
  const more = rowFor(ctx, 'chat-b').querySelector('.osa-history-more');
  more.click();
  const menu = rowFor(ctx, 'chat-b').querySelector('.osa-history-menu');
  assert(!menu.hidden && more.getAttribute('aria-expanded') === 'true', 'the menu opens');
  assertEqual([...menu.querySelectorAll('button')].map(text), ['Open', 'Delete'], 'with Open and Delete');
  ctx.sheet().dispatchEvent(key(ctx, 'Escape'));
  assert(menu.hidden && ctx.sheetOpen(), 'the first Escape closes only the menu');
  assert(ctx.window.document.activeElement === more, 'and returns to the row\'s button');
  ctx.sheet().dispatchEvent(key(ctx, 'Escape'));
  assert(!ctx.sheetOpen(), 'the second closes the sheet');
}

// ---------------------------------------------------------------------------
// Open
// ---------------------------------------------------------------------------

console.log('\nOpen restores the messages, and the next send carries that chat\'s session');
{
  const ctx = await start();
  ctx.button().click();
  rowFor(ctx, 'chat-a').querySelector('.osa-history-open').click();
  assert(!ctx.sheetOpen(), 'the sheet closes');
  assertEqual(ctx.hooks.getActiveConversation().id, 'chat-a', 'the chat is the active one');
  assert(text(ctx.q('.osa-chat-messages')).includes('Answer to Compare ERPs'), 'its messages are on screen');
  assertEqual(ctx.hooks.getSessionId(), 'session-a', 'its session is the working one');
  assert(ctx.q('.osa-context-notice') === null, 'the page is on the chat\'s dataset: no notice');
  assert(ctx.window.document.activeElement === ctx.q('.osa-chat-input input'), 'focus goes to the input');
  await send(ctx, 'And the N170?');
  assertEqual(ctx.sent.at(-1).session_id, 'session-a', 'the next send carries chat A\'s session id');
  assertEqual(ctx.sent.at(-1).page_context.dataset_id, A, 'and the dataset');
  assertEqual(ctx.hooks.listConversations().filter((c) => c.id === 'chat-a').length, 1, 'still one chat A, now longer');
}

console.log('\nthe menu\'s Open does the same');
{
  const ctx = await start();
  ctx.button().click();
  rowFor(ctx, 'chat-b').querySelector('.osa-history-more').click();
  rowFor(ctx, 'chat-b').querySelector('.osa-history-menu-open').click();
  assertEqual(ctx.hooks.getActiveConversation().id, 'chat-b', 'chat B is open');
  assertEqual(ctx.hooks.getSessionId(), 'session-b', 'with its session');
}

console.log('\na chat of another dataset than the page\'s shows the A1 notice, and the next message starts a new chat');
{
  const ctx = await start({ dataset: { id: B } });
  ctx.button().click();
  rowFor(ctx, 'chat-a').querySelector('.osa-history-open').click();
  const notice = ctx.q('.osa-context-notice');
  assert(!!notice, 'the notice is shown above the input');
  assert(text(notice).includes(B) && text(notice).includes(A), 'it names the page\'s dataset and the chat\'s');
  assertEqual(ctx.hooks.getSessionId(), 'session-a', 'the old chat is what is on screen');
  await send(ctx, 'What about this one?');
  assert(ctx.hooks.getActiveConversation().id !== 'chat-a', 'the message started a new chat');
  assertEqual(ctx.sent.at(-1).session_id ?? null, null, 'with no session carried over');
  assert(ctx.hooks.listConversations().some((c) => c.id === 'chat-a'), 'and chat A is still in History');
}

console.log('\nOpen of a chat another tab deleted says so, and leaves the sheet open');
{
  const ctx = await start();
  ctx.button().click();
  const record = ctx.stored();
  record.conversations = record.conversations.filter((c) => c.id !== 'chat-b');
  ctx.window.localStorage.setItem(KEY, JSON.stringify(record));
  rowFor(ctx, 'chat-b').querySelector('.osa-history-open').click();
  assert(ctx.sheetOpen(), 'the sheet stays');
  assert(/could not be opened/.test(text(ctx.q('.osa-history-note'))), 'with a line saying why');
  assert(!rowFor(ctx, 'chat-b'), 'and the list is redrawn without it');
}

// ---------------------------------------------------------------------------
// Delete, Clear all, New chat
// ---------------------------------------------------------------------------

console.log('\nDelete of an inactive chat');
{
  const ctx = await start();
  ctx.button().click();
  const activeBefore = ctx.hooks.getActiveConversation().id;
  rowFor(ctx, 'chat-b').querySelector('.osa-history-more').click();
  rowFor(ctx, 'chat-b').querySelector('.osa-history-menu-delete').click();
  assert(!rowFor(ctx, 'chat-b'), 'its row is gone');
  assertEqual(ctx.rows().map((row) => row.getAttribute('data-id')), ['chat-a', 'chat-c'], 'the others stay');
  assert(!ctx.stored().conversations.some((c) => c.id === 'chat-b'), 'and it is out of storage');
  assertEqual(ctx.hooks.getActiveConversation().id, activeBefore, 'the active chat is untouched');
  assert(ctx.sheetOpen(), 'the sheet stays open');
}

console.log('\nDelete of the active chat leaves an empty one in its place');
{
  const ctx = await start({ activeId: 'chat-a' });
  assertEqual(ctx.hooks.getActiveConversation().id, 'chat-a', 'sanity: chat A is the active chat');
  assert(text(ctx.q('.osa-chat-messages')).includes('Answer to Compare ERPs'), 'sanity: its messages are on screen');
  ctx.button().click();
  assertEqual(rowFor(ctx, 'chat-a').querySelector('.osa-history-open').getAttribute('aria-current'), 'true', 'its row is marked current');
  rowFor(ctx, 'chat-a').querySelector('.osa-history-more').click();
  rowFor(ctx, 'chat-a').querySelector('.osa-history-menu-delete').click();
  assert(!rowFor(ctx, 'chat-a'), 'its row is gone');
  assert(ctx.hooks.getActiveConversation().id !== 'chat-a', 'a new chat is the active one');
  assertEqual(ctx.hooks.getMessages().map((m) => m.content), [GREETING], 'with only the greeting');
  assertEqual(ctx.hooks.getSessionId(), null, 'and no session');
  assert(!text(ctx.q('.osa-chat-messages')).includes('Answer to Compare ERPs'), 'the chat behind the sheet shows it');
  assert(!ctx.stored().conversations.some((c) => c.id === 'chat-a'), 'chat A is out of storage');
}

console.log('\nClear all needs two clicks, and leaves one empty chat');
{
  const ctx = await start({ activeId: 'chat-a' });
  ctx.button().click();
  const clear = ctx.q('.osa-history-clear');
  assertEqual(text(clear), 'Clear all history', 'the button says what it does');
  clear.click();
  assert(/Confirm/.test(text(clear)), 'a first click asks to confirm');
  assertEqual(ctx.rows().length, 3, 'and deletes nothing');
  assert(/removes the chats only/.test(text(ctx.q('.osa-history-note'))) && /figures and code stay in this browser's workspace/.test(text(ctx.q('.osa-history-note'))),
    'and says that the figures and code stay in the workspace');
  ctx.q('.osa-history-day-label').click();
  assertEqual(text(ctx.q('.osa-history-clear')), 'Clear all history', 'a click elsewhere puts it back');
  assertEqual(text(ctx.q('.osa-history-note')), '', 'with the note');
  ctx.q('.osa-history-clear').click();
  ctx.q('.osa-history-clear').click();
  assertEqual(ctx.rows().length, 0, 'the second click clears the list');
  assert(/No saved chats/.test(text(ctx.q('.osa-history-list'))), 'which says so');
  const left = ctx.hooks.listConversations();
  assertEqual(left.length, 1, 'one chat is left');
  assertEqual(left[0].messages.map((m) => m.content), [GREETING], 'an empty one');
  assertEqual(ctx.stored().conversations.length, 1, 'and storage holds only it');
  assert(!ctx.stored().conversations.some((c) => c.messages.some((m) => m.role === 'user')), 'with nothing the reader said');
  assertEqual(ctx.hooks.getMessages().map((m) => m.content), [GREETING], 'the chat on screen is empty');
  assert(ctx.q('.osa-history-clear').disabled, 'and there is nothing left to clear');
}

console.log('\n+ New chat starts an empty chat in the page\'s context');
{
  const ctx = await start({ activeId: 'chat-b' });
  ctx.button().click();
  ctx.q('.osa-history-new').click();
  assert(!ctx.sheetOpen(), 'the sheet closes');
  const active = ctx.hooks.getActiveConversation();
  assert(active.id !== 'chat-b', 'a new conversation is active');
  assertEqual(active.context, { type: 'dataset', id: A }, 'in the page\'s dataset');
  assertEqual(ctx.hooks.getMessages().map((m) => m.content), [GREETING], 'empty');
  assert(ctx.hooks.listConversations().some((c) => c.id === 'chat-b'), 'and the chat left is saved');
  assert(ctx.window.document.activeElement === ctx.q('.osa-chat-input input'), 'focus goes to the input');
}

// ---------------------------------------------------------------------------
// The header stays live behind the sheet
// ---------------------------------------------------------------------------

console.log('\nthe header\'s Settings, Clear and Pop-out close the sheet, so it never lists a chat that is gone');
{
  const ctx = await start({ activeId: 'chat-a' });
  ctx.button().click();
  assert(ctx.sheetOpen(), 'the sheet is open on a chat that has messages');
  ctx.q('.osa-settings-btn-open').click();
  assert(!ctx.sheetOpen(), 'Settings closes the sheet, so Escape in Settings cannot close one under it');
  assert(ctx.q('.osa-settings-overlay').classList.contains('open'), 'and Settings is open');
  assertEqual(ctx.qa('[inert]').length, 0, 'with nothing left inert');
  ctx.q('.osa-settings-close-btn').click();

  ctx.button().click();
  assert(ctx.sheetOpen(), 'the sheet opens again');
  const id = ctx.hooks.getActiveConversation().id;
  ctx.q('.osa-reset-btn').click();
  assert(!ctx.sheetOpen(), 'Clear closes the sheet');
  assert(ctx.hooks.getActiveConversation().id !== id, 'on the chat it discarded');
  ctx.button().click();
  assert(!ctx.rows().some((row) => row.getAttribute('data-id') === id), 'which the sheet no longer lists, opened again');
  assert(text(ctx.q('.osa-history-note')) === '', 'with no note');

  ctx.window.open = () => null;
  ctx.window.alert = () => {};
  assert(ctx.sheetOpen(), 'the sheet is open');
  ctx.q('.osa-popout-btn').click();
  assert(!ctx.sheetOpen(), 'Pop-out closes it');
  assertEqual(ctx.qa('[inert]').length, 0, 'and the chat is live again');
}

console.log('\nthe scrim follows the header\'s height while the sheet is open, and stops when it closes');
{
  const ctx = await start();
  let height = 60;
  const header = ctx.q('.osa-chat-header');
  Object.defineProperty(header, 'offsetHeight', { get: () => height, configurable: true });
  const watchers = [];
  ctx.window.ResizeObserver = class {
    constructor(callback) { this.callback = callback; this.disconnected = false; watchers.push(this); }
    observe(target) { this.target = target; }
    disconnect() { this.disconnected = true; }
  };
  ctx.button().click();
  const scrim = ctx.q('.osa-history-scrim');
  assertEqual(scrim.style.top, '60px', 'the scrim starts below the header');
  assertEqual(watchers.length, 1, 'the header is watched');
  assert(watchers[0].target === header, 'the header itself');
  height = 84;
  watchers[0].callback();
  assertEqual(scrim.style.top, '84px', 'a title that wraps moves it');
  ctx.sheet().dispatchEvent(key(ctx, 'Escape'));
  assert(watchers[0].disconnected, 'closed, the sheet watches nothing');
}

// ---------------------------------------------------------------------------
// While a reply loads
// ---------------------------------------------------------------------------

console.log('\nwhile a reply loads, History is off, with a note');
{
  const ctx = await start();
  const release = ctx.hold();
  ctx.q('.osa-chat-input input').value = 'A slow question';
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => ctx.sent.length === 1, 'the request is on its way');
  assertEqual(ctx.button().getAttribute('aria-disabled'), 'true', 'the History button is disabled');
  assert(!ctx.button().disabled, 'but still focusable, so a keyboard user reaches it');
  assert(/reply is loading/.test(ctx.button().title), 'its title says why');
  const reason = ctx.container.querySelector(`#${ctx.button().getAttribute('aria-describedby')}`);
  assert(reason !== null && /reply is loading/.test(text(reason)), 'and so does its description, which a screen reader and touch read');
  ctx.button().click();
  assert(!ctx.sheetOpen(), 'even a click that gets through opens nothing');
  assertEqual(ctx.hooks.activateConversation('chat-b'), false, 'the store refuses to open a chat');
  assertEqual(ctx.hooks.deleteConversation(ctx.hooks.getActiveConversation().id), false, 'nor delete the chat the reply is for');
  release();
  await waitUntil(() => !ctx.q('.osa-chat-input input').disabled, 'the reply ends');
  assert(!ctx.button().hasAttribute('aria-disabled') && text(ctx.container.querySelector('#osa-history-busy')) === '', 'the button and its description are back when the reply ends');
}

console.log('\nan open sheet goes off when a reply starts, and back when it ends');
{
  const ctx = await start();
  ctx.button().click();
  ctx.hooks.setLoading(true);
  ctx.hooks.history.sync(ctx.container);
  assert(/not available while a reply is loading/.test(text(ctx.q('.osa-history-note'))), 'the sheet says so');
  assert(ctx.qa('.osa-history-open, .osa-history-more, .osa-history-new, .osa-history-clear').every((el) => el.disabled), 'every control is disabled');
  assert(!ctx.button().hasAttribute('aria-disabled'), 'the button stays, to close it');
  ctx.q('.osa-history-clear').click();
  assertEqual(ctx.hooks.clearAllConversations(), false, 'clearing is refused by the store');
  assertEqual(ctx.rows().length, 3, 'nothing was deleted');
  ctx.hooks.setLoading(false);
  ctx.hooks.history.sync(ctx.container);
  assert(text(ctx.q('.osa-history-note')) === '' && !ctx.q('.osa-history-open').disabled, 'and everything is back when it ends');
}

// ---------------------------------------------------------------------------
// The notebook tab
// ---------------------------------------------------------------------------

console.log('\nthe History button is hidden on the notebook tab, like the Clear button');
{
  const ctx = await start({ widget: { launcher: 'capsule' }, dataset: { id: 'nm000103', zarr: true } });
  const visibility = (selector) => ctx.window.getComputedStyle(ctx.q(selector)).visibility;
  assert(visibility('.osa-history-btn') !== 'hidden', 'shown on the chat tab');
  ctx.button().click();
  assert(ctx.sheetOpen(), 'sheet open on the chat tab');
  ctx.q('.osa-notebook-btn').click();
  assert(ctx.container.classList.contains('osa-tab-notebook'), 'the notebook tab is open');
  assertEqual(visibility('.osa-history-btn'), 'hidden', 'the History button is hidden');
  assertEqual(visibility('.osa-reset-btn'), 'hidden', 'as the Clear button is');
  assert(!ctx.sheetOpen(), 'and the sheet closed with the tab');
  assert(ctx.q('[inert].osa-view-chat') !== null && ctx.q('.osa-view-chat').closest('.osa-view-off') !== null, 'the chat view is off, as the tab does it');
  ctx.q('.osa-launcher-capsule .osa-chat-button').click();
  assert(visibility('.osa-history-btn') !== 'hidden', 'and back on the chat tab');
  ctx.button().click();
  assert(ctx.sheetOpen(), 'where it opens');
  assert(ctx.q('.osa-views').hasAttribute('inert'), 'with the views inert behind it');
}

// ---------------------------------------------------------------------------
// Saved figures
// ---------------------------------------------------------------------------

console.log('\na run keeps how many figures it drew, never the figures');
{
  const ctx = await start();
  const { normalizePersistedExecutions: normalize } = ctx.hooks;
  const counts = [3, 1, 9, 0, -2, 1.5, '2', null, undefined].map((figureCount) => normalize([run('c', { figureCount })])[0].figureCount);
  assertEqual(counts, [3, 1, 3, undefined, undefined, undefined, undefined, undefined, undefined],
    'a stored count is a whole number up to the cap, else none');

  const image = { mime: 'image/png', data_base64: PNG_BASE64 };
  const message = (executions) => ({ role: 'assistant', content: 'x', citations: [], executions });
  ctx.hooks.setMessages([{ role: 'user', content: 'plot' }, message([
    run('shown', { images: [image, image] }),
    run('capped', { images: [image, image, image, image, image] }),
    run('unread', { images: [], figureCount: 2 }),
    run('none'),
    run('both', { images: [image], figureCount: 3, _figuresMissing: 2, _figuresUnavailable: true, _figuresLoading: true }),
  ])]);
  ctx.hooks.saveHistory();
  const saved = ctx.stored().conversations.find((c) => c.id === ctx.hooks.getActiveConversation().id).messages[1].executions;
  assertEqual(saved.map((r) => r.figureCount), [2, 3, 2, undefined, 1], 'written from the images in memory; the stored count stands when there are none');
  assert(saved.every((r) => Array.isArray(r.images) && r.images.length === 0), 'no image is stored');
  assert(!JSON.stringify(saved).includes(PNG_BASE64), 'not a byte of one');
  assert(!/"_figures/.test(JSON.stringify(saved)), 'nor the transient flags of reading them');
}

console.log('\nfigures that cannot be read say so, and the count is kept for a later try');
{
  const ctx = await start({ activeId: 'chat-a', tools: true });
  await waitUntil(() => ctx.hooks.getMessages()[2].executions[0]._figuresUnavailable === true, 'the read fails, with no IndexedDB behind the workspace');
  const notes = ctx.qa('.osa-figure-note').map(text);
  assertEqual(notes, ['Figures from this run could not be loaded.', 'Figures from this run could not be loaded.'],
    'each run that drew a figure says it could not be loaded, which is not that the figure is gone');
  assert(ctx.hooks.getMessages()[2].executions.every((r) => r._figuresMissing === undefined), 'and none is counted as missing');
  assertEqual(ctx.qa('.osa-execution-figure img').length, 0, 'no figure is drawn');
  ctx.hooks.saveHistory();
  assertEqual(ctx.stored().conversations.find((c) => c.id === 'chat-a').messages[2].executions.map((r) => r.figureCount), [1, 1, undefined],
    'the stored count is not lost, so a later load can still find the figure');
  assert(!/_figures/.test(ctx.window.localStorage.getItem(KEY)), 'nor is the flag stored');
}

console.log('\na workspace whose read rejects is not a workspace without the figure');
{
  const ctx = await start({ activeId: 'chat-a', tools: true });
  await waitUntil(() => ctx.hooks.getMessages()[2].executions[0]._figuresUnavailable === true, 'the first read ends');
  // A real workspace whose database will not open yet: the factory answers the open with an
  // error, as IndexedDB does when the database is closed or blocked.
  const { store, failOpens } = await workspaceThatCanFail([['session-a', 'call-a1'], ['session-a', 'call-a2']]);
  failOpens(true);
  ctx.hooks.setWorkspaceStore(store);
  await ctx.hooks.hydrateFigures();
  const runs = ctx.hooks.getMessages()[2].executions;
  assert(runs.slice(0, 2).every((r) => r._figuresUnavailable === true && r._figuresMissing === undefined), 'the runs are unavailable, none missing');
  assertEqual(runs.slice(0, 2).map((r) => r.figureCount), [1, 1], 'with their counts left alone');
  assertEqual(ctx.qa('.osa-figure-note').map(text), ['Figures from this run could not be loaded.', 'Figures from this run could not be loaded.'], 'and say so');
  // The database opens again: the same store is asked again and the figures are drawn.
  failOpens(false);
  await ctx.hooks.hydrateFigures();
  assertEqual(ctx.qa('.osa-execution-figure img').length, 2, 'a later read finds both figures');
  assertEqual(ctx.qa('.osa-figure-note').length, 0, 'and the note is gone');
}

console.log('\na call id two runs share is unavailable, not missing');
{
  const ctx = await start({ activeId: 'chat-a', tools: true });
  await waitUntil(() => ctx.hooks.getMessages()[2].executions[0]._figuresUnavailable === true, 'the first read ends');
  // The reader's own runs are named person-run-N from a counter that starts over with each page load.
  ctx.hooks.getMessages()[2].executions[2].figureCount = 1;
  ctx.hooks.setWorkspaceStore(await workspaceWithFigures([['session-a', 'call-a1'], ['session-a', 'call-a2'], ['session-a', 'person-run-1'], ['session-z', 'person-run-1']]));
  await ctx.hooks.hydrateFigures();
  const runs = ctx.hooks.getMessages()[2].executions;
  assertEqual(runs[0].images.length + runs[1].images.length, 2, 'the figures that can be told are drawn');
  assertEqual([runs[2]._figuresUnavailable, runs[2]._figuresMissing], [true, undefined], 'the shared name is unavailable, and is not said to be gone');
  assertEqual(ctx.qa('.osa-figure-note').map(text), ['Figures from this run could not be loaded.'], 'with that note on that run alone');
}

// Two real workspaces over one in-memory database: one records the figures, the other is
// the one the panel reads, and its opens fail while `failOpens(true)` is in force, so the
// failure and the retry both go through WorkspaceStore's own _open and raceDeadline.
async function workspaceThatCanFail(runs) {
  const memory = memoryIndexedDB();
  let failing = false;
  const dbFactory = {
    open(name, version) {
      if (!failing) return memory.open(name, version);
      const request = {};
      setTimeout(() => {
        request.error = new Error('the database was closed');
        request.onerror?.();
      }, 0);
      return request;
    },
  };
  await workspaceWithFigures(runs, memory);
  return {
    store: new OSARuntime.WorkspaceStore({ community: 'nemar', dbFactory }),
    failOpens: (value) => { failing = value; },
  };
}

// A real workspace over the in-memory IndexedDB, with real figures recorded by its
// own recordRun, as the controller records a run.
async function workspaceWithFigures(runs, dbFactory = memoryIndexedDB()) {
  const store = new OSARuntime.WorkspaceStore({ community: 'nemar', dbFactory });
  for (const [session, callId] of runs) {
    const result = await store.recordRun({
      session, callId, status: 'ok', description: 'plot', code: 'plot()', stdout: '', stderr: '', summary: '',
      images: [{ data_base64: PNG_BASE64 }], explicitFiles: [],
    });
    if (result.failures.length > 0) throw new Error(`could not record ${callId}: ${JSON.stringify(result.failures)}`);
  }
  return store;
}

console.log('\nhydrateFigures fills run.images from the workspace, in memory only');
{
  const ctx = await start({ activeId: 'chat-a', tools: true });
  await waitUntil(() => ctx.hooks.getMessages()[2].executions[0]._figuresUnavailable === true, 'the first read, with no storage behind it, ends');
  ctx.hooks.setWorkspaceStore(await workspaceWithFigures([['session-a', 'call-a1'], ['session-a', 'call-a2'], ['session-b', 'call-b1']]));
  await ctx.hooks.hydrateFigures();
  const runs = ctx.hooks.getMessages()[2].executions;
  assertEqual(runs[0].images, [{ mime: 'image/png', data_base64: PNG_BASE64 }], 'the run has its figure, as the image a run just made has');
  assertEqual(runs[1].images.length, 1, 'so does the next');
  assertEqual(runs[2].images, [], 'the run that drew none has none');
  assert(runs.every((r) => r._figuresMissing === undefined && r._figuresUnavailable === undefined && r._figuresLoading === undefined), 'no note is left over, and nothing is marked as loading');
  const imgs = ctx.qa('.osa-execution-figure img');
  assertEqual(imgs.length, 2, 'both figures are on screen');
  assertEqual(imgs[0].getAttribute('src'), `data:image/png;base64,${PNG_BASE64}`, 'as the PNG the workspace holds');
  assertEqual(ctx.qa('.osa-figure-note').length, 0, 'with no note');

  const bytes = Uint8Array.from(atob(runs[0].images[0].data_base64), (c) => c.charCodeAt(0));
  assertEqual([...bytes], [...PNG_BYTES], 'the bytes are the ones saved, exactly');

  ctx.hooks.saveHistory();
  const raw = ctx.window.localStorage.getItem(KEY);
  assert(!raw.includes(PNG_BASE64), 'storage has not a byte of a figure');
  const storedRuns = ctx.stored().conversations.find((c) => c.id === 'chat-a').messages[2].executions;
  assertEqual(storedRuns.map((r) => r.images), [[], [], []], 'its runs have images: []');
  assertEqual(storedRuns.map((r) => r.figureCount), [1, 1, undefined], 'and their counts');
  ctx.hooks.setMessages([]);
  ctx.hooks.loadHistory();
  assertEqual(ctx.hooks.getMessages()[2].executions.map((r) => r.images), [[], [], []], 'a reload of storage has images: [] again');
  await ctx.hooks.hydrateFigures();
  assertEqual(ctx.hooks.getMessages()[2].executions[0].images.length, 1, 'and reads them back');
}

console.log('\na figure the workspace lacks among ones it has: the note counts only what is missing');
{
  const ctx = await start({ activeId: 'chat-a', tools: true });
  await waitUntil(() => ctx.hooks.getMessages()[2].executions[0]._figuresUnavailable === true, 'the first read ends');
  ctx.hooks.setWorkspaceStore(await workspaceWithFigures([['session-a', 'call-a1']]));
  await ctx.hooks.hydrateFigures();
  const runs = ctx.hooks.getMessages()[2].executions;
  assertEqual(runs[0].images.length, 1, 'the run the workspace has is drawn');
  assertEqual(runs[1]._figuresMissing, 1, 'the one it lacks is counted');
  assertEqual(ctx.qa('.osa-figure-note').length, 1, 'with one note, on that run');
  assertEqual(ctx.qa('.osa-execution-figure img').length, 1, 'and one figure');
}

console.log('\nopening a chat reads its figures; the pop-out reads the same workspace by its own init');
{
  const ctx = await start({ tools: true });
  ctx.hooks.setWorkspaceStore(await workspaceWithFigures([['session-a', 'call-a1'], ['session-a', 'call-a2'], ['session-b', 'call-b1']]));
  ctx.button().click();
  rowFor(ctx, 'chat-b').querySelector('.osa-history-open').click();
  await waitUntil(() => ctx.hooks.getMessages()[2].executions[0].images.length === 1, 'the opened chat\'s figure is read');
  assertEqual(ctx.qa('.osa-execution-figure img').length, 1, 'and drawn');
  assert(!ctx.window.localStorage.getItem(KEY).includes(PNG_BASE64), 'with nothing written to storage');
  // A pop-out is the same widget in another window of the origin: it loads the
  // active chat from storage and reads the figures with its own init, so no bytes
  // are passed to it (openPopout carries a config and a dataset, not a figure).
  const popout = await start({ tools: true, popout: true, storage: ctx.window.localStorage.getItem(KEY) });
  await waitUntil(() => popout.hooks.getMessages()[2].executions[0]._figuresUnavailable === true, 'its own first read, with no storage behind it, ends');
  popout.hooks.setWorkspaceStore(await workspaceWithFigures([['session-b', 'call-b1']]));
  await popout.hooks.hydrateFigures();
  assertEqual(popout.qa('.osa-execution-figure img').length, 1, 'a second window of the origin draws the active chat\'s figure');
}

// ---------------------------------------------------------------------------
// Thumbnails
// ---------------------------------------------------------------------------

console.log('\nthumbnails: the first figure of each visible row, an icon for the rest, revoked on close');
{
  const ctx = await start({ tools: true });
  ctx.hooks.setWorkspaceStore(await workspaceWithFigures([['session-a', 'call-a1'], ['session-a', 'call-a2'], ['session-b', 'call-b1']]));
  // What layout does in a browser: tell the observer a row is on screen.
  const observed = [];
  ctx.window.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; }
    observe(target) { observed.push(target); queueMicrotask(() => this.callback([{ isIntersecting: true, target }])); }
    unobserve() {}
    disconnect() {}
  };
  const made = [];
  const revoked = [];
  const realCreate = URL.createObjectURL;
  const realRevoke = URL.revokeObjectURL;
  URL.createObjectURL = (blob) => { const url = realCreate(blob); made.push({ url, size: blob.size, type: blob.type }); return url; };
  URL.revokeObjectURL = (url) => { revoked.push(url); realRevoke(url); };
  try {
    ctx.button().click();
    await waitUntil(() => ctx.qa('.osa-history-thumb img').length === 2, 'the two thumbnails are made');
    assertEqual(observed.length, 2, 'only the rows that drew a figure are watched for scrolling into view');
    assert(ctx.qa('.osa-history-thumb img').every((img) => img.getAttribute('src').startsWith('blob:')), 'each is an object URL');
    assert(made.every((m) => m.type === 'image/png' && m.size === PNG_BYTES.length), 'of the figure\'s exact bytes');
    assertEqual(rowFor(ctx, 'chat-c').querySelectorAll('img').length, 0, 'a chat with no figure keeps its icon');
    assert(rowFor(ctx, 'chat-c').querySelector('.osa-history-thumb svg') !== null, 'which is an icon');
    assertEqual(revoked.length, 0, 'nothing is revoked while the sheet is open');
    rowFor(ctx, 'chat-b').querySelector('.osa-history-more').click();
    assertEqual(ctx.qa('.osa-history-thumb img').length, 2, 'a menu does not redraw the thumbnails');
    ctx.sheet().dispatchEvent(key(ctx, 'Escape'));
    ctx.sheet().dispatchEvent(key(ctx, 'Escape'));
    assert(!ctx.sheetOpen(), 'the sheet is closed');
    assertEqual([...revoked].sort(), made.map((m) => m.url).sort(), 'every object URL made was revoked on close');
  } finally {
    URL.createObjectURL = realCreate;
    URL.revokeObjectURL = realRevoke;
  }
}

console.log('\nthumbnails: the rows in view are read in one call, figure 1 only; a read the sheet outlived makes no object URL');
{
  const ctx = await start({ tools: true });
  const real = await workspaceWithFigures([['session-a', 'call-a1'], ['session-a', 'call-a2'], ['session-b', 'call-b1']]);
  // The real store behind a recorder and a gate: every call is passed to it as it came.
  const calls = [];
  let gate = null;
  ctx.hooks.setWorkspaceStore({
    figuresForCalls: async (ids, options) => {
      calls.push({ ids: [...ids], options });
      if (gate) await gate.promise;
      return real.figuresForCalls(ids, options);
    },
  });
  ctx.window.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; }
    observe(target) { queueMicrotask(() => this.callback([{ isIntersecting: true, target }])); }
    unobserve() {}
    disconnect() {}
  };
  const made = [];
  const revoked = [];
  const realCreate = URL.createObjectURL;
  const realRevoke = URL.revokeObjectURL;
  URL.createObjectURL = (blob) => { const url = realCreate(blob); made.push(url); return url; };
  URL.revokeObjectURL = (url) => { revoked.push(url); realRevoke(url); };
  try {
    ctx.button().click();
    await waitUntil(() => ctx.qa('.osa-history-thumb img').length === 2, 'the thumbnails are made');
    assertEqual(calls.length, 1, 'one read for every row that came into view');
    assertEqual(calls[0].ids.sort(), ['call-a1', 'call-b1'], 'for the first figure\'s call of each chat');
    assertEqual(calls[0].options, { limit: 1 }, 'asking for figure 1 of each run only');
    ctx.sheet().dispatchEvent(key(ctx, 'Escape'));

    // Closed while a read is out, and opened again: the stale read makes nothing.
    made.length = 0;
    revoked.length = 0;
    calls.length = 0;
    let open;
    gate = { promise: new Promise((resolve) => { open = resolve; }) };
    ctx.button().click();
    await waitUntil(() => calls.length === 1, 'the read is out');
    ctx.sheet().dispatchEvent(key(ctx, 'Escape'));
    assert(!ctx.sheetOpen(), 'the sheet is closed');
    ctx.button().click();
    await waitUntil(() => calls.length === 2, 'and the sheet opened again reads for itself');
    open();
    gate = null;
    await waitUntil(() => ctx.qa('.osa-history-thumb img').length === 2, 'the new sheet\'s thumbnails are made');
    await new Promise((resolve) => setTimeout(resolve, 30));
    assertEqual(made.length, 2, 'one object URL per figure shown, none for the read the first sheet left');
    ctx.sheet().dispatchEvent(key(ctx, 'Escape'));
    assertEqual([...revoked].sort(), [...made].sort(), 'and each is revoked on close');
  } finally {
    URL.createObjectURL = realCreate;
    URL.revokeObjectURL = realRevoke;
  }
}

console.log('\nwithout the workspace, a row keeps its icon');
{
  const ctx = await start();
  ctx.button().click();
  await new Promise((resolve) => setTimeout(resolve, 50));
  assertEqual(ctx.qa('.osa-history-thumb img').length, 0, 'no thumbnail, and nothing thrown');
  assert(ctx.qa('.osa-history-thumb svg').length === 3, 'an icon on every row');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
process.exit(failed > 0 ? 1 : 0);
