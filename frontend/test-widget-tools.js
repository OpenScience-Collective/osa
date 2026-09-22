/**
 * The widget's browser-execution logic (#431 step 7), run for real.
 *
 * The widget source runs unmodified inside a happy-dom window, so escaping goes
 * through a real HTML serializer and parser rather than a stand-in written for
 * the test: a stand-in that escaped would make every escaping test pass by
 * construction. What stands in is the platform only: `fetch` answers with HTTP
 * fixtures, and the steps of a browser turn are passed to the loop that owns
 * the budget, which is the logic under test there.
 *
 * Run with: bun frontend/test-widget-tools.js
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

const SOURCE = readFileSync(new URL('./osa-chat-widget.js', import.meta.url), 'utf8');

function noNetwork(url) {
  return Promise.reject(new Error(`unexpected request in a unit test: ${url}`));
}

/**
 * The widget, evaluated in its own window. Nothing initializes on its own:
 * the script tag carries data-no-auto-init, as an embedder's may.
 */
function loadWidget({
  scriptSrc = 'http://localhost/static/osa-chat-widget.js',
  fetch = noNetwork,
  bundleLoads = false,
} = {}) {
  // Nothing is ever fetched. A script element the widget adds fires `error`,
  // as a refused bundle does, or `load` when a test needs the bundle to arrive.
  const window = new Window({
    url: 'http://localhost/page',
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      handleDisabledFileLoadingAsSuccess: bundleLoads,
    },
  });
  window.__OSA_TEST__ = true;
  const script = window.document.createElement('script');
  if (scriptSrc) script.setAttribute('src', scriptSrc);
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const run = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', SOURCE
  );
  run(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console);
  return { window, api: window.OSAChatWidget.__browser, widget: window.OSAChatWidget };
}

/**
 * Parse generated HTML with the real parser and report anything that is not
 * markup the widget itself writes. This is the property that matters for
 * innerHTML: whatever the content, the only elements and attributes that exist
 * afterwards are ours.
 */
function markupProblems(window, html, allowedTags) {
  const holder = window.document.createElement('div');
  holder.innerHTML = html;
  const problems = [];
  for (const element of holder.querySelectorAll('*')) {
    const tag = element.tagName.toLowerCase();
    if (!allowedTags.includes(tag)) problems.push(`unexpected <${tag}>`);
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith('on')) problems.push(`event handler ${name} on <${tag}>`);
      if (name === 'src' && !/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(attribute.value)) {
        problems.push(`src ${JSON.stringify(attribute.value.slice(0, 60))}`);
      }
      if ((name === 'href' || name === 'style' || name === 'srcdoc')) problems.push(`${name} on <${tag}>`);
    }
  }
  return { problems, holder };
}

const HOSTILE = [
  '<img src=x onerror=alert(1)>',
  '</code></pre><script>alert(1)</script>',
  '"><svg onload=alert(1)>',
  "' onmouseover='alert(1)",
  '&lt;already escaped&gt; & raw',
  '</summary></details><iframe srcdoc="x">',
];

console.log('='.repeat(60));
console.log('Widget: browser execution');
console.log('='.repeat(60));

console.log('\nthe widget stops at the run count the server enforces');
{
  const { api } = loadWidget();
  const limits = readFileSync(new URL('../src/core/limits.py', import.meta.url), 'utf8');
  const server = Number((limits.match(/^MAX_BROWSER_RUNS_PER_REPLY = (\d+)/m) || [])[1]);
  assertEqual(api.MAX_BROWSER_RUNS_PER_REPLY, server, 'MAX_BROWSER_RUNS_PER_REPLY matches src/core/limits.py');
}

console.log('\na run record puts only our markup on the page, whatever it holds');
{
  const { window, api } = loadWidget();
  const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
  const runs = HOSTILE.map((text, i) => ({
    callId: `c${i}`,
    tool: 'execute_code',
    description: text,
    code: `print(${JSON.stringify(text)})`,
    status: i === 0 ? '__proto__' : 'error',
    stdout: text,
    stderr: text,
    images: [
      { mime: 'image/png', data_base64: png, width: 1, height: 1 },
      { mime: 'image/svg+xml', data_base64: png, width: 1, height: 1 },
      { mime: 'image/png', data_base64: `${png}" onerror="alert(1)`, width: 1, height: 1 },
      { mime: 'image/png', data_base64: 'javascript:alert(1)', width: 1, height: 1 },
      null,
    ],
  }));
  const html = api.executionsHtml(runs);
  const { problems, holder } = markupProblems(window, html, ['details', 'summary', 'pre', 'code', 'img', 'span']);
  assertEqual(problems, [], 'no foreign element, handler or source survives parsing');
  const summaries = Array.from(holder.querySelectorAll('summary')).map((s) => s.textContent);
  assertEqual(summaries[1], `Python raised an error: ${HOSTILE[1]}`, 'hostile text comes back as the same text');
  assert(summaries[0].startsWith('Python: '), 'a status that names an Object.prototype key gets the plain label');
  assertEqual(holder.querySelectorAll('img').length, HOSTILE.length, 'exactly one image per run survives: the real PNG');
  const outputs = Array.from(holder.querySelectorAll('.osa-execution-output')).map((p) => p.textContent);
  assert(outputs.includes(HOSTILE[0]), 'printed output is shown as text');
}

console.log('\nthe permission gate puts only our markup on the page, whatever it asks');
{
  const { window, api } = loadWidget();
  for (const text of HOSTILE) {
    const asking = api.toolPanelHtml({ phase: 'asking', prompt: { code: text, description: text }, decide() {} });
    const running = api.toolPanelHtml({ phase: 'running', prompt: { code: text, description: text }, progress: text });
    for (const [label, html] of [['asking', asking], ['running', running]]) {
      const { problems, holder } = markupProblems(window, html,
        ['div', 'pre', 'code', 'button', 'label', 'input', 'span']);
      assert(problems.length === 0, `${label}: ${JSON.stringify(text.slice(0, 24))} leaves no foreign markup${problems.length ? ` (${problems.join('; ')})` : ''}`);
      const description = holder.querySelector('.osa-tool-panel-description');
      assert(description && description.textContent === text, `${label}: the description reads back unchanged`);
    }
  }
  assertEqual(api.toolPanelHtml(null), '', 'no activity, no panel');
}

console.log('\nwhat is stored is what is read back, within the same bounds');
{
  const { api } = loadWidget();
  const L = api.EXECUTION_FIELD_LIMITS;
  const record = api.executionRecord(
    { call_id: 'c'.repeat(L.callId + 10), tool: 'execute_code', args: { code: 'x'.repeat(L.code + 10), description: 'd'.repeat(L.description + 10) } },
    { status: 'ok', stdout: 'o'.repeat(L.stdout + 10), stderr: 'e'.repeat(L.stderr + 10), images: [{ mime: 'image/png' }] }
  );
  assertEqual(
    [record.callId.length, record.code.length, record.description.length, record.stdout.length, record.stderr.length],
    [L.callId, L.code, L.description, L.stdout, L.stderr],
    'a record is written within the limits'
  );
  const stored = JSON.parse(JSON.stringify({ ...record, images: [] }));
  assertEqual(api.normalizePersistedExecutions([stored])[0], stored, 'and reads back identically after a reload');

  assertEqual(api.normalizePersistedExecutions('not a list'), [], 'a non-list reads back as no runs');
  const junk = api.normalizePersistedExecutions([null, 'str', 42, [1], { stdout: 123, code: { a: 1 }, images: ['keep?'] }]);
  assertEqual(junk.length, 1, 'only objects survive');
  assertEqual([junk[0].stdout, junk[0].code, junk[0].images], ['', '', []], 'non-string fields become empty, and images are never restored');
  const many = api.normalizePersistedExecutions(Array.from({ length: 25 }, () => ({ status: 'ok' })));
  assertEqual(many.length, api.MAX_BROWSER_RUNS_PER_REPLY, 'at most one reply\'s worth of runs is restored');
  // Every field, from the one table both sides read: a limit changed on one
  // side only shows up here as a length that differs.
  const oversized = Object.fromEntries(Object.entries(L).map(([field, limit]) => [field, 'z'.repeat(limit * 3)]));
  const cut = api.normalizePersistedExecutions([oversized])[0];
  assertEqual(
    Object.fromEntries(Object.keys(L).map((field) => [field, cut[field].length])),
    { ...L },
    'every oversized stored field is cut to exactly its limit'
  );
}

console.log('\na continuation composes each run after the one before');
{
  const { api } = loadWidget();
  assertEqual(api.composeReply('', 'b'), 'b', 'first run: its own text');
  assertEqual(api.composeReply('a', ''), 'a', 'a run that adds nothing keeps what was there');
  assertEqual(api.composeReply('a', 'b'), 'a\n\nb', 'a later run follows after a blank line');
}

console.log('\nthe loop stops at the budget and says so in the reply');
{
  const { api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: 'Working on it.', executions: [{ status: 'ok' }] }]);
  let answered = 0;
  let error = null;
  try {
    await api.continueBrowserReply({ toolRequest: { call_id: 'r0' }, messageIndex: 0 }, {
      answer: async () => { answered += 1; return { status: 'ok' }; },
      resume: async () => 'stream',
      // A model that asks again every time.
      stream: async () => ({ toolRequest: { call_id: `r${answered}` }, messageIndex: 0 }),
    });
  } catch (err) {
    error = err;
  }
  assert(error !== null, 'it throws rather than looping');
  assertEqual(answered, api.MAX_BROWSER_RUNS_PER_REPLY, 'after answering exactly the budget');
  const content = api.getMessages()[0].content;
  assert(content.startsWith('Working on it.') && /_\[The reply stopped: .*most one reply may\]_$/.test(content),
    `the reply keeps its text and records why it stopped (got ${JSON.stringify(content)})`);
  assertEqual(api.getMessages()[0].executions.length, 1, 'and keeps the record of what ran');
}

console.log('\na reply that finishes needs no note');
{
  const { api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: '' }]);
  const seen = [];
  await api.continueBrowserReply({ toolRequest: { call_id: 'a' }, messageIndex: 0 }, {
    answer: async (request, index) => { seen.push([request.call_id, index]); return { status: 'ok' }; },
    resume: async (request) => `stream-${request.call_id}`,
    stream: async (resumed) => (resumed === 'stream-a' ? { toolRequest: { call_id: 'b' }, messageIndex: 0 } : null),
  });
  assertEqual(seen, [['a', 0], ['b', 0]], 'each request is answered against the same reply');
  assertEqual(api.getMessages()[0].content, '', 'and nothing is added to it');
  await api.continueBrowserReply(null, { answer() { throw new Error('no'); } });
  assert(true, 'a run that ended normally is not a browser turn at all');
}

console.log('\na refused resume is recorded once, by the loop; a failed stream is left to the stream');
{
  const { api } = loadWidget();
  api.setMessages([{ role: 'assistant', content: '', executions: [{ status: 'ok' }] }]);
  let error = null;
  try {
    await api.continueBrowserReply({ toolRequest: { call_id: 'a' }, messageIndex: 0 }, {
      answer: async () => ({ status: 'ok' }),
      resume: async () => { throw new Error('No outstanding browser call with that id for this session.'); },
      stream: async () => null,
    });
  } catch (err) {
    error = err;
  }
  assert(error && /No outstanding/.test(error.message), 'the resume error reaches the caller');
  assertEqual(api.getMessages()[0].content, '_[The reply stopped: No outstanding browser call with that id for this session.]_',
    'and a reply that only ran code says it stopped');

  api.setMessages([{ role: 'assistant', content: 'kept' }]);
  try {
    await api.continueBrowserReply({ toolRequest: { call_id: 'a' }, messageIndex: 0 }, {
      answer: async () => ({ status: 'ok' }),
      resume: async () => 'stream',
      stream: async () => { throw new Error('Stream interrupted'); },
    });
  } catch {
    // Expected.
  }
  assertEqual(api.getMessages()[0].content, 'kept', 'a stream failure is not annotated twice');
}

console.log('\nthe first message waits one shared deadline, not one per wait');
{
  const { api } = loadWidget();
  const never = new Promise(() => {});
  let started = Date.now();
  let declared = await api.declaredClientToolsWithin(never, () => never, 200);
  const took = Date.now() - started;
  assertEqual(declared, [], 'nothing arrives: nothing is declared');
  assert(took >= 190 && took < 350, `in one deadline, not two (took ${took}ms)`);

  started = Date.now();
  declared = await api.declaredClientToolsWithin(Promise.resolve(), () => Promise.resolve({ declared: ['execute_code', 'get_full_output'] }), 5000);
  assertEqual(declared, ['execute_code', 'get_full_output'], 'loaded tools are declared');
  assert(Date.now() - started < 100, 'at once, without waiting out the deadline');
  assertEqual(await api.declaredClientToolsWithin(Promise.resolve(), () => null, 5000), [], 'no tools configured: nothing');
  assertEqual(await api.declaredClientToolsWithin(Promise.resolve(), () => Promise.resolve(null), 5000), [], 'a runtime that failed to load: nothing');
  assertEqual(await api.declaredClientToolsWithin(null, () => Promise.resolve({ declared: ['x'] }), 5000), ['x'], 'no config fetch in flight: the tools alone decide');
}

console.log('\nthe runtime bundle is found beside the script, or not at all');
{
  let { api, widget } = loadWidget({ scriptSrc: 'http://localhost/static/osa-chat-widget.js' });
  assertEqual(api.runtimeBundleUrl(), 'http://localhost/static/osa-runtime.bundle.js', 'next to the widget script');
  ({ api, widget } = loadWidget({ scriptSrc: null }));
  assertEqual(api.runtimeBundleUrl(), null, 'an inline copy with no URL finds nothing');
  widget.setConfig({ widgetScriptUrl: 'https://cdn.example/osa@v1/frontend/osa-chat-widget.js' });
  assertEqual(api.runtimeBundleUrl(), 'https://cdn.example/osa@v1/frontend/osa-runtime.bundle.js',
    'the pop-out, which runs inline, finds it through the URL it is handed');
  ({ api, widget } = loadWidget({ scriptSrc: null }));
  assertEqual(await api.loadRuntimeBundle(), null, 'no URL at all: resolves null rather than throwing');
  assertEqual(widget.getBrowserRuntimeStatus().state, 'off', 'a community without tools reports off');
}

console.log('\na bundle the browser refuses turns code execution off, and says why');
{
  const { window, api } = loadWidget();
  const loading = api.loadRuntimeBundle();
  const script = window.document.head.querySelector('script[src$="osa-runtime.bundle.js"]');
  assert(script !== null, 'the bundle is requested with a script element');
  const integrity = (SOURCE.match(/const RUNTIME_BUNDLE_INTEGRITY = '([^']+)'/) || [])[1];
  assertEqual(script && script.getAttribute('integrity'), integrity, 'carrying the pinned integrity hash');
  assertEqual(script && script.getAttribute('crossorigin'), 'anonymous', 'and crossorigin, without which SRI does not apply');
  script.onerror(new window.Event('error'));
  assertEqual(await loading, null, 'a refused bundle resolves null rather than rejecting');
}

console.log('\nthe real bundle, loaded into the page, answers through the widget\'s own gate');
{
  const { window, api, widget } = loadWidget({ bundleLoads: true });
  // The bundle as built, evaluated as the browser evaluates the file before
  // firing `load`.
  // eslint-disable-next-line no-new-func
  new Function(readFileSync(new URL('./osa-runtime.bundle.js', import.meta.url), 'utf8'))();
  window.OSARuntime = globalThis.OSARuntime;
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.28.3', preload: [], fetch_allow: [], limits: {} } },
  });
  assertEqual(widget.getBrowserRuntimeStatus().state, 'loading', 'loading while the bundle is fetched');
  const tools = await api.declaredClientTools();
  assertEqual(tools, ['execute_code', 'get_full_output'], 'once loaded, the tools are declared');
  assertEqual(widget.getBrowserRuntimeStatus().state, 'ready', 'and the status says ready');

  // A container whose render throws: the gate must still settle, or the
  // server's call stays parked and the widget freezes.
  const broken = window.document.createElement('div');
  broken.className = 'osa-chat-widget';
  window.document.body.appendChild(broken);
  const answering = api.getBrowserTools().answer({
    call_id: 'g1', tool: 'execute_code', args: { code: 'print(1)', description: 'd' }, requires_permission: true,
  });
  await new Promise((resolve) => setTimeout(resolve, 10));
  const asking = api.getToolActivity();
  assert(asking && asking.phase === 'asking', 'the gate is showing');
  asking.decide(window.OSARuntime.GATE_DECISION.DENY, false);
  const result = await answering;
  assertEqual([result.call_id, result.status], ['g1', 'denied'], 'Don\'t run is answered as denied, although rendering failed');
  assertEqual(api.getToolActivity(), null, 'and the panel is cleared');
}

console.log('\na bundle that loads but defines nothing is reported, not ignored');
{
  const { api, widget } = loadWidget({ bundleLoads: true });
  api.setUpBrowserTools({
    client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
    runtime: { python: { pyodide_version: '0.28.3' } },
  });
  assertEqual(await api.declaredClientTools(), [], 'nothing is declared');
  const status = widget.getBrowserRuntimeStatus();
  assertEqual([status.state, status.reason], ['unavailable', 'bad-bundle'], 'and the status says why');
}

console.log('\nwithout a runtime, a request to run code fails with words a reader can act on');
{
  const { api } = loadWidget();
  let error = null;
  try {
    await api.answerToolRequest(null, { call_id: 'x', tool: 'execute_code', args: {} }, 0);
  } catch (err) {
    error = err;
  }
  assert(error && /not available on this page/.test(error.message) && /Send your message again/.test(error.message),
    `it explains and says what to do (got ${error && JSON.stringify(error.message)})`);
}

console.log('\nthe stream handler continues the same reply across runs');
{
  const encoder = new TextEncoder();
  const sse = (events) => new Response(new ReadableStream({
    start(controller) {
      for (const event of events) controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      controller.close();
    },
  }), { headers: { 'content-type': 'text/event-stream' } });
  const config = { default_model: 'm', offered_models: [], widget: {}, client_tools: [], runtime: null };
  const fetch = async (url) => {
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
  const { window, api, widget } = loadWidget({ fetch });
  widget.setConfig({ apiEndpoint: 'http://localhost/api', communityId: 'test', storageKey: 'osa-test' });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  const stream = api.handleStreamingResponse;
  assert(container !== null && typeof stream === 'function', 'the widget renders and exposes its stream handler');

  const before = api.getMessages().length;
  const first = await stream(sse([
    { event: 'session', session_id: 's' },
    { event: 'content', content: 'Let me ' },
    { event: 'content', content: 'check.' },
    { event: 'tool_request', session_id: 's', call_id: 'c1', tool: 'execute_code', args: {},
      content: 'Let me check.[1]', citations: [{ marker: 1, source: 'https://a.example', title: 'A', cited_text: '' }] },
  ]), container);
  assert(first && first.toolRequest && first.toolRequest.call_id === 'c1', 'a run ending on tool_request hands the request back');
  const index = first.messageIndex;
  assertEqual(api.getMessages().length, before + 1, 'one reply was started');
  assertEqual(api.getMessages()[index].content, 'Let me check.[1]', 'holding the canonical text the request carried');

  const second = await stream(sse([
    { event: 'session', session_id: 's' },
    { event: 'content', content: 'The peak is 10 Hz.' },
    { event: 'done', session_id: 's', content: 'The peak is 10 Hz.[2]',
      citations: [{ marker: 1, source: 'https://a.example' }, { marker: 2, source: 'https://b.example' }] },
  ]), container, { messageIndex: index });
  assertEqual(second, null, 'a run ending on done hands nothing back');
  assertEqual(api.getMessages().length, before + 1, 'the continuation wrote into the same reply');
  assertEqual(api.getMessages()[index].content, 'Let me check.[1]\n\nThe peak is 10 Hz.[2]', 'after the earlier run\'s text');
  assertEqual(api.getMessages()[index].citations.map((c) => c.marker), [1, 2], 'with the whole reply\'s citations');

  // A continuation that fails before writing anything keeps what was there.
  let error = null;
  try {
    await stream(sse([{ event: 'session', session_id: 's' }]), container, { messageIndex: index });
  } catch (err) {
    error = err;
  }
  assert(error === null, 'a stream that ends early with earlier text is reported in the reply, not thrown');
  assert(api.getMessages()[index].content.startsWith('Let me check.[1]\n\nThe peak is 10 Hz.[2]'),
    'and the earlier text survives it');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
clearTimeout(watchdog);
process.exit(failed > 0 ? 1 : 0);
