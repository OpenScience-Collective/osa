/**
 * A conversation the server forgot while the page stays open (#698), run against the real
 * widget source in happy-dom windows, the way test-widget-reseed.js runs reseeding.
 *
 * What it holds the widget to: once the server has confirmed the session in this page
 * load, a request carries no turns and says `expect_session: true`. When the server (which
 * restarted, or let the session go) answers the 409 `session_unknown` of the shared
 * fixture tests/fixtures/session_unknown_409.json, the widget sends the same request once
 * more, without `expect_session` and with the turns, and the reader sees one answer and no
 * error. The refused request spent its Turnstile token, so the resend carries a fresh one.
 * When the resend cannot be made, or is refused too, the reader is told so in plain
 * words, the question goes back in the input, and the next send carries the turns. A 409
 * with another code is an ordinary error and is not sent again.
 *
 * The pure part (`// BEGIN session-lost` to `// END session-lost` in the widget) is also
 * evaluated on its own, since the widget cannot import modules.
 *
 * What stands in: `fetch`, a server that keeps a session Map and speaks the contract the
 * backend's own test (tests/test_api/test_expect_session.py) holds the real server to,
 * answering the fixture's body where that test expects a 409; and Cloudflare's Turnstile
 * script, whose tokens the server accepts once each, as the worker verifies them.
 *
 * Run with: bun frontend/test-widget-session-lost.js
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
const SESSION_UNKNOWN_409 = JSON.parse(
  readFileSync(new URL('../tests/fixtures/session_unknown_409.json', import.meta.url), 'utf8')
);
const API = 'http://localhost/api';
const KEY = 'osa-test-session-lost';
const GREETING = 'Hello from the session-lost tests';
const HOUR_MS = 60 * 60 * 1000;
const LOST_TEXT = 'Earlier messages in this chat were not kept by the server. Your message was not sent; send it again.';
const JSON_HEADERS = { 'content-type': 'application/json' };

// The pure block, evaluated alone: it may use nothing of the rest of the widget.
const BLOCK = /\/\/ BEGIN session-lost[^\n]*\n(?:\s*\/\/[^\n]*\n)*([\s\S]*?)\/\/ END session-lost/.exec(SOURCE);
assert(!!BLOCK, 'the widget has a delimited session-lost block');
const pure = new Function(`${BLOCK[1]}
  return { SESSION_UNKNOWN_CODE, expectSessionFields, isSessionUnknown };`)();

// A server with a memory. It holds the sessions it was told of; `forget()` is a restart. It
// answers like the backend: a `session` event first, then the reply; and, for a request that
// expects a session it does not hold and carries no turns, the 409 of the shared fixture.
// `behavior`, which a test can change between messages, can make it refuse even a request
// that carries turns (`alwaysRefuse`, so the resend fails too) or change the refusal's code
// (`refusalCode`). A Turnstile token is accepted once.
function makeServer(behavior = {}) {
  const sessions = new Map();
  const spentTokens = new Set();
  const sent = [];
  const encoder = new TextEncoder();
  let requests = 0;
  const fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (!u.endsWith('/chat')) {
      return new Response(JSON.stringify({
        default_model: 'm', offered_models: [], widget: { title: 'Session Lost Tests' }, client_tools: [], runtime: null,
      }), { headers: JSON_HEADERS });
    }
    const body = JSON.parse(init.body);
    sent.push(body);
    requests += 1;
    const token = body.cf_turnstile_response;
    if (token) {
      if (spentTokens.has(token)) {
        return new Response(JSON.stringify({ error: 'Bot verification failed', details: 'timeout-or-duplicate' }), { status: 403, headers: JSON_HEADERS });
      }
      spentTokens.add(token);
    }
    const held = body.session_id && sessions.has(body.session_id);
    const refuse = behavior.alwaysRefuse ||(body.expect_session && !(body.prior_turns && body.prior_turns.length) && !held);
    if (refuse) {
      const refusal = behavior.refusalCode ? { ...SESSION_UNKNOWN_409, code: behavior.refusalCode } : SESSION_UNKNOWN_409;
      return new Response(JSON.stringify(refusal), { status: 409, headers: JSON_HEADERS });
    }
    const id = body.session_id || `server-${requests}`;
    sessions.set(id, true);
    const events = [
      { event: 'session', session_id: id },
      { event: 'content', content: `Answer ${requests}.` },
      { event: 'done', session_id: id, mode: 'quick' },
    ];
    return new Response(new ReadableStream({
      start(controller) {
        for (const event of events) controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        controller.close();
      },
    }), { headers: { 'content-type': 'text/event-stream' } });
  };
  return { fetch, sent, sessions, behavior, forget: () => sessions.clear() };
}

// Cloudflare's script: `render` hands out a token, `reset` hands out the next one a moment
// later. While `silent` is set, a reset hands out none, as a challenge that does not
// complete in time.
function makeTurnstile() {
  let count = 0;
  let callback = null;
  const api = {
    silent: false,
    resets: 0,
    render(_el, options) {
      callback = options.callback;
      count += 1;
      callback(`token-${count}`);
      return 'widget-1';
    },
    reset() {
      api.resets += 1;
      if (api.silent) return;
      count += 1;
      const token = `token-${count}`;
      setTimeout(() => callback(token), 20);
    },
  };
  return api;
}

// A conversation as the store keeps it. `exchanges` are [question, answer, mode?].
function conversation(id, exchanges, { updatedAt = Date.now() - HOUR_MS } = {}) {
  const messages = [{ role: 'assistant', content: GREETING }];
  for (const [question, answer, mode] of exchanges) {
    messages.push({ role: 'user', content: question });
    if (answer !== null) {
      messages.push({ role: 'assistant', content: answer, citations: [], executions: [], ...(mode ? { mode } : {}) });
    }
  }
  return { id, context: null, title: exchanges.length ? exchanges[0][0] : '', sessionId: `session-${id}`, createdAt: updatedAt - 1000, updatedAt, messages };
}

const envelope = (conversations, activeId) => JSON.stringify({ version: 3, activeId, conversations });

// A fresh window, as a page load of a browser session that has loaded the widget before.
function start({ storage = {}, server = makeServer(), turnstile = null } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  window.sessionStorage.setItem(`${KEY}-session`, '1');
  for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, value);
  if (turnstile) window.turnstile = turnstile;
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  window.fetch = server.fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, server.fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  api.setConfig({
    apiEndpoint: API,
    communityId: 'nemar',
    storageKey: KEY,
    initialMessage: GREETING,
    ...(turnstile ? { turnstileSiteKey: 'site-key' } : {}),
  });
  const sends = [];
  api.on('send', (payload) => sends.push(payload.text));
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const q = (s) => container.querySelector(s);
  return { window, api, hooks: api.__browser, container, q, sent: server.sent, server, sends, turnstile };
}

// Sends `text` and waits until the widget is idle again, whatever the outcome.
async function sendAndSettle(ctx, text) {
  const before = ctx.sent.length;
  ctx.q('.osa-chat-input input').value = text;
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => ctx.sent.length > before && !ctx.q('.osa-chat-input input').disabled, `the request for "${text}" settles`);
  return ctx.sent.slice(before);
}

const errorText = (ctx) => {
  const el = ctx.q('.osa-error');
  return el && el.style.display !== 'none' ? el.textContent : '';
};
const bubbles = (ctx) => ctx.hooks.getMessages().map((m) => `${m.role}: ${m.content}`);

console.log('='.repeat(60));
console.log('Widget: a conversation the server forgot, noticed while the page stays open (#698)');
console.log('='.repeat(60));

console.log('\nthe pure part');
{
  assertEqual(pure.SESSION_UNKNOWN_CODE, SESSION_UNKNOWN_409.code, 'the code is the one the server sends');
  assertEqual(pure.expectSessionFields({ sessionId: 's', reseed: null }), { expect_session: true }, 'a session and no turns: expected');
  assertEqual(pure.expectSessionFields({ sessionId: 's', reseed: { prior_turns: [] } }), null, 'turns carried: nothing to expect');
  assertEqual(pure.expectSessionFields({ sessionId: null, reseed: null }), null, 'no session id: nothing to expect');
  assertEqual(pure.expectSessionFields({ sessionId: '', reseed: null }), null, 'an empty id too');
  assertEqual(pure.expectSessionFields({ sessionId: undefined, reseed: null }), null, 'an absent one too');
  assert(pure.isSessionUnknown(409, SESSION_UNKNOWN_409), 'the fixture body with its status is the refusal');
  assert(!pure.isSessionUnknown(500, SESSION_UNKNOWN_409), 'another status is not');
  assert(!pure.isSessionUnknown(409, { ...SESSION_UNKNOWN_409, code: 'busy' }), 'another code is not');
  assert(!pure.isSessionUnknown(409, { detail: 'a conflict' }), 'no code is not');
  assert(!pure.isSessionUnknown(409, null) && !pure.isSessionUnknown(409, 'session_unknown') && !pure.isSessionUnknown(409, undefined), 'nor is a body that is not an object');
}

console.log('\na session the server forgot between two messages is sent again, once, with the turns');
{
  const ctx = start();
  const [first] = await sendAndSettle(ctx, 'find datasets with face stimuli');
  assert(!('session_id' in first) && !('expect_session' in first) && !('prior_turns' in first), 'the first message of a new chat carries none of it');
  assert(ctx.hooks.getServerConfirmedAt() !== null, 'the server confirmed the session');
  const sessionId = ctx.hooks.getSessionId();

  ctx.server.forget();
  const requests = await sendAndSettle(ctx, 'and how many participants');
  assertEqual(requests.length, 2, 'message 2 made two requests: the refused one and its resend');
  const [attempt, resend] = requests;
  assertEqual(attempt.session_id, sessionId, 'the first names the session');
  assert(attempt.expect_session === true, 'and expects it');
  assert(!('prior_turns' in attempt), 'carrying no turns');
  assert(!('expect_session' in resend), 'the resend does not expect it');
  assertEqual(resend.prior_turns, [
    { role: 'user', text: 'find datasets with face stimuli' },
    { role: 'assistant', text: 'Answer 1.' },
  ], 'it carries the first exchange');
  assertEqual(resend.mode, 'quick', 'and its tier');
  assertEqual(resend.message, attempt.message, 'it is the same message');
  assertEqual(resend.session_id, attempt.session_id, 'for the same session');
  assertEqual(errorText(ctx), '', 'the reader sees no error');
  assertEqual(bubbles(ctx).slice(-2), ['user: and how many participants', `assistant: Answer ${ctx.sent.length}.`], 'the reply is in the conversation, after the question');
  assertEqual(ctx.hooks.getMessages().filter((m) => m.content === 'and how many participants').length, 1, 'the question is in it once');
  assert(ctx.hooks.getServerConfirmedAt() !== null, 'the server has confirmed the session again');
  assertEqual(ctx.sends, ['find datasets with face stimuli', 'and how many participants'], 'the host heard one send per message');

  const [third] = await sendAndSettle(ctx, 'thanks');
  assert(third.expect_session === true, 'message 3 expects the session again');
  assert(!('prior_turns' in third), 'and carries no turns');
  assertEqual(ctx.sent.length, 4, 'with no resend, as the server holds it');
}

console.log('\nthe first request after a page load carries the turns, and does not expect the session');
{
  const stored = conversation('c1', [['find datasets with face stimuli', 'The closest match is on002718.', 'quick']]);
  const ctx = start({ storage: { [KEY]: envelope([stored], 'c1') } });
  const [body] = await sendAndSettle(ctx, 'which dataset');
  assertEqual(body.session_id, 'session-c1', 'it names the stored session');
  assert('prior_turns' in body, 'it carries the turns');
  assert(!('expect_session' in body), 'and does not expect the session');
  assertEqual(ctx.sent.length, 1, 'one request');
  const [next] = await sendAndSettle(ctx, 'and then');
  assert(next.expect_session === true && !('prior_turns' in next), 'the next one has the server confirmed, so it expects');
}

console.log('\na new chat carries neither');
{
  const ctx = start();
  await sendAndSettle(ctx, 'first question');
  assert(ctx.q('.osa-reset-btn') !== null, 'the chat can be reset');
  ctx.q('.osa-reset-btn').click();
  await waitUntil(() => ctx.hooks.getSessionId() === null, 'the chat is new');
  const [body] = await sendAndSettle(ctx, 'a question in a new chat');
  assert(!('expect_session' in body) && !('prior_turns' in body) && !('session_id' in body), 'no session, no turns, no expectation');
}

console.log('\na chat with no assistant reply is sent again plain');
{
  const stored = conversation('c1', [['a question that was never answered', null]]);
  const ctx = start({ storage: { [KEY]: envelope([stored], 'c1') } });
  const requests = await sendAndSettle(ctx, 'trying again');
  assertEqual(requests.length, 2, 'refused, then sent again');
  assert(requests[0].expect_session === true && !('prior_turns' in requests[0]), 'the first expected the session and had no turns to carry');
  assert(!('expect_session' in requests[1]) && !('prior_turns' in requests[1]), 'the resend is plain: there is no reply to carry');
  assertEqual(errorText(ctx), '', 'the reader sees no error');
  assertEqual(bubbles(ctx).at(-1), `assistant: Answer ${ctx.sent.length}.`, 'and the answer');
}

console.log('\nthe refused request spent its Turnstile token, so the resend carries a fresh one');
{
  const turnstile = makeTurnstile();
  const ctx = start({ turnstile });
  await sendAndSettle(ctx, 'find datasets with face stimuli');
  assertEqual(ctx.sent[0].cf_turnstile_response, 'token-1', 'the first request carried the widget\'s token');
  await new Promise((resolve) => setTimeout(resolve, 100));
  ctx.server.forget();
  const requests = await sendAndSettle(ctx, 'and how many participants');
  assertEqual(requests.length, 2, 'refused, then sent again');
  assert(requests[0].cf_turnstile_response && requests[1].cf_turnstile_response, 'both carry a token');
  assert(requests[0].cf_turnstile_response !== requests[1].cf_turnstile_response, 'not the same one');
  assertEqual(errorText(ctx), '', 'the server took the second: no error');
  assertEqual(bubbles(ctx).at(-1), `assistant: Answer ${ctx.sent.length}.`, 'and the answer is shown');
}

console.log('\nno fresh token in time: the plain message, the question back, the next send carries the turns');
{
  const turnstile = makeTurnstile();
  const ctx = start({ turnstile });
  ctx.api.__turnstileWait.set(200);
  await sendAndSettle(ctx, 'find datasets with face stimuli');
  await new Promise((resolve) => setTimeout(resolve, 100));
  turnstile.silent = true;
  ctx.server.forget();
  const started = Date.now();
  const requests = await sendAndSettle(ctx, 'and how many participants');
  assertEqual(requests.length, 1, 'only the refused request was made');
  assert(requests[0].cf_turnstile_response && requests[0].expect_session === true, 'which carried a token and expected the session');
  assert(Date.now() - started < 5000, 'the wait for a token is bounded');
  assertEqual(errorText(ctx).includes(LOST_TEXT), true, 'the reader is told in plain words');
  assertEqual(ctx.q('.osa-chat-input input').value, 'and how many participants', 'the question is back in the input');
  assertEqual(ctx.hooks.getServerConfirmedAt(), null, 'the server confirmed nothing');
  assertEqual(bubbles(ctx).filter((b) => b.includes('and how many participants')).length, 0, 'and the question is not in the conversation');
  turnstile.silent = false;
  await new Promise((resolve) => setTimeout(resolve, 100));
  const [next] = await sendAndSettle(ctx, 'and how many participants');
  assert('prior_turns' in next && !('expect_session' in next), 'the next send carries the turns, as for a page just loaded');
  assertEqual(errorText(ctx), '', 'and is answered');
}

console.log('\na second session_unknown shows the plain text and puts the question back');
{
  const server = makeServer();
  const stored = conversation('c1', [['find datasets with face stimuli', 'The closest match is on002718.', 'quick']]);
  const ctx = start({ server, storage: { [KEY]: envelope([stored], 'c1') } });
  await sendAndSettle(ctx, 'ask once');
  server.behavior.alwaysRefuse = true;
  const requests = await sendAndSettle(ctx, 'and how many participants');
  assertEqual(requests.length, 2, 'one request and one resend, no more');
  assert(!('expect_session' in requests[1]) && 'prior_turns' in requests[1], 'the resend carried the turns');
  assert(errorText(ctx).includes(LOST_TEXT), `the reader reads "${LOST_TEXT}"`);
  assertEqual(ctx.q('.osa-chat-input input').value, 'and how many participants', 'the question is back in the input');
  assertEqual(ctx.hooks.getServerConfirmedAt(), null, 'the server has confirmed nothing');
  assertEqual(ctx.hooks.getMessages().filter((m) => m.role === 'user').length, 2, 'only the earlier questions are in the conversation');
  await new Promise((resolve) => setTimeout(resolve, 100));
  assertEqual(ctx.sent.length, 3, 'and nothing is sent in the background');
}

console.log('\na 409 with another code is an ordinary error, with no resend');
{
  const server = makeServer({ refusalCode: 'something_else' });
  const stored = conversation('c1', [['find datasets with face stimuli', 'The closest match is on002718.', 'quick']]);
  const ctx = start({ server, storage: { [KEY]: envelope([stored], 'c1') } });
  const [first] = await sendAndSettle(ctx, 'ask once');
  assert(!('expect_session' in first), 'the first request carried the turns, so it expected nothing');
  ctx.server.forget();
  const requests = await sendAndSettle(ctx, 'ask again');
  assertEqual(requests.length, 1, 'one request');
  assert(requests[0].expect_session === true, 'which expected the session');
  assertEqual(errorText(ctx), SESSION_UNKNOWN_409.detail, 'the reader sees the server\'s own detail, as for any error');
  assert(!errorText(ctx).includes(LOST_TEXT), 'not the session-lost text');
  assertEqual(ctx.q('.osa-chat-input input').value, 'ask again', 'the question is back in the input');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
