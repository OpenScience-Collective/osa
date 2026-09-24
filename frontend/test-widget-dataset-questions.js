/**
 * Suggested questions about the dataset on screen (#477), run against the real widget
 * source in a happy-dom window, the way test-widget-first-paint.js runs the first paint.
 *
 * What it holds the widget to: a community's `dataset_suggested_questions` are
 * templates the page's setDataset facts fill ({dataset_id}, {subject}, {task}); on
 * the opening screen of a dataset page, up to three whose blanks were all filled,
 * the ones marked needs_zarr only when the dataset has a Zarr copy, replace the
 * general list; mid-conversation, a dataset the conversation has not been on yet
 * gets a compact row of up to two, gone once the reader sends from that page, and
 * still gone after a reload; a community that sets no templates, and every page
 * that names no dataset, keep the general list exactly as before.
 *
 * What stands in: `fetch` (HTTP fixtures for the community config and a one-line
 * chat reply). A reload is a fresh window whose storage starts with what the
 * previous window left, as a browser's does for the same origin.
 *
 * Run with: bun frontend/test-widget-dataset-questions.js
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
const HISTORY_KEY = 'osa-test-dataset-questions';

const GENERAL = ['Find EEG datasets with resting state recordings', 'Tell me about dataset nm000132'];
// NEMAR's own templates, in its order (src/assistants/nemar/config.yaml).
const TEMPLATES = [
  { text: 'What is {dataset_id} about, and how was it recorded?', needs_zarr: false },
  { text: "Plot 10 seconds of sub-{subject}'s {task} recording from {dataset_id}", needs_zarr: true },
  { text: "Show the power spectrum of sub-{subject}'s {task} recording from {dataset_id}", needs_zarr: true },
  { text: 'How do I download {dataset_id}?', needs_zarr: false },
  { text: 'What events are annotated in {dataset_id}?', needs_zarr: false },
];
const ERP_CORE = { id: 'nm000132', zarr: true, subject: '001', task: 'N170' };

function configResponse(widget) {
  return {
    default_model: 'm',
    offered_models: [],
    widget: { title: 'NEMAR Assistant', placeholder: 'Ask', suggested_questions: GENERAL, ...widget },
    client_tools: [],
    runtime: null,
  };
}

// The community config, answered at once, and a chat reply of one line; `sent` records
// every chat request's body, and `hold()` keeps the next reply back until the
// function it returns is called.
function serverFetch(config) {
  const sent = [];
  const encoder = new TextEncoder();
  let gate = null;
  const fetch = async (url, init) => {
    const u = String(url);
    if (u.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (u.endsWith('/chat')) {
      sent.push(JSON.parse(init.body));
      if (gate) await gate;
      return new Response(new ReadableStream({
        start(controller) {
          for (const event of [{ event: 'content', content: 'An answer.' }, { event: 'done' }]) {
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
          }
          controller.close();
        },
      }), { headers: { 'content-type': 'text/event-stream' } });
    }
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
  const hold = () => {
    let release;
    gate = new Promise((resolve) => { release = resolve; });
    return () => { gate = null; release(); };
  };
  return { fetch, sent, hold };
}

// A fresh window, as a new page load. `dataset` is setDataset's value before init
// (undefined: the page never calls it); `storage` seeds localStorage with what an
// earlier load left.
async function start({ widget = { dataset_suggested_questions: TEMPLATES }, dataset, storage = {}, setConfig = {} } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, value);
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const server = serverFetch(configResponse(widget));
  window.fetch = server.fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, server.fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  if (dataset !== undefined) api.setDataset(dataset);
  api.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: HISTORY_KEY, ...setConfig });
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const q = (s) => container.querySelector(s);
  // The config has arrived once its title is drawn.
  await waitUntil(() => q('.osa-chat-title').firstChild.textContent.trim() === 'NEMAR Assistant', 'the community config arrives');
  return { window, api, container, q, sent: server.sent, hold: server.hold };
}

const shown = (q) => (q('.osa-suggestions').style.display === 'none'
  ? null
  : [...q('.osa-suggestions-list').querySelectorAll('.osa-suggestion')].map((b) => b.textContent));
const label = (q) => q('.osa-suggestions-label').textContent;
const compact = (q) => q('.osa-suggestions').classList.contains('osa-suggestions-compact');

// Collect warnings, and keep the widget's own startup chatter (no offered_models in
// these fixtures) out of what the tests count.
function captureWarnings() {
  const warnings = [];
  const original = console.warn;
  console.warn = (...args) => {
    const text = args.map(String).join(' ');
    if (!text.includes('offered_models')) warnings.push(text);
  };
  return { warnings, restore: () => { console.warn = original; } };
}

async function send(ctx, text) {
  const button = [...ctx.q('.osa-suggestions-list').querySelectorAll('.osa-suggestion')].find((b) => b.textContent === text);
  if (button) {
    button.click();
  } else {
    const input = ctx.q('.osa-chat-input input');
    input.value = text;
    ctx.q('.osa-send-btn').click();
  }
  await waitUntil(() => {
    const messages = ctx.api.__browser.getMessages();
    return messages.at(-1).role === 'assistant' && messages.at(-1).content === 'An answer.'
      && !ctx.q('.osa-chat-input input').disabled;
  }, `the reply to "${text}" arrives`);
}

console.log('='.repeat(60));
console.log('Widget: suggested questions about the dataset on screen (#477)');
console.log('='.repeat(60));

console.log('\na page that names no dataset keeps the general list');
{
  const { q } = await start();
  assertEqual(shown(q), GENERAL, 'the community\'s general suggestions');
  assertEqual(label(q), 'Try asking:', 'under the usual label');
  assert(!compact(q), 'at full width');
}

console.log('\na dataset page with a Zarr copy: the first three templates, filled from setDataset');
{
  const { q } = await start({ dataset: ERP_CORE });
  assertEqual(shown(q), [
    'What is nm000132 about, and how was it recorded?',
    "Plot 10 seconds of sub-001's N170 recording from nm000132",
    "Show the power spectrum of sub-001's N170 recording from nm000132",
  ], 'three questions about nm000132, in the community\'s order, in place of the general list');
  assertEqual(label(q), 'Try asking:', 'under the usual label');
  assert(!compact(q), 'at full width on the opening screen');
}

console.log('\nno Zarr copy, or not known yet: the templates that do not need one');
{
  const noCode = [
    'What is nm000132 about, and how was it recorded?',
    'How do I download nm000132?',
    'What events are annotated in nm000132?',
  ];
  for (const [zarr, why] of [[false, 'no Zarr copy'], [undefined, 'Zarr copy not known yet']]) {
    const { q } = await start({ dataset: { ...ERP_CORE, zarr } });
    assertEqual(shown(q), noCode, `${why}: the needs_zarr questions are left out, and the next ones fill in`);
  }
}

console.log('\na Zarr copy but no subject or task: a template with an unfilled blank is left out');
{
  const { q } = await start({ dataset: { id: 'nm000132', zarr: true } });
  assertEqual(shown(q), [
    'What is nm000132 about, and how was it recorded?',
    'How do I download nm000132?',
    'What events are annotated in nm000132?',
  ], 'never a question with "{subject}" left in it');
  const { q: q2 } = await start({ dataset: { id: 'nm000132', zarr: true, subject: '001' } });
  assert(!shown(q2).some((text) => text.includes('{')), 'a subject without a task still leaves out a template that needs both');
}

console.log('\nsetDataset after the widget is drawn re-renders, and null brings the general list back');
{
  const { api, q } = await start();
  assertEqual(shown(q), GENERAL, 'before the page names a dataset: the general list');
  api.setDataset({ id: 'nm000132' });
  assertEqual(shown(q)[0], 'What is nm000132 about, and how was it recorded?', 'the dataset named: its questions at once');
  assertEqual(shown(q).length, 3, 'three of them');
  api.setDataset(ERP_CORE);
  assertEqual(shown(q)[1], "Plot 10 seconds of sub-001's N170 recording from nm000132", 'the Zarr answer and facts arrive: the plotting questions appear');
  api.setDataset(null);
  assertEqual(shown(q), GENERAL, 'null: the general list again');
}

console.log('\na community with no templates keeps the general list on a dataset page too');
{
  const { q } = await start({ widget: {}, dataset: ERP_CORE });
  assertEqual(shown(q), GENERAL, 'no dataset_suggested_questions: unchanged');
  const { q: q2 } = await start({ widget: { dataset_suggested_questions: null }, dataset: ERP_CORE });
  assertEqual(shown(q2), GENERAL, 'the field sent as null: unchanged');
  const { q: q3 } = await start({
    widget: { dataset_suggested_questions: [{ text: 'Plot {subject}', needs_zarr: true }] },
    dataset: { id: 'nm000132', zarr: true },
  });
  assertEqual(shown(q3), GENERAL, 'templates exist but none fits this page: the general list, never an empty area');
}

console.log('\ninvalid facts are refused, and leave the previous dataset in place');
{
  const { api, q } = await start({ dataset: ERP_CORE });
  const before = shown(q);
  const capture = captureWarnings();
  try {
    for (const bad of [
      { ...ERP_CORE, subject: 'sub-001' },
      { ...ERP_CORE, task: 'N170 task' },
      { ...ERP_CORE, subject: '<b>1</b>' },
      { ...ERP_CORE, task: 170 },
      { ...ERP_CORE, subject: '' },
    ]) {
      api.setDataset({ ...bad, id: 'nm000133' });
    }
  } finally {
    capture.restore();
  }
  assertEqual(capture.warnings.length, 5, 'each is refused with a warning');
  assert(capture.warnings.every((w) => w.startsWith('[OSA] setDataset: invalid ')), 'naming what was wrong');
  assertEqual(shown(q), before, 'and the questions still name nm000132');
}

console.log('\na template the server would refuse is skipped, not shown with its blank');
{
  const capture = captureWarnings();
  let ctx;
  try {
    ctx = await start({
      widget: {
        dataset_suggested_questions: [
          { text: 'Compare {session} in {dataset_id}' },
          'What is {dataset_id}?',
          null,
          { text: 42 },
          { text: 'Plot sub-{subject} from {dataset_id} & more', needs_zarr: true },
        ],
      },
      dataset: ERP_CORE,
    });
  } finally {
    capture.restore();
  }
  assertEqual(shown(ctx.q), ['Plot sub-001 from nm000132 & more'], 'only the well-formed template, filled');
  assertEqual(ctx.q('.osa-suggestion').innerHTML, 'Plot sub-001 from nm000132 &amp; more', 'and its text is escaped, not parsed as markup');
}

console.log('\nan embedder\'s setConfig outranks the community\'s templates');
{
  const own = [{ text: 'Our own question about {dataset_id}', needs_zarr: false }];
  const { q } = await start({ dataset: ERP_CORE, setConfig: { datasetSuggestedQuestions: own } });
  assertEqual(shown(q), ['Our own question about nm000132'], 'the embedder\'s template, not the community\'s');
}

let afterAsking = {};

console.log('\nasking from the dataset page records it; the suggestions go once the reader sends');
{
  const ctx = await start({ dataset: ERP_CORE });
  await send(ctx, "Show the power spectrum of sub-001's N170 recording from nm000132");
  assertEqual(ctx.sent.map((b) => b.message), ["Show the power spectrum of sub-001's N170 recording from nm000132"], 'the clicked question is what is sent');
  const user = ctx.api.__browser.getMessages().find((m) => m.role === 'user');
  assertEqual(user.dataset, 'nm000132', 'the message records the dataset on screen');
  const stored = JSON.parse(ctx.window.localStorage.getItem(HISTORY_KEY));
  assertEqual(stored.messages.find((m) => m.role === 'user').dataset, 'nm000132', 'and so does the saved history');
  assertEqual(shown(ctx.q), null, 'mid-conversation on the same dataset: nothing offered');
  afterAsking = { [HISTORY_KEY]: ctx.window.localStorage.getItem(HISTORY_KEY) };
}

console.log('\na reload on the same dataset page: still nothing offered');
{
  const { q } = await start({ dataset: ERP_CORE, storage: afterAsking });
  assertEqual(shown(q), null, 'the conversation has been on nm000132, so no row');
}

console.log('\na different dataset mid-conversation: a compact row of two, gone once the reader sends');
{
  const ctx = await start({ dataset: { id: 'xx099904', zarr: true, subject: '01', task: 'p300' }, storage: afterAsking });
  assertEqual(shown(ctx.q), [
    'What is xx099904 about, and how was it recorded?',
    "Plot 10 seconds of sub-01's p300 recording from xx099904",
  ], 'two questions about the new dataset');
  assertEqual(label(ctx.q), 'About xx099904:', 'labeled with the dataset they are about');
  assert(compact(ctx.q), 'as the compact row');
  const suggestions = ctx.q('.osa-suggestions');
  const input = ctx.q('.osa-chat-input');
  assert(suggestions.nextElementSibling && [...ctx.container.querySelectorAll('.osa-suggestions ~ .osa-chat-input')].includes(input),
    'above the input');

  // The reader types their own question instead of clicking one.
  await send(ctx, 'Does xx099904 have HED tags?');
  assertEqual(shown(ctx.q), null, 'the reader sent from this page: the row is gone');
  assertEqual(ctx.api.__browser.getMessages().filter((m) => m.role === 'user').map((m) => m.dataset), ['nm000132', 'xx099904'],
    'each message records the page it was sent from');

  ctx.api.setDataset({ id: 'xx099903', zarr: false });
  assertEqual(shown(ctx.q), ['What is xx099903 about, and how was it recorded?', 'How do I download xx099903?'],
    'another dataset named without a reload: a row for it at once');
  ctx.api.setDataset(null);
  assertEqual(shown(ctx.q), null, 'and no row once the page names none');
}

console.log('\nnothing is offered while a reply is on its way, and the row appears once it has arrived');
{
  const ctx = await start({ dataset: ERP_CORE, storage: afterAsking });
  const release = ctx.hold();
  const input = ctx.q('.osa-chat-input input');
  input.value = 'One more question';
  ctx.q('.osa-send-btn').click();
  await waitUntil(() => ctx.sent.length === 1, 'the question is sent');
  ctx.api.setDataset({ id: 'xx099904', zarr: false });
  assertEqual(shown(ctx.q), null, 'another dataset named mid-reply: no row while the reply streams (a click there could not send)');
  release();
  await waitUntil(() => !ctx.q('.osa-chat-input input').disabled, 'the reply arrives');
  assertEqual(label(ctx.q), 'About xx099904:', 'the reply is in: the row for the new dataset appears');
}

console.log('\na history saved before this feature, or with a bad dataset field, counts as not on the dataset');
{
  const history = (dataset) => JSON.stringify({
    version: 2,
    sessionId: 's',
    messages: [
      { role: 'assistant', content: 'Hi' },
      { role: 'user', content: 'Earlier question', ...(dataset === undefined ? {} : { dataset }) },
      { role: 'assistant', content: 'Earlier answer', citations: [] },
    ],
  });
  const { q } = await start({ dataset: ERP_CORE, storage: { [HISTORY_KEY]: history(undefined) } });
  assertEqual(label(q), 'About nm000132:', 'no dataset recorded: the row is offered');
  for (const bad of ['<img src=x>', 42, 'nm000132 ']) {
    const { api, q: q2 } = await start({ dataset: ERP_CORE, storage: { [HISTORY_KEY]: history(bad) } });
    assert(!('dataset' in api.__browser.getMessages()[1]), `a saved dataset of ${JSON.stringify(bad)} is dropped on load`);
    assertEqual(label(q2), 'About nm000132:', 'and the row is offered');
  }
  const { api: kept } = await start({ dataset: ERP_CORE, storage: { [HISTORY_KEY]: history('nm000132') } });
  assertEqual(kept.__browser.getMessages()[1].dataset, 'nm000132', 'a valid saved dataset is kept');
}

console.log('\nremembered from the last load (#475), and dropped when the community drops them');
{
  const remembered = JSON.stringify({ apiEndpoint: API, widget: configResponse({ dataset_suggested_questions: TEMPLATES }).widget });
  // The fresh config no longer has templates: the server leaves the field out.
  const { q } = await start({ widget: {}, dataset: ERP_CORE, storage: { 'osa-widget-config-nemar': remembered } });
  assertEqual(shown(q), GENERAL, 'the fresh config wins: the general list, not the remembered templates');
}

console.log('\nclearing the chat brings back the opening screen\'s three');
{
  const ctx = await start({ dataset: ERP_CORE, storage: afterAsking });
  ctx.q('.osa-reset-btn').click();
  assertEqual(shown(ctx.q)?.length, 3, 'three again');
  assertEqual(label(ctx.q), 'Try asking:', 'under the usual label');
  assert(!compact(ctx.q), 'at full width');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
