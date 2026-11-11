/**
 * OSAChatWidget.seed (#730), run against the real widget source in happy-dom windows,
 * the way test-widget-host-api.js runs the host API.
 *
 * What it holds the widget to: seed(text, {mode}?) puts `text` at the head of the chat
 * as the assistant's own reply and opens the window in the given mode, and sends nothing
 * (no request, no 'send' event, no Turnstile token). The text is drawn as Markdown, with
 * the same sanitizing a reply gets, and it has no thumbs row (it is not a model's answer).
 * The chat is kept: in History, with a readable title, and across a reload. A chat with
 * no message from the reader is reused; one with a message from the reader stays in
 * History and a new chat takes the seeded reply. The reader's first message then carries
 * the seeded text as the one earlier turn (`prior_turns`), and the server is not told to
 * expect a session it has never held. A bad call warns with the "[OSA] seed:" prefix and
 * does nothing; a call before init() is kept and replayed in order; a call while a reply
 * is loading is refused. The places that took "one message" for "an empty chat" (the
 * start screen, the reset button, setConfig's greeting, the suggestions) know better.
 *
 * It also holds the dialog's motion and the "Move to corner" icon to the brief of #730: the
 * open and the dock are 80 to 150 ms keyframe animations on the opened state, off under
 * reduced motion, with the state changing at once and nothing waiting on an animation event;
 * the icon points to the bottom right, at the header icons' 18 px.
 *
 * What stands in: `fetch` (the community config, and a one-line chat reply that can be
 * held, answering like the server with a `session` event first), and Cloudflare's
 * Turnstile script (render and reset, which hand out tokens). The request bodies it
 * records are what the widget sent. The server's side of `prior_turns` is
 * tests/test_api/test_reseed.py's.
 *
 * Run with: bun frontend/test-widget-seed.js
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
const KEY = 'osa-test-seed';
const GREETING = 'Hello from the seed tests';
const A = 'xx099904';
const TITLE = 'A test dataset';
const SEED = 'This dataset has **64 channels** and 3 sessions. See [the paper](https://example.org/paper) and [its page](/dataset/xx099904).';
const RESEED_TURN_MAX_CHARS = Number(/const RESEED_TURN_MAX_CHARS = (\d+);/.exec(SOURCE)[1]);

// The community config, answered at once, and a chat reply of one line. `calls` records
// every request the widget makes; `sent` every chat request's body; `hold()` keeps the
// next chat requests unanswered until the returned function is called.
function serverFetch() {
  const calls = [];
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
    calls.push(u);
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
      widget: { title: 'Seed Tests', search_paths: ['/discover'], suggested_questions: ['What can you do?'] },
      client_tools: [],
      runtime: null,
    }), { headers: { 'content-type': 'application/json' } });
  };
  const hold = () => {
    let open;
    gate = { promise: new Promise((resolve) => { open = resolve; }) };
    return () => { gate = null; open(); };
  };
  return { fetch, calls, sent, hold };
}

// Cloudflare's script, as far as the widget uses it: render hands out a token, reset
// counts and hands out another.
function makeTurnstile() {
  let count = 0;
  let callback = null;
  const api = {
    resets: 0,
    render(_el, options) {
      callback = options.callback;
      count += 1;
      callback(`token-${count}`);
      return 'widget-1';
    },
    reset() {
      api.resets += 1;
      count += 1;
      const token = `token-${count}`;
      setTimeout(() => callback(token), 5);
    },
  };
  return api;
}

let listening = null;
function listen() {
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

// A page before init(): the widget's script has run. `boot()` is what the page's init
// call does.
async function load({ path = '/dataset/x', dataset = { id: A, title: TITLE }, storage = null, turnstile = null, sessionMarker = true, popout = false } = {}) {
  const window = new Window({
    url: `http://localhost${path}`,
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  if (sessionMarker) window.sessionStorage.setItem(`${KEY}-session`, '1');
  if (storage) window.localStorage.setItem(KEY, storage);
  if (turnstile) window.turnstile = turnstile;
  if (popout) window.__OSA_CHAT_CONFIG__ = { apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING, fullscreen: true };
  window.document.body.innerHTML = '<main id="host-page"><h1>The host page</h1></main>';
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const server = serverFetch();
  window.fetch = server.fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, server.fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  return {
    window, api, hooks: api.__browser, sent: server.sent, calls: server.calls, hold: server.hold, document: window.document,
    async boot() {
      if (dataset) api.setDataset(dataset);
      api.setConfig({
        apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING,
        ...(turnstile ? { turnstileSiteKey: 'site-key' } : {}),
      });
      api.init();
      await waitUntil(() => this.container().querySelector('.osa-chat-title').firstChild.textContent.trim() === 'Seed Tests', 'the community config arrives');
      return this;
    },
    container: () => window.document.querySelector('.osa-chat-widget'),
    q: (s) => window.document.querySelector(`.osa-chat-widget ${s}`),
    qa: (s) => [...window.document.querySelectorAll(`.osa-chat-widget ${s}`)],
    win: () => window.document.querySelector('.osa-chat-window'),
    isOpen: () => window.document.querySelector('.osa-chat-window').classList.contains('open'),
    isCenter: () => window.document.querySelector('.osa-chat-window').classList.contains('osa-center'),
    stored: () => JSON.parse(window.localStorage.getItem(KEY)),
    messages: () => window.document.querySelectorAll('.osa-chat-widget .osa-chat-messages .osa-message'),
  };
}

async function start(options) {
  const ctx = await load(options);
  return ctx.boot();
}

function record(ctx) {
  const events = [];
  const offs = ['open', 'close', 'modechange', 'send'].map((name) => ctx.api.on(name, (payload) => events.push([name, payload])));
  return { events, names: () => events.map(([name]) => name), stop: () => offs.forEach((off) => off()) };
}

async function answered(ctx, count = 1) {
  await waitUntil(() => ctx.sent.length >= count && !ctx.q('.osa-chat-input input').disabled, 'the reply ends');
}

async function typeAndSend(ctx, question) {
  const expected = ctx.sent.length + 1;
  ctx.q('.osa-chat-input input').value = question;
  ctx.q('.osa-send-btn').click();
  await answered(ctx, expected);
}

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const idOf = (ctx) => ctx.hooks.getActiveConversation().id;

console.log('='.repeat(60));
console.log('Widget: OSAChatWidget.seed (#730)');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// What seed does
// ---------------------------------------------------------------------------

console.log('\nseed is a function, before init() and after');
{
  const ctx = await load();
  assertEqual(typeof ctx.api.seed, 'function', 'OSAChatWidget.seed is a function before init(), for a page that feature-detects it');
  await ctx.boot();
  assertEqual(typeof ctx.api.seed, 'function', 'and after');
}

console.log('\nseed sends nothing, spends no Turnstile token, and opens the window');
{
  const turnstile = makeTurnstile();
  const ctx = await start({ turnstile });
  const heard = record(ctx);
  const requests = ctx.calls.length;
  const resets = turnstile.resets;
  ctx.api.seed(SEED);
  assertEqual(ctx.calls.length, requests, 'zero fetch calls');
  assertEqual(ctx.sent.length, 0, 'no chat request');
  assert(!heard.names().includes('send'), 'no send event');
  assertEqual(turnstile.resets, resets, 'the Turnstile widget was not reset, so its token is not spent');
  assert(ctx.isOpen() && !ctx.isCenter(), 'the window is open, in the corner by default');
  assertEqual(heard.events, [['open', { mode: 'corner' }]], 'and the page heard one open');
  assertEqual(ctx.messages().length, 1, 'the chat is the one message');
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: SEED, seeded: true }], 'the seeded reply, in place of the greeting');
  assert(!text(ctx.q('.osa-chat-messages')).includes(GREETING), 'the greeting is not shown');
  assert(ctx.messages()[0].querySelector('.osa-message-usage') === null, 'it has no usage line');
  await typeAndSend(ctx, 'Does it have events?');
  assertEqual(ctx.sent[0].cf_turnstile_response, 'token-1', 'the reader\'s own message still has the page\'s first token');
}

console.log('\nseed opens in the given mode');
{
  const ctx = await start();
  const heard = record(ctx);
  ctx.api.seed(SEED, { mode: 'center' });
  assert(ctx.isOpen() && ctx.isCenter(), 'the dialog');
  assertEqual(heard.events, [['open', { mode: 'center' }], ['modechange', { mode: 'center' }]], 'open, then modechange, as open({mode}) does');
  assert(ctx.q('.osa-center-start').hidden && !ctx.win().classList.contains('osa-center-empty'), 'the start screen is not shown over the seeded reply');
  assert(text(ctx.q('.osa-chat-messages')).includes('64 channels'), 'the reply is');
  ctx.api.setMode('corner');
  assert(!ctx.isCenter() && ctx.messages().length === 1, 'docking keeps it');
  ctx.api.seed('Another note.', { mode: 'center' });
  assert(ctx.isCenter() && ctx.messages().length === 1 && text(ctx.messages()[0]).includes('Another note.'), 'seeding again, in the dialog, replaces it');
}

console.log('\nthe seeded text is Markdown, drawn like a reply, and sanitized');
{
  const ctx = await start();
  ctx.api.seed(SEED);
  const content = ctx.messages()[0].querySelector('.osa-message-content');
  assert(content.querySelector('strong') && text(content.querySelector('strong')) === '64 channels', 'bold is bold');
  const https = content.querySelector('a[href="https://example.org/paper"]');
  const relative = content.querySelector('a[href="/dataset/xx099904"]');
  assert(https && text(https) === 'the paper', 'an https link is a link');
  assert(relative && text(relative) === 'its page', 'and so is a relative /dataset link');
  assertEqual(https.getAttribute('rel'), 'noopener noreferrer', 'which opens safely');

  const hostile = await start();
  hostile.api.seed('Before <script>window.__pwned = 1</script> [bad](javascript:alert(1)) <img src=x onerror="window.__pwned=2"> after');
  const shown = hostile.messages()[0].querySelector('.osa-message-content');
  assert(shown.querySelector('script') === null, 'a script tag is not an element');
  assert(shown.querySelector('img') === null, 'neither is an image with a handler');
  assert(!shown.innerHTML.includes('href="javascript:'), 'a javascript: link has no href');
  assert(!shown.querySelector('[onerror]') && hostile.window.__pwned === undefined, 'and nothing ran');
  assert(text(shown).includes('Before') && text(shown).includes('after'), 'the words around it are still there');
}

console.log('\nthe seeded reply is not rated: it is not a model\'s answer');
{
  const ctx = await start();
  ctx.api.seed(SEED);
  assertEqual(ctx.qa('.osa-message-feedback').length, 0, 'no thumbs row under it');
  assertEqual(ctx.qa('.osa-message-copy-btn').length, 1, 'but it can be copied');
  await typeAndSend(ctx, 'Does it have events?');
  assertEqual(ctx.qa('.osa-message-feedback').length, 1, 'the answer to the reader has one, and only that');
}

// ---------------------------------------------------------------------------
// The places that took "one message" for "an empty chat"
// ---------------------------------------------------------------------------

console.log('\na seeded chat is not an empty chat');
{
  const ctx = await start({ dataset: null });
  assert(ctx.q('.osa-reset-btn').disabled, 'sanity: the reset button is off on the greeting');
  assert(ctx.q('.osa-suggestions').style.display === 'block' && ctx.qa('.osa-suggestion').length > 0, 'sanity: suggestions are offered on the greeting');
  ctx.api.seed(SEED);
  assert(!ctx.q('.osa-reset-btn').disabled, 'the reset button is on, so the reader can clear it');
  assert(ctx.q('.osa-suggestions').style.display === 'none', 'the suggestions are not offered under it, as they are not in a chat that has begun');
  ctx.api.setConfig({ initialMessage: 'A new greeting' });
  assertEqual(ctx.hooks.getMessages()[0].content, SEED, 'setConfig does not overwrite it with the greeting');
  assert(text(ctx.messages()[0]).includes('64 channels'), 'nor redraw it as one');
  ctx.q('.osa-reset-btn').click();
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: 'A new greeting' }], 'Reset gives the greeting back');
}

// ---------------------------------------------------------------------------
// The conversation: History, reload, reuse
// ---------------------------------------------------------------------------

console.log('\nthe conversation is in History, with a readable title');
{
  const ctx = await start();
  const id = idOf(ctx);
  ctx.api.seed(SEED);
  const saved = ctx.stored().conversations.find((c) => c.id === id);
  assert(saved && saved.messages.length === 1 && saved.messages[0].seeded === true, 'it is saved, with the mark');
  assertEqual(saved.title, Array.from(SEED).slice(0, 60).join(''), 'titled with the first 60 characters of the text');
  ctx.q('.osa-history-btn').click();
  const rows = ctx.qa('.osa-history-row');
  assertEqual(rows.length, 1, 'History lists it');
  assert(text(rows[0].querySelector('.osa-history-title')) !== '', 'under a title that is not blank');

  const untitled = await start({
    storage: JSON.stringify({
      version: 3,
      activeId: 's1',
      conversations: [{ id: 's1', context: null, title: '', sessionId: null, createdAt: 1, updatedAt: Date.now(), messages: [{ role: 'assistant', content: 'Seeded text', seeded: true }] }],
    }),
  });
  untitled.q('.osa-history-btn').click();
  assertEqual(text(untitled.q('.osa-history-row .osa-history-title')), 'Untitled chat', 'a saved one with no title shows the fallback label');
}

console.log('\nthe conversation survives a reload');
{
  const first = await start();
  first.api.seed(SEED);
  const raw = first.window.localStorage.getItem(KEY);
  const second = await start({ storage: raw });
  const [kept] = second.hooks.getMessages();
  assert(second.hooks.getMessages().length === 1 && kept.role === 'assistant' && kept.content === SEED && kept.seeded === true, 'the same message, with its mark');
  assertEqual(second.hooks.listConversations().length, 1, 'one conversation');
  second.api.open();
  assertEqual(second.messages().length, 1, 'it is drawn, once');
  assert(text(second.messages()[0]).includes('64 channels'), 'as it was');
  assertEqual(second.qa('.osa-message-feedback').length, 0, 'still with no thumbs row');
  await typeAndSend(second, 'A question after the reload');
  assertEqual(second.sent[0].prior_turns, [{ role: 'assistant', text: SEED }], 'and the reader\'s first message after it still carries the seeded text');

  const hand = await start({
    storage: JSON.stringify({
      version: 3,
      activeId: 'h1',
      conversations: [{ id: 'h1', context: null, title: 'x', sessionId: null, createdAt: 1, updatedAt: Date.now(), messages: [
        { role: 'assistant', content: 'Seeded text', seeded: 'yes' },
        { role: 'user', content: 'hi', seeded: true },
      ] }],
    }),
  });
  assertEqual(hand.hooks.getMessages().map((m) => m.seeded), [undefined, undefined], 'only the boolean true is kept as a mark, and only on an assistant message');
}

console.log('\nwith no message from the reader the chat is reused; with one, a new chat starts');
{
  const ctx = await start();
  const id = idOf(ctx);
  ctx.api.seed('First note.');
  assertEqual(idOf(ctx), id, 'a chat with only the greeting is reused');
  ctx.api.seed('Second note.');
  assertEqual(idOf(ctx), id, 'and so is one with only a seeded reply');
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: 'Second note.', seeded: true }], 'its message is replaced, not added to');
  assertEqual(ctx.hooks.listConversations().length, 1, 'one conversation in all');

  await typeAndSend(ctx, 'My own question');
  assert(ctx.hooks.getMessages().length >= 3, 'sanity: the chat has the reader\'s question now');
  ctx.api.seed('Third note.');
  const next = idOf(ctx);
  assert(next !== id, 'a chat with a message from the reader stays, and a new one opens');
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: 'Third note.', seeded: true }], 'with the seeded reply');
  const list = ctx.hooks.listConversations();
  assertEqual(list.length, 2, 'both are in History');
  const old = list.find((c) => c.id === id);
  assert(old && old.messages.some((m) => m.role === 'user' && m.content === 'My own question'), 'the old one still holds the reader\'s question');
  assertEqual(ctx.sent.length, 1, 'and still nothing was sent by seed');
  ctx.q('.osa-history-btn').click();
  assertEqual(ctx.qa('.osa-history-row').length, 2, 'the sheet lists both');
}

console.log('\na chat that had a session but no message from the reader does not keep it');
{
  const ctx = await start();
  ctx.hooks.setSessionId('session-from-before');
  ctx.api.seed(SEED);
  await typeAndSend(ctx, 'First question');
  assert(!('session_id' in ctx.sent[0]), 'the request names no session, so the server is given the seeded text');
  assertEqual(ctx.sent[0].prior_turns, [{ role: 'assistant', text: SEED }], 'and carries it');
}

console.log('\na seeded chat is content when the page moves or the reader comes back');
{
  const B = 'xx099905';
  const ctx = await start();
  ctx.api.seed(SEED);
  const id = idOf(ctx);
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: A }, 'the seeded chat has the page\'s context');
  ctx.api.setDataset({ id: B, title: 'Another dataset' });
  const notice = ctx.q('.osa-context-notice');
  assert(notice && text(notice).includes(B), 'the page moving to another dataset asks, instead of taking the context silently');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: A }, 'the chat keeps its own context until the reader chooses');
  await typeAndSend(ctx, 'About the new one');
  assert(idOf(ctx) !== id, 'the next message starts a new chat');
  assert(ctx.hooks.listConversations().some((c) => c.id === id && c.messages[0].seeded === true), 'and the seeded one is kept in History');
  assert(!('prior_turns' in ctx.sent[0]), 'without carrying the other dataset\'s summary into it');

  // A chat opened under no context takes the page's when it is seeded.
  const bare = await start({ dataset: null, path: '/elsewhere' });
  bare.api.setDataset({ id: A, title: TITLE });
  bare.api.seed(SEED);
  assertEqual(bare.hooks.getActiveConversation().context, { type: 'dataset', id: A }, 'a reused chat takes the context of the page it is seeded on');

  // A return in a new browser session starts a new chat, as for a chat the reader wrote in.
  const first = await start();
  first.api.seed(SEED);
  const raw = first.window.localStorage.getItem(KEY);
  const back = await start({ storage: raw, sessionMarker: false });
  assert(back.hooks.getActiveConversation().id !== idOf(first), 'a seeded chat is not taken for an empty one on a return');
  assertEqual(back.hooks.listConversations().filter((c) => c.messages.some((m) => m.seeded)).length, 1, 'the seeded one stays in History');
}

// ---------------------------------------------------------------------------
// What the first send carries
// ---------------------------------------------------------------------------

console.log('\nthe reader\'s first send carries the seeded text as the one earlier turn');
{
  const ctx = await start();
  ctx.api.seed(SEED);
  await typeAndSend(ctx, 'Does it have events?');
  const [first] = ctx.sent;
  assertEqual(first.message, 'Does it have events?', 'the message is the reader\'s');
  assertEqual(first.prior_turns, [{ role: 'assistant', text: SEED }], 'prior_turns is the seeded text and nothing else');
  assert(!('session_id' in first) && !('expect_session' in first), 'with no session, so no expect_session: the server holds none to expect');
  await typeAndSend(ctx, 'And the sessions?');
  const second = ctx.sent[1];
  assert(!('prior_turns' in second), 'the second send carries no prior_turns: the server holds the session now');
  assertEqual(second.session_id, 'session-1', 'it names the session');
  assertEqual(second.expect_session, true, 'and, as any request that names one without turns does, expects it');
}

console.log('\nthe message the reader sends is its own field, never one of the earlier turns');
{
  const ctx = await start();
  ctx.api.seed('Short.');
  await typeAndSend(ctx, 'Short.');
  assertEqual(ctx.sent[0].prior_turns, [{ role: 'assistant', text: 'Short.' }], 'the earlier turn is the seeded one; the message is its own field');
}

// ---------------------------------------------------------------------------
// Bad calls, the queue, a reply in flight
// ---------------------------------------------------------------------------

console.log('\na bad call warns with the seed prefix and does nothing');
{
  const ctx = await start();
  const heard = record(ctx);
  const warnings = listen();
  const refused = [
    () => ctx.api.seed(''),
    () => ctx.api.seed('   \n'),
    () => ctx.api.seed(5),
    () => ctx.api.seed(undefined),
    () => ctx.api.seed(null),
    () => ctx.api.seed({ text: 'x' }),
    () => ctx.api.seed('x'.repeat(RESEED_TURN_MAX_CHARS + 1)),
    () => ctx.api.seed('A note', { mode: 'sideways' }),
    () => ctx.api.seed('A note', 'center'),
    () => ctx.api.seed('A note', ['center']),
  ];
  for (const call of refused) call();
  warnings.stop();
  assertEqual(warnings.warn.length, refused.length, 'each says so, once');
  assert(warnings.warn.every((line) => line.startsWith('[OSA] seed: ')), 'each with the "[OSA] seed:" prefix');
  assert(!ctx.isOpen() && !ctx.isCenter(), 'the window is untouched');
  assertEqual(ctx.hooks.getMessages().map((m) => m.content), [GREETING], 'so is the chat');
  assertEqual(heard.names(), [], 'no event fired');
  assertEqual(ctx.sent.length, 0, 'nothing was sent');
  ctx.api.seed(' '.repeat(3) + 'x'.repeat(RESEED_TURN_MAX_CHARS - 3));
  assert(ctx.isOpen(), 'a text of exactly the limit is accepted');
  // The limit counts the text as stored and sent, which is trimmed.
  ctx.api.close();
  const padded = ' '.repeat(2) + 'y'.repeat(RESEED_TURN_MAX_CHARS) + '\n\n';
  ctx.api.seed(padded);
  assert(ctx.isOpen() && ctx.hooks.getMessages()[0].content === 'y'.repeat(RESEED_TURN_MAX_CHARS), 'a text that trims to the limit is accepted, and stored trimmed');

  const early = await load();
  const earlyWarnings = listen();
  early.api.seed('');
  early.api.seed('A note', { mode: 'left' });
  earlyWarnings.stop();
  assertEqual(earlyWarnings.warn.length, 2, 'before init() it says so at once, and does not keep the call');
  await early.boot();
  assert(!early.isOpen() && early.hooks.getMessages().length === 1, 'so there is nothing to replay');
}

console.log('\nthe pop-out shows its opener\'s chat: seed does nothing there');
{
  const ctx = await start({ popout: true });
  const heard = record(ctx);
  const warnings = listen();
  ctx.api.seed(SEED, { mode: 'center' });
  warnings.stop();
  assertEqual(warnings.warn.length, 1, 'it says so on the console, once');
  assert(warnings.warn[0].startsWith('[OSA] seed: ') && warnings.warn[0].includes('pop-out'), 'with the seed prefix, naming the pop-out');
  assertEqual(ctx.hooks.getMessages().map((m) => m.content), [GREETING], 'the chat is untouched');
  assert(!ctx.isCenter(), 'the window did not change mode');
  assertEqual(heard.events, [], 'no event fired');
  assertEqual(ctx.sent.length, 0, 'nothing was sent');
}

console.log('\na call before init() is kept and replayed, in order');
{
  const ctx = await load();
  const heard = record(ctx);
  ctx.api.seed('First note.');
  ctx.api.seed('Second note.', { mode: 'center' });
  ctx.api.setMode('corner');
  assert(ctx.container() === null && ctx.calls.length === 0, 'nothing is drawn or fetched while there is no widget');
  assertEqual(heard.names(), [], 'and nothing is heard');
  await ctx.boot();
  assertEqual(heard.events, [['open', { mode: 'corner' }], ['modechange', { mode: 'center' }], ['modechange', { mode: 'corner' }]],
    'they ran in the order they were made: the first seed opens, the second moves it to the dialog, then the dock');
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: 'Second note.', seeded: true }], 'the later text is the one that is left');
  assert(ctx.isOpen() && !ctx.isCenter(), 'the window is open and docked');
  assertEqual(ctx.sent.length, 0, 'nothing was sent');

  const mixed = await load();
  mixed.api.seed(SEED);
  mixed.api.ask('A question asked right after');
  await mixed.boot();
  await waitUntil(() => mixed.sent.length === 1, 'the question is sent');
  assertEqual(mixed.sent[0].prior_turns, [{ role: 'assistant', text: SEED }], 'a seed then an ask: the question carries the seeded text');
  await answered(mixed);
}

console.log('\na seed while a reply is loading is refused');
{
  const ctx = await start();
  const release = ctx.hold();
  ctx.api.ask('The first question');
  await waitUntil(() => ctx.sent.length === 1, 'the first question is in flight');
  const before = ctx.hooks.getMessages().map((m) => m.content);
  const heard = record(ctx);
  const warnings = listen();
  ctx.api.seed('A note for later', { mode: 'center' });
  warnings.stop();
  assertEqual(warnings.warn.length, 1, 'it warns');
  assert(warnings.warn[0].startsWith('[OSA] seed: '), 'with the seed prefix');
  assertEqual(ctx.hooks.getMessages().map((m) => m.content), before, 'the chat is as it was');
  assert(!ctx.isCenter() && heard.names().length === 0, 'the window did not move, and nothing was heard');
  release();
  await answered(ctx);
  assertEqual(ctx.sent.length, 1, 'only the first question was sent');
}

// ---------------------------------------------------------------------------
// The motion and the icon (#730)
// ---------------------------------------------------------------------------

// The text of the CSS block whose "{" is at `open`, braces matched.
function blockAt(css, open) {
  let depth = 0;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1;
    if (css[i] === '}') {
      depth -= 1;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  throw new Error('unbalanced braces');
}

const NEW_ANIMATIONS = ['osa-center-in', 'osa-dock-in', 'osa-center-scrim-in', 'osa-center-scrim-out'];
const settle = () => new Promise((resolve) => setTimeout(resolve, 200));

console.log('\nthe dialog opens and docks with a short keyframe animation');
{
  const ctx = await start();
  const css = [...ctx.document.querySelectorAll('style')].map((el) => el.textContent).join('\n').replace(/\/\*[\s\S]*?\*\//g, '');
  const reduced = /@media \(prefers-reduced-motion: reduce\)\s*\{/.exec(css);
  assert(!!reduced, 'the widget has its reduced-motion block');
  assertEqual(css.match(/@media \(prefers-reduced-motion: reduce\)/g).length, 1, 'and only one');
  const reducedBody = blockAt(css, reduced.index + reduced[0].length - 1);

  // Every rule outside the reduced-motion block that names one of the new animations.
  const outside = css.replace(reduced[0] + reducedBody + '}', '');
  const rules = [...outside.matchAll(/([^{}]+)\{([^{}]*animation:[^{}]*)\}/g)]
    .map(([, selector, body]) => ({ selector: selector.trim(), body }))
    .filter((rule) => NEW_ANIMATIONS.some((name) => rule.body.includes(name)));
  const selectors = rules.map((rule) => rule.selector);
  assert(selectors.some((sel) => sel.includes('.osa-center-scrim.open')), 'the scrim fades in on its opened state');
  assert(selectors.some((sel) => sel.includes('.osa-chat-window.osa-center.open')), 'the window fades and scales in on its opened state');
  assert(selectors.some((sel) => sel.includes('.osa-dock-in')), 'the corner window fades in after a dock');
  assert(selectors.some((sel) => sel.includes('.osa-center-scrim.closing')), 'the scrim fades out after a dock');
  assert(rules.every((rule) => !/transition/.test(rule.body)), 'all of them are animations, not transitions (the center rules force transition: none)');

  // A dock swaps the center-open rule for the dock rule in one style recalc; with the same
  // animation-name the browser keeps the finished animation and nothing plays.
  const animName = (selectorPart) => {
    const rule = rules.find((r) => r.selector.includes(selectorPart));
    return rule && /animation:\s*([\w-]+)/.exec(rule.body)[1];
  };
  const openName = animName('.osa-chat-window.osa-center.open');
  const dockName = animName('.osa-dock-in');
  assert(!!openName && !!dockName && openName !== dockName, `the dock animation has its own name (${openName} vs ${dockName}) so it restarts after a settled open`);

  const durations = rules.map((rule) => Number(/animation:\s*[\w-]+\s+(\d+)ms/.exec(rule.body)[1]));
  assert(durations.length === rules.length && durations.every((ms) => ms >= 80 && ms <= 150), `every duration is between 80 and 150 ms (${durations.join(', ')})`);
  const cleanup = Number(/const CENTER_FADE_CLEANUP_MS = (\d+);/.exec(SOURCE)[1]);
  assert(cleanup >= 80 && cleanup <= 150 && cleanup >= Math.max(...durations), `and so is the timer that removes the classes (${cleanup} ms), which outlasts the longest`);
  for (const name of NEW_ANIMATIONS) {
    assert(new RegExp(`@keyframes ${name}\\b`).test(css), `@keyframes ${name} is defined`);
  }

  // Reduced motion: no animation at all for each of the new rules.
  for (const selector of selectors) {
    assert(reducedBody.includes(selector), `reduced motion names ${selector}`);
  }
  const dockRule = /([^{}]*\.osa-dock-in[^{}]*)\{([^{}]*)\}/.exec(reducedBody);
  assert(dockRule && /animation:\s*none\s*!important/.test(dockRule[2]), 'and sets animation: none !important in the rule that names them');
  assert(/\.osa-center-scrim\.closing\s*\{[^}]*display:\s*none\s*!important/.test(reducedBody), 'the scrim that would fade out is not shown at all');

  // No state hangs on an animation or transition event.
  assert(!/transitionend|animationend|animationcancel|transitioncancel/i.test(SOURCE), 'nothing in the widget listens for transitionend or animationend');
}

console.log('\nopening and docking change the state at once, and the cosmetic classes go by timer');
{
  const ctx = await start();
  const scrim = ctx.q('.osa-center-scrim');
  const page = ctx.document.getElementById('host-page');
  ctx.api.open({ mode: 'center' });
  assert(ctx.isCenter() && scrim.classList.contains('open') && page.hasAttribute('inert'), 'open: the dialog, its scrim and the inert page, in the same call');
  assertEqual(ctx.document.body.style.overflow, 'hidden', 'with the scroll locked');
  ctx.q('.osa-center-dock-btn').click();
  assert(!ctx.isCenter() && ctx.isOpen(), 'dock: docked, at once');
  assert(!scrim.classList.contains('open') && !page.hasAttribute('inert'), 'the scrim is no longer open and the page is not inert, at once');
  assertEqual(ctx.document.body.style.overflow, '', 'the scroll is unlocked');
  assert(ctx.win().classList.contains('osa-dock-in') && scrim.classList.contains('closing'), 'only the cosmetic classes remain for a moment');
  await settle();
  assert(!ctx.win().classList.contains('osa-dock-in') && !scrim.classList.contains('closing'), 'and a timer takes them off');

  ctx.api.setMode('center');
  ctx.api.setMode('corner');
  assert(ctx.win().classList.contains('osa-dock-in') && scrim.classList.contains('closing'), 'setMode("corner") docks the same way');
  ctx.api.setMode('center');
  assert(!ctx.win().classList.contains('osa-dock-in') && !scrim.classList.contains('closing') && scrim.classList.contains('open'),
    'undocking again at once leaves neither class on');
  ctx.document.dispatchEvent(new ctx.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  assert(!ctx.isCenter() && ctx.win().classList.contains('osa-dock-in'), 'Escape docks the same way');
  await settle();
  ctx.api.setMode('center');
  ctx.api.close();
  assert(!ctx.win().classList.contains('osa-dock-in') && !scrim.classList.contains('closing') && !scrim.classList.contains('open'),
    'closing the dialog gives no fading scrim: the window goes with it');
}

console.log('\nMove to corner points to the bottom right');
{
  const ctx = await start();
  const icon = /ICONS\.dock = '([^']*)';/.exec(SOURCE)[1];
  assert(icon.includes('viewBox="0 0 24 24"'), 'a 24 by 24 view box');
  const paths = [...icon.matchAll(/<path d="([^"]*)"/g)].map((m) => m[1]);
  assertEqual(paths, ['M7 7l10 10', 'M17 7v10H7'], 'the shaft from the top left to the bottom right, and the head at its end');
  assert(icon.includes('fill="none"') && icon.includes('stroke="currentColor"') && icon.includes('stroke-width="2"'), 'a stroke icon like the others');
  const button = ctx.q('.osa-center-dock-btn');
  const drawn = button.querySelector('svg');
  assert(drawn && drawn.getAttribute('viewBox') === '0 0 24 24', 'it is what the button draws');
  assertEqual([...drawn.querySelectorAll('path')].map((p) => p.getAttribute('d')), paths, 'with those paths');
  const css = [...ctx.document.querySelectorAll('style')].map((el) => el.textContent).join('\n');
  assert(/\.osa-header-btn svg\s*\{[^}]*width:\s*18px;[^}]*height:\s*18px;/.test(css), 'at the header icons\' 18 px');
  assertEqual(button.getAttribute('aria-label'), 'Move to corner', 'the button is still named Move to corner');
  assertEqual(button.getAttribute('title'), 'Move to corner', 'with the same title');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
