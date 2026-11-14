/**
 * The host API and the center-stage dialog (E6.1, #701), run against the real widget
 * source in happy-dom windows, the way test-widget-history-panel.js runs the History
 * sheet.
 *
 * What it holds the widget to: OSAChatWidget has open, close, ask, setMode and on; a
 * call made before init() is kept and replayed in order; a bad mode, event name,
 * handler or empty question warns and does nothing; the events open, close, modechange
 * and send each fire once with plain data, and a handler that throws, or rejects,
 * cannot break the widget or another handler; on() returns the way to stop listening.
 * ask() goes through sendMessage, so History, the E1.3 context reset and the
 * loading guard apply. setMode('center') makes the one window a modal dialog (role,
 * aria-modal, a scrim, an inert page, no page scroll); Tab and Shift+Tab stay in it;
 * Escape and "Move to corner" dock it with the thread, the session and the input
 * untouched; Escape closes the History sheet or Settings first; Close gives focus back
 * to what had it before the window opened. The start screen (board C2) is for an empty
 * conversation only, its pills send their text, and its cards, note and docs link come
 * from the community config, the link only when it is an https address. The notebook is a
 * tab of the dialog (#738): the header's Chat and Notebook tabs show from the start (#743),
 * before any code has run, for a capsule whose dataset has a notebook and for no other, the tab keeps the one frame (never moved)
 * through switches and docking, open() and ask() show the chat, the tabs take arrow keys,
 * and Tab cannot strand focus after the frame.
 *
 * What stands in: `fetch` (the community config, and a one-line chat reply that can be
 * held). Layout, the real Tab key and the contrast of the colors are
 * browser-harness/center-stage-check.mjs's.
 *
 * Run with: bun frontend/test-widget-host-api.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

let passed = 0;
let failed = 0;

const SUITE_TIMEOUT_MS = 60_000;
const watchdog = setTimeout(() => {
  console.error(`\n  x FAIL: the suite did not finish within ${SUITE_TIMEOUT_MS / 1000}s.`);
  process.exit(1);
}, SUITE_TIMEOUT_MS);
watchdog.unref?.();

// The suite's own reports use the console as it was at the start, so a failure that
// happens while listen() holds the console is still printed, not collected as a warning.
const report = { log: console.log, error: console.error };

function assert(cond, msg) {
  if (cond) {
    report.log(`  ok ${msg}`);
    passed++;
  } else {
    report.error(`  x FAIL: ${msg}`);
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

const API = 'http://localhost/api';
const KEY = 'osa-test-host-api';
const GREETING = 'Hello from the host API tests';
const A = 'on002718';
const B = 'nm000241';
const TITLE = 'Face processing EEG dataset for EEGLAB';
const DOCS = { title: 'Analyze data in your browser', url: 'https://docs.example.org/web/analyze/' };
const CENTER_START = {
  capabilities: [
    { title: 'Describe', text: 'Summaries and citations.' },
    { title: 'Look closer', text: 'Events and channels.' },
    { title: 'Analyze', text: 'Runs Python in your browser.' },
  ],
  note: 'Code runs in your browser. AI can make mistakes.',
};
const SUGGESTED = ['What can you do?', 'Find resting state datasets'];
const DATASET_QUESTIONS = [{ text: 'What is {dataset_id} about?' }, { text: 'How do I download {dataset_id}?' }];

// The community config, answered at once, and a chat reply of one line. `sent` records
// every chat request's body; `hold()` keeps the next chat requests unanswered until the
// returned function is called.
function serverFetch(widget) {
  const sent = [];
  const encoder = new TextEncoder();
  let gate = null;
  let sessions = 0;
  const answer = (given) => new Response(new ReadableStream({
    start(controller) {
      const session = given || `session-${++sessions}`;
      for (const event of [{ event: 'session', session_id: session }, { event: 'content', content: 'An answer.' }, { event: 'done' }]) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      }
      controller.close();
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
  const fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (u.endsWith('/chat')) {
      const body = JSON.parse(init.body);
      sent.push(body);
      if (gate) await gate.promise;
      return answer(body.session_id);
    }
    return new Response(JSON.stringify({
      default_model: 'm',
      offered_models: [],
      widget: { title: 'Host API Tests', search_paths: ['/discover'], suggested_questions: SUGGESTED, ...widget },
      client_tools: [],
      runtime: null,
    }), { headers: { 'content-type': 'application/json' } });
  };
  const hold = () => {
    let open;
    gate = { promise: new Promise((resolve) => { open = resolve; }) };
    return () => { gate = null; open(); };
  };
  return { fetch, sent, hold };
}

// Everything a page can see of the console while a call is made: the warnings and the
// errors, so a refused call can be told from a silent one and a throwing handler's
// report is kept out of the test's output.
let listening = null;
function listen() {
  // A listener left running (a block that ended early) is given back before the next.
  if (listening) listening.stop();
  const heard = { warn: [], error: [] };
  const real = { warn: console.warn, error: console.error };
  listening = heard;
  console.warn = (...args) => { heard.warn.push(args.map(String).join(' ')); };
  console.error = (...args) => { heard.error.push(args.map(String).join(' ')); };
  heard.stop = () => {
    console.warn = real.warn;
    console.error = real.error;
    if (listening === heard) listening = null;
  };
  return heard;
}

// A page before init(): the widget's script has run and the host page has its own
// elements, one of which has focus. `boot()` is what the page's init call does.
async function load({
  path = '/dataset/x', widget = {}, dataset = { id: A, title: TITLE }, popout = false, storage = null, fastInitWarning = false,
} = {}) {
  const window = new Window({
    url: `http://localhost${path}`,
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  window.sessionStorage.setItem(`${KEY}-session`, '1');
  if (storage) window.localStorage.setItem(KEY, storage);
  if (popout) window.__OSA_CHAT_CONFIG__ = { apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING, fullscreen: true };
  window.document.body.innerHTML = `
    <main id="host-page"><h1>The host page</h1><button id="host-pill" type="button">Open AI Mode</button><a id="host-link" href="#top">A link</a></main>
    <footer id="host-footer">Footer</footer>`;
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const server = serverFetch(widget);
  window.fetch = server.fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  // The "init() was never called" warning waits seconds; a test that wants to see it
  // shortens that one wait (5000 ms) and leaves every other timer as it is.
  const timer = fastInitWarning ? (fn, ms, ...rest) => setTimeout(fn, ms === 5000 ? 20 : ms, ...rest) : setTimeout;
  run(window, window.document, window.localStorage, server.fetch, window.navigator, AbortSignal, URL,
    TextDecoder, timer, clearTimeout, console);
  const api = window.OSAChatWidget;
  const ctx = {
    window, api, hooks: api.__browser, sent: server.sent, hold: server.hold, document: window.document,
    async boot() {
      if (dataset) api.setDataset(dataset);
      api.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING });
      api.init();
      await waitUntil(() => this.container().querySelector('.osa-chat-title').firstChild.textContent.trim() === 'Host API Tests', 'the community config arrives');
      return this;
    },
    container: () => window.document.querySelector('.osa-chat-widget'),
    q: (s) => window.document.querySelector(`.osa-chat-widget ${s}`),
    qa: (s) => [...window.document.querySelectorAll(`.osa-chat-widget ${s}`)],
    win: () => window.document.querySelector('.osa-chat-window'),
    pill: () => window.document.getElementById('host-pill'),
    isOpen: () => window.document.querySelector('.osa-chat-window').classList.contains('open'),
    isCenter: () => window.document.querySelector('.osa-chat-window').classList.contains('osa-center'),
    stored: () => JSON.parse(window.localStorage.getItem(KEY)),
  };
  return ctx;
}

async function start(options) {
  const ctx = await load(options);
  return ctx.boot();
}

// The events a page hears, in the order it hears them.
function record(ctx) {
  const events = [];
  const offs = ['open', 'close', 'modechange', 'send'].map((name) => ctx.api.on(name, (payload) => events.push([name, payload])));
  return { events, names: () => events.map(([name]) => name), stop: () => offs.forEach((off) => off()) };
}

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const key = (ctx, name, init = {}) => new ctx.window.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...init });
const active = (ctx) => ctx.document.activeElement;
const inWindow = (ctx, el) => !!el && ctx.win().contains(el);

async function answered(ctx, count = 1) {
  await waitUntil(() => ctx.sent.length >= count && !ctx.q('.osa-chat-input input').disabled, 'the reply ends');
}

async function typeAndSend(ctx, question) {
  const expected = ctx.sent.length + 1;
  ctx.q('.osa-chat-input input').value = question;
  ctx.q('.osa-send-btn').click();
  await answered(ctx, expected);
}

console.log('='.repeat(60));
console.log('Widget: the host API and the center-stage dialog (#701)');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// The API
// ---------------------------------------------------------------------------

console.log('\nthe host API exists, before init() and after');
{
  const ctx = await load();
  for (const name of ['open', 'close', 'ask', 'setMode', 'on']) {
    assertEqual(typeof ctx.api[name], 'function', `OSAChatWidget.${name} is a function before init()`);
  }
  assert(ctx.container() === null, 'and nothing is drawn yet');
}

console.log('\ncalls made before init() are kept and replayed, in order');
{
  const ctx = await load();
  const heard = record(ctx);
  ctx.api.setMode('center');
  ctx.api.open();
  ctx.api.setMode('corner');
  ctx.api.ask('A question asked early');
  assertEqual(heard.names(), [], 'nothing runs while there is no widget');
  assert(ctx.container() === null && ctx.sent.length === 0, 'nothing is drawn or sent');
  await ctx.boot();
  await waitUntil(() => ctx.sent.length === 1, 'the early question is sent');
  assertEqual(heard.names().slice(0, 4), ['modechange', 'open', 'modechange', 'send'], 'they ran in the order they were made');
  assertEqual(heard.events.map(([, payload]) => payload), [{ mode: 'center' }, { mode: 'center' }, { mode: 'corner' }, { text: 'A question asked early' }],
    'each with its own data: the dialog opened in center, was docked, and the question was sent');
  assert(ctx.isOpen() && !ctx.isCenter(), 'the window is open, and docked');
  assertEqual(ctx.sent[0].message, 'A question asked early', 'the question reached the server as typed');
  await answered(ctx);
}

console.log('\na bad call warns and does nothing');
{
  const ctx = await start();
  const heard = record(ctx);
  const warnings = listen();
  const refused = [
    () => ctx.api.open({ mode: 'left' }),
    () => ctx.api.open('center'),
    () => ctx.api.open([]),
    () => ctx.api.setMode('left'),
    () => ctx.api.setMode(),
    () => ctx.api.setMode({ mode: 'center' }),
    () => ctx.api.ask(''),
    () => ctx.api.ask('   '),
    () => ctx.api.ask(5),
    () => ctx.api.ask(undefined),
    () => ctx.api.ask('A question', { mode: 'sideways' }),
  ];
  for (const call of refused) call();
  const off = ctx.api.on('opened', () => {});
  const noHandler = ctx.api.on('open', 'not a function');
  warnings.stop();
  assertEqual(warnings.warn.length, refused.length + 2, 'each says so, once');
  assert(warnings.warn.every((line) => line.startsWith('[OSA] ')), 'in the widget\'s own words');
  assert(typeof off === 'function' && typeof noHandler === 'function', 'a bad on() still returns a function to call');
  off();
  noHandler();
  assert(!ctx.isOpen() && !ctx.isCenter(), 'the window is untouched');
  assertEqual(ctx.sent.length, 0, 'nothing was sent');
  assertEqual(heard.names(), [], 'and no event fired');
  const early = await load();
  const earlyWarnings = listen();
  early.api.open({ mode: 'left' });
  early.api.ask('');
  earlyWarnings.stop();
  assertEqual(earlyWarnings.warn.length, 2, 'before init() it says so at once, and does not keep the call');
  await early.boot();
  assert(!early.isOpen() && early.sent.length === 0, 'so there is nothing to replay');
}

console.log('\nopen, close, modechange and send each fire once, with plain data');
{
  const ctx = await start();
  const heard = record(ctx);
  ctx.api.open();
  assertEqual(heard.events, [['open', { mode: 'corner' }]], 'open() in the corner: one open event');
  ctx.api.open();
  assertEqual(heard.names(), ['open'], 'open() on an open window fires nothing more');
  ctx.api.setMode('center');
  assertEqual(heard.events.slice(1), [['modechange', { mode: 'center' }]], 'setMode: one modechange');
  ctx.api.setMode('center');
  assertEqual(heard.names().length, 2, 'to the same mode, nothing');
  await ctx.api.ask('  What events are annotated?  ');
  await answered(ctx);
  assertEqual(heard.events.at(-1), ['send', { text: 'What events are annotated?' }], 'ask: send, with the text as sent');
  ctx.api.close();
  assertEqual(heard.events.slice(-2), [['modechange', { mode: 'corner' }], ['close', {}]], 'closing the dialog: it is the corner again, then close');
  ctx.api.close();
  assertEqual(heard.names().filter((name) => name === 'close').length, 1, 'close() on a closed window fires nothing more');
  ctx.api.open({ mode: 'center' });
  assertEqual(heard.events.slice(-2), [['open', { mode: 'center' }], ['modechange', { mode: 'center' }]], 'open({mode: "center"}): open, with the mode, then modechange');
  for (const [, payload] of heard.events) {
    assert(Object.getPrototypeOf(payload) === Object.prototype && JSON.stringify(payload) === JSON.stringify(JSON.parse(JSON.stringify(payload))),
      `a payload is plain data (${JSON.stringify(payload)})`);
  }
}

console.log('\na handler that throws, or rejects, cannot break the widget or another handler');
{
  const ctx = await start();
  const errors = listen();
  const seen = [];
  ctx.api.on('open', () => { throw new Error('a handler that throws'); });
  ctx.api.on('open', () => Promise.reject(new Error('a handler that rejects')));
  ctx.api.on('open', (payload) => { payload.mode = 'tampered'; });
  ctx.api.on('open', (payload) => seen.push(payload.mode));
  ctx.api.on('send', () => { throw new Error('a send handler that throws'); });
  ctx.api.open();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert(ctx.isOpen(), 'the window opened');
  assertEqual(seen, ['corner'], 'the next handler still ran, and saw its own copy of the data');
  ctx.api.setMode('center');
  assert(ctx.isCenter(), 'setMode still works');
  await ctx.api.ask('A question after the throwing handlers');
  await answered(ctx);
  assertEqual(ctx.sent.length, 1, 'and so does a send whose handler threw');
  ctx.api.close();
  errors.stop();
  assert(!ctx.isOpen(), 'close still works');
  assert(errors.error.some((line) => line.includes('threw')) && errors.error.some((line) => line.includes('failed')), 'each failure is reported on the console');
}

console.log('\non() returns the way to stop listening');
{
  const ctx = await start();
  const calls = [];
  const off = ctx.api.on('open', () => calls.push('a'));
  ctx.api.on('open', () => calls.push('b'));
  const offClose = ctx.api.on('close', () => calls.push('c'));
  ctx.api.open();
  off();
  off();
  ctx.api.close();
  ctx.api.open();
  assertEqual(calls, ['a', 'b', 'c', 'b'], 'the one that stopped hears no more, the others do');
  const self = ctx.api.on('close', () => { calls.push('d'); self(); });
  ctx.api.close();
  ctx.api.open();
  ctx.api.close();
  assertEqual(calls.slice(4), ['c', 'd', 'b', 'c'], 'a handler may stop itself while it runs, and the others still hear');
  offClose();
}

// ---------------------------------------------------------------------------
// ask goes through sendMessage
// ---------------------------------------------------------------------------

console.log('\nask opens the window and sends like a typed question');
{
  const ctx = await start({ path: '/dataset/a' });
  assert(!ctx.isOpen(), 'sanity: closed');
  ctx.api.ask('What is the sampling rate?');
  assert(ctx.isOpen() && !ctx.isCenter(), 'the window opens, in the corner by default');
  await answered(ctx);
  assertEqual(ctx.sent[0].message, 'What is the sampling rate?', 'the question is the request\'s message');
  assertEqual(ctx.sent[0].page_context.dataset_id, A, 'with the page context a typed question carries');
  const stored = ctx.stored().conversations.find((c) => c.id === ctx.stored().activeId);
  assert(stored.messages.some((m) => m.role === 'user' && m.content === 'What is the sampling rate?'), 'and it is in the saved conversation, so History has it');
  ctx.api.ask('And how was it recorded?', { mode: 'center' });
  assert(ctx.isCenter(), 'ask with a mode opens in that mode');
  await answered(ctx, 2);
  assertEqual(ctx.sent[1].session_id, 'session-1', 'the second question continues the session');
}

console.log('\nask after the page moved to another dataset starts a new chat (E1.3)');
{
  const ctx = await start();
  await typeAndSend(ctx, 'What is the sampling rate?');
  const first = ctx.hooks.getActiveConversation().id;
  ctx.api.setDataset({ id: B });
  ctx.api.ask('What is this dataset about?');
  await answered(ctx, 2);
  const second = ctx.hooks.getActiveConversation().id;
  assert(first !== second, 'a new conversation took the question');
  assert(ctx.sent[1].session_id === undefined, 'with no session carried over from the old chat');
  assertEqual(ctx.hooks.listConversations().length, 2, 'and the old one is kept');
}

console.log('\nask while a reply is loading is refused');
{
  const ctx = await start();
  const release = ctx.hold();
  const heard = record(ctx);
  ctx.api.ask('The first question');
  await waitUntil(() => ctx.sent.length === 1, 'the first question is in flight');
  const warnings = listen();
  ctx.api.ask('The second question', { mode: 'center' });
  warnings.stop();
  assertEqual(warnings.warn.length, 1, 'it warns');
  assert(!ctx.isCenter(), 'and does not even move the window');
  assertEqual(heard.names().filter((name) => name === 'send').length, 1, 'no second send event');
  release();
  await answered(ctx);
  assertEqual(ctx.sent.length, 1, 'only the first question was sent');
}

// ---------------------------------------------------------------------------
// The dialog
// ---------------------------------------------------------------------------

console.log('\ncenter: the one window becomes a modal dialog');
{
  const ctx = await start();
  const container = ctx.container();
  const windows = ctx.window.document.querySelectorAll('.osa-chat-window').length;
  ctx.pill().focus();
  ctx.api.open({ mode: 'center' });
  assert(ctx.window.document.querySelectorAll('.osa-chat-window').length === windows && ctx.isCenter(), 'the same window, with an osa-center class: no second instance');
  assertEqual(ctx.win().getAttribute('role'), 'dialog', 'role=dialog');
  assertEqual(ctx.win().getAttribute('aria-modal'), 'true', 'aria-modal=true');
  assertEqual(ctx.win().getAttribute('aria-label'), `AI Mode for ${A}`, 'named for the dataset');
  assert(ctx.q('.osa-center-scrim').classList.contains('open'), 'a scrim is shown');
  assert(ctx.document.getElementById('host-page').hasAttribute('inert') && ctx.document.getElementById('host-footer').hasAttribute('inert'),
    'the host page\'s siblings of the widget are inert');
  assert(!container.hasAttribute('inert') && !ctx.win().hasAttribute('inert'), 'the widget is not');
  assert(ctx.q('.osa-chat-button').hasAttribute('inert'), 'its launcher, which is behind the dialog, is');
  assertEqual(ctx.document.body.style.overflow, 'hidden', 'the page does not scroll');
  assertEqual(ctx.document.documentElement.style.overflow, 'hidden', 'neither does its root');
  assert(inWindow(ctx, active(ctx)), 'focus moved into the dialog');
  assertEqual(text(ctx.q('.osa-center-pill')), 'AI Mode', 'the header has the AI Mode pill');
  assertEqual(text(ctx.q('.osa-center-context')), `${A} · ${TITLE}`, 'the dataset\'s id and title');
  assertEqual(ctx.q('.osa-center-context code').textContent, A, 'the id as code');
  assertEqual(text(ctx.q('.osa-history-btn')), 'History', 'a History button');
  assertEqual(text(ctx.q('.osa-center-dock-btn')), 'Move to corner', 'a Move to corner button');
  assertEqual(ctx.q('.osa-close-btn').getAttribute('title'), 'Close', 'and a Close button');

  ctx.api.setMode('corner');
  assert(!ctx.isCenter() && ctx.isOpen(), 'docked: still open, no longer the dialog');
  assert(!ctx.win().hasAttribute('role') && !ctx.win().hasAttribute('aria-modal') && !ctx.win().hasAttribute('aria-label'), 'no dialog attributes');
  assert(!ctx.q('.osa-center-scrim').classList.contains('open'), 'no scrim');
  assert(ctx.qa('[inert]').length === 0 && ctx.document.querySelectorAll('[inert]').length === 0, 'nothing is inert');
  assertEqual([ctx.document.body.style.overflow, ctx.document.documentElement.style.overflow], ['', ''], 'the page scrolls again');
}

console.log('\nthe header: the title is optional and validated');
{
  const ctx = await start({ dataset: { id: A } });
  ctx.api.open({ mode: 'center' });
  assertEqual(text(ctx.q('.osa-center-context')), A, 'with no title, the id alone');
  const warnings = listen();
  for (const bad of [42, '', '   ', 'x'.repeat(201), { a: 1 }]) {
    ctx.api.setDataset({ id: A, title: bad });
    assertEqual(text(ctx.q('.osa-center-context')), A, `a title that is ${JSON.stringify(bad).slice(0, 20)} is dropped alone`);
  }
  assertEqual(warnings.warn.length, 5, 'each says so');
  ctx.api.setDataset({ id: A, title: 'x'.repeat(200) });
  assertEqual(text(ctx.q('.osa-center-context')), `${A} · ${'x'.repeat(200)}`, 'at 200 characters it is kept');
  ctx.api.setDataset({ id: A, title: '  A padded title  ' });
  assertEqual(text(ctx.q('.osa-center-context')), `${A} · A padded title`, 'trimmed');
  ctx.api.setDataset({ id: A, title: '<img src=x onerror=alert(1)>' });
  assert(ctx.q('.osa-center-context').querySelector('img') === null, 'and drawn as text, never as markup');
  ctx.api.setDataset(null);
  warnings.stop();
  assertEqual(ctx.win().getAttribute('aria-label'), 'AI Mode', 'with no dataset the dialog is just "AI Mode"');
  assertEqual(text(ctx.q('.osa-center-context')), 'Host API Tests', 'and the line names the community');
}

console.log('\nTab and Shift+Tab never leave the dialog');
{
  const ctx = await start();
  ctx.api.setMode('center');
  ctx.api.open();
  const container = ctx.container();
  const { focusables } = ctx.api.__center;
  const list = focusables(ctx.win());
  assert(list.length > 4, `there are controls to cycle through (${list.length})`);
  assert(list.every((el) => inWindow(ctx, el) && !el.closest('[inert]')), 'all of them in the dialog, none inert');
  // The expected cycle is named here, not taken from the helper under test: a control
  // the helper skipped would otherwise be missing from both the list and the wrap.
  for (const selector of ['.osa-center-dock-btn', '.osa-history-btn', '.osa-settings-btn-open', '.osa-close-btn', '.osa-center-input', '.osa-center-suggestion']) {
    const el = ctx.q(selector);
    assert(!!el && list.includes(el), `${selector} is in the cycle`);
  }
  assert(!list.includes(ctx.q('.osa-chat-input input')), 'the chat\'s own input, hidden behind the start screen, is not');
  assert(!list.some((el) => ctx.document.getElementById('host-pill') === el || ctx.document.getElementById('host-link') === el), 'none of the page\'s');
  list.at(-1).focus();
  const forward = key(ctx, 'Tab');
  active(ctx).dispatchEvent(forward);
  assert(forward.defaultPrevented && active(ctx) === list[0], 'Tab on the last control goes to the first');
  list[0].focus();
  const back = key(ctx, 'Tab', { shiftKey: true });
  active(ctx).dispatchEvent(back);
  assert(back.defaultPrevented && active(ctx) === list.at(-1), 'Shift+Tab on the first goes to the last');
  list[1].focus();
  const middle = key(ctx, 'Tab');
  active(ctx).dispatchEvent(middle);
  assert(!middle.defaultPrevented && active(ctx) === list[1], 'in between, the browser moves focus as it always does');
  ctx.document.body.focus();
  const lost = key(ctx, 'Tab');
  ctx.document.dispatchEvent(lost);
  assert(lost.defaultPrevented && active(ctx) === list[0], 'with focus on the page, Tab brings it back in');

  ctx.q('.osa-settings-btn-open').click();
  assert(ctx.q('.osa-settings-overlay').classList.contains('open'), 'Settings opens');
  const inner = focusables(ctx.q('.osa-settings-overlay'));
  assert(inner.length > 1 && inner.every((el) => ctx.q('.osa-settings-overlay').contains(el)), 'and holds the focus on its own controls');
  inner.at(-1).focus();
  const wrap = key(ctx, 'Tab');
  active(ctx).dispatchEvent(wrap);
  assert(wrap.defaultPrevented && active(ctx) === inner[0], 'Tab wraps inside it');
  assert(container.contains(active(ctx)), 'inside the widget');
}

console.log('\nEscape docks, and "Move to corner" does the same, with the conversation untouched');
for (const how of ['Escape', 'Move to corner']) {
  const ctx = await start();
  await typeAndSend(ctx, 'What is the sampling rate?');
  ctx.api.open({ mode: 'center' });
  const heard = record(ctx);
  const nodes = [...ctx.qa('.osa-chat-messages .osa-message')];
  const session = ctx.hooks.getSessionId();
  assert(nodes.length >= 2 && !!session, `sanity: a thread of ${nodes.length} messages and a session`);
  ctx.q('.osa-chat-input input').value = 'A draft not yet sent';
  ctx.q('.osa-chat-input input').focus();
  if (how === 'Escape') {
    const escape = key(ctx, 'Escape');
    active(ctx).dispatchEvent(escape);
    assert(escape.defaultPrevented, `${how}: the key is taken`);
  } else {
    ctx.q('.osa-center-dock-btn').click();
  }
  assert(ctx.isOpen() && !ctx.isCenter(), `${how}: docked, and still open`);
  assertEqual(heard.events, [['modechange', { mode: 'corner' }]], `${how}: one modechange, no close`);
  const after = [...ctx.qa('.osa-chat-messages .osa-message')];
  assert(after.length === nodes.length && after.every((node, i) => node === nodes[i]), `${how}: the very same message nodes`);
  assertEqual(ctx.hooks.getSessionId(), session, `${how}: the same session`);
  assertEqual(ctx.q('.osa-chat-input input').value, 'A draft not yet sent', `${how}: the same input value`);
  assert(inWindow(ctx, active(ctx)), `${how}: focus stays in the window`);
  assert(ctx.document.querySelectorAll('[inert]').length === 0 && ctx.document.body.style.overflow === '', `${how}: the page is live again`);
  await typeAndSend(ctx, 'Does it continue?');
  assertEqual(ctx.sent.at(-1).session_id, session, `${how}: and the next question continues the session`);
}

console.log('\nEscape closes History first, then docks');
{
  const ctx = await start();
  await typeAndSend(ctx, 'What is the sampling rate?');
  ctx.api.open({ mode: 'center' });
  ctx.q('.osa-history-btn').click();
  assert(ctx.q('.osa-history-sheet').classList.contains('open'), 'sanity: History is open');
  const first = key(ctx, 'Escape');
  active(ctx).dispatchEvent(first);
  assert(!ctx.q('.osa-history-sheet').classList.contains('open') && ctx.isCenter(), 'the first Escape closes History and the dialog stays');
  const second = key(ctx, 'Escape');
  active(ctx).dispatchEvent(second);
  assert(!ctx.isCenter() && ctx.isOpen(), 'the second docks');
  ctx.api.setMode('center');
  ctx.q('.osa-history-btn').click();
  ctx.document.body.focus();
  ctx.document.dispatchEvent(key(ctx, 'Escape'));
  assert(!ctx.q('.osa-history-sheet').classList.contains('open') && ctx.isCenter(), 'with focus on the page, Escape closes History too');
  ctx.q('.osa-settings-btn-open').click();
  active(ctx).dispatchEvent(key(ctx, 'Escape'));
  assert(!ctx.q('.osa-settings-overlay').classList.contains('open') && ctx.isCenter(), 'and Settings the same');
  ctx.q('.osa-feedback-link').click();
  assert(ctx.q('.osa-feedback-overlay').classList.contains('open'), 'sanity: the feedback panel is open');
  const third = key(ctx, 'Escape');
  active(ctx).dispatchEvent(third);
  assert(third.defaultPrevented && !ctx.q('.osa-feedback-overlay').classList.contains('open') && ctx.isCenter(), 'and the feedback panel the same: the dialog stays');
  active(ctx).dispatchEvent(key(ctx, 'Escape'));
  assert(!ctx.isCenter() && ctx.isOpen(), 'and then Escape docks');
}

console.log('\nClose gives focus back to what had it before the window opened');
for (const how of ['the Close button', 'close()']) {
  const ctx = await start();
  const heard = record(ctx);
  ctx.pill().focus();
  assert(active(ctx) === ctx.pill(), 'sanity: the page\'s own button has focus');
  ctx.api.open({ mode: 'center' });
  assert(active(ctx) !== ctx.pill() && inWindow(ctx, active(ctx)), `${how}: focus is in the dialog`);
  if (how === 'close()') ctx.api.close();
  else ctx.q('.osa-close-btn').click();
  assert(!ctx.isOpen() && !ctx.isCenter(), `${how}: closed, and the corner again`);
  assert(active(ctx) === ctx.pill(), `${how}: focus is back on the page's button`);
  assertEqual(heard.names(), ['open', 'modechange', 'modechange', 'close'], `${how}: open, modechange, then modechange to the corner and close`);
  assert(ctx.document.querySelectorAll('[inert]').length === 0 && ctx.document.body.style.overflow === '', `${how}: the page is live again`);
  ctx.api.open();
  assert(!ctx.isCenter(), `${how}: the next open() is the corner's`);
}

console.log('\nClose with no opener to return to puts focus on the launcher');
{
  const ctx = await start();
  ctx.api.open({ mode: 'center' });
  ctx.api.close();
  assert(active(ctx) === ctx.q('.osa-chat-button'), 'focus is on the launcher, not lost');
  ctx.api.setMode('center');
  assert(ctx.isCenter() && !ctx.isOpen(), 'setMode on a closed window is kept for the next open');
  assert(ctx.document.querySelectorAll('[inert]').length === 0, 'and makes nothing inert while there is no dialog');
  ctx.api.open();
  assert(ctx.isCenter() && ctx.isOpen() && ctx.document.getElementById('host-page').hasAttribute('inert'), 'then the dialog opens');
}

console.log('\nthe pop-out has no corner: setMode, open and close do nothing there');
{
  const ctx = await start({ popout: true });
  const heard = record(ctx);
  const warnings = listen();
  ctx.api.setMode('center');
  ctx.api.open({ mode: 'center' });
  ctx.api.close();
  warnings.stop();
  assertEqual(warnings.warn.length, 3, 'each says so on the console, not silently');
  assert(ctx.isOpen() && !ctx.isCenter(), 'still the open, plain window');
  assertEqual(heard.events, [], 'with no events');
  assert(ctx.document.querySelectorAll('[inert]').length === 0, 'and nothing made inert');
  ctx.api.ask('A question in the pop-out', { mode: 'center' });
  await answered(ctx);
  assertEqual(ctx.sent[0].message, 'A question in the pop-out', 'ask still sends');
  assert(!ctx.isCenter(), 'without a mode change');
}

console.log('\ncenter in a capsule community: the same dialog, with the chat tab');
{
  const ctx = await start({ widget: { launcher: 'capsule', center_start: CENTER_START } });
  assert(ctx.container().classList.contains('osa-capsule'), 'sanity: a capsule');
  ctx.api.open({ mode: 'center' });
  assert(ctx.isCenter() && ctx.win().getAttribute('role') === 'dialog', 'the dialog');
  assert(ctx.q('.osa-view-chat .osa-center-start') !== null && !ctx.q('.osa-center-start').hidden, 'its start screen is in the chat view');
  assert(ctx.qa('.osa-launcher-capsule').every((el) => el.hasAttribute('inert')), 'the capsule launcher is inert');
  const list = ctx.api.__center.focusables(ctx.win());
  assert(list.length > 4 && list.every((el) => inWindow(ctx, el)), 'and the trap\'s controls are all in the dialog');
  ctx.api.close();
  assert(ctx.qa('.osa-launcher-capsule').every((el) => !el.hasAttribute('inert')), 'closing gives the launcher back');
}

// ---------------------------------------------------------------------------
// The start screen
// ---------------------------------------------------------------------------

console.log('\nthe start screen (C2) is for an empty conversation in the dialog');
{
  const ctx = await start({ widget: { center_start: CENTER_START, analysis_docs: DOCS, dataset_suggested_questions: DATASET_QUESTIONS } });
  const start_ = () => ctx.q('.osa-center-start');
  ctx.api.open();
  assert(start_().hidden && !ctx.win().classList.contains('osa-center-empty'), 'in the corner it is not shown');
  ctx.api.setMode('center');
  assert(!start_().hidden && ctx.win().classList.contains('osa-center-empty'), 'in the dialog, with nothing said yet, it is');
  assertEqual(text(ctx.q('.osa-center-heading')), 'What would you like to know about this dataset?', 'the heading');
  const field = ctx.q('.osa-center-input');
  assertEqual(field.placeholder, ctx.hooks.getConfig().placeholder, 'the field has the community\'s placeholder');
  assertEqual(text(ctx.q('.osa-center-form label')), `Ask about ${A}`, 'and a label for a screen reader');
  assert(inWindow(ctx, active(ctx)) && start_().contains(active(ctx)), 'focus is in the field');
  const pills = ctx.qa('.osa-center-pills button').map(text);
  assertEqual(pills, DATASET_QUESTIONS.map((q) => q.text.replace('{dataset_id}', A)), 'the pills are the chat\'s own suggestions for this dataset');
  assert(ctx.qa('.osa-center-pills button').every((pill) => pill.type === 'button'), 'each a button');

  ctx.api.setMode('corner');
  assert(start_().hidden, 'docked, it is gone');
  ctx.api.setMode('center');
  await ctx.api.ask('A first question');
  await answered(ctx);
  assert(start_().hidden && !ctx.win().classList.contains('osa-center-empty'), 'once there is a message, the thread takes its place');
  assert(ctx.qa('.osa-chat-messages .osa-message').length >= 2, 'and the thread is there');
  ctx.q('.osa-history-btn').click();
  ctx.q('.osa-history-new').click();
  assert(!start_().hidden, 'a new chat from History shows it again');
}

console.log('\nthe pills and the field send');
{
  const ctx = await start({ widget: { dataset_suggested_questions: DATASET_QUESTIONS } });
  ctx.api.open({ mode: 'center' });
  const [pill] = ctx.qa('.osa-center-pills button');
  const wanted = text(pill);
  pill.click();
  await answered(ctx);
  assertEqual(ctx.sent[0].message, wanted, 'a pill sends its text');
  assert(ctx.q('.osa-center-start').hidden, 'and the thread appears');

  const second = await start({ dataset: null });
  second.api.open({ mode: 'center' });
  assertEqual(text(second.q('.osa-center-heading')), 'What would you like to know?', 'with no dataset the heading does not say "this dataset"');
  assertEqual(second.qa('.osa-center-pills button').map(text), SUGGESTED, 'and the pills are the community\'s general suggestions');
  const field = second.q('.osa-center-input');
  field.value = 'What events are annotated?';
  field.dispatchEvent(new second.window.Event('input', { bubbles: true }));
  assertEqual(second.q('.osa-chat-input input').value, 'What events are annotated?', 'what is typed is the chat\'s own input too');
  second.q('.osa-center-send').click();
  await waitUntil(() => second.sent.length === 1, 'the field sends');
  assertEqual(second.sent[0].message, 'What events are annotated?', 'with what was typed');
  await answered(second);

  const third = await start({ dataset: null });
  third.api.open({ mode: 'center' });
  third.q('.osa-center-send').click();
  assertEqual(third.sent.length, 0, 'an empty field sends nothing');
  third.q('.osa-center-input').value = 'A typed draft';
  third.q('.osa-center-input').dispatchEvent(new third.window.Event('input', { bubbles: true }));
  third.api.setMode('corner');
  assertEqual(third.q('.osa-chat-input input').value, 'A typed draft', 'docking from the start screen keeps what was typed');
  third.api.setMode('center');
  assertEqual(third.q('.osa-center-input').value, 'A typed draft', 'and the start screen shows it again');
}

console.log('\nthe cards and the footer come from the community config');
{
  const ctx = await start({ widget: { center_start: CENTER_START, analysis_docs: DOCS } });
  ctx.api.open({ mode: 'center' });
  assertEqual(ctx.qa('.osa-center-card').map((card) => [text(card.querySelector('.osa-center-card-title')), text(card.querySelector('.osa-center-card-text'))]),
    CENTER_START.capabilities.map((c) => [c.title, c.text]), 'a card for each capability, in order');
  assertEqual(text(ctx.q('.osa-center-note')), CENTER_START.note, 'the footer\'s note');
  assert(!ctx.q('.osa-center-footer').hidden, 'the footer shows');

  const bare = await start();
  bare.api.open({ mode: 'center' });
  assertEqual(bare.qa('.osa-center-card').length, 0, 'a community with no cards has none');
  assert(bare.q('.osa-center-footer').hidden, 'and no footer');
  assert(!/NEMAR/i.test(bare.q('.osa-center-start').textContent), 'and the start screen says nothing the config did not');

  const many = await start({ widget: { center_start: { capabilities: [...CENTER_START.capabilities, { title: 'Fourth', text: 'Not shown.' }, { title: 'x'.repeat(41), text: 'Too long a title.' }, { title: '', text: 'No title.' }, 'junk'], note: 'n'.repeat(301) } } });
  many.api.open({ mode: 'center' });
  assertEqual(many.qa('.osa-center-card').length, 3, 'a config with more than three cards shows three');
  assert(many.q('.osa-center-footer').hidden, 'and a note over its limit is not shown');

  const markup = await start({ widget: { center_start: { capabilities: [{ title: '<b>Bold</b>', text: '<img src=x onerror=alert(1)>' }], note: '<i>Note</i>' } } });
  markup.api.open({ mode: 'center' });
  assert(markup.q('.osa-center-start').querySelector('b, img, i') === null, 'markup in the copy is drawn as text');
}

console.log('\nthe docs link: in the start footer and in Settings, https only, absent when unset');
{
  const ctx = await start({ widget: { center_start: CENTER_START, analysis_docs: DOCS } });
  ctx.api.open({ mode: 'center' });
  const footerLink = ctx.q('.osa-center-docs-link');
  assert(!!footerLink, 'the footer has the link');
  assertEqual([text(footerLink), footerLink.getAttribute('href')], ['How it works (opens in a new tab)', DOCS.url], 'its text, with the new tab said for a screen reader, and the configured address');
  assertEqual(text(footerLink.querySelector('.osa-sr-only')), '(opens in a new tab)', 'the hint is visually hidden, not drawn');
  assertEqual([footerLink.getAttribute('target'), footerLink.getAttribute('rel')], ['_blank', 'noopener noreferrer'], 'it opens a new tab, with no opener');
  ctx.q('.osa-settings-btn-open').click();
  const settingsLink = ctx.q('.osa-settings-body .osa-docs-link');
  assert(!!settingsLink, 'Settings has a row');
  assertEqual([text(settingsLink), settingsLink.getAttribute('href')], [`${DOCS.title} (opens in a new tab)`, DOCS.url], 'with the configured title, the new tab said, and the address');
  assertEqual([settingsLink.getAttribute('target'), settingsLink.getAttribute('rel')], ['_blank', 'noopener noreferrer'], 'also a new tab, with no opener');
  assert(settingsLink.getAttribute('href').startsWith('https://'), 'an https address');
  ctx.api.setMode('corner');
  assert(ctx.q('.osa-settings-body .osa-docs-link') !== null, 'the Settings row is there in the corner too');

  const none = await start({ widget: { center_start: CENTER_START } });
  none.api.open({ mode: 'center' });
  assert(none.q('.osa-center-docs-link') === null && none.q('.osa-docs-link') === null && none.q('.osa-docs-field') === null, 'a community that sets none has no link anywhere');
  assertEqual(text(none.q('.osa-center-footer')), CENTER_START.note, 'and the footer holds the note alone');

  for (const bad of [
    { title: 'Docs', url: 'http://docs.example.org/' },
    { title: 'Docs', url: 'javascript:alert(1)' },
    { title: 'Docs', url: 'data:text/html,hi' },
    { title: 'Docs', url: '/relative/path' },
    { title: 'Docs', url: 'https://user:secret@docs.example.org/' },
    { title: 'Docs', url: 'https://' },
    { title: '', url: DOCS.url },
    { title: 'x'.repeat(81), url: DOCS.url },
    { url: DOCS.url },
    'https://docs.example.org/',
  ]) {
    const refused = await start({ widget: { analysis_docs: bad } });
    refused.api.open({ mode: 'center' });
    refused.q('.osa-settings-btn-open').click();
    assert(refused.q('.osa-center-docs-link') === null && refused.q('.osa-docs-link') === null,
      `${JSON.stringify(bad).slice(0, 60)} is refused by the widget too`);
  }

  const next = await load({ widget: {}, storage: null });
  next.window.localStorage.setItem('osa-widget-config-nemar', JSON.stringify({ apiEndpoint: API, widget: { analysis_docs: DOCS, center_start: CENTER_START } }));
  next.api.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING });
  next.api.init();
  assertEqual(next.api.getConfig().analysisDocs, DOCS, 'the last load\'s docs link is applied before the config arrives');
  next.api.open({ mode: 'center' });
  assertEqual(next.qa('.osa-center-card').length, 3, 'and its cards are drawn');
  await waitUntil(() => next.qa('.osa-center-card').length === 0, 'the fresh config replaces the remembered one');
  assert(next.q('.osa-center-docs-link') === null && next.q('.osa-docs-link') === null, 'a fresh config that sets none takes the link away again');
}

// ---------------------------------------------------------------------------
// The notebook as a tab of the dialog (#738)
// ---------------------------------------------------------------------------

// A capsule community's page on a dataset with a notebook, the window open in the corner
// on the notebook tab, as a reader leaves it after pressing the notebook circle.
const NOTEBOOK_DATASET = { id: A, title: TITLE, zarr: true };
async function onNotebook(options = {}) {
  const ctx = await start({ widget: { launcher: 'capsule', center_start: CENTER_START }, dataset: NOTEBOOK_DATASET, ...options });
  ctx.api.open();
  ctx.q('.osa-notebook-btn').click();
  ctx.tab = () => (ctx.container().classList.contains('osa-tab-notebook') ? 'notebook' : 'chat');
  ctx.tabs = () => ctx.q('.osa-center-tabs');
  ctx.frame = () => ctx.q('.osa-notebook-frame');
  ctx.tabButton = (name) => ctx.q(`.osa-center-tab-${name}`);
  return ctx;
}

console.log('\nthe dialog has its tabs from the start, before any code has run (#743)');
{
  const ctx = await start({ widget: { launcher: 'capsule', center_start: CENTER_START }, dataset: NOTEBOOK_DATASET });
  ctx.api.open({ mode: 'center' });
  assert(!ctx.q('.osa-center-tabs').hidden && ctx.win().classList.contains('osa-center-tabbed'), 'a dialog on a dataset with a notebook shows the tab row at once');
  assert(ctx.q('.osa-notebook-frame') === null && ctx.q('.osa-chat-messages .osa-run-card') === null, 'with the notebook never opened and no code run');
  const tabs = ctx.q('.osa-center-tabs');
  assertEqual([tabs.getAttribute('role'), tabs.getAttribute('aria-label')], ['tablist', 'Chat or notebook'], 'a tablist, named');
  assertEqual([...tabs.querySelectorAll('[role="tab"]')].map((el) => text(el)), ['Chat', 'Notebook'], 'of the Chat and Notebook tabs');
  assert([...tabs.querySelectorAll('button')].every((el) => el.type === 'button'), 'each a real button');
  assertEqual(['chat', 'notebook'].map((n) => ctx.q(`.osa-center-tab-${n}`).getAttribute('aria-selected')), ['true', 'false'], 'aria-selected follows the open tab: Chat, since open() opens on the chat');
  assertEqual(['chat', 'notebook'].map((n) => ctx.q(`.osa-center-tab-${n}`).getAttribute('tabindex')), ['0', '-1'], 'with only the selected tab in the Tab order');
  const stops = ctx.api.__center.focusables(ctx.win());
  assert(stops.includes(ctx.q('.osa-center-tab-chat')) && !stops.includes(ctx.q('.osa-center-tab-notebook')), 'the selected tab is a stop, the other one is reached by the arrows');
  for (const name of ['chat', 'notebook']) {
    const panel = ctx.q(`.osa-view-${name}`);
    const tab = ctx.q(`.osa-center-tab-${name}`);
    assert(panel.getAttribute('role') === 'tabpanel' && tab.getAttribute('aria-controls') === panel.id && panel.getAttribute('aria-labelledby') === tab.id,
      `the ${name} view is the tab panel its tab controls and is named by`);
  }

  // Selecting the Notebook tab the first time creates the frame, in the same window.
  ctx.q('.osa-center-tab-notebook').click();
  const frame = ctx.q('.osa-notebook-frame');
  assert(frame !== null && ctx.q('.osa-view-notebook').contains(frame) && ctx.win().contains(frame), 'the Notebook tab creates the frame the first time it is selected');
  assert(ctx.isCenter() && ctx.win().getAttribute('aria-modal') === 'true' && ctx.document.getElementById('host-page').hasAttribute('inert'), 'and the dialog stays modal');
  assertEqual(['chat', 'notebook'].map((n) => ctx.q(`.osa-center-tab-${n}`).getAttribute('aria-selected')), ['false', 'true'], 'aria-selected moves to Notebook');
  assertEqual(['chat', 'notebook'].map((n) => ctx.q(`.osa-center-tab-${n}`).getAttribute('tabindex')), ['-1', '0'], 'and so does the Tab stop');

  // The corner never shows them; raised again, the dialog does.
  ctx.api.setMode('corner');
  assert(ctx.q('.osa-center-tabs').hidden, 'the corner never shows them, on the notebook tab or not');
  assert(ctx.q('.osa-view-chat').getAttribute('role') === null && ctx.q('.osa-view-notebook').getAttribute('role') === null, 'docked, the views are plain regions again');
  ctx.api.setMode('center');
  assert(!ctx.q('.osa-center-tabs').hidden, 'raised again, the dialog shows them');
}

console.log('\nthe tabs follow the dataset: a dialog with no notebook stays chat only (#743)');
{
  const noDataset = await start({ widget: { launcher: 'capsule', center_start: CENTER_START } });
  noDataset.api.open({ mode: 'center' });
  assert(noDataset.q('.osa-center-tabs').hidden && !noDataset.win().classList.contains('osa-center-tabbed'), 'no dataset (a Discover search\'s "Ask a follow-up"): the tab row is hidden');
  assert(noDataset.api.__center.focusables(noDataset.win()).every((el) => !el.classList.contains('osa-center-tab')), 'and neither tab is a stop');
  noDataset.api.setDataset(NOTEBOOK_DATASET);
  assert(!noDataset.q('.osa-center-tabs').hidden, 'a dataset with a notebook arriving while the dialog is open brings the tabs');
  noDataset.api.setDataset(null);
  assert(noDataset.q('.osa-center-tabs').hidden, 'and leaving it takes them away');

  const noZarr = await start({ widget: { launcher: 'capsule', center_start: CENTER_START }, dataset: { id: A, title: TITLE, zarr: false } });
  noZarr.api.open({ mode: 'center' });
  assert(noZarr.q('.osa-center-tabs').hidden, 'a dataset with no Zarr copy has no notebook, so no tabs');
}

console.log('\nthe tabs are for a capsule with a notebook: not a bubble, not a dataset without one, not the pop-out');
{
  const bubble = await start({ widget: { center_start: CENTER_START }, dataset: NOTEBOOK_DATASET });
  bubble.api.open({ mode: 'center' });
  assert(bubble.q('.osa-center-tabs').hidden && bubble.q('.osa-view') === null, 'a bubble widget has no tabs and no views, in the dialog too');
  assert(bubble.api.__center.focusables(bubble.win()).length > 4, 'and its dialog is what it was');

  const ctx = await onNotebook();
  ctx.api.setMode('center');
  assert(!ctx.q('.osa-center-tabs').hidden, 'set up: the dialog has its tabs');
  ctx.api.setDataset({ id: A, title: TITLE, zarr: false });
  assert(ctx.q('.osa-center-tabs').hidden && ctx.tab() === 'chat', 'a page with no Zarr copy takes them away, and the dialog to the chat');
  assert(inWindow(ctx, active(ctx)) && active(ctx) !== ctx.q('.osa-center-tab-notebook'), 'focus is in the dialog, not on the tab that went');
  ctx.api.setDataset(NOTEBOOK_DATASET);
  ctx.tabButton('chat').focus();
  ctx.api.setDataset({ id: A, title: TITLE, zarr: false });
  assert(ctx.q('.osa-center-tabs').hidden && inWindow(ctx, active(ctx)) && !ctx.q('.osa-center-tabs').contains(active(ctx)),
    'and from the Chat tab too: focus leaves the tab row that is hidden');
  ctx.api.setDataset(NOTEBOOK_DATASET);
  assert(!ctx.q('.osa-center-tabs').hidden, 'and brings them back with the dataset that has one');

  const popout = await load({ popout: true, widget: { launcher: 'capsule' }, dataset: NOTEBOOK_DATASET });
  await popout.boot();
  assert(popout.q('.osa-center-tabs').hidden && popout.q('.osa-tab-strip') !== null, 'the pop-out keeps its own tab strip, and has no dialog tabs');
  popout.q('.osa-strip-notebook').click();
  assert(popout.q('.osa-center-tabs').hidden && popout.container().classList.contains('osa-tab-notebook'), 'whichever tab it opens');
  assert(popout.q('.osa-nb-guard').getAttribute('tabindex') === '-1', 'and its focus guard is no tab stop');
}

console.log('\nthe launcher is out of sight while the dialog is open, and back as it was after (#743)');
for (const launcher of ['capsule', 'bubble']) {
  const ctx = await start({ widget: { launcher, center_start: CENTER_START }, dataset: NOTEBOOK_DATASET });
  const parts = () => [...ctx.container().children].filter((el) => el.matches('.osa-launcher-capsule, .osa-chat-button, .osa-chat-tooltip'));
  const seen = (el) => ctx.window.getComputedStyle(el).visibility !== 'hidden';
  assert(parts().length > 0, `${launcher}: set up: the launcher has parts to hide`);
  ctx.api.open();
  const before = parts().map((el) => [ctx.window.getComputedStyle(el).visibility, ctx.window.getComputedStyle(el).opacity]);
  assert(!ctx.container().classList.contains('osa-center-engaged') && parts().every(seen), `${launcher}: open in the corner, the launcher shows`);
  ctx.api.setMode('center');
  assert(parts().every((el) => !seen(el)), `${launcher}: in the dialog, every part of the launcher is hidden`);
  assert(parts().filter((el) => !el.matches('.osa-chat-tooltip')).every((el) => el.hasAttribute('inert')), `${launcher}: and still inert`);
  if (launcher === 'capsule') {
    const circles = [...ctx.container().querySelectorAll('.osa-launcher-icon, .osa-chat-button')];
    assert(circles.length >= 3 && circles.every((el) => el.closest('[inert]') !== null), 'capsule: its circles are inert');
    // That the circles that show themselves while the panel is open are hidden too is a
    // matter of the cascade, which happy-dom does not resolve: the browser harness checks it.
  }
  ctx.q('.osa-center-dock-btn').click();
  assert(parts().every(seen) && !ctx.container().classList.contains('osa-center-engaged'), `${launcher}: Move to corner shows the launcher again`);
  assertEqual(parts().map((el) => [ctx.window.getComputedStyle(el).visibility, ctx.window.getComputedStyle(el).opacity]), before, `${launcher}: exactly as it was in the corner`);
  assert(parts().every((el) => !el.hasAttribute('inert')), `${launcher}: and no longer inert`);
  ctx.api.setMode('center');
  ctx.document.dispatchEvent(key(ctx, 'Escape'));
  assert(!ctx.isCenter() && parts().every(seen), `${launcher}: Escape shows it again`);
  ctx.api.setMode('center');
  ctx.api.close();
  assert(!ctx.isOpen() && parts().every(seen) && !ctx.container().classList.contains('osa-center-engaged'), `${launcher}: Close shows it again`);
  ctx.api.open({ mode: 'center' });
  assert(parts().every((el) => !seen(el)), `${launcher}: a window opened in the dialog hides it from the first frame`);
}

console.log('\nthe Notebook tab opens the notebook in the dialog: one frame, still modal');
{
  const ctx = await onNotebook();
  const frame = ctx.frame();
  assert(frame !== null && ctx.tab() === 'notebook', 'set up: the corner has opened the notebook');
  ctx.api.setMode('center');
  assert(ctx.isCenter() && ctx.tab() === 'notebook', 'setMode("center") on the notebook tab keeps the notebook');
  assert(ctx.frame() === frame && ctx.qa('.osa-notebook-frame').length === 1, 'and the same frame');
  assert(ctx.q('.osa-view-notebook').contains(frame) && ctx.win().contains(frame), 'which sits in the notebook view of the one window');
  assert(ctx.win().getAttribute('role') === 'dialog' && ctx.win().getAttribute('aria-modal') === 'true', 'the dialog is modal');
  assert(ctx.q('.osa-view-chat').hasAttribute('inert') && !ctx.q('.osa-view-notebook').hasAttribute('inert'), 'the chat view is inert, the notebook view is not');
  assert(ctx.q('.osa-center-scrim').classList.contains('open') && ctx.document.getElementById('host-page').hasAttribute('inert'), 'the page behind is inert, under the scrim');
  assert(active(ctx) === ctx.tabButton('notebook'), 'and focus is on the selected tab, not lost to the page');
  assert(ctx.q('.osa-center-start').hidden && !ctx.win().classList.contains('osa-center-empty'), 'the chat\'s start screen is not what shows');

  ctx.tabButton('chat').focus();
  ctx.tabButton('chat').click();
  assert(ctx.tab() === 'chat' && ctx.q('.osa-view-chat').hasAttribute('inert') === false && ctx.q('.osa-view-notebook').hasAttribute('inert'), 'the Chat tab opens the chat view and takes the notebook\'s out of play');
  assert(ctx.frame() === frame && ctx.isCenter(), 'the same frame, and the same dialog');
  assertEqual(['chat', 'notebook'].map((n) => ctx.tabButton(n).getAttribute('aria-selected')), ['true', 'false'], 'aria-selected follows');
  assert(active(ctx) === ctx.tabButton('chat'), 'the tab the reader used keeps focus');
  assert(!ctx.q('.osa-center-start').hidden, 'the start screen is back with the chat');
  ctx.tabButton('notebook').click();
  assert(ctx.tab() === 'notebook' && ctx.frame() === frame, 'the Notebook tab goes back, to the same frame');
  assert(ctx.win().classList.contains('osa-center') && ctx.win().classList.contains('open'), 'with the dialog up');

  // The frame is never moved: the same node, in the same place, through every switch and
  // a dock, so the browser has no reason to reload it.
  const parent = frame.parentElement;
  const next = frame.nextSibling;
  for (const step of [() => ctx.tabButton('chat').click(), () => ctx.tabButton('notebook').click(), () => ctx.api.setMode('corner'), () => ctx.api.setMode('center')]) {
    step();
    assert(ctx.frame() === frame && frame.parentElement === parent && frame.nextSibling === next, 'the frame has not moved');
  }
}

console.log('\nthe header on the Notebook tab: Move to corner, Close and the tabs stay; the chat\'s buttons go');
{
  const ctx = await onNotebook({ widget: { launcher: 'capsule', center_start: CENTER_START } });
  ctx.api.setMode('center');
  const hiddenOn = (el) => ctx.window.getComputedStyle(el).visibility === 'hidden';
  for (const selector of ['.osa-history-btn', '.osa-settings-btn-open', '.osa-center-notebook-btn']) {
    assert(ctx.q(selector).closest('.osa-chat-header') !== null, `${selector} is a header button`);
  }
  assert(hiddenOn(ctx.q('.osa-history-btn')) && hiddenOn(ctx.q('.osa-settings-btn-open')), 'History and Settings are hidden on the notebook tab');
  assert(!hiddenOn(ctx.q('.osa-center-dock-btn')) && !hiddenOn(ctx.q('.osa-close-btn')), 'Move to corner and Close are not');
  const stops = ctx.api.__center.focusables(ctx.win());
  for (const selector of ['.osa-center-dock-btn', '.osa-close-btn', '.osa-center-tab-notebook']) {
    assert(stops.includes(ctx.q(selector)), `${selector} is a tab stop`);
  }
  for (const selector of ['.osa-history-btn', '.osa-settings-btn-open', '.osa-chat-input input', '.osa-center-input']) {
    assert(!stops.includes(ctx.q(selector)), `${selector} is not`);
  }
  assert(stops.includes(ctx.frame()), 'and the notebook\'s frame is one');
  assert(!stops.includes(ctx.q('.osa-nb-guard')), 'the focus guard after the frame is not counted as a control');
  // A window that has not been asked before: happy-dom keeps the style it computed first.
  const chat = await onNotebook();
  chat.api.setMode('center');
  chat.tabButton('chat').click();
  const shownOn = (el) => chat.window.getComputedStyle(el).visibility !== 'hidden';
  assert(shownOn(chat.q('.osa-history-btn')) && shownOn(chat.q('.osa-settings-btn-open')), 'on the chat tab, History and Settings are shown');
}

console.log('\nMove to corner and Escape keep the tab, the frame and the conversation');
for (const how of ['Move to corner', 'Escape']) {
  const ctx = await onNotebook();
  ctx.api.setMode('center');
  const frame = ctx.frame();
  const heard = record(ctx);
  if (how === 'Escape') ctx.document.dispatchEvent(key(ctx, 'Escape'));
  else ctx.q('.osa-center-dock-btn').click();
  assert(!ctx.isCenter() && ctx.isOpen() && ctx.tab() === 'notebook', `${how}: docked and open, on the notebook tab`);
  assert(ctx.frame() === frame && ctx.q('.osa-view-notebook').contains(frame), `${how}: the same frame, in the same view`);
  assertEqual(heard.events, [['modechange', { mode: 'corner' }]], `${how}: one modechange, no close`);
  assert(ctx.q('.osa-center-tabs').hidden && ctx.document.getElementById('host-page').hasAttribute('inert') === false, `${how}: the corner has no tabs, and the page is live`);
  assert(inWindow(ctx, active(ctx)), `${how}: focus is in the window, not on the page`);
  ctx.api.setMode('center');
  assert(ctx.tab() === 'notebook' && ctx.frame() === frame, `${how}: raised again, still the notebook, still the frame`);
}

console.log('\nopen() and ask() open on the chat, because they are about asking a question');
{
  const ctx = await onNotebook();
  ctx.api.setMode('center');
  assert(ctx.tab() === 'notebook', 'set up: the dialog is on the notebook tab');
  ctx.api.open({ mode: 'center' });
  assert(ctx.tab() === 'chat' && ctx.isCenter(), 'open() takes the dialog to the chat');
  assert(ctx.frame() !== null && !ctx.q('.osa-center-tabs').hidden, 'the frame is kept, and so are the tabs');
  ctx.tabButton('notebook').click();
  ctx.api.close();
  ctx.api.setMode('center');
  ctx.api.open();
  assert(ctx.isCenter() && ctx.tab() === 'chat', 'a closed window opened by open() is on the chat too');
  ctx.tabButton('notebook').click();
  ctx.api.ask('Which channels are there?', { mode: 'center' });
  await answered(ctx);
  assert(ctx.tab() === 'chat' && ctx.isCenter(), 'ask() takes it to the chat, where the question and its answer are');
  assert(ctx.qa('.osa-chat-messages .osa-message').some((m) => /Which channels are there\?/.test(m.textContent)), 'and the question is in the thread');
}

console.log('\nthe tabs by keyboard: arrows, Home and End move and open; only the selected tab is in the Tab order');
{
  const ctx = await onNotebook();
  ctx.api.setMode('center');
  const frame = ctx.frame();
  ctx.tabButton('notebook').focus();
  ctx.tabButton('notebook').dispatchEvent(key(ctx, 'ArrowLeft'));
  assert(ctx.tab() === 'chat' && active(ctx) === ctx.tabButton('chat'), 'ArrowLeft goes to Chat, opens it and moves focus');
  assertEqual(['chat', 'notebook'].map((n) => ctx.tabButton(n).getAttribute('tabindex')), ['0', '-1'], 'and the Tab order follows');
  ctx.tabButton('chat').dispatchEvent(key(ctx, 'ArrowRight'));
  assert(ctx.tab() === 'notebook' && active(ctx) === ctx.tabButton('notebook'), 'ArrowRight goes to Notebook');
  ctx.tabButton('notebook').dispatchEvent(key(ctx, 'Home'));
  assert(ctx.tab() === 'chat', 'Home is the first tab');
  ctx.tabButton('chat').dispatchEvent(key(ctx, 'End'));
  assert(ctx.tab() === 'notebook', 'End is the last');
  const other = key(ctx, 'ArrowDown');
  ctx.tabButton('notebook').dispatchEvent(other);
  assert(!other.defaultPrevented && ctx.tab() === 'notebook', 'another key is left alone');
  assert(ctx.frame() === frame, 'all of it with the one frame');
}

console.log('\nTab stays in the dialog on the notebook tab, and the frame cannot strand it');
{
  const ctx = await onNotebook();
  ctx.api.setMode('center');
  const stops = ctx.api.__center.focusables(ctx.win());
  assert(stops.length >= 4 && stops.every((el) => inWindow(ctx, el) && !el.closest('[inert]')), `there are stops to cycle through, all in the dialog and none inert (${stops.length})`);
  assert(stops[0] === ctx.tabButton('notebook'), 'the first is the selected tab');
  assert(stops.at(-1) === ctx.frame(), 'the last is the notebook\'s frame');
  const guard = ctx.q('.osa-nb-guard');
  assert(guard.getAttribute('tabindex') === '0' && ctx.q('.osa-view-notebook').lastElementChild === guard, 'the guard is a tab stop, right after the frame\'s view content');
  assert(!!ctx.frame().compareDocumentPosition(guard) && (ctx.frame().compareDocumentPosition(guard) & 4) === 4, 'after the frame in document order, so Tab out of the frame lands on it');
  // Focus Tab carries out of the frame lands on the guard, which gives it back.
  guard.focus();
  assert(active(ctx) === stops[0] && inWindow(ctx, active(ctx)), 'focus that reaches the guard goes to the dialog\'s first stop, the tab');
  // From the first stop Shift+Tab wraps to the last, the frame; from the frame's own
  // element (focus never reaches the document\'s keydown from inside it), Tab is the browser's.
  stops[0].focus();
  const back = key(ctx, 'Tab', { shiftKey: true });
  active(ctx).dispatchEvent(back);
  assert(back.defaultPrevented && active(ctx) === stops.at(-1), 'Shift+Tab on the first stop goes to the frame, the last');
  stops.at(-1).focus();
  const forward = key(ctx, 'Tab');
  ctx.document.dispatchEvent(forward);
  assert(forward.defaultPrevented && active(ctx) === stops[0], 'Tab with focus on the frame element itself wraps to the first');
  ctx.api.setMode('corner');
  assert(ctx.q('.osa-nb-guard').getAttribute('tabindex') === '-1', 'in the corner the guard is no tab stop');
  guard.focus();
  assert(active(ctx) !== stops[0], 'and does nothing if it is reached');
}

console.log('\nEscape in the dialog\'s chrome on the notebook tab docks it, History and Settings aside');
{
  const ctx = await onNotebook();
  ctx.api.setMode('center');
  ctx.q('.osa-center-dock-btn').focus();
  const esc = key(ctx, 'Escape');
  active(ctx).dispatchEvent(esc);
  assert(esc.defaultPrevented && !ctx.isCenter() && ctx.tab() === 'notebook', 'Escape with focus on a header control docks it, keeping the notebook');
  assert(inWindow(ctx, active(ctx)), 'focus is in the window');
}

console.log('\nopening the notebook closes what was laid over the chat, and Try again keeps focus in the dialog');
{
  const ctx = await onNotebook();
  ctx.api.setMode('center');
  ctx.tabButton('chat').click();
  ctx.q('.osa-settings-btn-open').click();
  assert(ctx.q('.osa-settings-overlay.open') !== null, 'set up: Settings is open over the chat');
  ctx.tabButton('notebook').click();
  assert(ctx.q('.osa-settings-overlay.open') === null, 'the notebook tab closes it');
  assert(ctx.api.__center.focusables(ctx.win()).every((el) => !ctx.q('.osa-settings-overlay').contains(el)), 'so the Tab trap is the dialog\'s');
  // Focus inside a panel that the switch closes goes to the selected tab, not to nothing.
  ctx.tabButton('chat').click();
  ctx.q('.osa-settings-btn-open').click();
  ctx.q('.osa-settings-select').focus();
  assert(inWindow(ctx, active(ctx)) && ctx.q('.osa-settings-overlay').contains(active(ctx)), 'set up: focus is inside Settings');
  ctx.tabButton('notebook').click();
  assert(active(ctx) === ctx.tabButton('notebook'), 'focus inside the panel that closed goes to the selected tab');
  ctx.q('.osa-notebook-retry').focus();
  ctx.q('.osa-notebook-retry').click();
  assert(inWindow(ctx, active(ctx)) && active(ctx) === ctx.tabButton('notebook'), 'Try again puts focus on the selected tab, not on the inert page');
}

console.log('\nin the corner and the pop-out the notebook tab leaves Settings and the feedback panel as they were');
{
  const ctx = await onNotebook();
  ctx.q('.osa-launcher-capsule .osa-chat-button').click();
  assert(ctx.tab() === 'chat' && !ctx.isCenter(), 'set up: the corner window is on the chat');
  ctx.q('.osa-settings-btn-open').click();
  assert(ctx.q('.osa-settings-overlay.open') !== null, 'set up: Settings is open');
  ctx.q('.osa-notebook-btn').click();
  assert(ctx.tab() === 'notebook' && ctx.q('.osa-settings-overlay.open') !== null, 'corner: the notebook circle opens the notebook and Settings stays open');
  ctx.q('.osa-launcher-capsule .osa-chat-button').click();
  ctx.q('.osa-feedback-link').click();
  const feedback = ctx.q('.osa-feedback-overlay.open');
  assert(feedback !== null, 'set up: the feedback panel is open');
  ctx.q('.osa-notebook-btn').click();
  assert(ctx.tab() === 'notebook' && ctx.q('.osa-feedback-overlay.open') !== null, 'corner: and so does the feedback panel');

  const popout = await load({ popout: true, widget: { launcher: 'capsule' }, dataset: NOTEBOOK_DATASET });
  await popout.boot();
  popout.q('.osa-settings-btn-open').click();
  assert(popout.q('.osa-settings-overlay.open') !== null, 'set up: the pop-out has Settings open');
  popout.q('.osa-strip-notebook').click();
  assert(popout.container().classList.contains('osa-tab-notebook') && popout.q('.osa-settings-overlay.open') !== null, 'pop-out: the notebook tab opens and Settings stays open');
}

console.log('\na reply that ends on the Notebook tab is said and shown, and clears when the chat is opened');
{
  const ctx = await onNotebook();
  ctx.api.setMode('center');
  ctx.tabButton('chat').click();
  const release = ctx.hold();
  ctx.q('.osa-chat-input input').value = 'A question';
  ctx.q('.osa-send-btn').click();
  ctx.tabButton('notebook').click();
  const unread = () => ctx.tabButton('chat').classList.contains('osa-tab-unread');
  assert(!unread(), 'set up: no cue before the reply ends');
  release();
  await waitUntil(() => unread(), 'the reply ends on the Notebook tab');
  assert(unread() && text(ctx.tabButton('chat')) === 'Chat (new reply)', 'the Chat tab has the cue and says "new reply" in its name');
  const announcer = ctx.q('.osa-tab-announcer');
  await waitUntil(() => /replied/.test(announcer.textContent), 'the announcement');
  assertEqual([announcer.getAttribute('role'), announcer.getAttribute('aria-live')], ['status', 'polite'], 'a polite live region');
  assert(announcer.closest('.osa-view') === null && announcer.closest('[inert]') === null, 'outside the views, so the inert chat view does not silence it');
  ctx.tabButton('chat').click();
  assert(!unread() && text(ctx.tabButton('chat')) === 'Chat' && announcer.textContent === '', 'opening the chat clears the cue and the announcement');
  ctx.q('.osa-chat-input input').value = 'Another';
  ctx.q('.osa-send-btn').click();
  await answered(ctx, 2);
  assert(!unread(), 'a reply that ends while the chat is open leaves no cue');
}

console.log('\nContinue in notebook and a host import take focus with them');
{
  const ctx = await onNotebook();
  ctx.api.setMode('center');
  ctx.tabButton('chat').click();
  ctx.q('.osa-history-btn').focus();
  ctx.tabButton('notebook').click();
  assert(active(ctx) === ctx.tabButton('notebook'), 'a switch that hides the focused control puts focus on the selected tab');
  ctx.tabButton('chat').click();
  ctx.q('.osa-chat-input input').focus();
  ctx.api.setDataset(NOTEBOOK_DATASET);
  ctx.tabButton('notebook').click();
  assert(inWindow(ctx, active(ctx)) && active(ctx) !== ctx.document.body, 'and so does a switch from the chat\'s input');
}

console.log('\nask() keeps what the reader had typed and not sent');
{
  const ctx = await start({ widget: { center_start: CENTER_START } });
  ctx.api.open();
  const input = ctx.q('.osa-chat-input input');
  input.value = 'A half-typed question';
  ctx.api.ask('The page\'s question');
  await answered(ctx);
  assertEqual(ctx.sent[0].message, 'The page\'s question', 'the page\'s question is what was sent');
  assertEqual(input.value, 'A half-typed question', 'the draft is still in the chat input');
  ctx.api.setMode('center');
  ctx.q('.osa-history-btn').click();
  ctx.q('.osa-history-new').click();
  assertEqual(ctx.q('.osa-center-input').value, 'A half-typed question', 'and in the start screen\'s field once it shows again');

  const bare = await start({ dataset: null });
  bare.api.open({ mode: 'center' });
  const field = bare.q('.osa-center-input');
  field.value = 'A draft typed on the start screen';
  field.dispatchEvent(new bare.window.Event('input', { bubbles: true }));
  bare.api.ask('Asked by the page');
  await answered(bare);
  assertEqual(bare.q('.osa-chat-input input').value, 'A draft typed on the start screen', 'a draft from the start screen survives too');
  assertEqual(field.value, 'A draft typed on the start screen', 'in its own field');

  const empty = await start({ dataset: null });
  empty.api.ask('No draft');
  await answered(empty);
  assertEqual(empty.q('.osa-chat-input input').value, '', 'with no draft, the input is left empty');
}

console.log('\nthe start screen keeps the community\'s disclaimer and the feedback link');
{
  const shown = (ctx) => ctx.window.getComputedStyle(ctx.q('.osa-ai-disclaimer')).display !== 'none';
  const textShown = (ctx) => ctx.window.getComputedStyle(ctx.q('.osa-disclaimer-text')).display !== 'none';
  const bare = await start();
  bare.api.open({ mode: 'center' });
  assert(!bare.q('.osa-center-start').hidden, 'sanity: the start screen is up');
  assert(shown(bare) && textShown(bare), 'a community with no note shows its disclaimer row, text included');
  assertEqual(text(bare.q('.osa-disclaimer-text')), bare.hooks.getConfig().disclaimerText, 'with the community\'s own words');
  assert(text(bare.q('.osa-feedback-link')) === 'Send feedback', 'and the Send feedback link');
  bare.q('.osa-feedback-link').click();
  assert(bare.q('.osa-feedback-overlay').classList.contains('open'), 'which opens the feedback panel');

  const noted = await start({ widget: { center_start: CENTER_START } });
  noted.api.open({ mode: 'center' });
  assert(shown(noted) && !textShown(noted), 'a community with a note shows the link, and the note replaces the disclaimer text');
  assertEqual(text(noted.q('.osa-center-note')), CENTER_START.note, 'the note is the footer\'s');
  noted.api.setMode('corner');
  assert(textShown(noted), 'docked, the disclaimer text is the chat\'s again');
}

console.log('\ncalls before init() are bounded, and a page that never calls init() hears about it');
{
  const ctx = await load();
  const heard = record(ctx);
  const warnings = listen();
  for (let i = 0; i < 51; i++) ctx.api.setMode(i % 2 === 0 ? 'center' : 'corner');
  warnings.stop();
  assertEqual(warnings.warn.length, 1, 'the call past the 50th is refused, with a warning');
  await ctx.boot();
  assertEqual(heard.names().filter((name) => name === 'modechange').length, 50, 'the 50 kept ran');

  const idle = await load({ fastInitWarning: true });
  const quiet = listen();
  idle.api.open();
  await waitUntil(() => quiet.warn.length > 0, 'the warning for a widget that never initialized');
  quiet.stop();
  assert(/waiting; OSAChatWidget\.init\(\) has not been called or did not finish/.test(quiet.warn[0]), 'it says calls are waiting and init() was not called or did not finish');

  const prompt = await load({ fastInitWarning: true });
  const none = listen();
  prompt.api.open();
  await prompt.boot();
  await new Promise((resolve) => setTimeout(resolve, 80));
  none.stop();
  assertEqual(none.warn.filter((line) => /OSAChatWidget\.init/.test(line)), [], 'a page that does call init() in time hears nothing');
}

console.log('\nThe header\'s expand button follows widget.expand: pop-out, Expand to the dialog, or none (#756)');
{
  const plain = await start();
  plain.api.open();
  assert(!plain.q('.osa-popout-btn').hidden, 'by default the pop-out button is there');
  assert(plain.q('.osa-expand-btn').hidden, 'and Expand is not');

  const ctx = await start({ widget: { expand: 'center' } });
  ctx.api.open();
  await typeAndSend(ctx, 'What is the sampling rate?');
  assert(!ctx.isCenter(), 'sanity: the corner window');
  assert(ctx.q('.osa-popout-btn').hidden, 'with expand: center the pop-out button is gone');
  assert(!ctx.q('.osa-expand-btn').hidden, 'and Expand stands in its place');
  assertEqual(ctx.q('.osa-expand-btn').getAttribute('aria-label'), 'Expand', 'named Expand');
  let opened = 0;
  ctx.window.open = () => { opened++; return null; };
  ctx.q('.osa-popout-btn').click();
  assertEqual(opened, 0, 'the hidden pop-out button opens no window');
  const nodes = ctx.qa('.osa-chat-messages .osa-message').length;
  const session = ctx.hooks.getSessionId();
  const heard = record(ctx);
  ctx.q('.osa-expand-btn').click();
  assert(ctx.isCenter() && ctx.isOpen(), 'Expand opens the center-stage dialog');
  assertEqual(ctx.win().getAttribute('role'), 'dialog', 'as the modal dialog');
  assertEqual(heard.events, [['modechange', { mode: 'center' }]], 'the page hears modechange once, with center');
  assertEqual(ctx.qa('.osa-chat-messages .osa-message').length, nodes, 'with the same thread');
  assertEqual(ctx.hooks.getSessionId(), session, 'and the same session');
  assert(inWindow(ctx, active(ctx)), 'focus is in the dialog');
  assertEqual(ctx.window.getComputedStyle(ctx.q('.osa-expand-btn')).display, 'none', 'Expand is not drawn in the dialog');
  ctx.q('.osa-center-dock-btn').click();
  assert(ctx.isOpen() && !ctx.isCenter(), 'Move to corner brings it back');
  assert(!ctx.q('.osa-expand-btn').hidden, 'where Expand is again');
  assertEqual(heard.names(), ['modechange', 'modechange'], 'and the page hears the dock too');
  heard.stop();

  const none = await start({ widget: { expand: 'none' } });
  none.api.open();
  assert(none.q('.osa-popout-btn').hidden && none.q('.osa-expand-btn').hidden, 'expand: none shows neither button');

  const host = await load({ widget: { expand: 'center' } });
  host.api.setConfig({ expand: 'none' });
  await host.boot();
  host.api.open();
  assert(host.q('.osa-popout-btn').hidden && host.q('.osa-expand-btn').hidden, "a host page's expand outranks the community's");
  host.api.setConfig({ expand: 'center' });
  assert(!host.q('.osa-expand-btn').hidden, 'and a later setConfig redraws the header at once');

  const bad = await load({ widget: { expand: 'center' } });
  const warnings = listen();
  bad.api.setConfig({ expand: 'window' });
  warnings.stop();
  await bad.boot();
  bad.api.open();
  assert(warnings.warn.some((line) => /invalid expand "window"/.test(line)), 'a host value outside popout, center and none is refused with a warning');
  assert(!bad.q('.osa-expand-btn').hidden, "and the community's own value still applies");

  const wrong = await load({ widget: { expand: 'sideways' } });
  const heardWrong = listen();
  await wrong.boot();
  heardWrong.stop();
  wrong.api.open();
  assert(heardWrong.warn.some((line) => /invalid expand from the community config "sideways"/.test(line)), 'a community value outside them warns');
  assert(!wrong.q('.osa-popout-btn').hidden && wrong.q('.osa-expand-btn').hidden, 'and the pop-out default stays');

  // Escape after Expand docks, as it does any dialog, and the page hears it.
  const esc = await start({ widget: { expand: 'center' } });
  esc.api.open();
  const escHeard = record(esc);
  esc.q('.osa-expand-btn').click();
  const escape = key(esc, 'Escape');
  active(esc).dispatchEvent(escape);
  assert(esc.isOpen() && !esc.isCenter(), 'Escape after Expand docks to the corner');
  assertEqual(escHeard.events, [['modechange', { mode: 'center' }], ['modechange', { mode: 'corner' }]], 'with modechange center, then corner');
  escHeard.stop();

  // Expand with History open: the sheet goes into the dialog with the focus in it, not
  // on the field behind it, which is inert, nor on the Expand button the dialog hides.
  const hist = await start({ widget: { expand: 'center' } });
  hist.api.open();
  await typeAndSend(hist, 'A question for History');
  hist.q('.osa-history-btn').click();
  assert(hist.q('.osa-history-sheet').classList.contains('open'), 'sanity: History is open in the corner');
  hist.q('.osa-expand-btn').focus();
  hist.q('.osa-expand-btn').click();
  assert(hist.isCenter() && hist.q('.osa-history-sheet').classList.contains('open'), 'Expand keeps History open, now in the dialog');
  assert(hist.q('.osa-history-sheet').contains(active(hist)), 'and the focus is in the History sheet');
  const histEscape = key(hist, 'Escape');
  active(hist).dispatchEvent(histEscape);
  assert(!hist.q('.osa-history-sheet').classList.contains('open') && hist.isCenter(), 'the first Escape closes History, and the dialog stays');

  // A capsule on its notebook tab expands onto the notebook tab, as setMode('center') does.
  const nb = await onNotebook({ widget: { launcher: 'capsule', center_start: CENTER_START, expand: 'center' } });
  assertEqual(nb.tab(), 'notebook', 'sanity: the corner is on the notebook tab');
  assert(!nb.q('.osa-expand-btn').hidden, 'Expand is there on the notebook tab');
  nb.q('.osa-expand-btn').click();
  assert(nb.isCenter() && nb.tab() === 'notebook', 'Expand opens the dialog on the notebook tab');
  assertEqual(nb.tabButton('notebook').getAttribute('aria-selected'), 'true', 'with the Notebook tab selected');
  assertEqual(active(nb), nb.tabButton('notebook'), 'and the focus on it');

  const popout = await load({ popout: true, widget: { expand: 'center' } });
  await popout.boot();
  assert(popout.q('.osa-popout-btn').hidden && popout.q('.osa-expand-btn').hidden, 'a pop-out window shows neither: it is the expanded view');
}

console.log('\nThe dialog\'s New chat: a fresh conversation, the one on screen kept in History (#759)');
{
  const ctx = await start();
  ctx.api.open({ mode: 'center' });
  const newBtn = () => ctx.q('.osa-center-new-btn');
  const actions = [...ctx.q('.osa-header-actions').children].filter((el) => el.tagName === 'BUTTON');
  assert(actions.indexOf(newBtn()) === actions.indexOf(ctx.q('.osa-history-btn')) - 1, 'New chat is the pill just before History');
  assertEqual([newBtn().getAttribute('aria-label'), text(newBtn())], ['New chat', 'New chat'], 'named and labelled New chat');
  assert(newBtn().disabled, 'disabled while the conversation is empty');

  const release = ctx.hold();
  ctx.q('.osa-center-input').value = 'What is the sampling rate?';
  ctx.q('.osa-center-send').click();
  await waitUntil(() => ctx.sent.length === 1, 'the question is in flight');
  assert(newBtn().disabled, 'and while a reply is loading');
  release();
  await answered(ctx);
  assert(!newBtn().disabled, 'enabled once the conversation has a reply');

  const before = ctx.stored();
  const firstId = before.activeId;
  const session = ctx.hooks.getSessionId();
  const heard = record(ctx);
  newBtn().click();
  const after = ctx.stored();
  assert(after.activeId !== firstId, 'it starts a new conversation');
  assert(after.conversations.some((c) => c.id === firstId && c.messages.length >= 2), 'and the one on screen is kept, in History');
  assert(ctx.hooks.getSessionId() !== session, 'with a new session');
  assert(ctx.isCenter() && ctx.win().classList.contains('osa-center-empty'), 'the dialog stays, on its start screen');
  assertEqual(active(ctx), ctx.q('.osa-center-input'), 'with the focus in the start screen\'s field');
  assert(newBtn().disabled, 'and New chat disabled again, the new conversation being empty');
  assertEqual(heard.events, [], 'the page hears nothing: no mode change, no send');
  heard.stop();

  // History's own New chat, in the dialog, lands on the start screen's field too.
  ctx.q('.osa-center-input').value = 'A second question';
  ctx.q('.osa-center-send').click();
  await answered(ctx, 2);
  ctx.q('.osa-history-btn').click();
  ctx.q('.osa-history-new').click();
  assert(!ctx.q('.osa-history-sheet').classList.contains('open') && ctx.win().classList.contains('osa-center-empty'), 'History\'s New chat starts one as well');
  assertEqual(active(ctx), ctx.q('.osa-center-input'), 'with the focus in the start screen\'s field, not the hidden chat field');

  ctx.api.setMode('corner');
  assertEqual(ctx.window.getComputedStyle(newBtn()).display, 'none', 'the corner does not draw it: Clear chat is the corner\'s');

  const nb = await onNotebook();
  nb.api.setMode('center');
  assertEqual(nb.tab(), 'notebook', 'sanity: the dialog on the notebook tab');
  assertEqual(nb.window.getComputedStyle(nb.q('.osa-center-new-btn')).visibility, 'hidden', 'on the notebook tab it is hidden, with History');
}

console.log('\n' + '='.repeat(60));
console.log(`${passed} passed, ${failed} failed`);
console.log('='.repeat(60));
process.exit(failed ? 1 : 0);
