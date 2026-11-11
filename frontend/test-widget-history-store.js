/**
 * The conversation store (#654), run against the real widget source in happy-dom
 * windows: history format v3, up to 30 saved conversations in one record.
 *
 * What it holds the widget to: a record any earlier version wrote (v2, v1, the bare
 * messages array) becomes exactly one conversation with its messages and session
 * kept; the 31st conversation evicts the oldest by updatedAt, never the active one;
 * a save then a load returns the same messages with no in-page-only field; a record
 * that cannot be read (corrupt JSON, a version above 3) is never overwritten and
 * queues a notice; a full storage gives up the oldest other conversation and tries
 * again; and a second widget on the same storage, or the pop-out, never loses the
 * conversations it did not touch.
 *
 * What stands in: `fetch` (the community config and a health check). Storage is
 * real happy-dom storage; two widgets share one by being handed the same object, as
 * two tabs of one origin share theirs.
 *
 * Run with: bun frontend/test-widget-history-store.js
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

// Key order does not matter to what is stored, so it does not matter here.
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

function assertEqual(actual, expected, msg) {
  const same = JSON.stringify(canonical(actual)) === JSON.stringify(canonical(expected));
  assert(same, `${msg}${same ? '' : ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`}`);
}

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');
const API = 'http://localhost/api';
const KEY = 'osa-test-history-store';
const GREETING = 'Hello from the store tests';

function serverFetch(log = []) {
  return async (url, init = {}) => {
    const u = String(url);
    log.push({ url: u, body: init.body ? JSON.parse(init.body) : null });
    if (u.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    return new Response(JSON.stringify({
      default_model: 'm', offered_models: [], widget: { title: 'Store Tests' }, client_tools: [], runtime: null,
    }), { headers: { 'content-type': 'application/json' } });
  };
}

// A storage that can be told to refuse writes: a full disk, as a browser reports it.
function failingStorage(real) {
  // `failKey` refuses writes to one key only, with the error named `failName`.
  const control = { failNext: 0, failAlways: false, attempts: 0, failKey: null, failName: 'QuotaExceededError' };
  const storage = {
    getItem: (k) => real.getItem(k),
    setItem: (k, v) => {
      control.attempts += 1;
      if (control.failKey === k) {
        throw Object.assign(new Error('The write was refused.'), { name: control.failName });
      }
      if (control.failAlways || control.failNext > 0) {
        if (control.failNext > 0) control.failNext -= 1;
        throw Object.assign(new Error('The quota has been exceeded.'), { name: 'QuotaExceededError' });
      }
      real.setItem(k, v);
    },
    removeItem: (k) => real.removeItem(k),
    key: (i) => real.key(i),
    get length() { return real.length; },
  };
  return { storage, control };
}

// A fresh window, as a new page load. `storage` seeds localStorage with what an
// earlier load left; `shared` hands the widget another window's storage instead.
function start({ storage = {}, shared = null, config = {} } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true },
  });
  window.__OSA_TEST__ = true;
  const localStorage = shared || window.localStorage;
  for (const [key, value] of Object.entries(storage)) localStorage.setItem(key, value);
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const fetchLog = [];
  const fetch = serverFetch(fetchLog);
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const api = window.OSAChatWidget;
  api.setConfig({ apiEndpoint: API, communityId: 'nemar', storageKey: KEY, initialMessage: GREETING, ...config });
  api.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const q = (s) => container.querySelector(s);
  const hooks = api.__browser;
  const stored = () => JSON.parse(localStorage.getItem(KEY));
  const raw = () => localStorage.getItem(KEY);
  // What the widget shows the reader once opened: a notice queued at load.
  const notice = () => {
    q('.osa-chat-button').click();
    return q('.osa-error').textContent;
  };
  return { window, api, hooks, container, q, localStorage, stored, raw, notice, fetchLog };
}

const activeOf = (record) => record.conversations.find((c) => c.id === record.activeId);

function conversation(n, overrides = {}) {
  return {
    id: `conv-${n}`,
    context: null,
    title: `Question ${n}`,
    sessionId: `session-${n}`,
    createdAt: 1000 + n,
    updatedAt: 1000 + n,
    messages: [
      { role: 'assistant', content: GREETING },
      { role: 'user', content: `Question ${n}` },
      { role: 'assistant', content: `Answer ${n}`, citations: [], executions: [] },
    ],
    ...overrides,
  };
}

function envelope(conversations, activeId) {
  return JSON.stringify({ version: 3, activeId, conversations });
}

console.log('='.repeat(60));
console.log('Widget: the conversation store, history format v3 (#654)');
console.log('='.repeat(60));

// ---------------------------------------------------------------------------
// Migration
// ---------------------------------------------------------------------------

const LONG_QUESTION = 'Compare the event related potentials for famous versus scrambled faces in the recording';
const RUN = {
  callId: 'call-1', tool: 'execute_code', description: 'Plot the ERP', code: 'print("erp")\n',
  status: 'ok', stdout: 'erp\n', stderr: '', images: [], local: false, workspaceNote: '',
};
// The shape the v2 widget wrote (test-widget-dataset-questions.js), with a run and
// a citation: already in the form the loader returns, so the input is the output.
const V2_MESSAGES = [
  { role: 'assistant', content: 'Hi', citations: [], executions: [] },
  { role: 'user', content: LONG_QUESTION, dataset: 'nm000103' },
  { role: 'assistant', content: 'A dataset [1].', citations: [{ marker: 1, source: 'docs', title: 'Docs' }], executions: [] },
  { role: 'user', content: 'And the events?', dataset: 'nm000132' },
  { role: 'assistant', content: 'Done.', citations: [], executions: [RUN] },
];

console.log('\na v2 record becomes one conversation, nothing lost');
{
  const v2 = JSON.stringify({ version: 2, sessionId: 'session-v2', messages: V2_MESSAGES });
  const ctx = start({ storage: { [KEY]: v2 } });
  const record = ctx.stored();
  assertEqual(record.version, 3, 'the stored record is v3');
  assertEqual(record.conversations.length, 1, 'with exactly one conversation');
  const only = record.conversations[0];
  assertEqual(record.activeId, only.id, 'which is the active one');
  assertEqual(only.messages, V2_MESSAGES, 'its messages are the input\'s, runs and citations included');
  assertEqual(only.sessionId, 'session-v2', 'its session id is the input\'s');
  assertEqual(ctx.hooks.getMessages(), V2_MESSAGES, 'and the working copy is the same messages');
  assertEqual(ctx.hooks.getSessionId(), 'session-v2', 'and the same session');
  assertEqual(only.title, LONG_QUESTION.slice(0, 60), 'the title is the first user message cut to 60 characters');
  assertEqual(only.title.length, 60, 'which is 60 characters');
}

console.log('\nthe title is the FIRST user message; the context the LATEST dataset');
{
  const v2 = JSON.stringify({ version: 2, sessionId: 's', messages: V2_MESSAGES });
  const only = start({ storage: { [KEY]: v2 } }).stored().conversations[0];
  assertEqual(only.title, LONG_QUESTION.slice(0, 60), 'the first user message, not the latest');
  assertEqual(only.context, { type: 'dataset', id: 'nm000132' }, 'the dataset of the latest user message, as an object');
  assert(typeof only.id === 'string' && only.id.length > 0, 'with an id');
  assert(Number.isFinite(only.createdAt) && Number.isFinite(only.updatedAt) && Math.abs(only.updatedAt - Date.now()) < 60_000,
    'and timestamps from now');
}

console.log('\na v1 record (no version) and the bare messages array each become one conversation');
{
  const v1Messages = [
    { role: 'assistant', content: 'Hi' },
    { role: 'user', content: 'How do I tag a stimulus?' },
    { role: 'assistant', content: 'Use HED.' },
  ];
  const normalized = [
    { role: 'assistant', content: 'Hi', citations: [], executions: [] },
    v1Messages[1],
    { role: 'assistant', content: 'Use HED.', citations: [], executions: [] },
  ];
  for (const [label, record, session] of [
    ['v1', { messages: v1Messages, sessionId: 'session-v1' }, 'session-v1'],
    ['version 1', { version: 1, messages: v1Messages, sessionId: 'session-v1' }, 'session-v1'],
    ['bare array', v1Messages, null],
  ]) {
    const ctx = start({ storage: { [KEY]: JSON.stringify(record) } });
    const stored = ctx.stored();
    assertEqual(stored.version, 3, `${label}: stored as v3`);
    assertEqual(stored.conversations.length, 1, `${label}: one conversation`);
    assertEqual(stored.activeId, stored.conversations[0].id, `${label}: and it is active`);
    assertEqual(stored.conversations[0].messages, normalized, `${label}: messages kept, normalized`);
    assertEqual(stored.conversations[0].sessionId, session, `${label}: session id kept`);
    assertEqual(stored.conversations[0].title, 'How do I tag a stimulus?', `${label}: title`);
    assertEqual(stored.conversations[0].context, null, `${label}: no dataset on any message: no context`);
  }
}

console.log('\na record with no user message has an empty title; invalid messages are filtered');
{
  const record = { version: 2, sessionId: null, messages: [{ role: 'assistant', content: 'Hi' }, { role: 'system', content: 'no' }, 7] };
  const ctx = start({ storage: { [KEY]: JSON.stringify(record) } });
  const only = ctx.stored().conversations[0];
  assertEqual(only.title, '', 'the title is empty');
  assertEqual(only.messages, [{ role: 'assistant', content: 'Hi', citations: [], executions: [] }], 'the invalid messages are left out');
}

console.log('\nthe citation repair stays for records below version 2, and only for them');
{
  const content = 'See the runica[1] page. More text.';
  const citations = [{ marker: 1, source: 'docs' }];
  const repaired = start().api.__migrateLegacyCitationMarkers(content, citations);
  assert(repaired !== content, 'sanity: this reply needs the repair');
  const old = (version) => JSON.stringify({ ...(version === undefined ? {} : { version }),
    messages: [{ role: 'user', content: 'q' }, { role: 'assistant', content, citations }], sessionId: null });
  const fromV1 = start({ storage: { [KEY]: old(1) } });
  assertEqual(fromV1.stored().conversations[0].messages[1].content, repaired, 'a v1 reply is repaired');
  const fromBare = start({ storage: { [KEY]: JSON.stringify([{ role: 'user', content: 'q' }, { role: 'assistant', content, citations }]) } });
  assertEqual(fromBare.stored().conversations[0].messages[1].content, repaired, 'so is one in a bare array');
  const fromV2 = start({ storage: { [KEY]: old(2) } });
  assertEqual(fromV2.stored().conversations[0].messages[1].content, content, 'a v2 reply is left as it is');
}

console.log('\nloadHistory returns whether a save is needed');
{
  const v2 = JSON.stringify({ version: 2, sessionId: 's', messages: V2_MESSAGES });
  const ctx = start({ storage: { [KEY]: v2 } });
  assert(ctx.hooks.loadHistory() === false, 'a record init has already migrated needs no save');
  const legacy = start();
  legacy.localStorage.setItem(KEY, v2);
  assert(legacy.hooks.loadHistory() === true, 'a v2 record needs the migration saved');
  const none = start();
  none.localStorage.removeItem(KEY);
  assert(none.hooks.loadHistory() === false, 'no record needs none');
  assertEqual(none.hooks.getMessages().length, 1, 'and shows the greeting');
}

console.log('\nan empty storage shows the greeting and writes nothing until there is something to save');
{
  const ctx = start();
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'the greeting');
  assert(ctx.raw() === null, 'nothing is written by loading');
  assert(typeof ctx.hooks.getActiveConversation().id === 'string', 'but there is an active conversation to save into');
}

// ---------------------------------------------------------------------------
// The store's shape and its repair
// ---------------------------------------------------------------------------

console.log('\na v3 record with unusable entries is cleaned and written back');
{
  const good = conversation(1);
  const record = JSON.stringify({
    version: 3,
    activeId: 'missing',
    conversations: [good, good, { id: '<bad>', messages: [] }, 'nope', conversation(2, { context: 'search', updatedAt: 'x' })],
  });
  const ctx = start({ storage: { [KEY]: record } });
  const stored = ctx.stored();
  assertEqual(stored.conversations.map((c) => c.id).sort(), ['conv-1', 'conv-2'], 'duplicates and invalid entries are gone');
  assert(stored.conversations.some((c) => c.id === stored.activeId), 'the active id names a conversation that exists');
  const two = stored.conversations.find((c) => c.id === 'conv-2');
  assertEqual(two.context, null, 'a bare-string context is not a context');
  assert(Number.isFinite(two.updatedAt), 'a bad timestamp is replaced');
}

console.log('\na save that changes nothing does not make the conversation newer');
{
  const record = envelope([conversation(1), conversation(2)], 'conv-1');
  const ctx = start({ storage: { [KEY]: record } });
  ctx.hooks.saveHistory();
  const stored = ctx.stored();
  assertEqual(stored.conversations.find((c) => c.id === 'conv-1').updatedAt, 1001, 'updatedAt is kept');
  const voted = ctx.hooks.getMessages().map((m, i, all) => (i === all.length - 1 ? { ...m, feedback: 'up', feedbackCommitted: true } : m));
  ctx.hooks.setMessages(voted);
  ctx.hooks.saveHistory();
  const afterVote = ctx.stored().conversations.find((c) => c.id === 'conv-1');
  assertEqual(afterVote.updatedAt, 1001, 'a committed vote does not move it either');
  assertEqual(afterVote.messages.at(-1).feedback, 'up', 'though the vote itself is stored');
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'One more' }]);
  ctx.hooks.saveHistory();
  assert(ctx.stored().conversations[0].updatedAt > 1001, 'a new message moves it to the top');
  assertEqual(ctx.stored().conversations[0].id, 'conv-1', 'which is the active conversation');
}

console.log('\nthe pure functions: id fallback, title, context');
{
  const { conversationStore: store } = start().hooks;
  assert(/^[0-9a-f-]{36}$/.test(store.newConversationId()), 'an id is a random UUID where crypto has one');
  assert(/^c-[a-z0-9]+-[a-z0-9]+$/.test(store.newConversationId({})), 'and a fallback where it has not');
  assert(/^c-/.test(store.newConversationId({ randomUUID() { throw new Error('insecure context'); } })), 'also when randomUUID throws');
  assert(store.newConversationId({}) !== store.newConversationId({}), 'fallback ids differ');
  assertEqual(store.conversationTitle([]), '', 'no messages: no title');
  assertEqual(store.conversationTitle([{ role: 'assistant', content: 'Hi' }]), '', 'no user message: no title');
  assertEqual(store.conversationTitle([{ role: 'user', content: '  spaced \n out  ' }]), 'spaced out', 'whitespace is trimmed and collapsed');
  assertEqual(store.conversationTitle([{ role: 'user', content: 'x'.repeat(200) }]).length, 60, 'a long one is cut to 60');
}

// ---------------------------------------------------------------------------
// Eviction
// ---------------------------------------------------------------------------

console.log('\nthe 31st conversation evicts the oldest by updatedAt');
{
  const thirty = Array.from({ length: 30 }, (_, i) => conversation(i + 1));
  // Stored out of order, so the test does not rely on the oldest being first.
  const shuffled = [...thirty].reverse();
  const ctx = start({ storage: { [KEY]: envelope(shuffled, 'conv-30') } });
  assertEqual(ctx.stored().conversations.length, 30, 'thirty are kept as they are');
  const id = ctx.hooks.startConversation({ context: { type: 'search' } });
  assert(ctx.stored().conversations.some((c) => c.id === 'conv-1'), 'a new, empty conversation does not cost the oldest its place');
  assertEqual(ctx.stored().conversations.filter((c) => c.id.startsWith('conv-')).length, 30, 'all thirty real ones are still stored');
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'The 31st' }]);
  ctx.hooks.saveHistory();
  const stored = ctx.stored();
  assertEqual(stored.conversations.length, 30, 'still thirty after the 31st');
  assert(!stored.conversations.some((c) => c.id === 'conv-1'), 'the oldest, conv-1, is gone');
  assert(stored.conversations.some((c) => c.id === 'conv-2'), 'the next oldest is kept');
  assertEqual(stored.activeId, id, 'and the new one is the active one');
  const times = stored.conversations.map((c) => c.updatedAt);
  assertEqual(times, [...times].sort((a, b) => b - a), 'newest first');
}

console.log('\nan active conversation older than all the others survives');
{
  const thirtyOne = Array.from({ length: 31 }, (_, i) => conversation(i + 1));
  const ctx = start({ storage: { [KEY]: envelope(thirtyOne, 'conv-1') } });
  const stored = ctx.stored();
  assertEqual(stored.conversations.length, 30, 'cut to thirty on load');
  assert(stored.conversations.some((c) => c.id === 'conv-1'), 'the active, oldest, conversation is kept');
  assert(!stored.conversations.some((c) => c.id === 'conv-2'), 'the oldest of the others goes instead');
  assertEqual(ctx.hooks.getActiveConversation().id, 'conv-1', 'and it is the one in use');
  ctx.hooks.saveHistory();
  assert(ctx.stored().conversations.some((c) => c.id === 'conv-1'), 'a save does not evict it either');
  assertEqual(ctx.stored().conversations.length, 30, 'thirty');
  // Adding a 31st while the active one is the oldest.
  const store = ctx.hooks.conversationStore;
  const trimmed = store.trimConversations([...stored.conversations, conversation(99)], 'conv-1');
  assert(trimmed.some((c) => c.id === 'conv-1') && trimmed.length === 30, 'trimConversations keeps the active one too');
}

console.log('\nan empty conversation never takes one of the thirty places');
{
  const thirty = Array.from({ length: 30 }, (_, i) => conversation(i + 1));
  const ctx = start({ storage: { [KEY]: envelope(thirty, 'conv-30') } });
  const empty = ctx.hooks.startConversation();
  assertEqual(ctx.stored().activeId, empty, 'the empty one is the active one');
  assertEqual(ctx.stored().conversations.filter((c) => c.id.startsWith('conv-')).length, 30, 'with all thirty real conversations beside it');
  assert(ctx.hooks.activateConversation('conv-30') === true, 'going back to a saved conversation works');
  const ids = ctx.stored().conversations.map((c) => c.id);
  assertEqual(ids.length, 30, 'the empty one is dropped, leaving thirty');
  assert(ids.includes('conv-1') && !ids.includes(empty), 'conv-1 is still there, the empty one is not');
  // resetChat replaces the active one with an empty one: still no eviction.
  ctx.q('.osa-reset-btn').click();
  const afterReset = ctx.stored().conversations.map((c) => c.id);
  assert(afterReset.includes('conv-1'), 'resetChat does not evict the oldest either');
  assert(!afterReset.includes('conv-30'), 'while it discards the active one');
  assertEqual(afterReset.filter((id) => id.startsWith('conv-')).length, 29, 'leaving the other 29');
  // The pure function: empty conversations are not counted, the active one is kept.
  const store = ctx.hooks.conversationStore;
  const empties = [conversation(101, { messages: [{ role: 'assistant', content: GREETING }] }), conversation(102, { messages: [] })];
  const trimmed = store.trimConversations([...thirty, ...empties], 'conv-101');
  assertEqual(trimmed.length, 31, 'trimConversations: thirty real plus the empty active one');
  assert(trimmed.some((c) => c.id === 'conv-101') && !trimmed.some((c) => c.id === 'conv-102'), 'an empty one that is not active is dropped');
}

// ---------------------------------------------------------------------------
// Round trip
// ---------------------------------------------------------------------------

console.log('\nsave then load returns identical messages, with no in-page-only field and no images');
{
  const ctx = start();
  const withTransient = [
    { role: 'assistant', content: GREETING, citations: [], executions: [] },
    { role: 'user', content: 'plot it', dataset: 'nm000103' },
    {
      role: 'assistant', content: 'here', citations: [], _responseId: 'response-1', _feedbackCommitting: true,
      _feedbackJustOpened: true, _usageSoFar: { input_tokens: 1 }, _usageRuns: 1, feedbackDraft: 'half-typed',
      executions: [{
        ...RUN, images: [{ mime: 'image/png', data_base64: 'iVBORw0KGgo=' }],
        _editing: true, _draft: 'x', _runningLocal: false, _localResult: {}, _codeOpen: true,
      }],
    },
  ];
  ctx.hooks.setMessages(withTransient);
  ctx.hooks.setSessionId('session-rt');
  ctx.hooks.saveHistory();
  const saved = activeOf(ctx.stored());
  const underscored = JSON.stringify(saved.messages).match(/"_[A-Za-z]+"/g);
  assert(!underscored, `nothing underscored is stored${underscored ? ` (found ${underscored})` : ''}`);
  assert(!JSON.stringify(saved.messages).includes('feedbackDraft'), 'nor the unsent draft');
  assertEqual(saved.messages[2].executions[0].images, [], 'the stored run has no images');

  const expected = [
    { role: 'assistant', content: GREETING, citations: [], executions: [] },
    { role: 'user', content: 'plot it', dataset: 'nm000103' },
    { role: 'assistant', content: 'here', citations: [], executions: [{ ...RUN, images: [] }] },
  ];
  assertEqual(saved.messages, expected, 'what is stored is the input minus its in-page fields, dataset, run code and output kept');
  ctx.hooks.setMessages([]);
  ctx.hooks.setSessionId(null);
  const needsSave = ctx.hooks.loadHistory();
  assert(needsSave === false, 'a clean v3 record needs no re-save');
  assertEqual(ctx.hooks.getMessages(), expected, 'the messages come back as the expected literal');
  assertEqual(ctx.hooks.getSessionId(), 'session-rt', 'with the session');
  const loaded = ctx.hooks.getMessages();
  assert(!JSON.stringify(loaded).match(/"_[A-Za-z]+"/), 'no transient field comes back');
  assertEqual(loaded[2].executions[0].images, [], 'and images are empty');
  assertEqual(loaded[2].executions[0].code, RUN.code, 'while the run\'s code is kept');
  assertEqual(activeOf(ctx.stored()).title, 'plot it', 'the title is the first user message');
  assertEqual(activeOf(ctx.stored()).context, null, 'the context is not guessed on a save');
}

// ---------------------------------------------------------------------------
// Failure: unreadable records are never overwritten
// ---------------------------------------------------------------------------

console.log('\ncorrupt JSON is left byte-identical, with a notice');
{
  const corrupt = '{"version": 3, "conversations": [{"id": "a", "mess';
  const ctx = start({ storage: { [KEY]: corrupt } });
  assert(ctx.raw() === corrupt, 'storage is byte-identical after load');
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'the widget shows the greeting');
  assert(ctx.hooks.loadHistory() === false, 'and loadHistory asks for no save');
  assert(ctx.raw() === corrupt, 'still byte-identical');
  const shown = ctx.notice();
  assert(/corrupted/.test(shown), 'the reader is told it is corrupted');
  assert(shown.includes(`${KEY}-unreadable`), 'and where a copy will be kept');
  // The first message the reader sends is saved: the unreadable record is kept aside first.
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'First message' }]);
  ctx.hooks.saveHistory();
  assert(ctx.raw() !== corrupt && ctx.stored().version === 3, 'the first save then writes a fresh record');
  assert(ctx.localStorage.getItem(`${KEY}-unreadable`) === corrupt, 'after copying the unreadable one, byte for byte');
}

console.log('\nif the copy of an unreadable record fails for a reason other than a full storage, it is not overwritten');
{
  const corrupt = '{"version": 3, "conversations": [{"id": "a", "mess';
  const probe = start();
  const { storage, control } = failingStorage(probe.window.localStorage);
  probe.window.localStorage.setItem(KEY, corrupt);
  const ctx = start({ shared: storage });
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'x' }]);
  control.failKey = `${KEY}-unreadable`;
  control.failName = 'SecurityError';
  const originalError = console.error;
  console.error = () => {};
  let thrown = null;
  try {
    ctx.hooks.saveHistory();
  } catch (e) {
    thrown = e;
  } finally {
    console.error = originalError;
  }
  assert(thrown !== null, 'the save reports that it did not happen');
  assert(probe.window.localStorage.getItem(KEY) === corrupt, 'and the record is byte-identical');
}

console.log('\nif the storage is too full to hold the copy, the record is still replaced and the reader is told');
{
  const corrupt = '{"version": 3, "conversations": [{"id": "a", "mess';
  const probe = start();
  const { storage, control } = failingStorage(probe.window.localStorage);
  probe.window.localStorage.setItem(KEY, corrupt);
  const ctx = start({ shared: storage });
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'x' }]);
  control.failKey = `${KEY}-unreadable`;
  ctx.hooks.saveHistory();
  assertEqual(JSON.parse(probe.window.localStorage.getItem(KEY)).version, 3, 'the save goes through, so the widget can save again');
  assert(probe.window.localStorage.getItem(`${KEY}-unreadable`) === null, 'with no copy kept');
  assert(/could not be kept aside/.test(ctx.notice()), 'and the notice, shown when the panel opens, says the copy was not kept');
}

console.log('\na version 3 record without a conversations list is reported, kept aside, then replaced');
{
  const damaged = JSON.stringify({ version: 3, activeId: 'x', conversations: 'nope' });
  const ctx = start({ storage: { [KEY]: damaged } });
  assert(ctx.raw() === damaged, 'storage is byte-identical after load');
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'the widget shows the greeting');
  assert(/corrupted/.test(ctx.notice()), 'and the reader is told it is corrupted');
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'First message' }]);
  ctx.hooks.saveHistory();
  assert(ctx.localStorage.getItem(`${KEY}-unreadable`) === damaged, 'the damaged record is copied aside on the first save');
  assertEqual(ctx.stored().conversations.length, 1, 'and replaced by a record with the new conversation');
}

console.log('\na version above 3 is left byte-identical, with a notice, and never overwritten');
{
  const future = JSON.stringify({ version: 99, activeId: 'x', conversations: [conversation(1)], extra: { kept: true } });
  const ctx = start({ storage: { [KEY]: future } });
  assert(ctx.raw() === future, 'storage is byte-identical after load');
  assert(/newer version/.test(ctx.notice()), 'the reader is told it is from a newer version');
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'hello' }]);
  let thrown = null;
  const originalError = console.error;
  console.error = () => {};
  try {
    ctx.hooks.saveHistory();
  } catch (e) {
    thrown = e;
  } finally {
    console.error = originalError;
  }
  assert(thrown !== null, 'a save reports that it did not happen');
  assert(ctx.raw() === future, 'and storage is still byte-identical');
  const legacyFuture = JSON.stringify({ version: 4, messages: [{ role: 'user', content: 'x' }], sessionId: 's' });
  const other = start({ storage: { [KEY]: legacyFuture } });
  assert(other.raw() === legacyFuture, 'a future version in the old shape is left alone as well');
  assert(other.hooks.getMessages().length === 1 && other.hooks.getMessages()[0].role === 'assistant', 'and not read as current');
}

console.log('\nnon-record JSON is not an error and is not a conversation');
{
  const ctx = start({ storage: { [KEY]: '{"hello": 1}' } });
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'the greeting');
  assert(ctx.notice() === '', 'with no notice');
}

// ---------------------------------------------------------------------------
// Failure: a full storage
// ---------------------------------------------------------------------------

console.log('\na write that hits the storage limit once evicts the oldest inactive conversation and succeeds');
{
  const probe = start();
  const { storage, control } = failingStorage(probe.window.localStorage);
  const seeded = [conversation(1), conversation(2), conversation(3), conversation(4)];
  probe.window.localStorage.setItem(KEY, envelope(seeded, 'conv-1'));
  const ctx = start({ shared: storage });
  assertEqual(ctx.hooks.getActiveConversation().id, 'conv-1', 'sanity: conv-1, the oldest, is the active one');
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'a larger conversation' }]);
  control.failNext = 1;
  control.attempts = 0;
  ctx.hooks.saveHistory();
  const stored = JSON.parse(probe.window.localStorage.getItem(KEY));
  assertEqual(control.attempts, 2, 'one refused write and one that went through');
  assertEqual(stored.conversations.map((c) => c.id).sort(), ['conv-1', 'conv-3', 'conv-4'], 'the oldest inactive, conv-2, was given up');
  assert(activeOf(stored).messages.some((m) => m.content === 'a larger conversation'), 'and the new message is saved');
  // The panel is closed: the notice waits for the next time it opens.
  const shown = ctx.notice();
  assert(/storage was full.*oldest saved conversation was removed/.test(shown),
    'and the reader is told, when the panel opens, that the oldest saved conversation was removed');
  assert(!/NOT saved/.test(shown), 'which does not say the chat was lost');
}

console.log('\nwith the panel open, the eviction notice stays until dismissed');
{
  const probe = start();
  const { storage, control } = failingStorage(probe.window.localStorage);
  probe.window.localStorage.setItem(KEY, envelope([conversation(1), conversation(2), conversation(3)], 'conv-1'));
  const ctx = start({ shared: storage });
  ctx.q('.osa-chat-button').click();
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'a larger conversation' }]);
  control.failNext = 1;
  ctx.hooks.saveHistory();
  const notice = ctx.q('.osa-error');
  assert(notice.style.display === 'block' && /oldest saved conversation was removed/.test(notice.textContent), 'the notice is shown at once');
  assert(notice.classList.contains('osa-error-persistent'), 'and is the kind that does not time out');
}

console.log('\na storage that stays full gives up the others one by one, then reports it');
{
  const probe = start();
  const { storage, control } = failingStorage(probe.window.localStorage);
  probe.window.localStorage.setItem(KEY, envelope([conversation(1), conversation(2), conversation(3)], 'conv-3'));
  const ctx = start({ shared: storage });
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'x' }]);
  control.failAlways = true;
  control.attempts = 0;
  const originalError = console.error;
  console.error = () => {};
  let thrown = null;
  try {
    ctx.hooks.saveHistory();
  } catch (e) {
    thrown = e;
  } finally {
    console.error = originalError;
  }
  assertEqual(thrown && thrown.name, 'QuotaExceededError', 'the save fails with the storage error');
  assertEqual(control.attempts, 3, 'after trying with each fewer conversation until only the active one was left');
  assert(/Storage full/.test(ctx.q('.osa-error').textContent), 'and the reader is told the storage is full');
  assert(JSON.parse(probe.window.localStorage.getItem(KEY)).conversations.length === 3, 'nothing was written, so nothing was lost');
}

// ---------------------------------------------------------------------------
// Two widgets, one storage
// ---------------------------------------------------------------------------

console.log('\ntwo widgets on one storage keep both conversations');
{
  const a = start();
  a.hooks.setMessages([{ role: 'assistant', content: GREETING }, { role: 'user', content: 'From tab A' }]);
  a.hooks.saveHistory();
  const idA = a.hooks.getActiveConversation().id;

  const b = start({ shared: a.localStorage });
  assertEqual(b.hooks.getActiveConversation().id, idA, 'a second tab opens the stored active conversation');
  const idB = b.hooks.startConversation({ context: { type: 'search' } });
  b.hooks.setMessages([{ role: 'assistant', content: GREETING }, { role: 'user', content: 'From tab B' }]);
  b.hooks.saveHistory();
  assertEqual(a.stored().conversations.map((c) => c.id).sort(), [idA, idB].sort(), 'both are stored');

  // Tab A, which has never heard of B's conversation, saves again.
  a.hooks.setMessages([...a.hooks.getMessages(), { role: 'assistant', content: 'Answer for A', citations: [], executions: [] }]);
  a.hooks.saveHistory();
  const stored = a.stored();
  assertEqual(stored.conversations.map((c) => c.id).sort(), [idA, idB].sort(), 'tab A\'s save did not drop B\'s conversation');
  assertEqual(stored.conversations.find((c) => c.id === idB).messages.at(-1).content, 'From tab B', 'B\'s messages are as B saved them');
  assertEqual(stored.conversations.find((c) => c.id === idB).context, { type: 'search' }, 'and its context');
  assertEqual(stored.conversations.find((c) => c.id === idA).messages.at(-1).content, 'Answer for A', 'A\'s are A\'s newest');
  assertEqual(stored.activeId, idA, 'the active id is the tab that saved last');

  // resetChat in A discards A's conversation and leaves B's.
  a.q('.osa-reset-btn').disabled = false; // enabled when a reply finishes; these messages were set directly
  a.q('.osa-reset-btn').click();
  const afterReset = a.stored();
  assert(!afterReset.conversations.some((c) => c.id === idA), 'resetChat discards the active conversation');
  assert(afterReset.conversations.some((c) => c.id === idB), 'and leaves the other tab\'s');
  assertEqual(afterReset.conversations.length, 2, 'the discarded one is replaced by an empty one');
  assertEqual(activeOf(afterReset).messages, [{ role: 'assistant', content: GREETING }], 'which is just the greeting');
  assertEqual(a.hooks.getSessionId(), null, 'with no session');
  assertEqual(a.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'and the working copy is the same');
}

console.log('\nthe pop-out shares the storage, and so the conversations');
{
  const page = start();
  page.hooks.setMessages([{ role: 'assistant', content: GREETING }, { role: 'user', content: 'In the page' }]);
  page.hooks.saveHistory();
  const idPage = page.hooks.getActiveConversation().id;
  page.hooks.startConversation();
  const other = page.hooks.getActiveConversation().id;
  page.hooks.setMessages([{ role: 'assistant', content: GREETING }, { role: 'user', content: 'Another chat' }]);
  page.hooks.saveHistory();
  const popout = start({ shared: page.localStorage, config: { fullscreen: true } });
  assertEqual(popout.hooks.getActiveConversation().id, other, 'it opens what the page last had active');
  popout.hooks.setMessages([...popout.hooks.getMessages(), { role: 'user', content: 'Typed in the pop-out' }]);
  popout.hooks.saveHistory();
  assertEqual(page.stored().conversations.map((c) => c.id).sort(), [idPage, other].sort(), 'both conversations are still there');
}

// ---------------------------------------------------------------------------
// The API: start, list, activate, delete
// ---------------------------------------------------------------------------

console.log('\nstartConversation, listConversations, activateConversation, deleteConversation');
{
  const ctx = start();
  const { hooks } = ctx;
  hooks.setMessages([{ role: 'assistant', content: GREETING }, { role: 'user', content: 'First chat', dataset: 'nm000103' }]);
  hooks.setSessionId('session-first');
  hooks.saveHistory();
  const first = hooks.getActiveConversation().id;

  const second = hooks.startConversation({ context: { type: 'dataset', id: 'nm000132' } });
  assert(second && second !== first, 'startConversation returns the new conversation\'s id');
  assertEqual(hooks.getActiveConversation().id, second, 'which is now active');
  assertEqual(hooks.getActiveConversation().context, { type: 'dataset', id: 'nm000132' }, 'in the context given');
  assertEqual(hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'with only the greeting');
  assertEqual(hooks.getSessionId(), null, 'and no session');
  assertEqual(ctx.stored().activeId, second, 'the store says so too');
  assert(ctx.stored().conversations.some((c) => c.id === first), 'and keeps the first');

  hooks.setMessages([{ role: 'assistant', content: GREETING }, { role: 'user', content: 'Second chat' }]);
  hooks.setSessionId('session-second');
  hooks.saveHistory();
  const listed = hooks.listConversations();
  assertEqual(listed.map((c) => c.id), [second, first], 'listConversations is newest first');
  assertEqual(listed.map((c) => c.active), [true, false], 'and marks the active one');
  assertEqual(listed.map((c) => c.title), ['Second chat', 'First chat'], 'with titles');
  assertEqual(listed.map((c) => c.context), [{ type: 'dataset', id: 'nm000132' }, null],
    'and their contexts: the one given, and none for a chat started without (a save does not guess one from the messages)');

  assert(hooks.activateConversation(first) === true, 'activateConversation switches to the first');
  assertEqual(hooks.getMessages().map((m) => m.content), [GREETING, 'First chat'], 'its messages are the working copy');
  assertEqual(hooks.getSessionId(), 'session-first', 'and its session');
  assertEqual(ctx.stored().activeId, first, 'the store records it');
  assert(hooks.activateConversation('no-such-id') === false, 'an unknown id changes nothing');
  assert(hooks.activateConversation('<b>') === false, 'nor does an invalid one');
  assertEqual(hooks.getActiveConversation().id, first, 'the active one is still the first');

  assert(hooks.deleteConversation(second) === true, 'a saved conversation can be deleted');
  assertEqual(ctx.stored().conversations.map((c) => c.id), [first], 'and is gone from storage');
  assert(hooks.deleteConversation(second) === false, 'a second delete finds nothing');
  assertEqual(hooks.getActiveConversation().id, first, 'the active one is unchanged');

  assert(hooks.deleteConversation(first) === true, 'the active one can be deleted too');
  assert(hooks.getActiveConversation().id !== first, 'an empty conversation takes its place');
  assertEqual(hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'with the greeting');
  assertEqual(ctx.stored().conversations.map((c) => c.id), [hooks.getActiveConversation().id], 'and it is the only one stored');
}

console.log('\nan empty conversation is not kept when another takes over');
{
  const ctx = start();
  const { hooks } = ctx;
  hooks.setMessages([{ role: 'assistant', content: GREETING }, { role: 'user', content: 'Real chat' }]);
  hooks.saveHistory();
  const real = hooks.getActiveConversation().id;
  const empty = hooks.startConversation();
  hooks.startConversation({ context: { type: 'search' } });
  const ids = ctx.stored().conversations.map((c) => c.id);
  assert(!ids.includes(empty), 'a conversation that was only the greeting is dropped');
  assert(ids.includes(real), 'while the one with a message in it stays');
  assertEqual(ids.length, 2, 'with the new, empty, active one');
}

console.log('\na context is an object: null, a dataset, or search; anything else is null');
{
  const { hooks } = start();
  const contexts = [
    [{ type: 'dataset', id: 'search' }, { type: 'dataset', id: 'search' }],
    [{ type: 'search' }, { type: 'search' }],
    [undefined, null],
    ['search', null],
    [{ type: 'dataset', id: '<b>' }, null],
    [{ type: 'dataset' }, null],
    [{ type: 'other' }, null],
  ];
  for (const [given, expected] of contexts) {
    hooks.startConversation(given === undefined ? {} : { context: given });
    assertEqual(hooks.getActiveConversation().context, expected, `${JSON.stringify(given)} is stored as ${JSON.stringify(expected)}`);
  }
}

console.log('\nresetChat keeps today\'s meaning: an empty chat, the others left');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation(1), conversation(2), conversation(3)], 'conv-3') } });
  ctx.q('.osa-reset-btn').click();
  const stored = ctx.stored();
  assertEqual(stored.conversations.map((c) => c.id).filter((id) => id.startsWith('conv-')).sort(), ['conv-1', 'conv-2'], 'the other two stay');
  assert(!stored.conversations.some((c) => c.id === 'conv-3'), 'the active one is gone');
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'the chat is empty');
  const reloaded = start({ storage: { [KEY]: ctx.raw() } });
  assertEqual(reloaded.hooks.getMessages().map((m) => m.content), [GREETING], 'and a reload opens that empty chat, not another');
}

console.log('\na conversation to be dropped stays dropped until a save succeeds');
{
  const probe = start();
  const { storage, control } = failingStorage(probe.window.localStorage);
  probe.window.localStorage.setItem(KEY, envelope([conversation(1), conversation(2), conversation(3)], 'conv-3'));
  const ctx = start({ shared: storage });
  control.failAlways = true;
  const originalError = console.error;
  console.error = () => {};
  let deleted;
  try {
    deleted = ctx.hooks.deleteConversation('conv-1');
  } finally {
    console.error = originalError;
  }
  assert(deleted === true, 'deleteConversation answers true: the delete is queued');
  assert(JSON.parse(probe.window.localStorage.getItem(KEY)).conversations.some((c) => c.id === 'conv-1'), 'the failed save left conv-1 in storage');
  control.failAlways = false;
  ctx.hooks.setMessages([...ctx.hooks.getMessages(), { role: 'user', content: 'Later' }]);
  ctx.hooks.saveHistory();
  const ids = JSON.parse(probe.window.localStorage.getItem(KEY)).conversations.map((c) => c.id).sort();
  assertEqual(ids, ['conv-2', 'conv-3'], 'a later save applies the delete');
}

console.log('\nthe conversation functions do nothing while a reply is on its way');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation(1), conversation(2)], 'conv-2') } });
  const { hooks } = ctx;
  hooks.setLoading(true);
  assert(hooks.startConversation() === null, 'startConversation returns null');
  assert(hooks.activateConversation('conv-1') === false, 'activateConversation returns false');
  assert(hooks.deleteConversation('conv-2') === false, 'deleteConversation of the active one returns false');
  assertEqual(hooks.getActiveConversation().id, 'conv-2', 'the active conversation is unchanged');
  assertEqual(ctx.stored().conversations.map((c) => c.id).sort(), ['conv-1', 'conv-2'], 'and so is storage');
  assert(hooks.deleteConversation('conv-1') === true, 'a saved conversation that is not active can still be deleted');
  hooks.setLoading(false);
  assert(hooks.startConversation() !== null, 'with the reply over, startConversation works again');
}

console.log('\nan open thumbs-down comment is sent before another conversation takes over');
{
  const down = (n) => conversation(n, {
    messages: [
      { role: 'assistant', content: GREETING },
      { role: 'user', content: `Question ${n}` },
      { role: 'assistant', content: `Answer ${n}`, citations: [], executions: [], feedback: 'down', feedbackDraft: 'it was wrong', requestId: `req-${n}` },
    ],
  });
  const swaps = {
    startConversation: (hooks) => hooks.startConversation(),
    activateConversation: (hooks) => hooks.activateConversation('conv-2'),
    'deleteConversation (active)': (hooks) => hooks.deleteConversation('conv-1'),
  };
  for (const [name, swap] of Object.entries(swaps)) {
    const ctx = start({ storage: { [KEY]: envelope([down(1), conversation(2)], 'conv-1') } });
    ctx.hooks.setMessages(ctx.hooks.getMessages().map((m) => ({ ...m })));
    swap(ctx.hooks);
    const sent = ctx.fetchLog.filter((call) => call.url.endsWith('/feedback'));
    assert(sent.length === 1 && sent[0].body.sentiment === 'down' && sent[0].body.comment === 'it was wrong', `${name} sends the open down-vote with its comment`);
  }
}

console.log('\nloadHistory resets the working copy whenever there is no active conversation');
{
  const ctx = start({ storage: { [KEY]: envelope([conversation(1)], 'conv-1') } });
  assertEqual(ctx.hooks.getActiveConversation().id, 'conv-1', 'sanity: a conversation is active');
  ctx.localStorage.setItem(KEY, '{"hello": 1}');
  ctx.hooks.loadHistory();
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'an unrecognized record leaves only the greeting');
  assert(ctx.hooks.getSessionId() === null, 'and no session');
  assert(ctx.hooks.getActiveConversation().id !== 'conv-1', 'and a new, empty conversation, not the stale one');
  ctx.localStorage.setItem(KEY, envelope([], null));
  ctx.hooks.loadHistory();
  assertEqual(ctx.hooks.getMessages(), [{ role: 'assistant', content: GREETING }], 'an empty v3 record does the same');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
