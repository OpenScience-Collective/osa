/**
 * A fresh chat on return (#655), run against the real widget source in happy-dom
 * windows, the way test-widget-history-store.js runs the conversation store.
 *
 * What it holds the widget to: a page that loads with a conversation the reader has
 * spoken in starts a new one when the browser session has no marker yet (the first
 * load of a tab) or when the conversation is older than the server's 24-hour session
 * lifetime, and the old one stays in the store; an empty conversation is never
 * replaced; a sessionStorage that cannot be read means no reset; a pop-out, which has
 * a sessionStorage of its own and must show its opener's chat, never resets; and the
 * new chat's empty state offers the conversation just left, which a click makes the
 * active one again and the first send hides.
 *
 * What stands in: `fetch` (the community config and a one-line chat reply). A reload
 * is a fresh window whose localStorage starts with what the previous one left, and
 * whose sessionStorage is shared only when a test says it is the same browser session.
 *
 * Run with: bun frontend/test-widget-fresh-return.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

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
const API = 'http://localhost/api';
const KEY = 'osa-test-fresh-return';
const MARKER_KEY = `${KEY}-session`;
const GREETING = 'Hello from the fresh-return tests';
const HOUR_MS = 60 * 60 * 1000;
// The server's session lifetime, which the widget mirrors (test_widget_drift.py checks
// the widget's constant against the backend's).
const SESSION_TTL_HOURS = Number(/const SESSION_TTL_HOURS = (\d+);/.exec(SOURCE)[1]);

function serverFetch(sent) {
  const encoder = new TextEncoder();
  return async (url, init = {}) => {
    const u = String(url);
    if (u.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (u.endsWith('/chat')) {
      sent.push(JSON.parse(init.body));
      return new Response(new ReadableStream({
        start(controller) {
          for (const event of [{ event: 'content', content: 'An answer.' }, { event: 'done' }]) {
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
          }
          controller.close();
        },
      }), { headers: { 'content-type': 'text/event-stream' } });
    }
    return new Response(JSON.stringify({
      default_model: 'm', offered_models: [], widget: { title: 'Fresh Tests' }, client_tools: [], runtime: null,
    }), { headers: { 'content-type': 'application/json' } });
  };
}

// A conversation as the store keeps it: the reader said `question`, the assistant answered.
function conversation(id, question, { updatedAt = Date.now() - HOUR_MS, context = null, empty = false } = {}) {
  const messages = [{ role: 'assistant', content: GREETING }];
  if (!empty) {
    messages.push({ role: 'user', content: question });
    messages.push({ role: 'assistant', content: `Answer to ${question}`, citations: [], executions: [] });
  }
  return { id, context, title: empty ? '' : question, sessionId: `session-${id}`, createdAt: updatedAt - 1000, updatedAt, messages };
}

const envelope = (conversations, activeId) => JSON.stringify({ version: 3, activeId, conversations });

// A fresh window, as a page load. `marker` says whether this browser session has loaded
// the widget before (the sessionStorage marker is set); `sessionStorage: 'throws'` makes
// the browser refuse sessionStorage; `popout` presets what a pop-out's opener hands it.
function start({ storage = {}, marker = true, sessionStorage = 'works', popout = false, dataset } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  if (sessionStorage === 'throws') {
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      get() { throw new window.DOMException('The operation is insecure.', 'SecurityError'); },
    });
  } else if (marker) {
    window.sessionStorage.setItem(MARKER_KEY, '1');
  }
  for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, value);
  if (popout) window.__OSA_CHAT_CONFIG__ = { apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING, fullscreen: true };
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const sent = [];
  const fetch = serverFetch(sent);
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  if (dataset !== undefined) api.setDataset(dataset);
  api.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING });
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const q = (s) => container.querySelector(s);
  const hooks = api.__browser;
  return { window, api, hooks, container, q, sent, sessionValue: () => window.sessionStorage.getItem(MARKER_KEY) };
}

const idsOf = (ctx) => ctx.hooks.listConversations().map((c) => c.id).sort();
const activeId = (ctx) => ctx.hooks.getActiveConversation().id;

async function send(ctx, text) {
  ctx.q('.osa-chat-input input').value = text;
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => {
    const messages = ctx.hooks.getMessages();
    return messages.at(-1).role === 'assistant' && messages.at(-1).content === 'An answer.'
      && !ctx.q('.osa-chat-input input').disabled;
  }, `the reply to "${text}" arrives`);
}

console.log('='.repeat(60));
console.log('Widget: a fresh chat on return (#655)');
console.log('='.repeat(60));

console.log('\nthe widget mirrors the server\'s session lifetime');
{
  assertEqual(SESSION_TTL_HOURS, 24, 'it is a day');
}

console.log('\na new browser session starts a new chat, and the old one stays');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation('old', 'Compare ERPs')], 'old') }, marker: false });
  assert(activeId(ctx) !== 'old', 'the active conversation is a new one');
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'holding only the greeting');
  assertEqual(ctx.hooks.getSessionId(), null, 'with no server session');
  assert(idsOf(ctx).includes('old'), 'the old conversation is still listed');
  const old = ctx.hooks.listConversations().find((c) => c.id === 'old');
  assertEqual(old.title, 'Compare ERPs', 'with its title');
  assertEqual(old.messages.length, 3, 'and its messages');
  assertEqual(old.sessionId, 'session-old', 'and its server session');
  assert(old.active === false, 'no longer the active one');
  assert(ctx.sessionValue() !== null, 'the marker is set once the widget has looked');
  const stored = JSON.parse(ctx.window.localStorage.getItem(KEY));
  assertEqual(stored.activeId, activeId(ctx), 'and the store names the new one active');
}

console.log('\nthe next load of the same browser session leaves the new chat alone');
{
  const first = start({ storage: { [KEY]: envelope([conversation('old', 'Compare ERPs')], 'old') }, marker: false });
  const newId = activeId(first);
  await send(first, 'A new question');
  const second = start({ storage: { [KEY]: first.window.localStorage.getItem(KEY) }, marker: true });
  assertEqual(activeId(second), newId, 'a reload keeps the conversation the reader was in');
  assertEqual(second.hooks.getMessages().filter((m) => m.role === 'user').map((m) => m.content), ['A new question'], 'with its messages');
  assert(!second.q('.osa-welcome-back'), 'and shows no welcome');
}

console.log('\nthe marker present and the active conversation under 24 hours old: left alone');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation('recent', 'Recent', { updatedAt: Date.now() - (SESSION_TTL_HOURS - 1) * HOUR_MS })], 'recent') } });
  assertEqual(activeId(ctx), 'recent', 'the same conversation is active');
  assertEqual(ctx.hooks.getMessages().length, 3, 'with its messages');
  assertEqual(idsOf(ctx), ['recent'], 'and no other conversation exists');
  assert(!ctx.q('.osa-welcome-back'), 'no welcome');
}

console.log('\nthe marker present and the active conversation over 24 hours old: a new chat');
{
  const stale = conversation('stale', 'Last week', { updatedAt: Date.now() - (SESSION_TTL_HOURS + 1) * HOUR_MS });
  const ctx = start({ storage: { [KEY]: envelope([stale], 'stale') } });
  assert(activeId(ctx) !== 'stale', 'a new conversation is active');
  assert(idsOf(ctx).includes('stale'), 'the old one is kept');
  assertEqual(ctx.q('.osa-resume-card-text strong').textContent, 'Last week', 'and offered back');
}

console.log('\nan empty active conversation is never replaced');
{
  const stale = conversation('empty', '', { updatedAt: Date.now() - 5 * SESSION_TTL_HOURS * HOUR_MS, empty: true });
  for (const [label, marker] of [['no marker', false], ['a marker', true]]) {
    const ctx = start({ storage: { [KEY]: envelope([stale], 'empty') }, marker });
    assertEqual(activeId(ctx), 'empty', `${label}, and a day old: the empty conversation stays active`);
    assert(!ctx.q('.osa-welcome-back'), `${label}: no welcome`);
  }
  const fresh = start({ marker: false });
  assertEqual(fresh.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'a first visit shows the greeting');
  assert(!fresh.q('.osa-welcome-back'), 'and no welcome');
  assert(fresh.sessionValue() !== null, 'and sets the marker');
}

console.log('\na sessionStorage the browser refuses: no reset');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation('old', 'Compare ERPs')], 'old') }, sessionStorage: 'throws' });
  assertEqual(activeId(ctx), 'old', 'the conversation stays active');
  assert(!ctx.q('.osa-welcome-back'), 'with no welcome');
  assertEqual(ctx.hooks.getMessages().length, 3, 'and its messages');
  const stale = conversation('stale', 'Last week', { updatedAt: Date.now() - (SESSION_TTL_HOURS + 1) * HOUR_MS });
  const staleCtx = start({ storage: { [KEY]: envelope([stale], 'stale') }, sessionStorage: 'throws' });
  assert(activeId(staleCtx) !== 'stale', 'though the 24 hour rule still applies');
}

console.log('\na pop-out never resets');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation('old', 'Compare ERPs')], 'old') }, marker: false, popout: true });
  assertEqual(activeId(ctx), 'old', 'with no marker, the opener\'s conversation is shown');
  assertEqual(ctx.hooks.getMessages().length, 3, 'with its messages');
  assert(!ctx.q('.osa-welcome-back'), 'and no welcome');
  assertEqual(ctx.sessionValue(), null, 'and it sets no marker of its own');
  const stale = conversation('stale', 'Last week', { updatedAt: Date.now() - (SESSION_TTL_HOURS + 1) * HOUR_MS });
  const staleCtx = start({ storage: { [KEY]: envelope([stale], 'stale') }, popout: true });
  assertEqual(activeId(staleCtx), 'stale', 'nor does a conversation over a day old');
}

console.log('\nthe empty state names the dataset and offers the chat just left');
{
  const old = conversation('old', 'Compare ERPs for famous vs scrambled faces', { context: { type: 'dataset', id: 'on002718' } });
  const ctx = start({ storage: { [KEY]: envelope([old], 'old') }, marker: false, dataset: { id: 'nm000241' } });
  const welcome = ctx.q('.osa-welcome-back');
  assert(!!welcome, 'there is a welcome');
  assertEqual(ctx.q('.osa-welcome-back-text').textContent.trim(), 'Welcome back. This is a new chat about nm000241.', 'in the board\'s words, naming the page\'s dataset');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: 'nm000241' }, 'the new chat takes the page\'s context');
  const card = ctx.q('.osa-resume-card');
  assert(card && card.tagName === 'BUTTON', 'the card is a button');
  assertEqual(card.textContent.replace(/\s+/g, ' ').trim(), 'Pick up where you left off: Compare ERPs for famous vs scrambled faces', 'it shows the previous conversation\'s title');
  assert(ctx.q('.osa-chat-messages').contains(card), 'inside the messages, after the greeting');
  const greeting = ctx.q('.osa-chat-messages .osa-message');
  assert(greeting.compareDocumentPosition(card) & 4, 'the greeting comes first');

  const newId = activeId(ctx);
  card.click();
  assertEqual(activeId(ctx), 'old', 'clicking it makes the previous conversation active');
  assertEqual(ctx.hooks.getSessionId(), 'session-old', 'with its server session');
  assert(ctx.q('.osa-chat-messages').textContent.includes('Answer to Compare ERPs'), 'and its messages on screen');
  assert(!ctx.q('.osa-welcome-back'), 'the card is gone');
  assert(!idsOf(ctx).includes(newId), 'the empty chat it replaced is dropped');
}

console.log('\nthe welcome without a dataset says no more than it knows');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation('old', 'Compare ERPs')], 'old') }, marker: false });
  assertEqual(ctx.q('.osa-welcome-back-text').textContent.trim(), 'Welcome back. This is a new chat.', 'a new chat, about nothing in particular');
  assertEqual(ctx.hooks.getActiveConversation().context, null, 'with no context');
}

console.log('\nthe first send hides the card, and a reload does not bring it back');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation('old', 'Compare ERPs')], 'old') }, marker: false });
  assert(!!ctx.q('.osa-resume-card'), 'the card is there before the first message');
  await send(ctx, 'Something else');
  assert(!ctx.q('.osa-resume-card'), 'gone after the first send');
  assert(!ctx.q('.osa-welcome-back'), 'with the welcome');
  assert(idsOf(ctx).includes('old'), 'while the old conversation stays saved');
  const reload = start({ storage: { [KEY]: ctx.window.localStorage.getItem(KEY) }, marker: true });
  assert(!reload.q('.osa-resume-card'), 'a reload shows no card');
  const alone = start({ storage: { [KEY]: envelope([conversation('old', 'Compare ERPs')], 'old') }, marker: false });
  alone.hooks.renderMessages(alone.container);
  assert(!!alone.q('.osa-resume-card'), 'a redraw of the empty chat keeps the card');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
