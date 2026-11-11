/**
 * The context switch (#656), run against the real widget source in happy-dom windows,
 * the way test-widget-history-store.js runs the conversation store.
 *
 * What it holds the widget to: a chat has a context, the dataset the page names or
 * the search page (a path the community lists in `search_paths`); when the page's
 * context differs from the chat's and the chat holds the reader's questions, the old
 * chat stays on screen with a notice above the input, and the next message starts a
 * new chat while the old one stays saved; "Continue the <old> chat instead" keeps the
 * chat, shows a divider, and the next request carries `moved_from`, which a send the
 * server never answered does not use up; an empty chat switches silently; a page that
 * names neither keeps the context; a pop-out never asks. The dataset on screen rides
 * every request as `page_context.dataset_id`, whether or not the reader shares the
 * page's URL.
 *
 * What stands in: `fetch` (the community config, and a one-line chat reply that can be
 * told to fail). The page's address is happy-dom's own, moved with setURL.
 *
 * Run with: bun frontend/test-widget-context-switch.js
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
const KEY = 'osa-test-context-switch';
const GREETING = 'Hello from the context-switch tests';
const A = 'on002718';
const B = 'nm000241';
const SEARCH_PATHS = ['/discover'];
const NOTICE = '.osa-context-notice';

// The community config, answered at once, and a chat reply of one line. `sent` records
// every chat request's body; `failNext(n)` makes the next n chat requests fail with a
// 500, as a backend that is down does.
function serverFetch(widget) {
  const sent = [];
  const encoder = new TextEncoder();
  let failures = 0;
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
    if (u.endsWith('/chat/resume')) {
      sent.push(JSON.parse(init.body));
      return answer();
    }
    if (u.endsWith('/chat')) {
      sent.push(JSON.parse(init.body));
      if (gate) await gate.promise;
      if (failures > 0) {
        failures -= 1;
        return new Response(JSON.stringify({ detail: 'The backend is down' }), { status: 500, headers: { 'content-type': 'application/json' } });
      }
      return answer();
    }
    return new Response(JSON.stringify({
      default_model: 'm', offered_models: [], widget: { title: 'Switch Tests', ...widget }, client_tools: [], runtime: null,
    }), { headers: { 'content-type': 'application/json' } });
  };
  // `hold()` keeps the next chat requests unanswered until the returned function is called.
  const hold = () => {
    let open;
    gate = { promise: new Promise((resolve) => { open = resolve; }) };
    return () => { gate = null; open(); };
  };
  return { fetch, sent, hold, failNext: (n = 1) => { failures = n; } };
}

// A fresh window, as a page load in a browser session that has loaded the widget
// before (so a saved chat is kept: see test-widget-fresh-return.js for the rest).
// `searchPaths` reaches the widget as the community config does; `path` is the page's
// pathname; `dataset` is setDataset's value before init; `popout` presets what a
// pop-out's opener hands it; `configDelayed` holds the community config back until
// `releaseConfig()`.
async function start({ searchPaths = SEARCH_PATHS, path = '/dataset/x', dataset, storage = {}, popout = false, configDelayed = false } = {}) {
  const window = new Window({
    url: `http://localhost${path}`,
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  window.sessionStorage.setItem(`${KEY}-session`, '1');
  for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, value);
  if (popout) window.__OSA_CHAT_CONFIG__ = { apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING, fullscreen: true };
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const server = serverFetch(searchPaths ? { search_paths: searchPaths } : {});
  let release;
  const gate = configDelayed ? new Promise((resolve) => { release = resolve; }) : null;
  const fetch = gate
    ? async (url, init) => {
      if (!String(url).endsWith('/health') && !String(url).endsWith('/chat')) await gate;
      return server.fetch(url, init);
    }
    : server.fetch;
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
  if (!configDelayed) await waitUntil(() => q('.osa-chat-title').firstChild.textContent.trim() === 'Switch Tests', 'the community config arrives');
  return {
    window, api, hooks, container, q, sent: server.sent, failNext: server.failNext, hold: server.hold,
    releaseConfig: () => release(),
    navigate: (to) => window.happyDOM.setURL(`http://localhost${to}`),
  };
}

const notice = (ctx) => ctx.q(NOTICE);
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const userMessages = (list) => list.filter((m) => m.role === 'user').map((m) => m.content);
const conversations = (ctx) => ctx.hooks.listConversations();
const lastBody = (ctx) => ctx.sent.at(-1);

async function send(ctx, question) {
  ctx.q('.osa-chat-input input').value = question;
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => !ctx.q('.osa-chat-input input').disabled, `the send of "${question}" ends`);
}

const answered = (ctx) => ctx.hooks.getMessages().at(-1).role === 'assistant' && ctx.hooks.getMessages().at(-1).content === 'An answer.';

// A chat that has the reader's question, on dataset A.
async function onDataset(options = {}) {
  const ctx = await start({ dataset: { id: A }, ...options });
  await send(ctx, 'What is the sampling rate?');
  assert(answered(ctx), 'sanity: the first question was answered');
  return ctx;
}

console.log('='.repeat(60));
console.log('Widget: the context switch (#656)');
console.log('='.repeat(60));

console.log('\nthe page context, as a pure function');
{
  const { contextForPage: page, sameContext: same } = (await start()).hooks;
  assertEqual(page({ id: A }, '/discover', SEARCH_PATHS), { type: 'dataset', id: A }, 'a dataset wins over a search path');
  assertEqual(page(null, '/discover', SEARCH_PATHS), { type: 'search' }, 'a search path is the search context');
  assertEqual(page(null, '/discover/', SEARCH_PATHS), { type: 'search' }, 'with a trailing slash too');
  assertEqual(page(null, '/discover/results/2', SEARCH_PATHS), { type: 'search' }, 'and anything below it');
  assertEqual(page(null, '/discovery', SEARCH_PATHS), null, 'but not a longer name that starts the same');
  assertEqual(page(null, '/about/discover', SEARCH_PATHS), null, 'nor a path that merely contains it');
  assertEqual(page(null, '/dataset/x', SEARCH_PATHS), null, 'a page that matches nothing gives none');
  assertEqual(page(null, '/discover', []), null, 'with no search paths, none');
  assertEqual(page(null, '/discover', undefined), null, 'also when there is no list');
  assertEqual(page(null, '/b/c', ['/a', '/b']), { type: 'search' }, 'any of several paths');
  assertEqual(page(null, '/', ['/']), { type: 'search' }, '"/" is the home page');
  assertEqual(page(null, '/dataset/x', ['/']), null, 'and not every page under it');
  assertEqual(page(null, '/dataset/x', ['/', '/discover']), null, 'nor when another path is listed beside it');
  assertEqual(page(null, '/discover/x', ['/', '/discover']), { type: 'search' }, 'while the other path still matches');
  assertEqual(page({ id: 'bad id' }, '/x', []), null, 'a dataset id the widget would refuse gives none');
  assert(same(null, null) && same({ type: 'search' }, { type: 'search' }) && same({ type: 'dataset', id: A }, { type: 'dataset', id: A }), 'equal contexts are the same');
  assert(!same(null, { type: 'search' }) && !same({ type: 'dataset', id: A }, { type: 'dataset', id: B }) && !same({ type: 'dataset', id: 'search' }, { type: 'search' }),
    'different ones are not, and a dataset called "search" is not the search context');
}

console.log('\nsearch_paths from the community config reaches the widget, and is remembered');
{
  const ctx = await start({ searchPaths: ['/discover', '/find'] });
  assertEqual(ctx.hooks.getConfig().searchPaths, ['/discover', '/find'], 'the config\'s paths');
  const next = await start({ searchPaths: null, storage: { 'osa-widget-config-nemar': JSON.stringify({ apiEndpoint: API, widget: { search_paths: ['/remembered'] } }) }, configDelayed: true });
  assertEqual(next.hooks.getConfig().searchPaths, ['/remembered'], 'the last load\'s are applied before the config arrives');
  next.releaseConfig();
  await waitUntil(() => next.q('.osa-chat-title').firstChild.textContent.trim() === 'Switch Tests', 'the config arrives');
  assertEqual(next.hooks.getConfig().searchPaths, [], 'and a config that names none puts them back to none');
  const mistyped = await start({ searchPaths: ['/ok', 'no-slash', 7] });
  assertEqual(mistyped.hooks.getConfig().searchPaths, ['/ok'], 'an entry that is not a path is ignored');
}

console.log('\na new dataset: the notice, above the input, naming the old one');
{
  const ctx = await onDataset();
  assert(!notice(ctx), 'no notice while the page and the chat agree');
  ctx.api.setDataset({ id: B });
  const el = notice(ctx);
  assert(!!el, 'a notice appears');
  assertEqual(text(el.querySelector('p')), `You're on a new dataset, ${B}. Your next message starts a new chat; the ${A} chat is saved.`, 'in the board\'s words, naming both datasets');
  assert(el.parentElement.nextElementSibling === ctx.q('.osa-chat-input'), 'directly above the input');
  assertEqual(text(el.querySelector('.osa-context-continue')), `Continue the ${A} chat instead`, 'with the way to keep the old chat');
  const slot = el.parentElement;
  assert(slot.classList.contains('osa-context-notice-slot') && slot.getAttribute('role') === 'status', 'in a status region');
  assert(ctx.q('.osa-context-notice-slot') === slot, 'which is the one that was in the page before the notice came');
  ctx.api.setDataset({ id: A });
  assert(!notice(ctx) && ctx.q('.osa-context-notice-slot') === slot && slot.children.length === 0, 'and stays, empty, when the notice goes');
  ctx.api.setDataset({ id: B });
  assert(ctx.q('.osa-chat-messages').textContent.includes('What is the sampling rate?'), 'the old chat stays on screen');
  assertEqual(conversations(ctx).length, 1, 'and nothing else is started yet');
  ctx.api.setDataset({ id: A });
  assert(!notice(ctx), 'going back to the first dataset takes the notice away');
  ctx.api.setDataset({ id: B });
  assert(!!notice(ctx), 'and forward again brings it back');
}

console.log('\nthe next send starts a new chat, and the old one stays whole');
{
  const ctx = await onDataset();
  const oldId = ctx.hooks.getActiveConversation().id;
  const oldMessages = ctx.hooks.getMessages().map((m) => ({ ...m }));
  ctx.api.setDataset({ id: B });
  await send(ctx, 'And this one?');
  const list = conversations(ctx);
  assertEqual(list.length, 2, 'there are two conversations');
  const old = list.find((c) => c.id === oldId);
  assert(old && !old.active, 'the old one is saved, no longer active');
  assertEqual(userMessages(old.messages), ['What is the sampling rate?'], 'with its question');
  assertEqual(old.messages.length, oldMessages.length, 'and every message it had');
  assertEqual(old.context, { type: 'dataset', id: A }, 'in its own context');
  assertEqual(userMessages(ctx.hooks.getMessages()), ['And this one?'], 'the new chat holds only the new question');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: B }, 'in the new dataset\'s context');
  assertEqual(ctx.hooks.getSessionId(), null, 'with no server session of the old chat\'s');
  const body = lastBody(ctx);
  assertEqual(body.page_context.dataset_id, B, 'the request names the new dataset');
  assert(!('moved_from' in body.page_context), 'and does not say the reader moved');
  assert(!('session_id' in body), 'nor reuses the old session');
  assert(!notice(ctx), 'the notice is gone');
  assert(!ctx.q('.osa-context-move'), 'with no divider');
}

console.log('\ncontinue the old chat: the divider, the note, and moved_from on the next request');
{
  const ctx = await onDataset();
  const oldId = ctx.hooks.getActiveConversation().id;
  ctx.api.setDataset({ id: B });
  ctx.q('.osa-context-continue').click();
  assert(!notice(ctx), 'the notice is gone');
  assertEqual(ctx.hooks.getActiveConversation().id, oldId, 'the same conversation is active');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: B }, 'now in the new dataset\'s context');
  const divider = ctx.q('.osa-context-divider');
  assert(!!divider, 'a divider is in the chat');
  assert(!divider.hasAttribute('role'), 'with no separator role, which would hide its words from a screen reader');
  assertEqual(text(divider), `Moved to ${B} · continuing this chat`, 'saying where the reader moved to');
  assertEqual(text(ctx.q('.osa-context-told')), `The assistant is told: the reader moved from ${A} to ${B}; compare or continue as they ask.`, 'and a dashed note on what the assistant is told');
  const before = ctx.q('.osa-chat-messages').children.length;
  await send(ctx, 'Compare them');
  assertEqual(conversations(ctx).length, 1, 'still one conversation');
  assertEqual(lastBody(ctx).page_context.moved_from, { type: 'dataset', id: A }, 'the request carries moved_from');
  assertEqual(lastBody(ctx).page_context.dataset_id, B, 'and the dataset the reader is on');
  assertEqual(lastBody(ctx).session_id, undefined, 'sanity: this fixture has no server session');
  assert(!!ctx.q('.osa-context-divider'), 'the divider stays after the reply');
  assert(ctx.q('.osa-chat-messages').children.length > before, 'with the new messages after it');
  const divided = [...ctx.q('.osa-chat-messages').children].findIndex((el) => el.classList.contains('osa-context-move'));
  const question = [...ctx.q('.osa-chat-messages').children].findIndex((el) => el.textContent.includes('Compare them'));
  assert(divided >= 0 && divided < question, 'between the earlier messages and the new question');
  await send(ctx, 'And the events?');
  assert(!('moved_from' in lastBody(ctx).page_context), 'the request after that does not repeat it');
  assertEqual(lastBody(ctx).page_context.dataset_id, B, 'though it still names the dataset');
}

console.log('\na send that failed does not use up the move');
{
  const ctx = await onDataset();
  ctx.api.setDataset({ id: B });
  ctx.q('.osa-context-continue').click();
  ctx.failNext(1);
  await send(ctx, 'Compare them');
  assert(ctx.q('.osa-error').style.display !== 'none', 'sanity: the send failed and the reader was told');
  assertEqual(lastBody(ctx).page_context.moved_from, { type: 'dataset', id: A }, 'the failed request carried the move');
  assertEqual(ctx.hooks.getMessages().filter((m) => m.role === 'user').length, 1, 'sanity: the question was taken out of the chat');
  assert(!!ctx.q('.osa-context-divider'), 'the divider is still there');
  await send(ctx, 'Compare them');
  assert(answered(ctx), 'the second try was answered');
  assertEqual(lastBody(ctx).page_context.moved_from, { type: 'dataset', id: A }, 'and carried the move again');
  await send(ctx, 'Anything else?');
  assert(!('moved_from' in lastBody(ctx).page_context), 'once the server has answered it is spent');
}

console.log('\nthe move is told only while the page is still where the reader moved to');
{
  const ctx = await onDataset();
  ctx.api.setDataset({ id: B });
  ctx.q('.osa-context-continue').click();
  ctx.api.setDataset(null);
  assert(!notice(ctx), 'sanity: a page that is neither gives no notice');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: B }, 'and the chat keeps the dataset it moved to');
  await send(ctx, 'Where am I?');
  assert(!lastBody(ctx).page_context || !('moved_from' in lastBody(ctx).page_context), 'a request from such a page does not claim a move to the search page');
  assert(!lastBody(ctx).page_context || !('dataset_id' in lastBody(ctx).page_context), 'and names no dataset');
  ctx.api.setDataset({ id: B });
  await send(ctx, 'Compare them');
  assertEqual(lastBody(ctx).page_context.moved_from, { type: 'dataset', id: A }, 'back on the dataset it moved to, the move is still there to tell');
}

console.log('\na move told by a request sent before Continue is not used up');
{
  const ctx = await onDataset();
  const release = ctx.hold();
  ctx.q('.osa-chat-input input').value = 'A slow question';
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => ctx.sent.length === 2, 'the slow request is sent');
  assert(!('moved_from' in lastBody(ctx).page_context), 'sanity: it does not carry a move');
  ctx.api.setDataset({ id: B });
  ctx.q('.osa-context-continue').click();
  release();
  await waitUntil(() => !ctx.q('.osa-chat-input input').disabled, 'the slow reply ends');
  await send(ctx, 'Compare them');
  assertEqual(lastBody(ctx).page_context.moved_from, { type: 'dataset', id: A }, 'the move is carried by the next request');
  await send(ctx, 'And now?');
  assert(!('moved_from' in lastBody(ctx).page_context), 'and spent once that was answered');
}

console.log('\na resumed reply that carried the move uses it up');
{
  const ctx = await onDataset();
  ctx.api.setDataset({ id: B });
  ctx.q('.osa-context-continue').click();
  const response = await ctx.hooks.postResume({ session_id: 's' }, { ok: true });
  assert(!!response, 'sanity: the resume was answered');
  assertEqual(lastBody(ctx).page_context.moved_from, { type: 'dataset', id: A }, 'it carried the move');
  await send(ctx, 'And now?');
  assert(!('moved_from' in lastBody(ctx).page_context), 'the next message does not repeat it');
}

console.log('\ntwo moves before a send are one, from where the chat started');
{
  const ctx = await onDataset();
  ctx.api.setDataset({ id: B });
  ctx.q('.osa-context-continue').click();
  const third = 'ds004000';
  ctx.api.setDataset({ id: third });
  assert(!!notice(ctx), 'the next dataset shows the notice again');
  notice(ctx).querySelector('.osa-context-continue').click();
  assertEqual(text(ctx.q('.osa-context-told')), `The assistant is told: the reader moved from ${A} to ${third}; compare or continue as they ask.`, 'the note names where the chat started');
  assertEqual(ctx.container.querySelectorAll('.osa-context-move').length, 1, 'with one divider');
  await send(ctx, 'Compare them');
  assertEqual(lastBody(ctx).page_context.moved_from, { type: 'dataset', id: A }, 'moved_from is the first dataset');
  assertEqual(lastBody(ctx).page_context.dataset_id, third, 'beside the dataset the reader is on');
}

console.log('\na move back to where the chat started is no move');
{
  const ctx = await onDataset();
  ctx.api.setDataset({ id: B });
  ctx.q('.osa-context-continue').click();
  ctx.api.setDataset({ id: A });
  notice(ctx).querySelector('.osa-context-continue').click();
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: A }, 'the chat is on the first dataset again');
  assert(!ctx.q('.osa-context-move'), 'with no divider');
  await send(ctx, 'Back where I was');
  assert(!('moved_from' in lastBody(ctx).page_context), 'and the request tells of no move');
  assertEqual(lastBody(ctx).page_context.dataset_id, A, 'only the dataset');
}

console.log('\nthe browser\'s back button is noticed before a send');
{
  const ctx = await onDataset();
  ctx.api.setDataset(null);
  ctx.navigate('/about');
  ctx.window.dispatchEvent(new ctx.window.Event('popstate'));
  assert(!notice(ctx), 'a page that gives no context shows no notice');
  ctx.navigate('/discover');
  ctx.window.dispatchEvent(new ctx.window.Event('popstate'));
  assert(!!notice(ctx), 'a move to the search page shows it at once');
  assert(text(notice(ctx).querySelector('p')).startsWith("You're on the dataset search."), 'naming the search page');
}

console.log('\nfrom a dataset to the search page, and back');
{
  const ctx = await onDataset();
  ctx.navigate('/discover/results');
  ctx.api.setDataset(null);
  const el = notice(ctx);
  assert(!!el, 'leaving the dataset for a search path shows the notice');
  assertEqual(text(el.querySelector('p')), `You're on the dataset search. Your next message starts a new chat; the ${A} chat is saved.`, 'naming the search and the old dataset');
  await send(ctx, 'Find resting state datasets');
  assertEqual(conversations(ctx).length, 2, 'the send starts a new chat');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'search' }, 'in the search context');
  assert(!('dataset_id' in lastBody(ctx).page_context) && !('moved_from' in lastBody(ctx).page_context), 'whose request names no dataset and no move');

  ctx.api.setDataset({ id: B });
  const back = notice(ctx);
  assert(!!back, 'a dataset page after the search shows the notice');
  assertEqual(text(back.querySelector('.osa-context-continue')), 'Continue the search chat instead', 'offering to continue the search chat');
  assert(text(back.querySelector('p')).startsWith(`You're on a new dataset, ${B}.`), 'naming the new dataset');
  back.querySelector('.osa-context-continue').click();
  assertEqual(text(ctx.q('.osa-context-told')), `The assistant is told: the reader moved from the dataset search to ${B}; compare or continue as they ask.`, 'the note names the search page');
  await send(ctx, 'Is this one in the results?');
  assertEqual(lastBody(ctx).page_context.moved_from, { type: 'search' }, 'moved_from is the search context, with no id');
  assertEqual(lastBody(ctx).page_context.dataset_id, B, 'beside the dataset');
  assertEqual(conversations(ctx).length, 2, 'in the same chat');
}

console.log('\nand continuing from a dataset to the search page');
{
  const ctx = await onDataset();
  ctx.navigate('/discover');
  ctx.api.setDataset(null);
  notice(ctx).querySelector('.osa-context-continue').click();
  await send(ctx, 'Find similar datasets');
  assertEqual(lastBody(ctx).page_context.moved_from, { type: 'dataset', id: A }, 'moved_from names the dataset left');
  assert(!('dataset_id' in lastBody(ctx).page_context), 'and there is no dataset on the search page');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'search' }, 'the chat is now a search chat');
}

console.log('\nan empty chat switches silently');
{
  const ctx = await start({ dataset: { id: A } });
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: A }, 'sanity: the empty chat took the first dataset');
  assert(!notice(ctx), 'no notice for it');
  ctx.api.setDataset({ id: B });
  assert(!notice(ctx), 'no notice for the next dataset either');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: B }, 'the chat simply takes it');
  ctx.navigate('/discover');
  ctx.api.setDataset(null);
  assert(!notice(ctx), 'nor for the search page');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'search' }, 'which it takes');
  await send(ctx, 'A first question');
  assertEqual(conversations(ctx).length, 1, 'and the first question stays in the same chat');
  assert(!('moved_from' in lastBody(ctx).page_context), 'with no move');
}

console.log('\na chat with questions but no context yet takes the page\'s silently');
{
  const legacy = JSON.stringify({ version: 2, sessionId: 's', messages: [
    { role: 'assistant', content: GREETING }, { role: 'user', content: 'An old question' }, { role: 'assistant', content: 'An old answer.' },
  ] });
  const ctx = await start({ storage: { [KEY]: legacy }, dataset: { id: A } });
  assertEqual(userMessages(ctx.hooks.getMessages()), ['An old question'], 'sanity: the old chat is the active one');
  assert(!notice(ctx), 'no notice');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: A }, 'it takes the page\'s context');
}

console.log('\na page that matches neither keeps the context');
{
  const ctx = await onDataset();
  ctx.navigate('/about');
  ctx.api.setDataset(null);
  assert(!notice(ctx), 'no notice');
  assertEqual(ctx.hooks.getActiveConversation().context, { type: 'dataset', id: A }, 'the chat keeps its context');
  await send(ctx, 'Another question');
  assertEqual(conversations(ctx).length, 1, 'and the next question stays in it');
  assert(lastBody(ctx).page_context === undefined || !('dataset_id' in lastBody(ctx).page_context), 'a page with no dataset sends none');
  assert(!lastBody(ctx).page_context || !('moved_from' in lastBody(ctx).page_context), 'and no move');
  ctx.api.setDataset({ id: B });
  ctx.api.setDataset(null);
  assert(!notice(ctx), 'a dataset named and cleared before any send leaves no notice behind');
}

console.log('\nthe widget loads on a page that already differs from the chat');
{
  const first = await onDataset();
  const saved = first.window.localStorage.getItem(KEY);
  const sameDataset = await start({ storage: { [KEY]: saved }, dataset: { id: A } });
  assert(!notice(sameDataset), 'the same dataset: no notice');
  const other = await start({ storage: { [KEY]: saved }, dataset: { id: B } });
  assert(!!notice(other), 'another dataset: the notice at load');
  assertEqual(userMessages(other.hooks.getMessages()), ['What is the sampling rate?'], 'over the old chat');
  const search = await start({ storage: { [KEY]: saved }, path: '/discover' });
  assert(!!notice(search), 'the search page: the notice at load, from the remembered paths');
}

console.log('\nthe community config arriving makes a search page a search page');
{
  const first = await onDataset();
  const saved = first.window.localStorage.getItem(KEY);
  const ctx = await start({ storage: { [KEY]: saved }, path: '/discover', configDelayed: true });
  assert(!notice(ctx), 'before the config, the page is no known place');
  ctx.releaseConfig();
  await waitUntil(() => !!notice(ctx), 'the notice appears once the paths are known');
  assert(text(notice(ctx).querySelector('p')).startsWith("You're on the dataset search."), 'for the search page');
}

console.log('\nthe dataset rides every request, with the page URL or without');
{
  const ctx = await start({ dataset: { id: A } });
  await send(ctx, 'With the URL shared');
  assertEqual(lastBody(ctx).page_context.dataset_id, A, 'the dataset is sent');
  assert(typeof lastBody(ctx).page_context.url === 'string', 'beside the URL, which the reader shares by default');
  // The reader's choice is "Use this page for answers" in the Privacy choices dialog.
  ctx.api.open();
  ctx.q('.osa-privacy-btn').click();
  const toggle = ctx.q('#osa-privacy-page');
  toggle.checked = false;
  toggle.dispatchEvent(new ctx.window.Event('change'));
  await send(ctx, 'With the URL not shared');
  assertEqual(lastBody(ctx).page_context, { dataset_id: A }, 'with the choice off, only the dataset is sent');
  ctx.api.setDataset(null);
  await send(ctx, 'On a page with no dataset');
  assert(lastBody(ctx).page_context === undefined, 'with the choice off and no dataset, no page_context at all');
}

console.log('\na pop-out never asks');
{
  const first = await onDataset();
  const saved = first.window.localStorage.getItem(KEY);
  const ctx = await start({ storage: { [KEY]: saved }, popout: true, dataset: { id: B } });
  assert(!notice(ctx), 'no notice at load');
  ctx.api.setDataset({ id: A });
  ctx.api.setDataset({ id: B });
  assert(!notice(ctx), 'nor when the opener names another dataset');
  const activeBefore = ctx.hooks.getActiveConversation().id;
  await send(ctx, 'In the pop-out');
  assertEqual(ctx.hooks.getActiveConversation().id, activeBefore, 'a send stays in the opener\'s chat');
  assertEqual(conversations(ctx).length, 1, 'and starts no other');
  assertEqual(lastBody(ctx).page_context.dataset_id, B, 'though the request still names the dataset');
}

console.log('\nthe resume card brings back a chat that may not match the page');
{
  const first = await onDataset();
  const stale = JSON.parse(first.window.localStorage.getItem(KEY));
  stale.conversations[0].updatedAt = Date.now() - 48 * 60 * 60 * 1000;
  const ctx = await start({ storage: { [KEY]: JSON.stringify(stale) }, dataset: { id: B } });
  assert(!notice(ctx), 'a fresh chat on the new dataset: no notice');
  ctx.q('.osa-resume-card').click();
  assertEqual(userMessages(ctx.hooks.getMessages()), ['What is the sampling rate?'], 'the old chat is back');
  assert(!!notice(ctx), 'with the notice, since it is about another dataset');
  assert(text(notice(ctx).querySelector('.osa-context-continue')).includes(A), 'naming it');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
