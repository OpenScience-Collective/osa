#!/usr/bin/env bun
/**
 * Real-Chrome check of what a pending reply says it is doing (#538).
 *
 * The Bun suite (frontend/test-widget-activity-status.js) runs the status in happy-dom
 * with a hand-turned clock; this runs it in headless Chrome, where the timers, the
 * layout and the painted frames are the browser's own, and the elapsed time is real
 * seconds. It stands up its own server, so it needs no backend and no network: the
 * widget, a community config, and a `/chat` stream shaped like a tool-using reply.
 *
 * The stream: thinking for a moment, then a `tool_call` for a dataset search that
 * takes about six seconds, its `tool_start` and `tool_end`, a moment of reading the
 * results, some text, then a second tool call mid-reply (a documentation lookup) and
 * the rest of the text. What a reader sees is sampled on every animation frame, in the
 * page: the loading bubble's label, the status line under the reply, the elapsed time
 * beside either, and whether any frame breaks the invariants the Bun suite holds (no
 * status ever makes a bubble, no assistant message is on the page with no text, the
 * loading bubble and a status line are never there together).
 *
 * Every run carries its controls, so that a pass measures something:
 *   - the page is visible, so its frames and timers run at their asked-for rate;
 *   - the conversation is fresh, so the reply measured is this one;
 *   - the search's label was on screen before any reply text was (a status that only
 *     ever appears under text would pass the mid-reply checks and prove nothing about
 *     the loading bubble), and the stream's events arrived in the order sent.
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
const TITLE = 'Activity check';

let failed = 0;
function report(ok, label, detail) {
  if (ok) console.log(`ok: ${label}`);
  else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}

// ------------------------------------------------------------------ the stream

const GREETING_REPLY = 'NEMAR hosts EEG, MEG and iEEG datasets in BIDS.';
const FIRST = 'I found three datasets about attention: nm000103, nm000132 and nm000140. ';
const SECOND = 'The documentation says to read each one with read_window, one recording at a time.';
const SEARCH_MS = 6300; // long enough for the elapsed time to show (it does from 5 s)
const sse = (event) => `data: ${JSON.stringify(event)}\n\n`;

// The first question gets a plain answer, so the conversation has an earlier reply
// the reader can rate while the second one is pending: a thumbs-up redraws the whole
// conversation, which is when an empty placeholder would show if it could.
function firstStream() {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(sse({ event: 'session', session_id: 'activity-check' })));
      controller.enqueue(encoder.encode(sse({ event: 'content', content: GREETING_REPLY })));
      controller.enqueue(encoder.encode(sse({ event: 'done', session_id: 'activity-check', content: GREETING_REPLY, citations: [], request_id: 'r1' })));
      controller.close();
    },
  }), { headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' } });
}

function chatStream() {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    async start(controller) {
      const send = (event) => controller.enqueue(encoder.encode(sse(event)));
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
      for (let i = 0; i < FIRST.length; i += 9) send({ event: 'content', content: FIRST.slice(i, i + 9) });
      await Bun.sleep(1200);
      send({ event: 'tool_call', name: 'retrieve_test_docs' });
      await Bun.sleep(900);
      send({ event: 'tool_start', name: 'retrieve_test_docs', input: { url: 'https://example.org/read_window' } });
      await Bun.sleep(400);
      send({ event: 'tool_end', name: 'retrieve_test_docs', output: 'the page' });
      await Bun.sleep(1000);
      for (let i = 0; i < SECOND.length; i += 9) send({ event: 'content', content: SECOND.slice(i, i + 9) });
      await Bun.sleep(200);
      send({ event: 'done', session_id: 'activity-check', content: FIRST + SECOND, citations: [] });
      controller.close();
    },
  }), { headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' } });
}

const PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>activity status</title></head>
<body><script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: 'test', storageKey: 'activity-status-check-' + Math.random() });
  OSAChatWidget.init();
</script></body></html>`;

function startServer() {
  let chats = 0;
  return Bun.serve({
    port: 0,
    hostname: '127.0.0.1',
    fetch(request) {
      const { pathname } = new URL(request.url);
      if (pathname === '/') return new Response(PAGE, { headers: { 'content-type': 'text/html' } });
      if (pathname === '/osa-chat-widget.js') return new Response(WIDGET, { headers: { 'content-type': 'text/javascript' } });
      if (pathname === '/api/health') return Response.json({ status: 'healthy' });
      if (pathname === '/api/test/chat') return (chats++ === 0 ? firstStream : chatStream)();
      if (pathname === '/api/feedback') return Response.json({ status: 'ok' });
      if (pathname === '/api/test') {
        return Response.json({ default_model: 'm', offered_models: [], widget: { title: TITLE }, client_tools: [], runtime: null });
      }
      return new Response('not found', { status: 404 });
    },
  });
}

// ------------------------------------------------------------------ in the page

// Send one question, and record every frame of what the reader sees until the reply
// has settled, with when each of the stream's events arrived.
const MEASURE = `(async () => {
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
  const observer = new MutationObserver((records) => {
    if (searching && records.some((r) => [...r.removedNodes].some((n) => n.classList && n.classList.contains('osa-loading')))) {
      redrawsWhileSearching++;
    }
  });
  observer.observe(messagesEl, { childList: true });

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
  const problems = new Set();
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
    if (assistants.length > assistantBefore + 1) problems.add('more than one reply message');
    for (const message of assistants) {
      if (!message.querySelector('.osa-message-content').textContent.trim() && !message.querySelector('.osa-execution-run')) {
        problems.add('an assistant message with no text and no run record');
      }
    }
    if (loading && lines.length) problems.add('the loading bubble and a status line at once');
    if (lines.length > 1) problems.add('two status lines');
    for (const line of lines) {
      if (line.closest('.osa-message') !== reply) problems.add('a status line outside the reply');
      else if (!replyText) problems.add('a status line under a reply with no text');
    }
    if (loading && !shown(loading.querySelector('.osa-loading-label'))) problems.add('a loading label that is not on screen');
    if (lines[0] && !shown(lines[0])) problems.add('a status line that is not on screen');
    const status = loading || lines[0];
    if (status && aria === null) {
      const label = status.querySelector('[role="status"]');
      const elapsed = status.querySelector('.osa-status-elapsed');
      aria = {
        live: label ? label.getAttribute('aria-live') : null,
        elapsedLive: elapsed ? elapsed.getAttribute('aria-live') : null,
        elapsedInsideLabel: Boolean(label && elapsed && label.contains(elapsed)),
      };
    }
    frames.push({
      at,
      loading: text(loading, '.osa-loading-label'),
      loadingElapsed: text(loading, '.osa-status-elapsed'),
      line: text(lines[0], '.osa-activity-label'),
      lineElapsed: text(lines[0], '.osa-status-elapsed'),
      replyLength: replyText.length,
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
    problems: [...problems],
    aria,
    arrivals: arrivals.map(([t, e]) => [t - sentOffset, e]),
    intervalsAtSettle,
    intervalsAfter: liveIntervals.size,
    leftAfter: messagesEl.querySelectorAll('.osa-loading, .osa-activity-status').length,
    finalText: [...messagesEl.querySelectorAll('.osa-message.assistant .osa-message-content')].pop().textContent,
    saved: Object.keys(localStorage).filter((k) => k.startsWith('activity-status-check-')).map((k) => localStorage.getItem(k)).join(''),
  };
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

async function openPage(cdp, base) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  await cdp.send('Page.enable', {}, sessionId);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
  await cdp.send('Page.navigate', { url: `${base}/` }, sessionId);
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (await evaluate(cdp, sessionId, `!!document.querySelector('.osa-chat-button') && !!window.OSAChatWidget`)) break;
    await Bun.sleep(100);
  }
  return { targetId, sessionId };
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

    const page = await openPage(cdp, base);
    const measuring = evaluate(cdp, page.sessionId, MEASURE);
    if (screenshotDir) {
      await Bun.sleep(3000);
      await screenshot(cdp, page.sessionId, screenshotDir, 'searching.png');
      await Bun.sleep(8000);
      await screenshot(cdp, page.sessionId, screenshotDir, 'mid-reply-status.png');
    }
    const run = await measuring;
    await screenshot(cdp, page.sessionId, screenshotDir, 'after.png');

    const { frames } = run;
    const firstText = frames.find((f) => f.replyLength > 0);
    const arrived = (name) => (run.arrivals.find(([, e]) => e === name) || [Infinity])[0];
    const searchFrames = frames.filter((f) => f.loading === 'Searching datasets...');
    const searchFrom = searchFrames.length ? searchFrames[0].at : Infinity;
    const loadingLabels = sequence(frames.filter((f) => !firstText || f.at < firstText.at), 'loading');
    const lineLabels = sequence(frames, 'line');
    const elapsedSeen = searchFrames.map((f) => [f.at - searchFrom, f.loadingElapsed]).filter(([, e]) => e);
    const earliestElapsed = elapsedSeen.length ? Math.min(...elapsedSeen.map(([t]) => t)) : null;
    const lineElapsed = frames.filter((f) => f.lineElapsed).length;
    console.log(JSON.stringify({
      frames: frames.length,
      firstTextAt: firstText && Math.round(firstText.at),
      loadingLabels,
      lineLabels,
      elapsedShown: [...new Set(elapsedSeen.map(([, e]) => e))],
      earliestElapsedMs: earliestElapsed && Math.round(earliestElapsed),
      arrivals: run.arrivals.filter(([, e]) => !['content', 'thinking'].includes(e)).map(([t, e]) => [Math.round(t), e]),
      problems: run.problems,
      aria: run.aria,
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
    report(frames.length > 300, `control: every frame was sampled (${frames.length} frames)`);
    report(arrived('tool_call') < arrived('tool_start') && arrived('tool_start') < arrived('content'),
      'control: the stream\'s events arrived in the order they were sent', run.arrivals.map(([, e]) => e));
    report(Boolean(firstText) && searchFrames.length > 0 && searchFrom < firstText.at,
      `control: "Searching datasets..." was on screen before any reply text (${Math.round(searchFrom)} ms, text at ${firstText && Math.round(firstText.at)} ms)`);

    // What the reader saw, in order
    const order = ['Thinking...', 'Searching datasets...', 'Analyzing results...'];
    report(JSON.stringify(loadingLabels.filter((l) => l !== TITLE)) === JSON.stringify(order) && [TITLE, 'Thinking...'].includes(loadingLabels[0]),
      `before any text the loading label read ${JSON.stringify(loadingLabels)}`);
    report(searchFrom - arrived('tool_call') < 250,
      `the search was named within a moment of its tool_call (${Math.round(searchFrom - arrived('tool_call'))} ms)`);
    report(JSON.stringify(lineLabels) === JSON.stringify(['Looking up documentation...', 'Analyzing results...']),
      `once the reply had text, the second tool was a line under it: ${JSON.stringify(lineLabels)}`);
    report(elapsedSeen.some(([, e]) => /^[56] s$/.test(e)), `the search's wait showed its length (${JSON.stringify([...new Set(elapsedSeen.map(([, e]) => e))])})`);
    report(earliestElapsed !== null && earliestElapsed >= 4900, `and not before five seconds (first at ${earliestElapsed && Math.round(earliestElapsed)} ms)`);
    report(lineElapsed === 0, `the mid-reply waits were short, so their lines showed no time (${lineElapsed} frames did)`);

    // The invariants, in every frame
    report(run.problems.length === 0, `no frame broke an invariant (${frames.length} frames)`, run.problems);
    report(run.assistantsAfter === run.assistantBefore + 1, 'one new reply: no status made a message of its own', run.assistantsAfter);
    report(run.finalText.includes(FIRST.trim().slice(0, 30)) && run.finalText.includes(SECOND.slice(0, 30)), 'the reply holds both parts of the text');

    // Accessibility, and nothing left behind
    report(run.aria && run.aria.live === 'polite' && run.aria.elapsedLive === 'off' && !run.aria.elapsedInsideLabel,
      'the label is a polite live region and the ticking time is outside it', run.aria);
    report(run.leftAfter === 0, 'no status is left on the page after the reply', run.leftAfter);
    report(run.intervalsAtSettle === 0 && run.intervalsAfter === 0,
      'and its once-a-second timer stopped with the reply, not a tick later', [run.intervalsAtSettle, run.intervalsAfter]);
    report(run.saved.includes('read_window') && !/Searching|Analyzing|Looking up/.test(run.saved), 'the saved conversation holds the reply and none of the statuses');

    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] }, page.sessionId);
    const still = await evaluate(cdp, page.sessionId, PULSE_UNDER_REDUCED_MOTION);
    report(still.reduced && still.animation === 'none', 'with prefers-reduced-motion: reduce emulated, the status line\'s pulse holds still', still);
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
