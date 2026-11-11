/**
 * Reseeding a conversation the server forgot (E1.6, #659), run against the real widget
 * source in happy-dom windows, the way test-widget-fresh-return.js runs the fresh chat.
 *
 * What it holds the widget to: a request carries `prior_turns` (the conversation so far,
 * the user's and the assistant's text only, the newest 12, each cut from the middle at
 * 2,000 characters) and `mode` (the tier of the last reply that said one) on the
 * requests where the server might not hold the session, and carries neither where it
 * does. It might not when the page has just loaded (a restored conversation), when a
 * conversation was opened from History or the resume card, when the server has not
 * answered the last request, and when the conversation has been idle for longer than the
 * server keeps a session (SESSION_TTL_MS). It does not when the chat is new and empty,
 * when the server confirmed the session in this page load, or when the conversation has
 * no assistant reply to carry.
 *
 * The pure part (`// BEGIN reseed` to `// END reseed` in the widget) is also evaluated on
 * its own, since the widget cannot import modules.
 *
 * What stands in: `fetch` (the community config and a one-line chat reply that, like the
 * server, begins with a `session` event). The assertions read the request bodies the
 * widget sent. Time is turned by hand where a test needs a day to pass.
 *
 * Run with: bun frontend/test-widget-reseed.js
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
const KEY = 'osa-test-reseed';
const GREETING = 'Hello from the reseed tests';
const HOUR_MS = 60 * 60 * 1000;
const SESSION_TTL_HOURS = Number(/const SESSION_TTL_HOURS = (\d+);/.exec(SOURCE)[1]);
const SESSION_TTL_MS = SESSION_TTL_HOURS * HOUR_MS;

// The pure block, evaluated alone: it may use nothing of the rest of the widget.
const BLOCK = /\/\/ BEGIN reseed[^\n]*\n(?:\s*\/\/[^\n]*\n)*([\s\S]*?)\/\/ END reseed/.exec(SOURCE);
assert(!!BLOCK, 'the widget has a delimited reseed block');
const pure = new Function(`${BLOCK[1]}
  return { RESEED_MAX_TURNS, RESEED_TURN_MAX_CHARS, clipTurnMiddle, selectReseedTurns, lastReplyMode, reseedFields };`)();

// A server that answers like the real one: a `session` event first, then the reply. `plan`
// says how it answers the Nth request: 'ok' (default) or 'fail' (a 500 before any event).
function serverFetch(sent, plan = []) {
  const encoder = new TextEncoder();
  let requests = 0;
  return async (url, init = {}) => {
    const u = String(url);
    if (u.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (u.endsWith('/chat')) {
      const body = JSON.parse(init.body);
      sent.push(body);
      requests += 1;
      if (plan[requests - 1] === 'fail') {
        return new Response(JSON.stringify({ detail: 'down' }), { status: 500, headers: { 'content-type': 'application/json' } });
      }
      const events = [
        { event: 'session', session_id: body.session_id || `server-${requests}` },
        { event: 'content', content: 'An answer.' },
        { event: 'done', session_id: body.session_id || `server-${requests}`, mode: 'quick' },
      ];
      return new Response(new ReadableStream({
        start(controller) {
          for (const event of events) controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
          controller.close();
        },
      }), { headers: { 'content-type': 'text/event-stream' } });
    }
    return new Response(JSON.stringify({
      default_model: 'm', offered_models: [], widget: { title: 'Reseed Tests' }, client_tools: [], runtime: null,
    }), { headers: { 'content-type': 'application/json' } });
  };
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
function start({ storage = {}, plan = [] } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  window.sessionStorage.setItem(`${KEY}-session`, '1');
  for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, value);
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const sent = [];
  const fetch = serverFetch(sent, plan);
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  api.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING });
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const q = (s) => container.querySelector(s);
  return { window, api, hooks: api.__browser, container, q, sent };
}

async function send(ctx, text) {
  const before = ctx.sent.length;
  ctx.q('.osa-chat-input input').value = text;
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => {
    if (ctx.sent.length === before) return false;
    const messages = ctx.hooks.getMessages();
    return !ctx.q('.osa-chat-input input').disabled && messages.at(-1).role === 'assistant';
  }, `the request for "${text}" is answered`);
  return ctx.sent.at(-1);
}

// Time is turned by hand: the widget reads `Date.now()`.
async function afterHours(hours, fn) {
  const real = Date.now;
  Date.now = () => real() + hours * HOUR_MS;
  try {
    return await fn();
  } finally {
    Date.now = real;
  }
}

console.log('='.repeat(60));
console.log('Widget: reseed a conversation the server forgot (E1.6)');
console.log('='.repeat(60));

console.log('\nthe widget mirrors the server\'s lifetime, and the bounds are the brief\'s');
{
  assertEqual(SESSION_TTL_HOURS, 24, 'a session lasts a day');
  assertEqual(pure.RESEED_MAX_TURNS, 12, 'at most 12 turns');
  assertEqual(pure.RESEED_TURN_MAX_CHARS, 2000, 'of at most 2,000 characters');
}

console.log('\nthe first request of a restored conversation carries it');
{
  const stored = conversation('c1', [
    ['find datasets with face stimuli', 'The closest match is on002718.', 'quick'],
    ['plot the ERP', 'Here is the plot.', 'analysis'],
  ]);
  const ctx = start({ storage: { [KEY]: envelope([stored], 'c1') } });
  assertEqual(ctx.hooks.getServerConfirmedAt(), null, 'the server has confirmed nothing in this page load');
  const body = await send(ctx, 'which dataset were we discussing');
  assertEqual(body.session_id, 'session-c1', 'it names the stored session');
  assertEqual(body.prior_turns, [
    { role: 'user', text: 'find datasets with face stimuli' },
    { role: 'assistant', text: 'The closest match is on002718.' },
    { role: 'user', text: 'plot the ERP' },
    { role: 'assistant', text: 'Here is the plot.' },
  ], 'with the turns before the message, and not the greeting');
  assertEqual(body.message, 'which dataset were we discussing', 'the message itself is not among them');
  assertEqual(body.mode, 'analysis', 'and the tier of the last reply that said one');
  assert(ctx.hooks.getServerConfirmedAt() !== null, 'the server confirmed the session');

  const next = await send(ctx, 'and the participants?');
  assert(!('prior_turns' in next), 'a request after the server confirmed the session carries none');
  assert(!('mode' in next), 'and no mode');
  const third = await send(ctx, 'thanks');
  assert(!('prior_turns' in third), 'nor does any later one');
}

console.log('\na reload asks again, since a page cannot know what the server kept');
{
  const first = start({ storage: { [KEY]: envelope([conversation('c1', [['hi', 'hello']])], 'c1') } });
  await send(first, 'one more');
  const second = start({ storage: { [KEY]: first.window.localStorage.getItem(KEY) } });
  const body = await send(second, 'and another');
  assertEqual(body.prior_turns.map((t) => t.text), ['hi', 'hello', 'one more', 'An answer.'], 'the reload carries everything so far');
}

console.log('\na new, empty chat carries nothing');
{
  const ctx = start();
  const body = await send(ctx, 'a first question');
  assert(!('prior_turns' in body), 'no prior_turns');
  assert(!('mode' in body), 'no mode');
  assert(!('session_id' in body), 'and no session either');
}

console.log('\na conversation with no assistant reply carries nothing');
{
  const stored = conversation('c1', [['a question that was never answered', null]]);
  const ctx = start({ storage: { [KEY]: envelope([stored], 'c1') } });
  const body = await send(ctx, 'trying again');
  assert(!('prior_turns' in body), 'there is no reply to carry, so no prior_turns');
  assert(!('mode' in body), 'and no mode');
}

console.log('\na conversation opened from History carries itself, once the server is known to hold another');
{
  const a = conversation('a', [['about A', 'answer A', 'quick']], { updatedAt: Date.now() - 2 * HOUR_MS });
  const b = conversation('b', [['about B', 'answer B', 'analysis']], { updatedAt: Date.now() - 3 * HOUR_MS });
  const ctx = start({ storage: { [KEY]: envelope([a, b], 'a') } });
  await send(ctx, 'follow up on A');
  assert(ctx.hooks.getServerConfirmedAt() !== null, 'the server holds the session of A');
  assert(ctx.hooks.activateConversation('b'), 'B is opened');
  assertEqual(ctx.hooks.getServerConfirmedAt(), null, 'what the server holds says nothing about B');
  const body = await send(ctx, 'follow up on B');
  assertEqual(body.session_id, 'session-b', 'its own session');
  assertEqual(body.prior_turns, [
    { role: 'user', text: 'about B' },
    { role: 'assistant', text: 'answer B' },
  ], 'with its own turns');
  assertEqual(body.mode, 'analysis', 'and its own tier');
}

console.log('\na chat idle for longer than the server keeps a session carries itself');
{
  const stored = { [KEY]: envelope([conversation('c1', [['hi', 'hello']])], 'c1') };
  const early = start({ storage: stored });
  await send(early, 'first');
  const within = await afterHours(SESSION_TTL_HOURS - 1, () => send(early, 'still the same day'));
  assert(!('prior_turns' in within), 'an hour short of the lifetime: nothing');
  const ctx = start({ storage: stored });
  await send(ctx, 'first');
  const after = await afterHours(SESSION_TTL_HOURS + 1, () => send(ctx, 'the next day'));
  assertEqual(after.prior_turns.at(0), { role: 'user', text: 'hi' }, 'an hour past it: the whole chat');
  assertEqual(after.prior_turns.at(-1).role, 'assistant', 'ending on the last reply');
  const again = await send(ctx, 'and now');
  assert(!('prior_turns' in again), 'and the server has confirmed it again');
}

console.log('\na request the server never answered leaves the chat unconfirmed');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation('c1', [['hi', 'hello']])], 'c1') }, plan: ['fail'] });
  ctx.q('.osa-chat-input input').value = 'this one fails';
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => ctx.sent.length === 1 && !ctx.q('.osa-chat-input input').disabled, 'the failure is shown');
  assert('prior_turns' in ctx.sent[0], 'the failed request carried the chat');
  assertEqual(ctx.hooks.getServerConfirmedAt(), null, 'and the server did not confirm');
  const retry = await send(ctx, 'this one works');
  assert('prior_turns' in retry, 'so the next one carries it again');
}

console.log('\nthe newest 12 turns, each cut from the middle');
{
  const exchanges = [];
  for (let i = 1; i <= 10; i += 1) exchanges.push([`question ${i}`, `answer ${i}`, 'quick']);
  const ctx = start({ storage: { [KEY]: envelope([conversation('c1', exchanges)], 'c1') } });
  const body = await send(ctx, 'last');
  assertEqual(body.prior_turns.length, 12, 'twelve of the twenty');
  assertEqual(body.prior_turns[0], { role: 'user', text: 'question 5' }, 'the newest ones');
  assertEqual(body.prior_turns.at(-1), { role: 'assistant', text: 'answer 10' }, 'ending on the last reply');
}
{
  const long = `START ${'x'.repeat(5000)} END`;
  const clipped = pure.clipTurnMiddle(long);
  assertEqual(clipped.length, 2000, 'a long turn is cut to the limit');
  assert(clipped.startsWith('START ') && clipped.endsWith(' END'), 'keeping its start and its end');
  assert(clipped.includes('…'), 'with an ellipsis between');
  assertEqual(clipped.indexOf('…'), 1000, 'in the middle');
  assertEqual(pure.clipTurnMiddle('short'), 'short', 'a short one is left alone');
  const exact = 'y'.repeat(2000);
  assertEqual(pure.clipTurnMiddle(exact), exact, 'one at the limit is left alone');
  const emoji = '\u{1F9E0}'.repeat(2000);
  const cut = pure.clipTurnMiddle(emoji);
  assert(cut.length <= 2000 && !/[\uD800-\uDBFF]$/.test(cut.slice(0, cut.indexOf('…'))) && !/^[\uDC00-\uDFFF]/.test(cut.slice(cut.indexOf('…') + 1)), 'a cut never splits a surrogate pair');
}

console.log('\nonly text is carried');
{
  const stored = conversation('c1', [['run it', 'Ran it.', 'analysis']]);
  stored.messages[2].executions = [{ callId: 'c', tool: 'execute_code', code: 'SECRET_CODE()', stdout: 'SECRET_OUTPUT', stderr: '', traceback: '', images: ['data:image/png;base64,AAAA'], figureCount: 1, local: false }];
  stored.messages[2].usage = { input_tokens: 1 };
  stored.messages[2].handover = { stage: 'started' };
  stored.messages[1].dataset = 'on002718';
  const ctx = start({ storage: { [KEY]: envelope([stored], 'c1') } });
  const body = await send(ctx, 'again');
  const wire = JSON.stringify(body);
  assert(!wire.includes('SECRET_CODE') && !wire.includes('SECRET_OUTPUT') && !wire.includes('image/png'), 'no code, output or image');
  assertEqual(Object.keys(body.prior_turns[0]).sort(), ['role', 'text'], 'a turn is its role and its text');
  assertEqual(body.prior_turns.map((t) => t.role), ['user', 'assistant'], 'one of each');
}

console.log('\nthe pure part');
{
  assertEqual(pure.selectReseedTurns([]), [], 'nothing in, nothing out');
  assertEqual(pure.selectReseedTurns(null), [], 'a missing list too');
  assertEqual(pure.selectReseedTurns([
    { role: 'assistant', content: 'greeting' },
    { role: 'assistant', content: 'second greeting line' },
    { role: 'user', content: '  hi  ' },
    { role: 'assistant', content: '   ' },
    { role: 'tool', content: 'result' },
    { role: 'assistant', content: 'hello' },
  ]), [{ role: 'user', text: 'hi' }, { role: 'assistant', text: 'hello' }], 'the greeting, blanks and other roles are left out, text is trimmed');
  assertEqual(pure.lastReplyMode([
    { role: 'assistant', content: 'a', mode: 'analysis' },
    { role: 'user', content: 'b' },
    { role: 'assistant', content: 'c' },
  ]), 'analysis', 'the last reply that says a mode (a stopped reply says none)');
  assertEqual(pure.lastReplyMode([{ role: 'assistant', content: 'a', mode: 'turbo' }]), null, 'a mode the server does not know is not sent');
  const list = [{ role: 'user', content: 'q' }, { role: 'assistant', content: 'a' }];
  const now = 10 * SESSION_TTL_MS;
  assertEqual(pure.reseedFields({ list, confirmedAt: null, now, ttlMs: SESSION_TTL_MS }), { prior_turns: [{ role: 'user', text: 'q' }, { role: 'assistant', text: 'a' }] }, 'unconfirmed: sent, and no mode when no reply says one');
  assertEqual(pure.reseedFields({ list, confirmedAt: now - SESSION_TTL_MS, now, ttlMs: SESSION_TTL_MS }), null, 'confirmed exactly one lifetime ago: still held');
  assert(pure.reseedFields({ list, confirmedAt: now - SESSION_TTL_MS - 1, now, ttlMs: SESSION_TTL_MS }) !== null, 'a millisecond longer: sent');
  assertEqual(pure.reseedFields({ list: [{ role: 'user', content: 'q' }], confirmedAt: null, now, ttlMs: SESSION_TTL_MS }), null, 'no assistant reply: nothing');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
