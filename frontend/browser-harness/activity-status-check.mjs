#!/usr/bin/env bun
/**
 * Real-Chrome check of what a pending reply says it is doing (#538).
 *
 * The Bun suite (frontend/test-widget-activity-status.js) runs the status in happy-dom
 * with a hand-turned clock; this runs it in headless Chrome, where the timers, the
 * layout and the painted frames are the browser's own, and the elapsed time is real
 * seconds. It stands up its own server, so it needs no backend and no network: the
 * widget, a community config, and `/chat` streams shaped like tool-using replies.
 *
 * The main stream: thinking for a moment, then a `tool_call` for a dataset search that
 * takes about six seconds, its `tool_start` and `tool_end`, a moment of reading the
 * results, some text, then a second tool call mid-reply (a documentation lookup) and
 * the rest of the text. What a reader sees is sampled on every animation frame, and
 * on every change to the conversation (a MutationObserver), in the page: the loading
 * bubble's label, the status line under the reply, the elapsed time beside either,
 * what the status announcer (the one live region a screen reader hears) says, and
 * whether any sample breaks the invariants the Bun suite holds (no status ever makes
 * a bubble, no assistant message is on the page with no text, the loading bubble and
 * a status line are never there together, no status is a live region of its own).
 *
 * Then a second page runs replies with chunks that are only whitespace, as reasoning
 * models often send before a tool call: whitespace then a search, whitespace then the
 * answer, thinking then whitespace then a lookup, whitespace between a tool's result
 * and a thinking event, whitespace then a done whose text is whitespace or empty, and
 * whitespace then code run in the page (the real runtime bundle and controller, over
 * the test worker the Bun suites use in place of Pyodide) then the second run's
 * answer. None of them may ever show an empty bubble or a status line under nothing.
 *
 * Every run carries its controls, so that a pass measures something:
 *   - the page is visible, so its frames and timers run at their asked-for rate;
 *   - the conversation is fresh, so the reply measured is this one;
 *   - the search's label was on screen before any reply text was (a status that only
 *     ever appears under text would pass the mid-reply checks and prove nothing about
 *     the loading bubble), and the stream's events arrived in the order sent;
 *   - on the whitespace page, the runtime bundle loaded and the code really ran.
 *
 * Usage: bun frontend/browser-harness/activity-status-check.mjs [screenshot-dir]
 * Chrome is found at CHROME_PATH or where it installs (see chrome.js). Under CI a
 * missing Chrome fails; elsewhere it skips.
 */

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { connect, findChrome, launch } from './chrome.js';

const WIDGET = readFileSync(new URL('../osa-chat-widget.js', import.meta.url), 'utf8');
const RUNTIME_BUNDLE = readFileSync(new URL('../osa-runtime.bundle.js', import.meta.url), 'utf8');
const TEST_WORKER = readFileSync(new URL('../test-workers/executing.js', import.meta.url), 'utf8');
const TITLE = 'Activity check';

let failed = 0;
function report(ok, label, detail) {
  if (ok) console.log(`ok: ${label}`);
  else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}

// ------------------------------------------------------------------ the streams

const GREETING_REPLY = 'NEMAR hosts EEG, MEG and iEEG datasets in BIDS.';
const FIRST = 'I found three datasets about attention: nm000103, nm000132 and nm000140. ';
const SECOND = 'The documentation says to read each one with read_window, one recording at a time.';
const SEARCH_MS = 6300; // long enough for the elapsed time to show (it does from 5 s)
const sse = (event) => `data: ${JSON.stringify(event)}\n\n`;
const SSE_HEADERS = { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' };

/** A stream that runs `script(send)`, then closes. */
function stream(script) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    async start(controller) {
      await script((event) => controller.enqueue(encoder.encode(sse(event))));
      controller.close();
    },
  }), { headers: SSE_HEADERS });
}

const chunked = (send, text) => {
  for (let i = 0; i < text.length; i += 9) send({ event: 'content', content: text.slice(i, i + 9) });
};

// The first question gets a plain answer, so the conversation has an earlier reply
// the reader can rate while the second one is pending: a thumbs-up redraws the whole
// conversation, which is when an empty placeholder would show if it could.
const firstStream = () => stream(async (send) => {
  send({ event: 'session', session_id: 'activity-check' });
  send({ event: 'content', content: GREETING_REPLY });
  send({ event: 'done', session_id: 'activity-check', content: GREETING_REPLY, citations: [], request_id: 'r1' });
});

const chatStream = () => stream(async (send) => {
  send({ event: 'session', session_id: 'activity-check' });
  for (let i = 0; i < 3; i++) {
    send({ event: 'thinking' });
    await Bun.sleep(300);
  }
  send({ event: 'tool_call', name: 'nemar_search_datasets' });
  await Bun.sleep(SEARCH_MS - 700);
  send({ event: 'tool_start', name: 'nemar_search_datasets', input: { query: 'attention' } });
  await Bun.sleep(700);
  send({ event: 'tool_end', name: 'nemar_search_datasets', output: '3 datasets' });
  for (let i = 0; i < 4; i++) {
    send({ event: 'thinking' });
    await Bun.sleep(300);
  }
  chunked(send, FIRST);
  await Bun.sleep(1200);
  send({ event: 'tool_call', name: 'retrieve_test_docs' });
  await Bun.sleep(900);
  send({ event: 'tool_start', name: 'retrieve_test_docs', input: { url: 'https://example.org/read_window' } });
  await Bun.sleep(400);
  send({ event: 'tool_end', name: 'retrieve_test_docs', output: 'the page' });
  await Bun.sleep(1000);
  chunked(send, SECOND);
  await Bun.sleep(200);
  send({ event: 'done', session_id: 'activity-check', content: FIRST + SECOND, citations: [] });
});

// The whitespace replies, each chosen by the question that asks for it.
const WS = '\n\n';
const WS_ANSWER = 'Three datasets match: nm000103, nm000132 and nm000140.';
const WS_CODE_ANSWER = 'The alpha peak is at 10 Hz.';
const WS_STREAMS = {
  'ws-tool': (send) => (async () => {
    send({ event: 'session', session_id: 'ws' });
    send({ event: 'content', content: WS });
    await Bun.sleep(400);
    send({ event: 'tool_call', name: 'nemar_search_datasets' });
    await Bun.sleep(500);
    send({ event: 'tool_start', name: 'nemar_search_datasets', input: {} });
    await Bun.sleep(300);
    send({ event: 'tool_end', name: 'nemar_search_datasets', output: '3 datasets' });
    await Bun.sleep(300);
    chunked(send, WS_ANSWER);
    await Bun.sleep(100);
    send({ event: 'done', session_id: 'ws', content: WS + WS_ANSWER, citations: [] });
  })(),
  'ws-answer': (send) => (async () => {
    send({ event: 'session', session_id: 'ws' });
    send({ event: 'content', content: WS });
    await Bun.sleep(500);
    chunked(send, WS_ANSWER);
    await Bun.sleep(100);
    send({ event: 'done', session_id: 'ws', content: WS + WS_ANSWER, citations: [] });
  })(),
  'think-ws': (send) => (async () => {
    send({ event: 'session', session_id: 'ws' });
    send({ event: 'thinking' });
    await Bun.sleep(300);
    send({ event: 'content', content: ' ' });
    await Bun.sleep(400);
    send({ event: 'tool_call', name: 'retrieve_wstest_docs' });
    await Bun.sleep(400);
    chunked(send, WS_ANSWER);
    await Bun.sleep(100);
    send({ event: 'done', session_id: 'ws', content: WS_ANSWER, citations: [] });
  })(),
  // Whitespace between tools, then a thinking event: the status is still true.
  'ws-between': (send) => (async () => {
    send({ event: 'session', session_id: 'ws' });
    send({ event: 'tool_call', name: 'nemar_search_datasets' });
    await Bun.sleep(300);
    send({ event: 'tool_start', name: 'nemar_search_datasets', input: {} });
    await Bun.sleep(200);
    send({ event: 'tool_end', name: 'nemar_search_datasets', output: '3 datasets' });
    await Bun.sleep(300);
    send({ event: 'content', content: WS });
    await Bun.sleep(300);
    send({ event: 'thinking' });
    await Bun.sleep(400);
    chunked(send, WS_ANSWER);
    await Bun.sleep(100);
    send({ event: 'done', session_id: 'ws', content: WS + WS_ANSWER, citations: [] });
  })(),
  'ws-done-ws': (send) => (async () => {
    send({ event: 'session', session_id: 'ws' });
    send({ event: 'content', content: WS });
    await Bun.sleep(400);
    send({ event: 'done', session_id: 'ws', content: WS, citations: [] });
  })(),
  'ws-empty': (send) => (async () => {
    send({ event: 'session', session_id: 'ws' });
    send({ event: 'content', content: WS });
    await Bun.sleep(300);
    send({ event: 'tool_call', name: 'nemar_search_datasets' });
    await Bun.sleep(400);
    send({ event: 'done', session_id: 'ws', content: '', citations: [] });
  })(),
  'ws-code': (send) => (async () => {
    send({ event: 'session', session_id: 'ws' });
    send({ event: 'content', content: WS });
    await Bun.sleep(300);
    send({ event: 'tool_call', name: 'execute_code' });
    await Bun.sleep(400);
    send({
      event: 'tool_request', session_id: 'ws', call_id: 'c1', tool: 'execute_code',
      args: { code: 'DELAY:300\nprint(1)', description: 'Band power' }, requires_permission: true,
      content: WS, citations: [],
    });
  })(),
};
const wsResumeStream = () => stream(async (send) => {
  send({ event: 'session', session_id: 'ws' });
  send({ event: 'thinking' });
  await Bun.sleep(400);
  chunked(send, WS_CODE_ANSWER);
  await Bun.sleep(100);
  send({ event: 'done', session_id: 'ws', content: WS_CODE_ANSWER, citations: [] });
});

// ------------------------------------------------------------------ the server

const PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>activity status</title></head>
<body><script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: 'test', storageKey: 'activity-status-check-' + Math.random() });
  OSAChatWidget.init();
</script></body></html>`;

// The whitespace page carries the test hooks, so it can put a controller over the
// test worker in place of real Pyodide, as the Bun suites do.
const WS_PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>activity status, whitespace</title></head>
<body><script>window.__OSA_TEST__ = true;</script>
<script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: 'wstest', storageKey: 'activity-status-ws-' + Math.random() });
  OSAChatWidget.init();
</script></body></html>`;

const RUNTIME_CONFIG = { pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 60 } };
const CLIENT_TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];

function startServer() {
  let chats = 0;
  return Bun.serve({
    port: 0,
    hostname: '127.0.0.1',
    async fetch(request) {
      const { pathname } = new URL(request.url);
      const js = (body) => new Response(body, { headers: { 'content-type': 'text/javascript' } });
      if (pathname === '/') return new Response(PAGE, { headers: { 'content-type': 'text/html' } });
      if (pathname === '/ws') return new Response(WS_PAGE, { headers: { 'content-type': 'text/html' } });
      if (pathname === '/osa-chat-widget.js') return js(WIDGET);
      if (pathname === '/osa-runtime.bundle.js') return js(RUNTIME_BUNDLE);
      if (pathname === '/test-workers/executing.js') return js(TEST_WORKER);
      if (pathname === '/api/health') return Response.json({ status: 'healthy' });
      if (pathname === '/api/test/chat') return (chats++ === 0 ? firstStream : chatStream)();
      if (pathname === '/api/wstest/chat') {
        const { message } = await request.json();
        const script = WS_STREAMS[message];
        return script ? stream(script) : new Response('no such scenario', { status: 404 });
      }
      if (pathname === '/api/wstest/chat/resume') return wsResumeStream();
      if (pathname === '/api/feedback') return Response.json({ status: 'ok' });
      if (pathname === '/api/test') {
        return Response.json({ default_model: 'm', offered_models: [], widget: { title: TITLE }, client_tools: [], runtime: null });
      }
      if (pathname === '/api/wstest') {
        return Response.json({
          default_model: 'm', offered_models: [], widget: { title: TITLE },
          client_tools: CLIENT_TOOLS, runtime: { python: RUNTIME_CONFIG },
        });
      }
      return new Response('not found', { status: 404 });
    },
  });
}

// ------------------------------------------------------------------ in the page

// What is wrong with the conversation right now, if anything, in words: shared by
// both pages, and run on every frame and on every change to the conversation.
const PROBLEMS_IN = `function problemsIn(messagesEl, baseline) {
  const problems = [];
  const assistants = [...messagesEl.querySelectorAll('.osa-message.assistant')];
  if (assistants.length > baseline + 1) problems.push('more than one reply message');
  for (const message of assistants) {
    const content = message.querySelector('.osa-message-content');
    if (!(content && content.textContent.trim()) && !message.querySelector('.osa-execution-run')) {
      problems.push('an assistant message with no text and no run record');
    }
  }
  const loading = messagesEl.querySelector('.osa-loading');
  const lines = [...messagesEl.querySelectorAll('.osa-activity-status')];
  if (loading && lines.length) problems.push('the loading bubble and a status line at once');
  if (lines.length > 1) problems.push('two status lines');
  for (const line of lines) {
    const message = line.closest('.osa-message.assistant');
    const content = message && message.querySelector('.osa-message-content');
    if (!(content && content.textContent.trim())) problems.push('a status line under a message with no text');
  }
  // The announcer speaks for the status; the run panel is a region of its own.
  const live = '[aria-live], [role="status"]';
  if ([...messagesEl.querySelectorAll('.osa-loading, .osa-activity-status')].some((el) => el.matches(live) || el.querySelector(live))) {
    problems.push('a status that is a live region of its own');
  }
  return problems;
}`;

// Send one question, and record every frame of what the reader sees until the reply
// has settled, with when each of the stream's events arrived.
const MEASURE = `(async () => {
  ${PROBLEMS_IN}
  const widget = document.querySelector('.osa-chat-widget');
  const t0 = performance.now();
  const arrivals = [];
  const realFetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await realFetch.apply(this, args);
    if (/\\/chat(\\?|$)/.test(String(args[0])) && (res.headers.get('content-type') || '').includes('event-stream')) {
      const reader = res.clone().body.getReader();
      const decoder = new TextDecoder();
      (async () => {
        let buffer = '';
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split('\\n\\n');
          buffer = parts.pop();
          for (const part of parts) {
            try { arrivals.push([performance.now() - t0, JSON.parse(part.slice(6)).event]); } catch (err) { /* not an event */ }
          }
        }
      })();
    }
    return res;
  };
  // The status's once-a-second timer, seen from outside: live 1000 ms intervals.
  const liveIntervals = new Set();
  const realSetInterval = window.setInterval;
  const realClearInterval = window.clearInterval;
  window.setInterval = function (fn, ms, ...rest) {
    const id = realSetInterval.call(window, fn, ms, ...rest);
    if (ms === 1000) liveIntervals.add(id);
    return id;
  };
  window.clearInterval = function (id) {
    liveIntervals.delete(id);
    return realClearInterval.call(window, id);
  };

  widget.querySelector('.osa-chat-button').click();
  await new Promise((r) => setTimeout(r, 300));
  const messagesEl = widget.querySelector('.osa-chat-messages');
  const input = widget.querySelector('.osa-chat-input input');
  const greetingOnly = messagesEl.querySelectorAll('.osa-message').length === 1;
  // The first question, answered at once, so there is an earlier reply to rate.
  input.value = 'What does NEMAR host?';
  widget.querySelector('.osa-send-btn').click();
  for (let i = 0; i < 200 && (input.disabled || messagesEl.querySelector('.osa-loading')); i++) {
    await new Promise((r) => setTimeout(r, 25));
  }
  await new Promise((r) => setTimeout(r, 300));
  const earlier = [...messagesEl.querySelectorAll('.osa-message.assistant')].pop();
  const earlierText = earlier ? earlier.querySelector('.osa-message-content').textContent.trim() : '';
  const assistantBefore = messagesEl.querySelectorAll('.osa-message.assistant').length;
  arrivals.length = 0;
  let redrawsWhileSearching = 0;
  let searching = false;
  const problems = new Set();
  let mutations = 0;
  const observer = new MutationObserver((records) => {
    mutations++;
    for (const problem of problemsIn(messagesEl, assistantBefore)) problems.add(problem);
    if (searching && records.some((r) => [...r.removedNodes].some((n) => n.classList && n.classList.contains('osa-loading')))) {
      redrawsWhileSearching++;
    }
  });
  observer.observe(messagesEl, { childList: true, subtree: true, characterData: true });
  // What a screen reader hears: every text the announcer takes, in order.
  const announcer = widget.querySelector('.osa-status-announcer');
  const announced = [];
  const announcerObserver = announcer && new MutationObserver(() => announced.push(announcer.textContent));
  if (announcerObserver) announcerObserver.observe(announcer, { childList: true, characterData: true, subtree: true });

  input.value = 'Which datasets are about attention?';
  const sentAt = performance.now();
  widget.querySelector('.osa-send-btn').click();
  let ratedAt = null;

  const text = (el, selector) => {
    const found = el && el.querySelector(selector);
    return found ? found.textContent : null;
  };
  const shown = (el) => Boolean(el) && el.getBoundingClientRect().height > 0 && getComputedStyle(el).visibility !== 'hidden';
  const frames = [];
  let aria = null;
  let settledAt = null;
  let intervalsAtSettle = null;
  for (;;) {
    await new Promise((r) => requestAnimationFrame(r));
    const at = performance.now() - sentAt;
    const assistants = [...messagesEl.querySelectorAll('.osa-message.assistant')];
    const loading = messagesEl.querySelector('.osa-loading');
    const lines = [...messagesEl.querySelectorAll('.osa-activity-status')];
    const reply = assistants.length > assistantBefore ? assistants[assistants.length - 1] : null;
    const replyText = reply ? reply.querySelector('.osa-message-content').textContent.trim() : '';
    for (const problem of problemsIn(messagesEl, assistantBefore)) problems.add(problem);
    for (const line of lines) {
      if (line.closest('.osa-message') !== reply) problems.add('a status line outside the reply');
    }
    if (loading && !shown(loading.querySelector('.osa-loading-label'))) problems.add('a loading label that is not on screen');
    if (lines[0] && !shown(lines[0])) problems.add('a status line that is not on screen');
    const status = loading || lines[0];
    if (status && aria === null) {
      const label = status.querySelector('.osa-loading-label, .osa-activity-label');
      const elapsed = status.querySelector('.osa-status-elapsed');
      aria = {
        labelHidden: label ? label.getAttribute('aria-hidden') : null,
        labelRole: label ? label.getAttribute('role') : null,
        elapsedInLiveRegion: Boolean(elapsed && elapsed.closest('[aria-live]')),
        announcerRole: announcer ? announcer.getAttribute('role') : null,
        announcerLive: announcer ? announcer.getAttribute('aria-live') : null,
        announcerOutside: Boolean(announcer) && !messagesEl.contains(announcer),
        announcerOnScreen: Boolean(announcer) && announcer.getBoundingClientRect().width > 1,
      };
    }
    frames.push({
      at,
      loading: text(loading, '.osa-loading-label'),
      loadingElapsed: text(loading, '.osa-status-elapsed'),
      line: text(lines[0], '.osa-activity-label'),
      lineElapsed: text(lines[0], '.osa-status-elapsed'),
      replyLength: replyText.length,
      announcer: announcer ? announcer.textContent : null,
    });
    // A reader rates the earlier answer while this one is still searching: the
    // conversation is redrawn with the reply's empty placeholder in it.
    searching = frames[frames.length - 1].loading === 'Searching datasets...';
    const searchStart = frames.find((f) => f.loading === 'Searching datasets...');
    if (ratedAt === null && searching && at - searchStart.at > 1500) {
      const thumb = assistants[assistantBefore - 1] && assistants[assistantBefore - 1].querySelector('.osa-feedback-up');
      if (thumb) {
        thumb.click();
        ratedAt = at;
      }
    }
    const idle = !loading && !lines.length && !input.disabled;
    if (idle && settledAt === null) {
      settledAt = performance.now();
      if (intervalsAtSettle === null) intervalsAtSettle = liveIntervals.size;
    }
    if (!idle) settledAt = null;
    if (settledAt !== null && performance.now() - settledAt > 800) break;
    if (at > 45000) break;
  }
  observer.disconnect();
  if (announcerObserver) announcerObserver.disconnect();
  // Nothing may still tick once the reply is over: wait out two of its seconds.
  await new Promise((r) => setTimeout(r, 2200));
  const sentOffset = sentAt - t0;
  return {
    visibility: document.visibilityState,
    greetingOnly,
    earlierText,
    ratedAt,
    redrawsWhileSearching,
    earlierRated: Boolean(messagesEl.querySelectorAll('.osa-message.assistant')[assistantBefore - 1]
      .querySelector('.osa-feedback-up.selected')),
    assistantBefore,
    assistantsAfter: messagesEl.querySelectorAll('.osa-message.assistant').length,
    frames,
    mutations,
    problems: [...problems],
    aria,
    announced,
    announcerSame: Boolean(announcer) && widget.querySelector('.osa-status-announcer') === announcer,
    announcerAfter: announcer ? announcer.textContent : null,
    arrivals: arrivals.map(([t, e]) => [t - sentOffset, e]),
    intervalsAtSettle,
    intervalsAfter: liveIntervals.size,
    leftAfter: messagesEl.querySelectorAll('.osa-loading, .osa-activity-status').length,
    finalText: [...messagesEl.querySelectorAll('.osa-message.assistant .osa-message-content')].pop().textContent,
    saved: Object.keys(localStorage).filter((k) => k.startsWith('activity-status-check-')).map((k) => localStorage.getItem(k)).join(''),
  };
})()`;

// The whitespace page: put a controller over the test worker in place of Pyodide,
// then ask each question in turn and record every frame and every change until its
// reply has settled.
const RUN_WHITESPACE = `(async () => {
  ${PROBLEMS_IN}
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const widget = document.querySelector('.osa-chat-widget');
  widget.querySelector('.osa-chat-button').click();
  const api = OSAChatWidget.__browser;
  for (let i = 0; i < 400 && !(api.getBrowserTools() && window.OSARuntime); i++) await sleep(25);
  const bundleLoaded = Boolean(api.getBrowserTools() && window.OSARuntime);
  if (bundleLoaded) {
    const runtime = new OSARuntime.PyodideRuntime({
      runtime: ${JSON.stringify(RUNTIME_CONFIG)},
      workerFactory: () => new Worker('/test-workers/executing.js'),
    });
    const controller = new OSARuntime.ClientToolController({
      runtime,
      tools: ${JSON.stringify(CLIENT_TOOLS)},
      gate: async () => { throw new Error('the gate is not asked: the reader chose to always run'); },
    });
    controller.autoRun = true;
    api.setBrowserTools(controller);
    api.setBrowserRuntime(runtime);
  }
  const messagesEl = widget.querySelector('.osa-chat-messages');
  const input = widget.querySelector('.osa-chat-input input');
  const text = (el, selector) => {
    const found = el && el.querySelector(selector);
    return found ? found.textContent : null;
  };
  const sequence = (values) => values.filter((v, i) => v !== null && v !== values[i - 1]);
  const results = {};
  for (const name of ${JSON.stringify(Object.keys(WS_STREAMS))}) {
    const before = messagesEl.querySelectorAll('.osa-message.assistant').length;
    const problems = new Set();
    let mutations = 0;
    const observer = new MutationObserver(() => {
      mutations++;
      for (const problem of problemsIn(messagesEl, before)) problems.add(problem);
    });
    observer.observe(messagesEl, { childList: true, subtree: true, characterData: true });
    input.value = name;
    widget.querySelector('.osa-send-btn').click();
    const loadingSeen = [];
    const lineSeen = [];
    let panelSeen = false;
    let frames = 0;
    let settledAt = null;
    const startedAt = performance.now();
    for (;;) {
      await new Promise((r) => requestAnimationFrame(r));
      frames++;
      for (const problem of problemsIn(messagesEl, before)) problems.add(problem);
      loadingSeen.push(text(messagesEl.querySelector('.osa-loading'), '.osa-loading-label'));
      lineSeen.push(text(messagesEl.querySelector('.osa-activity-status'), '.osa-activity-label'));
      if (messagesEl.querySelector('.osa-tool-panel')) panelSeen = true;
      const idle = !messagesEl.querySelector('.osa-loading, .osa-activity-status, .osa-tool-panel') && !input.disabled;
      if (idle && settledAt === null) settledAt = performance.now();
      if (!idle) settledAt = null;
      if (settledAt !== null && performance.now() - settledAt > 500) break;
      if (performance.now() - startedAt > 20000) break;
    }
    observer.disconnect();
    const replies = [...messagesEl.querySelectorAll('.osa-message.assistant')];
    const last = replies[replies.length - 1];
    results[name] = {
      problems: [...problems],
      mutations,
      frames,
      loading: sequence(loadingSeen),
      lines: sequence(lineSeen),
      panelSeen,
      added: replies.length - before,
      lastText: replies.length > before ? last.querySelector('.osa-message-content').textContent.trim() : null,
      runs: replies.length > before ? last.querySelectorAll('.osa-execution-run').length : 0,
      left: messagesEl.querySelectorAll('.osa-loading, .osa-activity-status').length,
      announcer: widget.querySelector('.osa-status-announcer').textContent,
    };
    await sleep(100);
  }
  return { visibility: document.visibilityState, bundleLoaded, results };
})()`;

// Whether the status line's pulse holds still under reduced motion, in this browser.
const PULSE_UNDER_REDUCED_MOTION = `(() => {
  const probe = document.createElement('div');
  probe.className = 'osa-activity-status';
  probe.innerHTML = '<span class="osa-activity-pulse"></span>';
  document.querySelector('.osa-chat-widget').appendChild(probe);
  const style = getComputedStyle(probe.firstChild);
  const result = { reduced: matchMedia('(prefers-reduced-motion: reduce)').matches, animation: style.animationName };
  probe.remove();
  return result;
})()`;

/** The distinct values a field took, in order. */
function sequence(frames, field) {
  const out = [];
  for (const frame of frames) {
    const value = frame[field];
    if (value !== null && out[out.length - 1] !== value) out.push(value);
  }
  return out;
}

// ------------------------------------------------------------------ the check

async function evaluate(cdp, sessionId, expression) {
  const { result, exceptionDetails } = await cdp.send('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise: true,
  }, sessionId);
  if (exceptionDetails) throw new Error(`page exception: ${exceptionDetails.exception?.description || exceptionDetails.text}`);
  return result.value;
}

async function screenshot(cdp, sessionId, dir, name) {
  if (!dir) return;
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, sessionId);
  writeFileSync(join(dir, name), Buffer.from(data, 'base64'));
}

async function openPage(cdp, url) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  await cdp.send('Page.enable', {}, sessionId);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
  await cdp.send('Page.navigate', { url }, sessionId);
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (await evaluate(cdp, sessionId, `!!document.querySelector('.osa-chat-button') && !!window.OSAChatWidget`)) break;
    await Bun.sleep(100);
  }
  return { targetId, sessionId };
}

function checkMainRun(run) {
  const { frames } = run;
  const firstText = frames.find((f) => f.replyLength > 0);
  const arrived = (name) => (run.arrivals.find(([, e]) => e === name) || [Infinity])[0];
  const searchFrames = frames.filter((f) => f.loading === 'Searching datasets...');
  const searchFrom = searchFrames.length ? searchFrames[0].at : Infinity;
  const analyzingFrames = frames.filter((f) => f.loading === 'Analyzing results...');
  const loadingLabels = sequence(frames.filter((f) => !firstText || f.at < firstText.at), 'loading');
  const lineLabels = sequence(frames, 'line');
  // How late the slowest frame came, beyond an ordinary 17 ms one: nothing on a quiet
  // machine, and what a busy runner adds to a time that is held to a bound below.
  const worstGap = frames.slice(1).reduce((worst, f, i) => Math.max(worst, f.at - frames[i].at), 0);
  const stall = Math.min(2000, Math.max(0, Math.round(worstGap - 17)));
  // Measured from the send, when the reader's wait began.
  const elapsedSeen = frames.filter((f) => f.loadingElapsed).map((f) => [f.at, f.loadingElapsed]);
  const earliestElapsed = elapsedSeen.length ? Math.min(...elapsedSeen.map(([t]) => t)) : null;
  const searchElapsed = [...new Set(searchFrames.map((f) => f.loadingElapsed).filter(Boolean))];
  const analyzingElapsed = [...new Set(analyzingFrames.map((f) => f.loadingElapsed).filter(Boolean))];
  // The same, as whole seconds. Which seconds depends on how long the stream's own sleeps
  // really took, so they are held to the clock and to each other, not to fixed digits.
  const wholeSeconds = (shown) => shown.map((text) => Number.parseInt(text, 10));
  const searchSeconds = wholeSeconds(searchElapsed);
  const analyzingSeconds = wholeSeconds(analyzingElapsed);
  // A wait that is shown is the time since the send, to the second: never ahead of it, and
  // no more than the tick that redraws it (a second) and this run's slowest frame behind.
  const untrue = frames.filter((f) => f.loadingElapsed && !(
    Number.parseInt(f.loadingElapsed, 10) <= f.at / 1000 + 0.1
    && f.at / 1000 - Number.parseInt(f.loadingElapsed, 10) < 2 + stall / 1000
  ));
  const lineElapsed = frames.filter((f) => f.lineElapsed).length;
  console.log(JSON.stringify({
    frames: frames.length,
    mutations: run.mutations,
    firstTextAt: firstText && Math.round(firstText.at),
    loadingLabels,
    lineLabels,
    searchElapsed,
    analyzingElapsed,
    earliestElapsedMs: earliestElapsed && Math.round(earliestElapsed),
    arrivals: run.arrivals.filter(([, e]) => !['content', 'thinking'].includes(e)).map(([t, e]) => [Math.round(t), e]),
    problems: run.problems,
    aria: run.aria,
    announced: run.announced,
    intervalsAfter: run.intervalsAfter,
  }));

  // Controls
  report(run.visibility === 'visible', 'control: the page is visible, so its frames and timers run at their asked-for rate', run.visibility);
  report(run.greetingOnly && run.assistantBefore === 2 && run.earlierText === GREETING_REPLY,
    `control: a fresh conversation with one earlier answer, so the reply measured is this one (${run.assistantBefore} assistant messages before it)`,
    [run.greetingOnly, run.assistantBefore, run.earlierText]);
  report(run.ratedAt !== null && run.redrawsWhileSearching >= 1 && run.earlierRated,
    `control: the reader rated the earlier answer mid-search, and the conversation was redrawn while the reply was pending (${run.redrawsWhileSearching} redraws)`,
    [run.ratedAt, run.redrawsWhileSearching, run.earlierRated]);
  report(frames.length > 300 && run.mutations > 10,
    `control: every frame, and every batch of changes, was sampled (${frames.length} frames, ${run.mutations} batches)`);
  report(arrived('tool_call') < arrived('tool_start') && arrived('tool_start') < arrived('content'),
    'control: the stream\'s events arrived in the order they were sent', run.arrivals.map(([, e]) => e));
  report(Boolean(firstText) && searchFrames.length > 0 && searchFrom < firstText.at,
    `control: "Searching datasets..." was on screen before any reply text (${Math.round(searchFrom)} ms, text at ${firstText && Math.round(firstText.at)} ms)`);

  // What the reader saw, in order
  const order = ['Thinking...', 'Searching datasets...', 'Analyzing results...'];
  report(JSON.stringify(loadingLabels.filter((l) => l !== TITLE)) === JSON.stringify(order) && [TITLE, 'Thinking...'].includes(loadingLabels[0]),
    `before any text the loading label read ${JSON.stringify(loadingLabels)}`);
  report(searchFrom - arrived('tool_call') < 250 + stall,
    `the search was named within a moment of its tool_call (${Math.round(searchFrom - arrived('tool_call'))} ms, under ${250 + stall})`);
  report(JSON.stringify(lineLabels) === JSON.stringify(['Looking up documentation...', 'Analyzing results...']),
    `once the reply had text, the second tool was a line under it: ${JSON.stringify(lineLabels)}`);

  // The elapsed time: the reader's whole wait, from the send
  report(searchSeconds.length > 0 && Math.max(...searchSeconds) >= 5,
    `the wait showed its length during the search, once it passed five seconds (${JSON.stringify(searchElapsed)})`);
  report(earliestElapsed !== null && earliestElapsed >= 4900, `and not before five seconds after the send (first at ${earliestElapsed && Math.round(earliestElapsed)} ms)`);
  report(untrue.length === 0,
    `and every time shown was the real time since the send, to the second (${untrue.length} of ${elapsedSeen.length} frames were not)`,
    untrue.slice(0, 3).map((f) => [Math.round(f.at), f.loadingElapsed]));
  report(analyzingSeconds.length > 0 && searchSeconds.length > 0 && Math.min(...analyzingSeconds) >= Math.max(...searchSeconds),
    `the new label "Analyzing results..." went on counting the same wait, never back to a smaller number (${JSON.stringify(analyzingElapsed)} after ${JSON.stringify(searchElapsed)})`);
  report(lineElapsed === 0,
    `the reply's text began a new wait, and the mid-reply waits were short, so their lines showed no time (${lineElapsed} frames did)`);

  // The invariants, in every frame and every change
  report(run.problems.length === 0, `no frame or change broke an invariant (${frames.length} frames, ${run.mutations} changes)`, run.problems);
  report(run.assistantsAfter === run.assistantBefore + 1, 'one new reply: no status made a message of its own', run.assistantsAfter);
  report(run.finalText.includes(FIRST.trim().slice(0, 30)) && run.finalText.includes(SECOND.slice(0, 30)), 'the reply holds both parts of the text');

  // What a screen reader hears
  const a = run.aria;
  report(a && a.labelHidden === 'true' && a.labelRole === null && !a.elapsedInLiveRegion,
    'the visible label is hidden from a screen reader and is no live region, and the ticking time is in none', a);
  report(a && a.announcerRole === 'status' && a.announcerLive === 'polite' && a.announcerOutside && !a.announcerOnScreen,
    'the status announcer is one polite status region, outside the conversation, on no screen', a);
  report(run.announcerSame, 'the same announcer node from the first frame to the last, through every redraw');
  const expectedAnnounced = ['Thinking...', 'Searching datasets...', 'Analyzing results...', '', 'Looking up documentation...', 'Analyzing results...', ''];
  report(JSON.stringify(run.announced) === JSON.stringify(expectedAnnounced),
    'it said each label once, in order, and nothing once each status ended (the rating\'s redraw said nothing again)', run.announced);
  report(run.announced.every((t) => !/\d/.test(t)) && frames.every((f) => f.announcer === null || !/\d/.test(f.announcer)),
    'and never a number, so never the seconds');

  // Nothing left behind
  report(run.leftAfter === 0 && run.announcerAfter === '', 'no status is left on the page, or in the announcer, after the reply', [run.leftAfter, run.announcerAfter]);
  report(run.intervalsAtSettle === 0 && run.intervalsAfter === 0,
    'and its once-a-second timer stopped with the reply, not a tick later', [run.intervalsAtSettle, run.intervalsAfter]);
  report(run.saved.includes('read_window') && !/Searching|Analyzing|Looking up/.test(run.saved), 'the saved conversation holds the reply and none of the statuses');
}

function checkWhitespaceRun(ws) {
  console.log(JSON.stringify(ws));
  report(ws.visibility === 'visible', 'whitespace page, control: the page is visible', ws.visibility);
  report(ws.bundleLoaded, 'whitespace page, control: the runtime bundle loaded, so code can run in the page', ws.bundleLoaded);
  const expected = {
    'ws-tool': { loading: ['Searching datasets...', 'Analyzing results...'], added: 1, text: WS_ANSWER },
    'ws-answer': { loading: [], added: 1, text: WS_ANSWER },
    'think-ws': { loading: ['Thinking...', 'Looking up documentation...'], added: 1, text: WS_ANSWER },
    'ws-between': { loading: ['Searching datasets...', 'Analyzing results...'], added: 1, text: WS_ANSWER },
    'ws-done-ws': { loading: [], added: 0, text: null },
    'ws-empty': { loading: ['Searching datasets...'], added: 0, text: null },
    'ws-code': { loading: ['Writing code...', 'Analyzing results...'], added: 1, text: WS_CODE_ANSWER },
  };
  for (const [name, want] of Object.entries(expected)) {
    const got = ws.results[name];
    if (!got) {
      report(false, `${name}: ran`, Object.keys(ws.results));
      continue;
    }
    // Between a tool_request and the run panel the bubble reads the title for a few
    // microtasks; a frame may or may not land there, so the title is left out.
    const loading = got.loading.filter((l) => l !== TITLE);
    report(got.problems.length === 0,
      `${name}: no frame or change broke an invariant (${got.frames} frames, ${got.mutations} changes)`, got.problems);
    report(JSON.stringify(loading) === JSON.stringify(want.loading),
      `${name}: after the title, the loading bubble said ${JSON.stringify(want.loading)}`, got.loading);
    report(got.lines.length === 0, `${name}: no status line was ever drawn under the whitespace`, got.lines);
    report(got.added === want.added && got.lastText === want.text,
      want.added ? `${name}: one reply, holding the answer and nothing of the whitespace` : `${name}: a reply that was only whitespace is dropped, not left as an empty bubble`,
      [got.added, got.lastText]);
    report(got.left === 0 && got.announcer === '', `${name}: nothing is left on the page or in the announcer`, [got.left, got.announcer]);
  }
  const code = ws.results['ws-code'];
  if (code) {
    report(code.panelSeen && code.runs === 1, 'ws-code, control: the code really ran in the page (the run panel showed, and the reply keeps its run)', [code.panelSeen, code.runs]);
  }
}

async function check(screenshotDir) {
  const chromePath = findChrome();
  if (!chromePath) {
    if (process.env.CI) {
      report(false, 'no Chrome found, and CI must run this check');
      return;
    }
    console.log('SKIP: no Chrome found');
    return;
  }
  if (screenshotDir) mkdirSync(screenshotDir, { recursive: true });
  const server = startServer();
  const base = `http://127.0.0.1:${server.port}`;
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-activity-status-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { product } = await cdp.send('Browser.getVersion');
    console.log(`Chrome: ${product}`);

    const page = await openPage(cdp, `${base}/`);
    const measuring = evaluate(cdp, page.sessionId, MEASURE);
    if (screenshotDir) {
      await Bun.sleep(3000);
      await screenshot(cdp, page.sessionId, screenshotDir, 'searching.png');
      await Bun.sleep(8000);
      await screenshot(cdp, page.sessionId, screenshotDir, 'mid-reply-status.png');
    }
    const run = await measuring;
    await screenshot(cdp, page.sessionId, screenshotDir, 'after.png');
    checkMainRun(run);

    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] }, page.sessionId);
    const still = await evaluate(cdp, page.sessionId, PULSE_UNDER_REDUCED_MOTION);
    report(still.reduced && still.animation === 'none', 'with prefers-reduced-motion: reduce emulated, the status line\'s pulse holds still', still);

    console.log('\nwhitespace before any text');
    const wsPage = await openPage(cdp, `${base}/ws`);
    const ws = await evaluate(cdp, wsPage.sessionId, RUN_WHITESPACE);
    await screenshot(cdp, wsPage.sessionId, screenshotDir, 'whitespace-after.png');
    checkWhitespaceRun(ws);
  } finally {
    try { cdp?.close?.(); } catch (error) { /* already gone */ }
    chrome?.kill();
    server.stop(true);
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
}

await check(process.argv[2]);
if (failed) {
  console.error(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log('\nall checks passed');
process.exit(0);
