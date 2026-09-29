/**
 * What a pending reply is doing (#538), run against the real widget source.
 *
 * While a reply is pending the widget used to say only "Thinking..." or the community's
 * title. It now says what is happening, from the stream's own events: "Searching
 * datasets..." while a search runs (or while the model writes one, from the new
 * `tool_call` event), "Writing code..." while the model writes a call to run code,
 * "Analyzing results..." once a tool has answered, and after five seconds how long the
 * wait has lasted. Before any reply text is on screen that is the loading bubble's
 * label; once the reply has text, a later activity is a status line under that text,
 * in the same message.
 *
 * The property that matters most is the one a reader would notice first if it broke:
 * a status never makes a bubble. So every flow below is sampled every few milliseconds
 * while it runs, and each sample must hold that no status adds an assistant message,
 * that no assistant message is on the page with no text and no run record, that the
 * loading bubble and a status line are never there together, and that nothing is left
 * behind however the stream ends.
 *
 * What stands in: `fetch` answers with SSE fixtures the test feeds event by event,
 * the clock the elapsed time is read from (turned by hand, so no test waits real
 * seconds), and, for the browser-execution flow, the test worker the other widget
 * suites drive in place of Pyodide. The widget, its stream handler, its reveal and its
 * runtime controller are the real ones.
 *
 * Run with: bun frontend/test-widget-activity-status.js
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

let passed = 0;
let failed = 0;

const SUITE_TIMEOUT_MS = 120_000;
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
const STORAGE_KEY = 'osa-test-activity';

// ------------------------------------------------------------------ fixtures

/** A clock turned by hand: `every` intervals fire in order as time is advanced. */
function handClock() {
  let time = 1_000_000;
  let nextId = 1;
  const intervals = new Map();
  return {
    now: () => time,
    every: (fn, ms) => {
      const id = nextId++;
      intervals.set(id, { fn, ms, due: time + ms });
      return id;
    },
    cancel: (id) => { intervals.delete(id); },
    live: () => intervals.size,
    advance(ms) {
      const until = time + ms;
      for (;;) {
        const next = [...intervals.entries()].filter(([, t]) => t.due <= until).sort((a, b) => a[1].due - b[1].due)[0];
        if (!next) break;
        const [id, interval] = next;
        time = interval.due;
        interval.due += interval.ms;
        if (intervals.has(id)) interval.fn();
      }
      time = until;
    },
  };
}

/** An SSE response the test writes to one event at a time. */
function manualStream() {
  const encoder = new TextEncoder();
  let controller;
  const body = new ReadableStream({ start(c) { controller = c; } });
  return {
    response: new Response(body, { headers: { 'content-type': 'text/event-stream' } }),
    send(event) { controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`)); },
    close() { controller.close(); },
    fail(error) { controller.error(error); },
  };
}

const LOCAL_RUNTIME_CONFIG = { pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 60 } };
const LOCAL_TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];

/**
 * The widget in its own window, initialized, with `fetch` answering /chat and
 * /chat/resume from the streams a test queues, in order.
 */
function loadWidget({ matchMedia, runtime = false } = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      handleDisabledFileLoadingAsSuccess: runtime,
    },
  });
  window.__OSA_TEST__ = true;
  if (matchMedia) window.matchMedia = matchMedia;
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  const config = {
    default_model: 'm',
    offered_models: [],
    widget: { title: TITLE },
    client_tools: runtime ? LOCAL_TOOLS : [],
    runtime: runtime ? { python: LOCAL_RUNTIME_CONFIG } : null,
  };
  const queued = [];
  const requests = [];
  const fetch = async (url, init) => {
    const s = String(url);
    if (s.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (s.endsWith('/chat') || s.endsWith('/chat/resume')) {
      requests.push({ url: s, body: init && init.body });
      const next = queued.shift();
      if (!next) throw new Error(`no stream queued for ${s}`);
      if (next.held) await next.held;
      return next.response;
    }
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  const widget = window.OSAChatWidget;
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: STORAGE_KEY });
  widget.init();
  const clock = handClock();
  widget.__activity.setClock(clock);
  const container = window.document.querySelector('.osa-chat-widget');
  return {
    window, widget, container, clock, requests,
    api: widget.__browser,
    activity: widget.__activity,
    /**
     * Queue the stream the next /chat or /chat/resume request gets. A held one is
     * answered only once `release()` is called, as a request still on the network.
     */
    queue({ hold = false } = {}) {
      const stream = manualStream();
      if (hold) stream.held = new Promise((resolve) => { stream.release = resolve; });
      queued.push(stream);
      return stream;
    },
    /** Queue a whole response (an HTTP error, say), held until `release()`. */
    queueResponse(make) {
      const entry = { get response() { return make(); } };
      entry.held = new Promise((resolve) => { entry.release = resolve; });
      queued.push(entry);
      return entry;
    },
  };
}

/** Send a question through the real send button. */
function send(loaded, question = 'Which datasets are about attention?') {
  const input = loaded.container.querySelector('.osa-chat-input input');
  input.value = question;
  loaded.container.querySelector('.osa-send-btn').dispatchEvent(new loaded.window.Event('click', { bubbles: true }));
}

const settled = (loaded) => !loaded.container.querySelector('.osa-send-btn').disabled;

/**
 * Wait until the widget has handled every event sent on `stream` so far. The stream is
 * read in order, so a `session` event sent last, with an id of its own, is handled
 * last: once the widget holds that id, everything before it has been handled (and
 * anything it painted, it painted then). A reveal's paced redraws are the exception,
 * on a timer of their own, and are waited for by what they show.
 */
let barriers = 0;
async function handled(loaded, stream) {
  const id = `barrier-${++barriers}`;
  stream.send({ event: 'session', session_id: id });
  await waitUntil(() => loaded.api.getSessionId() === id, `the widget handled the events before ${id}`);
}

/** What the page shows of the reply's status, read as a reader would. */
function view(container) {
  const loading = container.querySelector('.osa-loading');
  const line = container.querySelector('.osa-activity-status');
  const text = (holder, selector) => {
    const el = holder && holder.querySelector(selector);
    return el ? el.textContent : null;
  };
  return {
    loading: text(loading, '.osa-loading-label'),
    loadingElapsed: text(loading, '.osa-status-elapsed'),
    line: text(line, '.osa-activity-label'),
    lineElapsed: text(line, '.osa-status-elapsed'),
    assistants: container.querySelectorAll('.osa-message.assistant').length,
  };
}

/** The text of the last assistant message on the page, or '' when there is none. */
function lastReplyText(container) {
  const content = container.querySelector('.osa-message.assistant:last-child .osa-message-content');
  return content ? content.textContent : '';
}

/**
 * The invariants, checked on one sample of the page. Returns what is wrong, if
 * anything, in words.
 */
function problemsIn(container, baseline) {
  const problems = [];
  const assistants = [...container.querySelectorAll('.osa-message.assistant')];
  if (assistants.length > baseline + 1) problems.push(`${assistants.length} assistant messages, ${baseline + 1} at most`);
  for (const message of assistants) {
    const content = message.querySelector('.osa-message-content');
    const text = content ? content.textContent.trim() : '';
    if (!text && !message.querySelector('.osa-execution-run')) {
      problems.push('an assistant message on the page with no text and no run record (an empty bubble)');
    }
  }
  const loading = container.querySelectorAll('.osa-loading');
  const lines = [...container.querySelectorAll('.osa-activity-status')];
  if (loading.length > 1) problems.push('two loading bubbles');
  if (loading.length && lines.length) problems.push('the loading bubble and a status line at once');
  if (lines.length > 1) problems.push('two status lines');
  for (const line of lines) {
    const message = line.closest('.osa-message.assistant');
    if (!message) {
      problems.push('a status line outside an assistant message');
      continue;
    }
    if (!message.querySelector('.osa-message-content').textContent.trim()) {
      problems.push('a status line under a message with no text');
    }
    // Below the content, above the sources and the feedback row.
    const children = [...message.children];
    const at = children.indexOf(line);
    const contentAt = children.findIndex((el) => el.classList.contains('osa-message-content'));
    const laterAt = children.findIndex((el) => el.classList.contains('osa-message-sources') || el.classList.contains('osa-message-feedback'));
    if (!(contentAt !== -1 && contentAt < at && (laterAt === -1 || at < laterAt))) {
      problems.push('a status line that is not between the content and the sources and feedback');
    }
  }
  return problems;
}

/** Sample the page every few milliseconds until stopped; collect every label seen. */
function sampler(container) {
  const baseline = container.querySelectorAll('.osa-message.assistant').length;
  const state = { samples: 0, mutations: 0, problems: [], loadingLabels: [], lineLabels: [], running: true };
  const note = (list, value) => {
    if (value !== null && list[list.length - 1] !== value) list.push(value);
  };
  const sample = () => {
    state.samples++;
    for (const problem of problemsIn(container, baseline)) {
      if (!state.problems.includes(problem)) state.problems.push(problem);
    }
    const now = view(container);
    note(state.loadingLabels, now.loading);
    note(state.lineLabels, now.line);
  };
  // Every batch of changes to the conversation, as the widget leaves it at the end of
  // a task (which is what a browser would paint), so a state that lasts one task
  // cannot hide between polls; and a poll besides, for what changes without a mutation.
  const window = container.ownerDocument.defaultView;
  const observer = new window.MutationObserver(() => {
    state.mutations++;
    sample();
  });
  observer.observe(container.querySelector('.osa-chat-messages'), { childList: true, subtree: true, characterData: true });
  (async () => {
    while (state.running) {
      sample();
      await sleep(2);
    }
  })();
  return {
    state,
    async stop() {
      // A last sample after everything has settled.
      await sleep(10);
      sample();
      state.running = false;
      observer.disconnect();
      return state;
    },
  };
}

/** Count full redraws of the conversation (renderMessages clears it with innerHTML). */
function countRedraws(window, container) {
  const el = container.querySelector('.osa-chat-messages');
  let proto = Object.getPrototypeOf(el);
  while (proto && !Object.getOwnPropertyDescriptor(proto, 'innerHTML')) proto = Object.getPrototypeOf(proto);
  const descriptor = Object.getOwnPropertyDescriptor(proto, 'innerHTML');
  const counter = { redraws: 0 };
  Object.defineProperty(el, 'innerHTML', {
    configurable: true,
    get() { return descriptor.get.call(this); },
    set(value) { counter.redraws++; descriptor.set.call(this, value); },
  });
  return counter;
}

// Long enough to read, and a single chunk, so the paced reveal shows it within a tick.
const ANSWER = 'Three datasets match: nm000103, nm000132 and nm000140.';
const REVEAL_MS = 250;
// Text a reader cannot see, as reasoning models stream it before a tool call (#538).
const WHITESPACE = '\n\n';

/** Nothing of a status is left: no element, nothing ticking, no activity. */
function assertNothingLeft(loaded, label) {
  const { container, activity, clock } = loaded;
  assertEqual(
    {
      loading: container.querySelectorAll('.osa-loading').length,
      lines: container.querySelectorAll('.osa-activity-status').length,
      ticking: activity.ticking(),
      intervals: clock.live(),
      activity: activity.state().activity,
    },
    { loading: 0, lines: 0, ticking: false, intervals: 0, activity: null },
    `${label}: no loading bubble, no status line, no timer and no activity is left`,
  );
}

console.log('='.repeat(60));
console.log('Widget: what a pending reply is doing (#538)');
console.log('='.repeat(60));

// ---------------------------------------------------------------- the labels

console.log('\nthe label a tool call gets, from its name alone');
{
  const { activity: A } = loadWidget();
  const label = (name, phase = 'running', community = '') => A.classifyToolActivity(name, phase, community).label;
  const kind = (name) => A.classifyToolActivity(name, 'running', '').kind;
  assertEqual(label('nemar_search_datasets'), 'Searching datasets...', 'nemar_search_datasets: the server prefix is dropped');
  assertEqual(label('nemar_describe_dataset'), 'Looking up dataset...', 'nemar_describe_dataset');
  assertEqual(label('nemar_list_recordings'), 'Listing recordings...', 'nemar_list_recordings');
  assertEqual(label('nemar_get_events'), 'Looking up events...', 'nemar_get_events');
  assertEqual(label('nemar_read_window'), 'Reading recording data...', 'nemar_read_window');
  assertEqual(label('nemar_render_overview'), 'Rendering...', 'nemar_render_overview');
  assertEqual(label('retrieve_nwb_docs', 'running', 'nwb'), 'Looking up documentation...', 'retrieve_nwb_docs in the NWB widget: its own community is left out');
  assertEqual(label('retrieve_nwb_docs'), 'Looking up NWB documentation...', 'retrieve_nwb_docs elsewhere names NWB');
  assertEqual(label('search_hed_papers_live', 'running', 'hed'), 'Searching papers...', 'search_hed_papers_live');
  assertEqual(label('search_eeglab_code_docs', 'running', 'eeglab'), 'Searching code documentation...', 'search_eeglab_code_docs: a search about code is a search');
  assertEqual(label('list_hed_recent', 'running', 'hed'), 'Listing recent activity...', 'list_hed_recent');
  assertEqual(label('search_hed_faq', 'running', 'hed'), 'Searching FAQ...', 'search_hed_faq');
  assertEqual(label('validate_hed_string', 'running', 'hed'), 'Validating string...', 'validate_hed_string');
  assertEqual(label('lookup_bep'), 'Looking up BEP...', 'lookup_bep');
  assertEqual(label('get_full_output'), 'Looking up full output...', 'get_full_output');
  assertEqual(label('fetch_current_page'), 'Fetching current page...', 'fetch_current_page');
  assertEqual(label('execute_code', 'writing'), 'Writing code...', 'execute_code while the model writes it');
  assertEqual(label('execute_code', 'running'), 'Running code...', 'execute_code while it runs');
  assertEqual(label('run_python', 'writing'), 'Writing code...', 'run_python is code too');
  assertEqual(label('python_repl'), 'Running code...', 'a name with no verb but python in it is code');
  assertEqual(label('searchDatasets'), 'Searching datasets...', 'a camelCase name reads the same');
  assertEqual(label('look_up_thing'), 'Looking up thing...', 'look up, as two words');
  assertEqual(label('frobnicate_widgets'), 'Working...', 'an unknown verb is Working...');
  assertEqual(label(''), 'Working...', 'an empty name is Working...');
  assertEqual(label(undefined), 'Working...', 'no name at all is Working...');
  assertEqual(label({ toString: () => 'search_x' }), 'Working...', 'a name that is not a string is Working...');
  assertEqual(label('search'), 'Searching...', 'a verb alone');
  assertEqual([kind('nemar_search_datasets'), kind('execute_code'), kind('nemar_render_overview'), kind('validate_x'), kind('zzz')],
    ['search', 'code', 'render', 'work', 'other'], 'each family has its kind');
  const long = label(`search_${'verylongword_'.repeat(20)}`);
  assert(long.length <= 'Searching '.length + 40 + 3, `a very long name gives a short label (${long.length} characters)`);
  const odd = ['search__x', 'GET_Stuff', 'list-things', 'read.file', 'search_<img src=x>', 'describe_ümlaut', 'fetch_123'];
  assert(odd.every((n) => /^[A-Z][A-Za-z0-9 ]*\.\.\.$/.test(label(n))),
    `every label is plain words ending in "..." (${odd.map((n) => label(n)).join(' | ')})`);
  assert(odd.concat(['nemar_search_datasets', 'execute_code']).every((n) => !label(n).includes('_')), 'no label shows a raw tool name');
}

// ---------------------------------------------------------- label transitions

console.log('\na search: the loading label says what is happening, in order, without a redraw');
{
  const loaded = loadWidget();
  const { container, window } = loaded;
  await sleep(20); // the community config
  const stream = loaded.queue();
  const watch = sampler(container);
  send(loaded);
  await waitUntil(() => view(container).loading !== null, 'the loading bubble');
  const seen = [view(container).loading];
  const step = async (event, wait = 25) => {
    stream.send(event);
    await sleep(wait);
    seen.push(view(container).loading);
  };
  await step({ event: 'session', session_id: 's' });
  await step({ event: 'thinking' });
  await step({ event: 'tool_call', name: 'nemar_search_datasets' });
  const labelNode = container.querySelector('.osa-loading-label');
  const counter = countRedraws(window, container);
  await step({ event: 'tool_start', name: 'nemar_search_datasets', input: { query: 'SECRET-QUERY-TEXT' } });
  await step({ event: 'tool_end', name: 'nemar_search_datasets', output: '3 datasets' });
  await step({ event: 'thinking' });
  assertEqual(counter.redraws, 0, 'the label changed in place: no redraw of the conversation');
  assert(container.querySelector('.osa-loading-label') === labelNode, 'the same label element, so a screen reader hears it change');
  assertEqual(seen, [TITLE, TITLE, 'Thinking...', 'Searching datasets...', 'Searching datasets...', 'Analyzing results...', 'Analyzing results...'],
    'title, then Thinking..., then Searching datasets..., then Analyzing results... (a later thinking does not undo it)');
  stream.send({ event: 'content', content: ANSWER });
  await sleep(REVEAL_MS);
  const during = view(container);
  assertEqual([during.loading, during.line], [null, null], 'the first text replaces the loading bubble, and no status line is left');
  stream.send({ event: 'done', session_id: 's', content: ANSWER, citations: [] });
  stream.close();
  await waitUntil(() => settled(loaded), 'the send settles');
  const run = await watch.stop();
  assertEqual(run.problems, [], `no invariant broke in ${run.samples} samples`);
  assert(!container.textContent.includes('SECRET-QUERY-TEXT'), 'a tool\'s input never reaches the page');
  assertNothingLeft(loaded, 'after done');
  const saved = window.localStorage.getItem(STORAGE_KEY) || '';
  assert(saved.includes(ANSWER) && !/Searching|Analyzing|Thinking|"activity"/.test(saved),
    'the saved conversation has the answer and nothing of the statuses');
}

console.log('\ntext, then a tool, then more text: the status is a line in the same message, never a new bubble');
{
  const loaded = loadWidget();
  const { container } = loaded;
  await sleep(20);
  const stream = loaded.queue();
  const watch = sampler(container);
  send(loaded);
  stream.send({ event: 'session', session_id: 's' });
  stream.send({ event: 'content', content: 'Let me look that up in the documentation first.' });
  await sleep(REVEAL_MS);
  const withText = view(container);
  assertEqual([withText.loading, withText.line], [null, null], 'the text is shown, with no loading bubble and no status yet');
  const count = withText.assistants;
  stream.send({ event: 'tool_call', name: 'retrieve_test_docs' });
  await sleep(30);
  assertEqual(view(container).line, 'Looking up documentation...', 'the tool call is a status line (the widget\'s own community left out)');
  const line = container.querySelector('.osa-activity-status');
  const lineMessage = line && line.closest('.osa-message');
  assertEqual(lineMessage && lineMessage.querySelector('.osa-message-content').textContent.trim(),
    'Let me look that up in the documentation first.', 'under the reply\'s own text');
  assertEqual(view(container).assistants, count, 'and no message was added for it');
  const label = line && line.querySelector('.osa-activity-label');
  assertEqual(label && [label.getAttribute('role'), label.getAttribute('aria-live')], ['status', 'polite'], 'the label is a polite live region');
  stream.send({ event: 'tool_start', name: 'retrieve_test_docs', input: { url: 'https://x' } });
  stream.send({ event: 'tool_end', name: 'retrieve_test_docs', output: 'the page' });
  await sleep(30);
  assertEqual(view(container).line, 'Analyzing results...', 'the line follows the tool through to its result');
  assert(label !== null && container.querySelector('.osa-activity-label') === label, 'in place');
  stream.send({ event: 'content', content: ' The page says to use NWBHDF5IO.' });
  await sleep(REVEAL_MS);
  assertEqual(view(container).line, null, 'more text takes the line away');
  stream.send({ event: 'done', session_id: 's', content: 'Let me look that up in the documentation first. The page says to use NWBHDF5IO.', citations: [] });
  stream.close();
  await waitUntil(() => settled(loaded), 'the send settles');
  const run = await watch.stop();
  assertEqual(run.lineLabels, ['Looking up documentation...', 'Analyzing results...'], 'the line said what happened, in order');
  assertEqual(run.problems, [], `no invariant broke in ${run.samples} samples`);
  assertEqual(view(container).assistants, count, 'one reply, the same message throughout');
  assertNothingLeft(loaded, 'after done');
}

console.log('\nparallel tools: analyzed only once the last one has ended');
{
  const loaded = loadWidget();
  const { container } = loaded;
  await sleep(20);
  const stream = loaded.queue();
  send(loaded);
  stream.send({ event: 'tool_start', name: 'nemar_search_datasets', input: {} });
  stream.send({ event: 'tool_start', name: 'nemar_list_recordings', input: {} });
  stream.send({ event: 'tool_end', name: 'nemar_search_datasets', output: '' });
  await sleep(30);
  assertEqual(view(container).loading, 'Listing recordings...', 'one tool still running: its label stays');
  stream.send({ event: 'tool_end', name: 'nemar_list_recordings', output: '' });
  await sleep(30);
  assertEqual(view(container).loading, 'Analyzing results...', 'both done: Analyzing results...');
  stream.send({ event: 'content', content: ANSWER });
  stream.send({ event: 'done', content: ANSWER, citations: [] });
  stream.close();
  await waitUntil(() => settled(loaded), 'the send settles');
}

console.log('\na tool whose tool_end never came does not keep the next batch from reading as analyzed');
{
  const loaded = loadWidget();
  const { container, activity } = loaded;
  await sleep(20);
  const stream = loaded.queue();
  send(loaded);
  await waitUntil(() => view(container).loading !== null, 'the loading bubble');
  // A tool starts and its end never arrives (a call that failed before it ran, a
  // lost event); the model reads the failure and writes its next call.
  stream.send({ event: 'tool_start', name: 'nemar_search_datasets', input: {} });
  await handled(loaded, stream);
  assertEqual([view(container).loading, activity.state().toolsRunning], ['Searching datasets...', 1], 'the first tool runs, and is never reported ended');
  stream.send({ event: 'tool_call', name: 'nemar_list_recordings' });
  await handled(loaded, stream);
  assertEqual([view(container).loading, activity.state().toolsRunning], ['Listing recordings...', 0],
    'a new call means the tools before it are over: none is counted as running');
  stream.send({ event: 'tool_start', name: 'nemar_list_recordings', input: {} });
  stream.send({ event: 'tool_end', name: 'nemar_list_recordings', output: '' });
  await handled(loaded, stream);
  assertEqual([view(container).loading, activity.state().toolsRunning], ['Analyzing results...', 0],
    'so once the new call ends, the reply reads as analyzing its result');
  stream.send({ event: 'content', content: ANSWER });
  stream.send({ event: 'done', content: ANSWER, citations: [] });
  stream.close();
  await waitUntil(() => settled(loaded), 'the send settles');
  assertNothingLeft(loaded, 'after the drift');
}

console.log('\nan old server, with none of the new events, still gets the generic labels');
{
  const loaded = loadWidget();
  const { container } = loaded;
  await sleep(20);
  const stream = loaded.queue();
  const watch = sampler(container);
  send(loaded);
  for (const event of [{ event: 'session', session_id: 's' }, { event: 'thinking' }, { event: 'thinking' }]) {
    stream.send(event);
    await sleep(25);
  }
  stream.send({ event: 'content', content: ANSWER });
  await sleep(REVEAL_MS);
  stream.send({ event: 'done', content: ANSWER, citations: [] });
  stream.close();
  await waitUntil(() => settled(loaded), 'the send settles');
  const run = await watch.stop();
  assertEqual(run.loadingLabels, [TITLE, 'Thinking...'], 'the title, then Thinking..., as before');
  assertEqual(run.lineLabels, [], 'and no status line');
  assertEqual(run.problems, [], `no invariant broke in ${run.samples} samples`);

  // An old server does send tool_start and tool_end, which a new widget reads.
  const again = loaded.queue();
  const watchAgain = sampler(container);
  send(loaded, 'And the second question?');
  for (const event of [
    { event: 'session', session_id: 's' },
    { event: 'tool_start', name: 'nemar_search_datasets', input: {} },
    { event: 'tool_end', name: 'nemar_search_datasets', output: '' },
  ]) {
    again.send(event);
    await sleep(25);
  }
  again.send({ event: 'content', content: ANSWER });
  again.send({ event: 'done', content: ANSWER, citations: [] });
  again.close();
  await waitUntil(() => settled(loaded), 'the second send settles');
  const second = await watchAgain.stop();
  assertEqual(second.loadingLabels, [TITLE, 'Searching datasets...', 'Analyzing results...'], 'tool_start and tool_end are enough to say what happened');
  assertEqual(second.problems, [], `no invariant broke in ${second.samples} samples`);
}

console.log('\nan event this widget does not know is only warned about, and the reply completes');
{
  const loaded = loadWidget();
  const { container, window } = loaded;
  await sleep(20);
  const warnings = [];
  const realWarn = console.warn;
  console.warn = (...args) => { warnings.push(args.map(String).join(' ')); };
  const stream = loaded.queue();
  send(loaded);
  stream.send({ event: 'tool_call_progress_v9', name: 'x' });
  stream.send({ event: 'content', content: ANSWER });
  stream.send({ event: 'done', content: ANSWER, citations: [] });
  stream.close();
  await waitUntil(() => settled(loaded), 'the send settles');
  console.warn = realWarn;
  assert(warnings.some((w) => w.includes('Unknown SSE event type') && w.includes('tool_call_progress_v9')), 'a console warning names it');
  assert(lastReplyText(container).includes('Three datasets'), 'and the reply is shown');
  void window;
}

// ------------------------------------------------------------- elapsed time

console.log('\nthe elapsed time: none before five seconds, then whole seconds, ticking in place');
{
  const loaded = loadWidget();
  const { container, window, clock, activity } = loaded;
  await sleep(20);
  const stream = loaded.queue();
  send(loaded);
  stream.send({ event: 'tool_call', name: 'nemar_search_datasets' });
  await sleep(30);
  const label = container.querySelector('.osa-loading-label');
  const elapsed = container.querySelector('.osa-loading .osa-status-elapsed');
  assert(activity.ticking(), 'a timer runs while the status is up');
  assertEqual(elapsed && elapsed.getAttribute('aria-live'), 'off', 'the time is not a live region');
  assert(elapsed && !label.contains(elapsed), 'and not inside the label\'s');
  const counter = countRedraws(window, container);
  clock.advance(4999);
  assertEqual(view(container).loadingElapsed, '', 'at 4.999 s, nothing');
  clock.advance(1);
  assertEqual(view(container).loadingElapsed, '5 s', 'at 5 s, "5 s"');
  clock.advance(7000);
  assertEqual(view(container).loadingElapsed, '12 s', 'at 12 s, "12 s"');
  assertEqual(view(container).loading, 'Searching datasets...', 'beside the label, which is unchanged');
  assertEqual(counter.redraws, 0, 'eight ticks, and not one redraw of the conversation');
  assert(container.querySelector('.osa-loading .osa-status-elapsed') === elapsed, 'the same element was written each second');

  stream.send({ event: 'tool_start', name: 'nemar_search_datasets', input: {} });
  await sleep(30);
  clock.advance(1000);
  assertEqual(view(container).loadingElapsed, '13 s', 'the same activity running on keeps its time');
  stream.send({ event: 'tool_end', name: 'nemar_search_datasets', output: '' });
  await sleep(30);
  assertEqual([view(container).loading, view(container).loadingElapsed], ['Analyzing results...', ''], 'a new activity starts its own time');
  clock.advance(6000);
  assertEqual(view(container).loadingElapsed, '6 s', 'and counts it');

  stream.send({ event: 'content', content: 'Here is what I found. ' });
  await sleep(REVEAL_MS);
  stream.send({ event: 'tool_call', name: 'nemar_describe_dataset' });
  await sleep(30);
  clock.advance(9000);
  assertEqual([view(container).line, view(container).lineElapsed], ['Looking up dataset...', '9 s'], 'a status line counts too');
  stream.send({ event: 'content', content: ANSWER });
  stream.send({ event: 'done', content: `Here is what I found. ${ANSWER}`, citations: [] });
  stream.close();
  await waitUntil(() => settled(loaded), 'the send settles');
  assertNothingLeft(loaded, 'after done');
}

// -------------------------------------------------------- every way it ends

console.log('\nevery way a stream can end leaves nothing behind');
{
  const endings = {
    done: (s) => { s.send({ event: 'content', content: ANSWER }); s.send({ event: 'done', content: ANSWER, citations: [] }); s.close(); },
    'error event': (s) => { s.send({ event: 'error', message: 'the model went away' }); s.close(); },
    'no done, after text': (s) => { s.send({ event: 'content', content: ANSWER }); s.close(); },
    'no done, no text': (s) => { s.close(); },
    'reader error': (s) => { s.fail(new Error('connection reset')); },
    // A run's tool_request carries the run's text, as the server sends it.
    'tool_request, no runtime on the page': (s, text) => {
      s.send({ event: 'tool_request', session_id: 's', call_id: 'c1', tool: 'execute_code', args: { code: 'x' }, requires_permission: true, content: text, citations: [] });
      s.close();
    },
  };
  for (const [name, end] of Object.entries(endings)) {
    for (const midReply of [false, true]) {
      const loaded = loadWidget();
      const { container } = loaded;
      await sleep(20);
      const stream = loaded.queue();
      const watch = sampler(container);
      send(loaded);
      if (midReply) {
        stream.send({ event: 'content', content: 'Some text first. ' });
        await sleep(REVEAL_MS);
      }
      stream.send({ event: 'tool_call', name: 'nemar_search_datasets' });
      await sleep(30);
      const before = view(container);
      assert(midReply ? before.line === 'Searching datasets...' : before.loading === 'Searching datasets...',
        `${name}${midReply ? ', mid-reply' : ''}: the status was up (${JSON.stringify(midReply ? before.line : before.loading)})`);
      loaded.clock.advance(6000);
      const ticked = view(container);
      assert(loaded.activity.ticking() && (midReply ? ticked.lineElapsed : ticked.loadingElapsed) === '6 s',
        `${name}${midReply ? ', mid-reply' : ''}: and its timer was running (it read "6 s")`);
      end(stream, midReply ? 'Some text first.' : '');
      await waitUntil(() => settled(loaded), `${name}: the send settles`);
      const run = await watch.stop();
      assertEqual(run.problems, [], `${name}${midReply ? ', mid-reply' : ''}: no invariant broke in ${run.samples} samples`);
      assertNothingLeft(loaded, `${name}${midReply ? ', mid-reply' : ''}`);
    }
  }
}

console.log('\nthe stream handler on its own, however it ends, stops its timer and leaves no line');
{
  // Called directly, as the other widget suites call it: no loading bubble, so a
  // status is shown only as a line under text.
  // A tool call before any text has nowhere to show here (there is no loading bubble,
  // and the reply is still an empty placeholder), and must not make one.
  {
    const loaded = loadWidget();
    const { container, api } = loaded;
    await sleep(20);
    const stream = manualStream();
    const watch = sampler(container);
    const handled = api.handleStreamingResponse(stream.response, container).catch(() => 'threw');
    stream.send({ event: 'tool_call', name: 'nemar_search_datasets' });
    stream.send({ event: 'tool_start', name: 'nemar_search_datasets', input: {} });
    stream.send({ event: 'tool_end', name: 'nemar_search_datasets', output: '' });
    await sleep(40);
    assertEqual([view(container).loading, view(container).line], [null, null], 'no text yet, no loading bubble: nothing is shown');
    stream.send({ event: 'content', content: 'Text first.' });
    stream.send({ event: 'done', content: 'Text first.', citations: [] });
    stream.close();
    await handled;
    const run = await watch.stop();
    assertEqual(run.problems, [], `and no empty bubble was made for it, in ${run.samples} samples`);
    assertNothingLeft(loaded, 'a tool call before any text, called directly');
  }
  const cases = {
    done: (s) => { s.send({ event: 'done', content: 'Text first.', citations: [] }); s.close(); },
    'error event': (s) => { s.send({ event: 'error', message: 'gone' }); s.close(); },
    'no done': (s) => { s.close(); },
    'reader error': (s) => { s.fail(new Error('connection reset')); },
    'tool_request': (s) => {
      s.send({ event: 'tool_request', call_id: 'c', tool: 'execute_code', args: {}, content: 'Text first.' });
      s.close();
    },
    // Not what the server sends (its content is the run's text), but the reply goes
    // on after it, so text the reader has seen must not be wiped to nothing.
    'tool_request with no canonical text': (s) => {
      s.send({ event: 'tool_request', call_id: 'c', tool: 'execute_code', args: {}, content: WHITESPACE });
      s.close();
    },
  };
  for (const [name, end] of Object.entries(cases)) {
    const loaded = loadWidget();
    const { container, api } = loaded;
    await sleep(20);
    const stream = manualStream();
    const handled = api.handleStreamingResponse(stream.response, container).catch(() => 'threw');
    stream.send({ event: 'content', content: 'Text first.' });
    await sleep(REVEAL_MS);
    stream.send({ event: 'tool_call', name: 'nemar_search_datasets' });
    await sleep(30);
    assertEqual(view(container).line, 'Searching datasets...', `${name}: the line was up`);
    end(stream);
    await handled;
    assertNothingLeft(loaded, `${name}, called directly`);
    assert(lastReplyText(container).includes('Text first.'),
      `${name}: and the reply's text is still there`);
  }
}

// ------------------------------------------------ browser-executed code, real

/**
 * Run code the way a page with the runtime does: the real bundle and its real
 * controller, over the test worker the other widget suites drive in place of
 * Pyodide, with "always run" chosen so no gate is asked.
 */
async function withLocalRuntime(loaded) {
  const { window, api } = loaded;
  // eslint-disable-next-line no-new-func
  new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
  window.OSARuntime = globalThis.OSARuntime;
  await sleep(20);
  api.setUpBrowserTools({ client_tools: LOCAL_TOOLS, runtime: { python: LOCAL_RUNTIME_CONFIG } });
  await api.declaredClientTools();
  const runtime = new window.OSARuntime.PyodideRuntime({
    runtime: LOCAL_RUNTIME_CONFIG,
    workerFactory: () => new Worker(new URL('./test-workers/executing.js', import.meta.url).href),
  });
  const controller = new window.OSARuntime.ClientToolController({
    runtime,
    tools: LOCAL_TOOLS,
    gate: async () => { throw new Error('the gate is not asked: the reader chose to always run'); },
  });
  controller.autoRun = true;
  api.setBrowserTools(controller);
  api.setBrowserRuntime(runtime);
  return runtime;
}

const CODE_REQUEST = {
  event: 'tool_request', session_id: 's', call_id: 'c1', tool: 'execute_code',
  args: { code: 'DELAY:200\nprint(1)', description: 'Band power' }, requires_permission: true, content: '', citations: [],
};

console.log('\ncode run in the browser: Writing code..., the Run panel alone, then Analyzing results...');
{
  const loaded = loadWidget({ runtime: true });
  const { container } = loaded;
  const runtime = await withLocalRuntime(loaded);

  const first = loaded.queue();
  const second = loaded.queue({ hold: true });
  const watch = sampler(container);
  const panelSeen = { panel: false, statusWithPanel: false };
  const panelWatch = (async () => {
    while (watch.state.running) {
      if (container.querySelector('.osa-tool-panel')) {
        panelSeen.panel = true;
        if (container.querySelector('.osa-loading, .osa-activity-status')) panelSeen.statusWithPanel = true;
      }
      await sleep(2);
    }
  })();
  send(loaded, 'Plot the alpha power.');
  await waitUntil(() => view(container).loading !== null, 'the loading bubble');
  await sleep(10);
  first.send({ event: 'session', session_id: 's' });
  first.send({ event: 'tool_call', name: 'execute_code' });
  await sleep(40);
  assertEqual(view(container).loading, 'Writing code...', 'while the model writes the code, the bubble says so');
  first.send(CODE_REQUEST);
  first.close();
  await waitUntil(() => container.querySelector('.osa-tool-panel'), 'the run panel');
  await waitUntil(() => loaded.requests.some((r) => r.url.endsWith('/chat/resume')), 'the result goes back');
  await sleep(30);
  assertEqual(view(container).loading, 'Analyzing results...',
    'once the code has run, the model reads what it printed: said while the result is still on its way');
  assert(container.querySelector('.osa-execution-run'), 'with the run on the page above it');
  second.release();
  await sleep(20);
  second.send({ event: 'session', session_id: 's' });
  second.send({ event: 'thinking' });
  await sleep(30);
  assertEqual(view(container).loading, 'Analyzing results...', 'the continuing stream keeps saying so');
  second.send({ event: 'content', content: 'The alpha peak is at 10 Hz.' });
  await sleep(REVEAL_MS);
  second.send({ event: 'done', session_id: 's', content: 'The alpha peak is at 10 Hz.', citations: [] });
  second.close();
  await waitUntil(() => settled(loaded), 'the send settles', 10_000);
  const run = await watch.stop();
  await panelWatch;
  // Between tool_request and the panel the bubble reads the title again, for a few
  // microtasks and no frame; a sample may or may not land there, so it is left out.
  assertEqual(run.loadingLabels.filter((l, i) => i === 0 || l !== TITLE), [TITLE, 'Writing code...', 'Analyzing results...'],
    'the title, then Writing code..., then (after the panel) Analyzing results...');
  assert(panelSeen.panel && !panelSeen.statusWithPanel, 'the Run panel showed, and never beside a status');
  assertEqual(run.problems, [], `no invariant broke in ${run.samples} samples`);
  assertEqual(container.querySelectorAll('.osa-message.assistant').length, 2, 'the greeting and one reply');
  assertNothingLeft(loaded, 'after the browser run');
  runtime.terminate?.();
}

console.log('\na browser run whose result the server refuses: the reply says it stopped, and no status is left');
{
  const loaded = loadWidget({ runtime: true });
  const { container } = loaded;
  const runtime = await withLocalRuntime(loaded);
  const first = loaded.queue();
  const refusal = loaded.queueResponse(() => new Response(JSON.stringify({ detail: 'That call is no longer outstanding.' }),
    { status: 409, headers: { 'content-type': 'application/json' } }));
  const watch = sampler(container);
  send(loaded, 'Plot the alpha power.');
  first.send({ event: 'content', content: 'I will plot it in your browser.' });
  await sleep(REVEAL_MS);
  first.send({ event: 'tool_call', name: 'execute_code' });
  await sleep(30);
  assertEqual(view(container).line, 'Writing code...', 'the reply has text, so the code being written is a line under it');
  first.send({ ...CODE_REQUEST, content: 'I will plot it in your browser.' });
  first.close();
  await waitUntil(() => loaded.requests.some((r) => r.url.endsWith('/chat/resume')), 'the result goes back');
  await sleep(30);
  assertEqual(view(container).line, 'Analyzing results...', 'while the result is on its way, the line says the model will read it');
  refusal.release();
  await waitUntil(() => settled(loaded), 'the send settles', 10_000);
  const run = await watch.stop();
  assertEqual(run.lineLabels, ['Writing code...', 'Analyzing results...'], 'Writing code..., then Analyzing results... while the result was sent');
  const reply = lastReplyText(container);
  assert(/The reply stopped/.test(reply) && reply.includes('I will plot it'), 'the reply keeps its text and says it stopped');
  assertEqual(run.problems, [], `no invariant broke in ${run.samples} samples`);
  assertNothingLeft(loaded, 'after the refused result');
  runtime.terminate?.();
}

// --------------------------------------------------------- whitespace is not text

// A reasoning model often streams "\n\n" before its first tool call. That is not a
// reply a reader can see: the loading bubble stays, no status line goes under it, and
// a reply that ends as only whitespace is dropped as an empty one is.

console.log('\nwhitespace before any text leaves the loading bubble up, and never becomes a bubble of its own');
{
  const cases = {
    'whitespace, then a tool call, then the answer': {
      steps: [
        { event: 'content', content: WHITESPACE },
        { event: 'tool_call', name: 'nemar_search_datasets' },
        { event: 'tool_start', name: 'nemar_search_datasets', input: {} },
        { event: 'tool_end', name: 'nemar_search_datasets', output: '' },
        { event: 'content', content: ANSWER },
        { event: 'done', content: WHITESPACE + ANSWER, citations: [] },
      ],
      loading: ['Searching datasets...', 'Analyzing results...'],
      reply: ANSWER,
    },
    'whitespace, then the answer (no tool)': {
      steps: [
        { event: 'content', content: WHITESPACE },
        { event: 'content', content: ANSWER },
        { event: 'done', content: WHITESPACE + ANSWER, citations: [] },
      ],
      loading: [],
      reply: ANSWER,
    },
    'thinking, then whitespace, then a tool call': {
      steps: [
        { event: 'thinking' },
        { event: 'content', content: ' ' },
        { event: 'tool_call', name: 'retrieve_test_docs' },
        { event: 'content', content: ANSWER },
        { event: 'done', content: ANSWER, citations: [] },
      ],
      loading: ['Thinking...', 'Looking up documentation...'],
      reply: ANSWER,
    },
    'whitespace, then done with an empty canonical text': {
      steps: [
        { event: 'content', content: WHITESPACE },
        { event: 'tool_call', name: 'nemar_search_datasets' },
        { event: 'done', content: '', citations: [] },
      ],
      loading: ['Searching datasets...'],
      reply: null,
    },
    'whitespace, then done with whitespace as the canonical text': {
      steps: [
        { event: 'content', content: WHITESPACE },
        { event: 'done', content: WHITESPACE, citations: [] },
      ],
      loading: [],
      reply: null,
    },
    'whitespace, then the stream ends with no done': {
      steps: [{ event: 'content', content: WHITESPACE }, { event: 'tool_call', name: 'nemar_search_datasets' }],
      loading: ['Searching datasets...'],
      reply: null,
    },
    'whitespace, then the connection fails': {
      steps: [{ event: 'content', content: WHITESPACE }, { event: 'tool_call', name: 'nemar_search_datasets' }, 'FAIL'],
      loading: ['Searching datasets...'],
      reply: null,
    },
  };
  for (const [name, { steps, loading, reply }] of Object.entries(cases)) {
    const loaded = loadWidget();
    const { container, window } = loaded;
    await sleep(20);
    const stream = loaded.queue();
    const watch = sampler(container);
    const before = container.querySelectorAll('.osa-message.assistant').length;
    send(loaded);
    await waitUntil(() => view(container).loading !== null, `${name}: the loading bubble`);
    let sawWhitespaceWait = false;
    let failed = false;
    for (const event of steps) {
      if (event === 'FAIL') {
        stream.fail(new Error('connection reset'));
        failed = true;
        continue;
      }
      stream.send(event);
      await sleep(event.event === 'content' ? REVEAL_MS : 30);
      if (event.event === 'content' && !/\S/.test(event.content)) {
        // Only whitespace has arrived: the reader still sees the loading bubble.
        sawWhitespaceWait = view(container).loading !== null
          && container.querySelectorAll('.osa-message.assistant').length === before
          && container.querySelectorAll('.osa-activity-status').length === 0;
      }
    }
    if (!failed) stream.close();
    await waitUntil(() => settled(loaded), `${name}: the send settles`);
    const run = await watch.stop();
    assert(sawWhitespaceWait, `${name}: after only whitespace, the loading bubble is still up and no reply is on the page`);
    assertEqual(run.loadingLabels.filter((l) => l !== TITLE), loading,
      `${name}: after the title, the loading bubble said ${JSON.stringify(loading)}`);
    assertEqual(run.lineLabels, [], `${name}: no status line was ever drawn`);
    assertEqual(run.problems, [], `${name}: no invariant broke in ${run.samples} samples (${run.mutations} of them on a change)`);
    const after = container.querySelectorAll('.osa-message.assistant').length;
    if (reply === null) {
      assertEqual(after, before, `${name}: a reply that was only whitespace is dropped, not left as an empty bubble`);
    } else {
      assertEqual([after, lastReplyText(container).trim()], [before + 1, reply], `${name}: one reply, holding the answer`);
    }
    assertNothingLeft(loaded, name);
    void window;
  }
}

console.log('\nwhitespace, then code run in the browser, then the answer: never an empty bubble');
{
  const loaded = loadWidget({ runtime: true });
  const { container } = loaded;
  const runtime = await withLocalRuntime(loaded);
  const first = loaded.queue();
  const second = loaded.queue();
  const watch = sampler(container);
  send(loaded, 'Plot the alpha power.');
  await waitUntil(() => view(container).loading !== null, 'the loading bubble');
  first.send({ event: 'content', content: WHITESPACE });
  await sleep(REVEAL_MS);
  first.send({ event: 'tool_call', name: 'execute_code' });
  await waitUntil(() => view(container).loading === 'Writing code...', 'Writing code... in the loading bubble');
  assertEqual(view(container).line, null, 'the whitespace did not become text: the code being written is the loading bubble\'s label');
  first.send({ ...CODE_REQUEST, content: WHITESPACE });
  first.close();
  await waitUntil(() => loaded.requests.some((r) => r.url.endsWith('/chat/resume')), 'the result goes back');
  second.send({ event: 'content', content: 'The alpha peak is at 10 Hz.' });
  second.send({ event: 'done', session_id: 's', content: 'The alpha peak is at 10 Hz.', citations: [] });
  second.close();
  await waitUntil(() => settled(loaded), 'the send settles', 10_000);
  const run = await watch.stop();
  assertEqual(run.problems, [], `no invariant broke in ${run.samples} samples (${run.mutations} of them on a change)`);
  assertEqual(run.lineLabels, [], 'no status line was ever drawn under the whitespace');
  const replies = [...container.querySelectorAll('.osa-message.assistant')];
  assertEqual(replies.length, 2, 'the greeting and one reply');
  assertEqual(lastReplyText(container).trim(), 'The alpha peak is at 10 Hz.', 'the reply holds run two\'s answer, with nothing of the whitespace before it');
  assertNothingLeft(loaded, 'after whitespace, a browser run and the answer');
  runtime.terminate?.();
}

console.log('\nthe renderer holds the same rule on its own, whatever wrote the message');
{
  // Every writer in the stream handler keeps whitespace out of a reply (the checks
  // above). The renderer does not rely on it: a message that holds only whitespace is
  // not drawn while the reply is pending, and never gets a status line under it. Here
  // the whitespace is written into the message directly, as a writer that forgot the
  // rule would leave it.
  const loaded = loadWidget();
  const { container, api } = loaded;
  await sleep(20);
  const stream = loaded.queue();
  send(loaded);
  stream.send({ event: 'tool_call', name: 'nemar_search_datasets' });
  await waitUntil(() => view(container).loading === 'Searching datasets...', 'the loading bubble names the search');
  const list = api.getMessages();
  const index = list.length - 1;
  assertEqual([list[index].role, list[index].content], ['assistant', ''], 'the reply is still an empty placeholder');
  const greetingOnly = view(container).assistants;
  list[index].content = WHITESPACE;
  api.renderMessages(container);
  assertEqual([view(container).assistants, view(container).loading], [greetingOnly, 'Searching datasets...'],
    'while pending, a reply holding only whitespace is not drawn: the loading bubble stands for it');
  assertEqual(problemsIn(container, greetingOnly), [], 'and nothing on the page breaks an invariant');
  list[index].content = '';

  stream.send({ event: 'content', content: ANSWER });
  await sleep(REVEAL_MS);
  stream.send({ event: 'tool_call', name: 'retrieve_test_docs' });
  await waitUntil(() => view(container).line === 'Looking up documentation...', 'the status line under the text');
  const text = list[index].content;
  list[index].content = WHITESPACE;
  api.renderMessages(container);
  assertEqual(view(container).line, null, 'a reply holding only whitespace never gets a status line under it');
  list[index].content = text;
  api.renderMessages(container);
  assertEqual(view(container).line, 'Looking up documentation...', 'and with its text back, the line is back');
  stream.send({ event: 'content', content: ' More.' });
  stream.send({ event: 'done', content: `${ANSWER} More.`, citations: [] });
  stream.close();
  await waitUntil(() => settled(loaded), 'the send settles');
  assertNothingLeft(loaded, 'after the renderer\'s own checks');
}

// ------------------------------------------------------------- appearance

console.log('\nthe status line takes the theme\'s muted text color, light and dark, and holds still for reduced motion');
{
  const { window } = loadWidget();
  const probe = (dark) => {
    const holder = window.document.createElement('div');
    holder.className = `osa-chat-widget${dark ? ' osa-dark' : ''}`;
    holder.innerHTML = '<div class="osa-activity-status"><span class="osa-activity-pulse"></span><span class="osa-activity-label">x</span><span class="osa-status-elapsed">6 s</span></div>';
    window.document.body.appendChild(holder);
    return holder;
  };
  const light = probe(false);
  const dark = probe(true);
  const color = (holder, selector) => window.getComputedStyle(holder.querySelector(selector)).color;
  assertEqual([color(light, '.osa-activity-status'), color(light, '.osa-status-elapsed')], ['#6b7280', '#6b7280'], 'light: the muted text color');
  assertEqual([color(dark, '.osa-activity-status'), color(dark, '.osa-status-elapsed')], ['#9ca3af', '#9ca3af'], 'dark: the dark panel\'s own');
  const stills = [];
  for (const sheet of window.document.styleSheets) {
    for (const rule of sheet.cssRules) {
      if (!rule.media || !/prefers-reduced-motion\s*:\s*reduce/.test(String(rule.media.mediaText))) continue;
      for (const inner of rule.cssRules) {
        if (/\.osa-activity-pulse$/.test(inner.selectorText)) stills.push(inner.style.getPropertyValue('animation') || inner.style.animation);
      }
    }
  }
  assert(stills.length === 1 && /none/.test(stills[0]), `the pulse is still under prefers-reduced-motion: reduce (${JSON.stringify(stills)})`);
}

console.log('\nreduced motion: the reveal is not paced, and the labels are the same');
{
  const loaded = loadWidget({ matchMedia: (query) => ({ matches: /prefers-reduced-motion/.test(query), media: query }) });
  const { container } = loaded;
  await sleep(20);
  const stream = loaded.queue();
  const watch = sampler(container);
  send(loaded);
  await waitUntil(() => view(container).loading !== null, 'the loading bubble');
  await sleep(10);
  stream.send({ event: 'tool_call', name: 'nemar_search_datasets' });
  await sleep(30);
  stream.send({ event: 'tool_end', name: 'nemar_search_datasets', output: '' });
  await sleep(30);
  stream.send({ event: 'content', content: ANSWER });
  stream.send({ event: 'done', content: ANSWER, citations: [] });
  stream.close();
  await waitUntil(() => settled(loaded), 'the send settles');
  const run = await watch.stop();
  assertEqual(run.loadingLabels, [TITLE, 'Searching datasets...', 'Analyzing results...'], 'the same labels');
  assertEqual(run.problems, [], `no invariant broke in ${run.samples} samples`);
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed} checks, passed: ${passed}, failed: ${failed}`);
process.exit(failed === 0 ? 0 : 1);
