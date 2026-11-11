/**
 * The handover card, the session divider and the mode label (#677, E8.3, board A6), run
 * against the real widget source in happy-dom windows.
 *
 * A community that routes between a quick and an analysis model streams `handover` events
 * when a turn moves from one to the other (src/api/handover.py): `summarizing`,
 * `summarized` (the summary, the messages and words it stands for, `degraded` when it is the
 * reader's own words), and `started` (the new session and its model), and then the analysis
 * run, whose `done` carries `mode`. What the widget must show:
 *
 *   - a live card at `summarizing`, the summary as TEXT in a native <details> at
 *     `summarized` (a summary that holds markup stays inert), a "could not be written" line
 *     when degraded, the hand-off step and a role=separator divider naming the model at
 *     `started`; an unknown stage or field changes nothing;
 *   - the header's mode label and the footnote only while a reply has reported a mode, a
 *     "Quick answers" tag on quick replies, and a first message that flips its own session
 *     (no handover events) showing the label and no card;
 *   - all of it back from stored data after a reload and when an older chat is opened from
 *     History, and gone from a new chat; stored fields bounded and normalized.
 *
 * What stands in: `fetch` answers with the SSE the test writes event by event. The widget,
 * its stream handler, its store and its History panel are the real ones.
 *
 * Run with: bun frontend/test-widget-handover-card.js
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
const KEY = 'osa-test-handover-card';
const SONNET = 'claude-sonnet-5-5';
const SUMMARY = 'The reader chose on002718 (famous, unfamiliar and scrambled faces). They now ask for the ERP.';
const HOSTILE = 'Chose on002718 <img src=x onerror="window.__pwned = 1"> <script>window.__pwned = 2</script> done';
const NOTE = 'Quick answers use a smaller, faster model. Running code and plots use the analysis model.';

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

/**
 * The widget in its own window, initialized. `storage` is what localStorage holds at load
 * (a reload passes what the last window left); `marker` says this browser session has
 * loaded the widget before, so the conversation is not replaced by a fresh one.
 */
function loadWidget({ storage = null, marker = false } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  if (marker) window.sessionStorage.setItem(`${KEY}-session`, '1');
  if (storage) window.localStorage.setItem(KEY, storage);
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
  const fetch = async (url) => {
    const s = String(url);
    if (s.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (s.endsWith('/chat')) {
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
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: KEY });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  return {
    window, widget, container,
    api: widget.__browser,
    q: (selector) => container.querySelector(selector),
    qa: (selector) => [...container.querySelectorAll(selector)],
    stored: () => window.localStorage.getItem(KEY),
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

/** Wait until every event sent so far has been handled (the stream is read in order). */
let barriers = 0;
async function handled(loaded, stream) {
  const id = `barrier-${++barriers}`;
  stream.send({ event: 'session', session_id: id });
  await waitUntil(() => loaded.api.getSessionId() === id, `the widget handled the events before ${id}`);
}

const text = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
const stepTexts = (loaded) => loaded.qa('.osa-handover .osa-handover-step').map(text);
const visible = (el) => !!el && !el.hidden;

/** One routed turn that hands over, up to (and not including) its analysis reply. */
async function handOver(loaded, stream, { summary = SUMMARY, degraded = false, model = SONNET, messages = 4, words = 90 } = {}) {
  send(loaded, 'Plot the ERP for famous faces in on002718');
  stream.send({ event: 'session', session_id: 'old-session' });
  stream.send({ event: 'handover', stage: 'summarizing' });
  await handled(loaded, stream);
  stream.send({
    event: 'handover', stage: 'summarized', messages, words, summary, ...(degraded ? { degraded: true } : {}),
  });
  stream.send({ event: 'handover', stage: 'started', session_id: 'new-session', model });
  await handled(loaded, stream);
}

function finishAnalysis(stream, content = 'Here is the ERP.', mode = 'analysis') {
  stream.send({ event: 'session', session_id: 'new-session' });
  stream.send({ event: 'content', content });
  stream.send({
    event: 'done', session_id: 'new-session', request_id: 'req-1', model: SONNET, content, citations: [], mode,
  });
  stream.close();
}

console.log('='.repeat(60));
console.log('Widget: the handover card, divider and mode label (#677)');
console.log('='.repeat(60));

console.log('\nsummarizing: a live card, with nothing else yet');
{
  const loaded = loadWidget();
  await loaded.ready();
  const stream = loaded.queue();
  send(loaded, 'Plot the ERP for famous faces');
  stream.send({ event: 'session', session_id: 'old-session' });
  stream.send({ event: 'handover', stage: 'summarizing' });
  await handled(loaded, stream);

  assertEqual(loaded.qa('.osa-handover').length, 1, 'one card is on the page');
  assertEqual(stepTexts(loaded), ['Summarizing our conversation…'], 'it says it is summarizing, and no more');
  assert(!!loaded.q('.osa-handover .osa-handover-live'), 'the step is live (a turning ring)');
  assert(!loaded.q('.osa-handover details'), 'there is no summary to show yet');
  assert(!loaded.q('[role="separator"]'), 'and no divider: the session has not changed');
  assertEqual(loaded.q('.osa-handover').getAttribute('aria-label'), 'Handing over to the analysis model', 'the card is a labeled group, not a landmark');
  assertEqual(loaded.q('.osa-handover').getAttribute('role'), 'group', 'with role=group');
  assertEqual(loaded.q('.osa-handover').tagName, 'DIV', 'on a div, so it is not a region landmark');
  assertEqual(text(loaded.q('.osa-status-announcer')), 'Summarizing our conversation', 'a screen reader is told it is summarizing');
  assert(!visible(loaded.q('.osa-mode-label')) && !visible(loaded.q('.osa-mode-note')), 'no mode is known yet: no label and no footnote');
  finishAnalysis(stream);
  await waitUntil(() => settled(loaded), 'the reply finishes');
}

console.log('\nsummarized and started, then the analysis reply');
{
  const loaded = loadWidget();
  await loaded.ready();
  const stream = loaded.queue();
  send(loaded, 'Plot the ERP for famous faces in on002718');
  stream.send({ event: 'session', session_id: 'old-session' });
  stream.send({ event: 'handover', stage: 'summarizing' });
  stream.send({ event: 'handover', stage: 'summarized', messages: 4, words: 90, summary: SUMMARY });
  await handled(loaded, stream);

  assertEqual(stepTexts(loaded), ['Summarized our conversation (4 messages, 90 words)'], 'summarized: the counts are shown');
  assert(!loaded.q('.osa-handover .osa-handover-live'), 'nothing is live between the two steps');
  const details = loaded.q('.osa-handover details');
  assert(!!details && details.tagName === 'DETAILS', 'the summary is in a native <details>');
  assertEqual(text(details.querySelector('summary')), 'What the analysis model is told', 'whose summary line is "What the analysis model is told"');
  assertEqual(details.open, false, 'closed');
  assertEqual(details.querySelector('p').textContent, SUMMARY, 'holding the summary');

  stream.send({ event: 'handover', stage: 'started', session_id: 'new-session', model: SONNET });
  await handled(loaded, stream);
  assertEqual(
    text(loaded.q('.osa-status-announcer')),
    'Summarized our conversation. Handing it to the analysis model with your question',
    'the back-to-back summarized and started are spoken together, so the first is not dropped'
  );
  assertEqual(
    stepTexts(loaded),
    ['Summarized our conversation (4 messages, 90 words)', 'Handing it to the analysis model with your question'],
    'started: the hand-off step follows'
  );
  assert(!!loaded.q('.osa-handover .osa-handover-live'), 'and is live until the reply starts');
  const divider = loaded.q('[role="separator"]');
  assert(!!divider, 'the divider is a role=separator');
  assertEqual(text(divider), 'Analysis session · Claude Sonnet 5.5', 'it names the model by the widget’s own label');
  const group = loaded.q('.osa-handover-group');
  assert(group.contains(divider) && group.firstElementChild.classList.contains('osa-handover'), 'the card comes first, then the divider');
  assert(!visible(loaded.q('.osa-mode-label')), 'the header label waits for done.mode');

  finishAnalysis(stream);
  await waitUntil(() => settled(loaded), 'the reply finishes');
  assert(!loaded.q('.osa-handover .osa-handover-live'), 'when the reply is done nothing in the card is live');
  assertEqual(loaded.qa('.osa-handover').length, 1, 'the card is drawn once');
  const reply = loaded.qa('.osa-message.assistant').pop();
  assertEqual(text(reply.querySelector('.osa-message-content')), 'Here is the ERP.', 'the reply is the analysis run’s, once');
  const order = [...loaded.q('.osa-chat-messages').children].map((el) => el.className.split(' ')[0]);
  assert(order.indexOf('osa-handover-group') < order.lastIndexOf('osa-message'), 'the card sits before the reply it belongs to');
  assertEqual(text(loaded.q('.osa-mode-label')), 'Analysis', 'done.mode puts "Analysis" in the header');
  assertEqual(loaded.q('.osa-mode-label').getAttribute('aria-label'), 'Mode: Analysis', 'with an aria-label');
  assert(visible(loaded.q('.osa-mode-note')) && text(loaded.q('.osa-mode-note')) === NOTE, 'and the footnote is shown');
  assert(!loaded.q('.osa-quick-tag'), 'an analysis reply has no "Quick answers" tag');
}

console.log('\nwhat the reader opened stays open across redraws');
{
  const loaded = loadWidget();
  await loaded.ready();
  const stream = loaded.queue();
  await handOver(loaded, stream);
  const details = loaded.q('.osa-handover details');
  details.open = true;
  details.dispatchEvent(new loaded.window.Event('toggle'));
  stream.send({ event: 'content', content: 'Working on it.' });
  await waitUntil(() => /Working on it/.test(loaded.q('.osa-chat-messages').textContent), 'a streamed chunk is drawn');
  assertEqual(loaded.q('.osa-handover details').open, true, 'a redraw keeps the details open');
  finishAnalysis(stream, 'Working on it. Done.');
  await waitUntil(() => settled(loaded), 'the reply finishes');
  assertEqual(loaded.q('.osa-handover details').open, true, 'and so does the finished reply');
}

console.log('\nthe summary is text, never markup');
{
  const loaded = loadWidget();
  await loaded.ready();
  const stream = loaded.queue();
  await handOver(loaded, stream, { summary: HOSTILE });

  const told = loaded.q('.osa-handover details p');
  assertEqual(told.textContent, HOSTILE, 'the summary is shown verbatim, as text');
  assertEqual(loaded.qa('.osa-handover img, .osa-handover script').length, 0, 'no element came out of it');
  assertEqual(loaded.window.__pwned, undefined, 'and nothing ran');
  finishAnalysis(stream);
  await waitUntil(() => settled(loaded), 'the reply finishes');
  assertEqual(loaded.qa('.osa-chat-messages img, .osa-chat-messages script').length, 0, 'still inert after the reply');
  assertEqual(loaded.q('.osa-handover details p').textContent, HOSTILE, 'and still the same text');
}

console.log('\ndegraded: the summary could not be written');
{
  const loaded = loadWidget();
  await loaded.ready();
  const stream = loaded.queue();
  await handOver(loaded, stream, { summary: 'Plot the ERP for famous faces in on002718', degraded: true, messages: 2, words: 9 });

  const first = stepTexts(loaded)[0];
  assert(/could not be written/.test(first) && /your own words/.test(first), `the first step says so (${first})`);
  assert(!/Summarized our conversation/.test(first), 'and does not claim a summary');
  assertEqual(loaded.q('.osa-handover details p').textContent, 'Plot the ERP for famous faces in on002718', 'the details hold the reader’s own words');
  assert(!!loaded.q('[role="separator"]'), 'the hand-off still goes ahead');
  finishAnalysis(stream);
  await waitUntil(() => settled(loaded), 'the reply finishes');
}

console.log('\na model the widget does not know is shown as given; singular counts');
{
  const loaded = loadWidget();
  await loaded.ready();
  const stream = loaded.queue();
  await handOver(loaded, stream, { model: 'acme/new-model-9', messages: 1, words: 1 });
  assertEqual(text(loaded.q('[role="separator"]')), 'Analysis session · acme/new-model-9', 'an unknown model id appears as given');
  assertEqual(stepTexts(loaded)[0], 'Summarized our conversation (1 message, 1 word)', 'one message and one word are singular');
  finishAnalysis(stream);
  await waitUntil(() => settled(loaded), 'the reply finishes');
}

console.log('\nunknown stages and fields are ignored');
{
  const loaded = loadWidget();
  await loaded.ready();
  const stream = loaded.queue();
  await handOver(loaded, stream);
  const before = loaded.q('.osa-handover-group').innerHTML;

  stream.send({ event: 'handover', stage: 'brand-new-stage', whatever: { nested: true }, summary: '<b>x</b>' });
  stream.send({ event: 'handover', summary: 'no stage at all' });
  stream.send({ event: 'handover', stage: 42 });
  stream.send({ event: 'handover', stage: '__proto__', model: 'm' });
  await handled(loaded, stream);
  assertEqual(loaded.q('.osa-handover-group').innerHTML, before, 'the card and divider are exactly as they were');

  // A field this widget has never heard of, on a stage it knows, is read past.
  stream.send({ event: 'handover', stage: 'started', model: SONNET, extra: { a: 1 }, summary: 'ignored on started' });
  await handled(loaded, stream);
  assertEqual(loaded.q('.osa-handover details p').textContent, SUMMARY, 'a known stage with extra fields keeps what it had');
  finishAnalysis(stream);
  await waitUntil(() => settled(loaded), 'the reply finishes');
}

console.log('\nthe mode label, the tag and the footnote come from done.mode');
{
  // A community that sends no mode: nothing appears.
  const plain = loadWidget();
  await plain.ready();
  const s1 = plain.queue();
  send(plain, 'What is HED?');
  s1.send({ event: 'session', session_id: 's1' });
  s1.send({ event: 'done', session_id: 's1', content: 'Hierarchical Event Descriptors.', model: 'claude-haiku-5-5', citations: [] });
  s1.close();
  await waitUntil(() => settled(plain), 'the plain reply finishes');
  assert(!visible(plain.q('.osa-mode-label')), 'no mode in the stream: no header label');
  assert(!visible(plain.q('.osa-mode-note')), 'and no footnote');
  assert(!plain.q('.osa-quick-tag') && !plain.q('.osa-handover'), 'no tag and no card');

  // A quick reply.
  const loaded = loadWidget();
  await loaded.ready();
  const s2 = loaded.queue();
  send(loaded, 'Find datasets with face stimuli');
  s2.send({ event: 'session', session_id: 'q1' });
  s2.send({ event: 'done', session_id: 'q1', content: 'Try on002718.', model: 'claude-haiku-5-5', citations: [], mode: 'quick' });
  s2.close();
  await waitUntil(() => settled(loaded), 'the quick reply finishes');
  assertEqual(text(loaded.q('.osa-mode-label')), 'Quick', 'a quick reply: the header says "Quick"');
  assertEqual(loaded.q('.osa-mode-label').getAttribute('aria-label'), 'Mode: Quick', 'with its aria-label');
  assertEqual(loaded.qa('.osa-quick-tag').map(text), ['Quick answers'], 'the reply carries a "Quick answers" tag');
  assert(loaded.q('.osa-quick-tag').closest('.osa-message-header') !== null, 'in the reply’s header row');
  assertEqual(text(loaded.q('.osa-mode-note')), NOTE, 'the footnote reads as designed');
  assert(visible(loaded.q('.osa-mode-note')), 'and is shown');
  assert(!loaded.q('.osa-handover'), 'no card for a quick reply');

  // The next turn reports analysis with no handover events.
  const s3 = loaded.queue();
  send(loaded, 'Plot the alpha band');
  s3.send({ event: 'session', session_id: 'q1' });
  s3.send({ event: 'done', session_id: 'q1', content: 'Plotting.', model: SONNET, citations: [], mode: 'analysis' });
  s3.close();
  await waitUntil(() => settled(loaded), 'the analysis reply finishes');
  assertEqual(text(loaded.q('.osa-mode-label')), 'Analysis', 'the label follows the latest reply');
  assertEqual(loaded.qa('.osa-quick-tag').length, 1, 'the earlier quick reply keeps its tag');

  // A value the widget does not know is no mode.
  const odd = loadWidget();
  await odd.ready();
  const s4 = odd.queue();
  send(odd, 'hello');
  s4.send({ event: 'done', session_id: 'o1', content: 'Hi.', model: 'm', citations: [], mode: '<b>turbo</b>' });
  s4.close();
  await waitUntil(() => settled(odd), 'the reply finishes');
  assert(!visible(odd.q('.osa-mode-label')), 'an unknown mode shows no label');
}

console.log('\na first message that flips its own session: the label, and no card');
{
  const loaded = loadWidget();
  await loaded.ready();
  const stream = loaded.queue();
  send(loaded, 'Plot the ERP for famous faces in on002718');
  stream.send({ event: 'session', session_id: 'flip-1' });
  stream.send({ event: 'content', content: 'Plotting.' });
  stream.send({ event: 'done', session_id: 'flip-1', content: 'Plotting.', model: SONNET, citations: [], mode: 'analysis' });
  stream.close();
  await waitUntil(() => settled(loaded), 'the reply finishes');
  assertEqual(text(loaded.q('.osa-mode-label')), 'Analysis', 'the header says "Analysis"');
  assertEqual(loaded.qa('.osa-handover, [role="separator"]').length, 0, 'no card and no divider');
  assertEqual(loaded.qa('.osa-quick-tag').length, 0, 'and no quick tag');
}

console.log('\nreload: the card, the divider and the label come back from storage');
let storedAfterHandover = null;
{
  const first = loadWidget();
  await first.ready();
  const stream = first.queue();
  await handOver(first, stream);
  first.q('.osa-handover details').open = true;
  first.q('.osa-handover details').dispatchEvent(new first.window.Event('toggle'));
  finishAnalysis(stream);
  await waitUntil(() => settled(first), 'the reply finishes');
  storedAfterHandover = first.stored();
  const record = JSON.parse(storedAfterHandover);
  const reply = record.conversations[0].messages.find((m) => m.handover);
  assertEqual(
    reply.handover,
    { stage: 'started', messages: 4, words: 90, summary: SUMMARY, model: SONNET },
    'the handover is stored on the reply, as the five fields and its stage'
  );
  assertEqual(reply.mode, 'analysis', 'and the mode on the reply');
  assert(!('_handoverOpen' in reply), 'what the reader opened is not stored');

  const reloaded = loadWidget({ storage: storedAfterHandover, marker: true });
  await reloaded.ready();
  assertEqual(reloaded.qa('.osa-handover').length, 1, 'the card is drawn');
  assertEqual(stepTexts(reloaded), ['Summarized our conversation (4 messages, 90 words)', 'Handing it to the analysis model with your question'], 'with both steps');
  assert(!reloaded.q('.osa-handover-live'), 'none of them live: the chat is not under way');
  assertEqual(reloaded.q('.osa-handover details').open, false, 'the details are closed');
  assertEqual(reloaded.q('.osa-handover details p').textContent, SUMMARY, 'holding the summary');
  assertEqual(text(reloaded.q('[role="separator"]')), 'Analysis session · Claude Sonnet 5.5', 'the divider is back');
  assertEqual(text(reloaded.q('.osa-mode-label')), 'Analysis', 'and the header label');
  assert(visible(reloaded.q('.osa-mode-note')), 'and the footnote');
}

console.log('\nHistory: an older chat brings it back, a new chat clears it');
{
  const loaded = loadWidget({ storage: storedAfterHandover, marker: true });
  await loaded.ready();
  assertEqual(text(loaded.q('.osa-mode-label')), 'Analysis', 'the restored chat has its label');

  loaded.q('.osa-history-btn').click();
  loaded.q('.osa-history-new').click();
  assertEqual(loaded.qa('.osa-handover, [role="separator"]').length, 0, 'a new chat: no card or divider');
  assert(!visible(loaded.q('.osa-mode-label')), 'the label is cleared');
  assert(!visible(loaded.q('.osa-mode-note')), 'and so is the footnote');
  assertEqual(loaded.q('.osa-mode-label').getAttribute('aria-label'), null, 'with no aria-label left behind');

  loaded.q('.osa-history-btn').click();
  const older = loaded.q('.osa-history-row:not(.osa-history-row-active) .osa-history-open');
  assert(!!older, 'the older chat is listed in History');
  older.click();
  assertEqual(loaded.qa('.osa-handover').length, 1, 'opened from History: the card is back');
  assertEqual(loaded.q('.osa-handover details').open, false, 'with its details closed');
  assertEqual(text(loaded.q('[role="separator"]')), 'Analysis session · Claude Sonnet 5.5', 'the divider is back');
  assertEqual(text(loaded.q('.osa-mode-label')), 'Analysis', 'and the label');
  assert(visible(loaded.q('.osa-mode-note')), 'and the footnote');
}

console.log('\nstored fields are bounded and normalized');
{
  const tampered = JSON.parse(storedAfterHandover);
  const reply = tampered.conversations[0].messages.find((m) => m.handover);
  reply.handover = {
    stage: 'started',
    messages: -5,
    words: '12',
    summary: 'x'.repeat(5000),
    degraded: 'yes',
    model: 'not a model id!',
    extra: '<img src=x onerror=1>',
  };
  reply.mode = 'turbo';
  const loaded = loadWidget({ storage: JSON.stringify(tampered), marker: true });
  await loaded.ready();
  const kept = loaded.api.getMessages().find((m) => m.handover);
  assertEqual(kept.handover.summary.length, 2000, 'the summary is cut to 2000 characters');
  assert(!('messages' in kept.handover) && !('words' in kept.handover), 'counts that are not whole numbers are dropped');
  assert(!('degraded' in kept.handover) && !('model' in kept.handover) && !('extra' in kept.handover), 'a non-boolean flag, an invalid model id and unknown fields are dropped');
  assert(!('mode' in kept), 'an unknown mode is dropped');
  assertEqual(stepTexts(loaded)[0], 'Summarized our conversation', 'the card falls back to a plain first step');
  assertEqual(loaded.qa('[role="separator"]').map(text), ['Analysis session'], 'and a divider with no model name');
  assert(!visible(loaded.q('.osa-mode-label')), 'no label for an unknown mode');

  reply.handover = { stage: 'nonsense', summary: 'x' };
  reply.mode = 'quick';
  const second = loadWidget({ storage: JSON.stringify(tampered), marker: true });
  await second.ready();
  assertEqual(second.qa('.osa-handover').length, 0, 'a stored handover with an unknown stage draws no card');
  assertEqual(text(second.q('.osa-mode-label')), 'Quick', 'a valid stored mode still does its work');

  reply.handover = ['not', 'an', 'object'];
  const third = loadWidget({ storage: JSON.stringify(tampered), marker: true });
  await third.ready();
  assertEqual(third.qa('.osa-handover').length, 0, 'a stored handover that is not an object draws no card');
}

console.log('\na reload in the middle of a summary: a step that did not finish');
{
  const tampered = JSON.parse(storedAfterHandover);
  const reply = tampered.conversations[0].messages.find((m) => m.handover);
  reply.handover = { stage: 'summarizing' };
  const loaded = loadWidget({ storage: JSON.stringify(tampered), marker: true });
  await loaded.ready();
  assertEqual(stepTexts(loaded), ['–Did not finish summarizing our conversation'], 'the step says it did not finish');
  assert(!loaded.q('.osa-handover-live'), 'with no turning ring');
  assertEqual(loaded.qa('.osa-handover-stopped').length, 1, 'a dash marker stands in for it');
  assert(!loaded.q('[role="separator"]'), 'and no divider: the session never changed');
}

console.log('\nreduced motion: the stylesheet holds the ring still');
{
  // happy-dom cannot compute the cascade, so read it from the source: the override must
  // outrank the ring's own rule (two classes, later in the sheet).
  const block = SOURCE.slice(SOURCE.indexOf('@media (prefers-reduced-motion: reduce)'));
  const override = /([^{}]*\.osa-handover-marker\.osa-handover-live)\s*\{\s*animation:\s*none;/.exec(block);
  assert(!!override, 'the reduced-motion block stops the ring');
  const classes = (override ? override[1] : '').match(/\.[\w-]+/g) || [];
  assert(classes.length >= 3, 'with a selector of three classes or more, to outrank the ring rule');
}

clearTimeout(watchdog);
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
