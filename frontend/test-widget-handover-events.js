/**
 * The widget against the stream of a quick-to-analysis handover (#670, E8.2), run against
 * the real widget source.
 *
 * A community that routes between a quick and an analysis model answers a turn that asks
 * for code or a plot on a NEW session. The stream carries three `handover` events
 * (`summarizing`, `summarized`, `started`), and then the analysis run, which opens with
 * its own `session` event naming the new id and ends with a `done` that carries it again.
 * The handover card that draws those stages is tested in test-widget-handover-card.js;
 * this file holds the stream contract around it:
 *
 *   - each `handover` event moves the page on one step (a card at `summarizing`, its
 *     summary line at `summarized`, the divider at `started`), and none of them adds a
 *     reply of its own or ends the loading state;
 *   - the second `session` event is adopted, so the next message goes to the new session;
 *   - the reply the analysis run writes is the reply the reader sees, once, and
 *     `done.session_id` is what the widget keeps;
 *   - a `handover` event with fields the widget has never seen, or a stage it does not
 *     know, is ignored.
 *
 * What stands in: `fetch` answers with the SSE the test writes event by event. The widget,
 * its stream handler and its reveal are the real ones, in a happy-dom window.
 *
 * Run with: bun frontend/test-widget-handover-events.js
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitUntil(predicate, label, timeoutMs = 5000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) throw new Error(`timed out after ${timeoutMs} ms: ${label}`);
    await sleep(3);
  }
}

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');
const TITLE = 'Test Assistant';

/** An SSE response the test writes to one event at a time. */
function manualStream() {
  const encoder = new TextEncoder();
  let controller;
  const body = new ReadableStream({ start(c) { controller = c; } });
  return {
    response: new Response(body, { headers: { 'content-type': 'text/event-stream' } }),
    send(event) { controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`)); },
    close() { controller.close(); },
  };
}

/** The widget in its own window, initialized, with `fetch` answering /chat from queued streams. */
function loadWidget() {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const config = {
    default_model: 'm',
    offered_models: [],
    widget: { title: TITLE },
    client_tools: [],
    runtime: null,
  };
  const queued = [];
  const requests = [];
  const fetch = async (url, init) => {
    const s = String(url);
    if (s.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (s.endsWith('/chat')) {
      requests.push({ url: s, body: init && init.body ? JSON.parse(init.body) : null });
      const next = queued.shift();
      if (!next) throw new Error(`no stream queued for ${s}`);
      return next.response;
    }
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', 'performance', 'Date', SOURCE
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console, globalThis.performance, Date);
  const widget = window.OSAChatWidget;
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test-handover' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  return {
    window, widget, container, requests,
    api: widget.__browser,
    ready: () => waitUntil(() => widget.__browser.getConfig().title === TITLE, 'the community config'),
    queue() {
      const stream = manualStream();
      queued.push(stream);
      return stream;
    },
  };
}

function send(loaded, question) {
  const input = loaded.container.querySelector('.osa-chat-input input');
  input.value = question;
  loaded.container.querySelector('.osa-send-btn').dispatchEvent(new loaded.window.Event('click', { bubbles: true }));
}

const settled = (loaded) => !loaded.container.querySelector('.osa-send-btn').disabled;

/** The steps the handover card lists, in order. */
function cardSteps(container) {
  return [...container.querySelectorAll('.osa-handover .osa-handover-step')].map((el) => el.textContent.replace(/\s+/g, ' ').trim());
}

/** What the page shows of a pending reply, as a reader would read it. */
function view(container) {
  const text = (selector) => {
    const el = container.querySelector(selector);
    return el ? el.textContent : null;
  };
  return {
    assistants: container.querySelectorAll('.osa-message.assistant').length,
    loading: text('.osa-loading .osa-loading-label'),
    line: text('.osa-activity-status .osa-activity-label'),
    cards: container.querySelectorAll('.osa-handover').length,
  };
}

/**
 * Wait until the widget has handled every event sent on `stream` so far, by sending a
 * `session` event last: the stream is read in order, so once the widget holds that id,
 * everything before it has been handled.
 */
let barriers = 0;
async function handled(loaded, stream) {
  const id = `barrier-${++barriers}`;
  stream.send({ event: 'session', session_id: id });
  await waitUntil(() => loaded.api.getSessionId() === id, `the widget handled the events before ${id}`);
}

console.log('='.repeat(60));
console.log('Widget: the quick-to-analysis handover stream (#670, #677)');
console.log('='.repeat(60));

console.log('\neach handover event moves the card on, and the new session is adopted');
{
  const loaded = loadWidget();
  await loaded.ready();
  const stream = loaded.queue();

  send(loaded, 'plot the ERP for famous faces');
  stream.send({ event: 'session', session_id: 'old-session' });
  await handled(loaded, stream);
  const before = view(loaded.container);
  assertEqual(before.cards, 0, 'before the handover there is no card');

  stream.send({ event: 'handover', stage: 'summarizing' });
  await handled(loaded, stream);
  assertEqual(cardSteps(loaded.container), ['Summarizing our conversation\u2026'], 'summarizing: the card says so');
  assertEqual(view(loaded.container).assistants, before.assistants, 'summarizing: no reply is added');

  stream.send({
    event: 'handover',
    stage: 'summarized',
    messages: 4,
    words: 12,
    summary: 'The reader chose on002718, 18 participants.',
  });
  await handled(loaded, stream);
  assertEqual(cardSteps(loaded.container), ['Summarized our conversation (4 messages, 12 words)'], 'summarized: the card says what was summarized');
  assertEqual(view(loaded.container).assistants, before.assistants, 'summarized: still no reply');

  stream.send({ event: 'handover', stage: 'started', session_id: 'new-session', model: 'claude-sonnet-5-5' });
  await handled(loaded, stream);
  assertEqual(
    cardSteps(loaded.container),
    ['Summarized our conversation (4 messages, 12 words)', 'Handing it to the analysis model with your question'],
    'started: the hand-off step follows'
  );
  assert(!!loaded.container.querySelector('[role="separator"]'), 'started: the divider is drawn');
  assertEqual(view(loaded.container).cards, 1, 'still one card');

  // A stage and a field this widget has never heard of change nothing.
  const cardBefore = loaded.container.querySelector('.osa-handover-group').innerHTML;
  stream.send({ event: 'handover', stage: 'brand-new-stage', whatever: { nested: true } });
  stream.send({ event: 'handover', stage: 'started', model: 'claude-sonnet-5-5', whatever: 'else' });
  await handled(loaded, stream);
  assertEqual(loaded.container.querySelector('.osa-handover-group').innerHTML, cardBefore, 'an unknown stage, and an unknown field on a known one: ignored');

  // The analysis run: its own session event names the new id, and done carries it again.
  stream.send({ event: 'session', session_id: 'new-session' });
  await waitUntil(() => loaded.api.getSessionId() === 'new-session', 'the new session id is adopted from session');
  assert(true, 'the second session event is adopted as the session id');

  stream.send({ event: 'content', content: 'Here is the plot.' });
  stream.send({
    event: 'done',
    session_id: 'new-session',
    request_id: 'req-1',
    model: 'claude-sonnet-5-5',
    content: 'Here is the plot.',
    citations: [],
    mode: 'analysis',
  });
  stream.close();
  await waitUntil(() => settled(loaded), 'the reply finishes');

  assertEqual(loaded.api.getSessionId(), 'new-session', 'the widget keeps the new session after done');
  const after = view(loaded.container);
  assertEqual([after.assistants, after.loading, after.line], [before.assistants + 1, null, null], 'one reply added, no loading bubble, no status left');
  const reply = loaded.container.querySelector('.osa-message.assistant:last-child .osa-message-content');
  assertEqual(reply && reply.textContent.trim(), 'Here is the plot.', 'the reader sees the analysis run reply, once');

  // The next message goes to the new session.
  const next = loaded.queue();
  send(loaded, 'and the alpha band?');
  await waitUntil(() => loaded.requests.length === 2, 'the second request is sent');
  assertEqual(loaded.requests[1].body.session_id, 'new-session', 'the next message names the new session');
  next.send({ event: 'done', session_id: 'new-session', content: 'Alpha.', model: 'claude-sonnet-5-5', citations: [] });
  next.close();
  await waitUntil(() => settled(loaded), 'the second reply finishes');
}

clearTimeout(watchdog);
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
