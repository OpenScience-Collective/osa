/**
 * The widget on a page of its own, for the tests of the plan card (E2.3, E2.4), and the
 * server's side of its conversation, as recorded events.
 *
 * Shared by frontend/test-widget-plan-card.js and tests/test_frontend/build_plan_payload_fixture.js,
 * so the payload the Python test feeds to the server's own models is the one the widget
 * really builds. What stands in: `fetch`, which answers with the SSE the test writes event
 * by event, in the shape the server sends; and the Python worker behind the runtime, the
 * test worker the other widget suites use. The widget, the runtime bundle as built, the
 * stream handler and the gate are the real ones, in a happy-dom window.
 */

import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function waitUntil(predicate, label, timeoutMs = 8000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) throw new Error(`timed out after ${timeoutMs} ms: ${label}`);
    await sleep(4);
  }
}

export const SOURCE = readFileSync(new URL('../osa-chat-widget.js', import.meta.url), 'utf8');
const BUNDLE = readFileSync(new URL('../osa-runtime.bundle.js', import.meta.url), 'utf8');
// The runtime bundle as built, evaluated once as the browser evaluates the file.
// eslint-disable-next-line no-new-func
new Function(BUNDLE)();
const REAL_RUNTIME = globalThis.OSARuntime;

const API = 'http://localhost/api';
const TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];
const PYTHON = { pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 60 } };
export const TITLE = 'Plan Tests';

// ---------------------------------------------------------------------------
// What the server sends
// ---------------------------------------------------------------------------

export const PLAN_ARGS = {
  findings: "sub-002's recording has 1,479 events in three conditions; the EEG is 70 channels at 250 Hz.",
  steps: [
    { title: 'Count face onsets', detail: 'Count the onsets per condition in the events file.' },
    { title: 'Epoch the EEG', detail: 'Cut epochs from -200 to 600 ms around each onset.' },
    { title: 'Average per condition', detail: 'Average the epochs and find the N170 channels.' },
  ],
  est_peak_mb: 300,
};

export const sessionEvent = (id = 'sess-1') => ({ event: 'session', session_id: id });
export const planRequest = (callId = 'plan-1', args = PLAN_ARGS) => ({
  event: 'tool_request', call_id: callId, tool: 'propose_plan', args, requires_permission: false,
  content: '', citations: [], usage: null, session_id: 'sess-1',
});
export const codeRequest = (callId, code, description = 'Run the step') => ({
  event: 'tool_request', call_id: callId, tool: 'execute_code', args: { code, description }, requires_permission: true,
  content: '', citations: [], usage: null, session_id: 'sess-1',
});
export const doneEvent = (content) => ({ event: 'done', session_id: 'sess-1', content, citations: [], model: 'm' });

/** An SSE response the test writes to one event at a time. */
export function manualStream() {
  const encoder = new TextEncoder();
  let controller;
  const body = new ReadableStream({ start(c) { controller = c; } });
  return {
    response: new Response(body, { headers: { 'content-type': 'text/event-stream' } }),
    send(event) { controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`)); },
    close() { controller.close(); },
  };
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

let pages = 0;

/**
 * The widget in its own window, initialized and open. `bundle` is what the page's runtime
 * script is: 'full' (the bundle as built), 'old' (a bundle with no plan logic, as one
 * built before this feature), or 'none' (the script does not load). With 'full' or 'old'
 * the runtime runs over the test worker (test-workers/executing.js). `widgetConfig` is added
 * to the community's widget config (a launcher, say), and `dataset` is what the page tells
 * the widget it is on (setDataset).
 */
export async function start({
  bundle = 'full', storage = null, storageKey = `osa-test-plan-${++pages}`, allowPageContext = false, settings = null,
  widgetConfig = {}, dataset = null,
} = {}) {
  const window = new Window({
    url: 'http://localhost/page',
    settings: {
      disableJavaScriptFileLoading: true,
      disableCSSFileLoading: true,
      handleDisabledFileLoadingAsSuccess: bundle !== 'none',
    },
  });
  window.__OSA_TEST__ = true;
  window.sessionStorage.setItem(`${storageKey}-session`, '1');
  if (storage) window.localStorage.setItem(storageKey, storage);
  // What the reader saved in Settings, as stored: a string is written as it is (a corrupt or
  // hand-edited value), anything else as JSON.
  if (settings !== null) {
    window.localStorage.setItem('osa-settings-test', typeof settings === 'string' ? settings : JSON.stringify(settings));
  }
  if (bundle !== 'none') {
    const { plan: withPlan, ...withoutPlan } = REAL_RUNTIME;
    const base = bundle === 'old' ? withoutPlan : { ...withoutPlan, plan: withPlan };
    window.OSARuntime = {
      ...base,
      PyodideRuntime: class extends REAL_RUNTIME.PyodideRuntime {
        constructor(options) {
          super({ ...options, workerFactory: () => new Worker(new URL('../test-workers/executing.js', import.meta.url).href) });
        }
      },
    };
  }
  const script = window.document.createElement('script');
  script.setAttribute('src', 'http://localhost/static/osa-chat-widget.js');
  script.setAttribute('data-no-auto-init', '');
  Object.defineProperty(window.document, 'currentScript', { value: script, configurable: true });

  const config = {
    default_model: 'm', offered_models: [], widget: { title: TITLE, ...widgetConfig }, client_tools: TOOLS, runtime: { python: PYTHON },
  };
  const queued = [];
  const requests = [];
  const fetch = async (url, init = {}) => {
    const s = String(url);
    if (s.endsWith('/health')) return new Response(JSON.stringify({ status: 'healthy' }));
    if (s.endsWith('/chat') || s.endsWith('/chat/resume')) {
      requests.push({ kind: s.endsWith('/resume') ? 'resume' : 'chat', body: JSON.parse(init.body) });
      const next = queued.shift();
      if (!next) throw new Error(`no stream queued for ${s}`);
      return next.response;
    }
    return new Response(JSON.stringify(config), { headers: { 'content-type': 'application/json' } });
  };
  window.fetch = fetch;
  // eslint-disable-next-line no-new-func
  const load = new Function(
    'window', 'document', 'localStorage', 'fetch', 'navigator', 'AbortSignal', 'URL',
    'TextDecoder', 'setTimeout', 'clearTimeout', 'console', 'performance', 'Date', SOURCE
  );
  load(window, window.document, window.localStorage, fetch, window.navigator, AbortSignal, URL,
    TextDecoder, setTimeout, clearTimeout, console, globalThis.performance, Date);
  const widget = window.OSAChatWidget;
  if (dataset) widget.setDataset(dataset);
  // No page context by default, so a body holds what the plan adds to it and nothing else.
  widget.setConfig({ apiEndpoint: API, communityId: 'test', storageKey, allowPageContext });
  widget.init();
  const container = window.document.querySelector('.osa-chat-widget');
  await waitUntil(() => widget.__browser.getConfig().title === TITLE, 'the community config');
  await widget.__browser.declaredClientTools();
  const page = {
    window, widget, container, requests, storageKey, api: widget.__browser,
    q: (selector) => container.querySelector(selector),
    qa: (selector) => [...container.querySelectorAll(selector)],
    queue() {
      const stream = manualStream();
      queued.push(stream);
      return stream;
    },
    stored: () => JSON.parse(window.localStorage.getItem(storageKey)),
    storedSettings: () => JSON.parse(window.localStorage.getItem('osa-settings-test')),
  };
  return page;
}

export function click(page, el) {
  el.dispatchEvent(new page.window.Event('click', { bubbles: true }));
}

export function typeInto(page, el, value) {
  el.value = value;
  el.dispatchEvent(new page.window.Event('input', { bubbles: true }));
}

export function send(page, question) {
  page.q('.osa-chat-input input').value = question;
  click(page, page.q('.osa-send-btn'));
}

export const settled = (page) => !page.q('.osa-send-btn').disabled;
export const text = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
export const messagesOf = (page) => page.api.getMessages();
export const lastReply = (page) => [...messagesOf(page)].reverse().find((m) => m.role === 'assistant');
export const cardText = (page, selector) => text(page.q(selector));
export const stepTitles = (page) => page.qa('.osa-plan-card .osa-plan-step-title').map(text);
export const barButtons = (page) => page.qa('.osa-plan-bar button').map(text);

/** Send a question and have the server answer it with a plan. Resolves with the stream used. */
export async function proposeFirst(page, args = PLAN_ARGS, question = 'Compare ERPs for famous vs scrambled faces') {
  const stream = page.queue();
  send(page, question);
  stream.send(sessionEvent());
  stream.send(planRequest('plan-1', args));
  stream.close();
  await waitUntil(() => page.q('.osa-plan-card.osa-plan-live'), 'the plan card is on screen');
  return stream;
}

/** Approve the plan on screen; resolves with the stream the resume reads. */
export async function approve(page, { runAll = false } = {}) {
  if (runAll) {
    const radio = page.q('.osa-plan-card input[value="run_all"]');
    radio.checked = true;
    radio.dispatchEvent(new page.window.Event('change', { bubbles: true }));
  }
  const resumed = page.queue();
  click(page, page.q('.osa-plan-approve'));
  await waitUntil(() => page.requests.some((r) => r.kind === 'resume'), 'the decision is posted');
  return resumed;
}
