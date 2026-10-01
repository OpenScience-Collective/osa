#!/usr/bin/env bun
/**
 * Real-Chrome check of the widget's paced reveal (#531).
 *
 * The Bun suite (frontend/test-widget-paced-streaming.js) runs the reveal in happy-dom;
 * this runs it in headless Chrome, where timers, layout and `matchMedia` are the
 * browser's own. It stands up its own server, so it needs no backend and no network:
 * the widget, a community config, and a `/chat` stream shaped like a reasoning model's.
 * The stream is silent for a while, then delivers a reply of about 900 characters,
 * with a fenced code block in the middle, in well under a second: the shape of GPT-6
 * Luna at maximum effort, measured on the dev API (25 s of nothing, then over 1,000
 * characters a second).
 *
 * What is measured is what a reader sees: the text of the last assistant message,
 * sampled every 25 ms in the page, against when the stream's chunks arrived (read from
 * a clone of the response, in the page).
 *
 * The bounds on time are for a quiet machine, and a shared runner is not one: each is
 * stretched by how late the page's own 25 ms sampler ran in the window it is timing
 * (`stall`, capped), so a stalled runner does not fail what a quiet one passes, and the
 * numbers that say what the reveal does are unchanged when nothing stalled.
 *
 * Every run carries its controls, so that a pass measures something:
 *   - the page is visible and its timers run at their asked-for rate (a hidden page's
 *     are throttled to one a second, which would make every number here meaningless);
 *   - the stream really was a burst: all of its text arrived in under a second;
 *   - with `prefers-reduced-motion: reduce` emulated by the browser, the same burst is
 *     shown at once, so the pacing above is the widget's doing and can be switched off.
 *
 * Usage: bun frontend/browser-harness/paced-reveal-check.mjs [screenshot-dir]
 * Chrome is found at CHROME_PATH or where it installs (see chrome.js). Under CI a
 * missing Chrome fails; elsewhere it skips.
 */

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { connect, findChrome, launch } from './chrome.js';

const WIDGET = readFileSync(new URL('../osa-chat-widget.js', import.meta.url), 'utf8');

let failed = 0;
function report(ok, label, detail) {
  if (ok) console.log(`ok: ${label}`);
  else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}

// ------------------------------------------------------------------ the stream

const CODE = [
  '```python',
  'from pynwb import NWBHDF5IO',
  '',
  'with NWBHDF5IO("session.nwb", "r") as io:',
  '    nwbfile = io.read()',
  '    for name, series in nwbfile.acquisition.items():',
  '        print(name, series.data.shape)',
  '```',
].join('\n');

const REPLY = [
  'Open the file with `NWBHDF5IO` in read mode and call `read()`. The handle has to stay open while you read data from it, so keep the work inside the `with` block.[1]',
  '',
  CODE,
  '',
  'Everything the recording stored as raw acquisition sits in `nwbfile.acquisition`, keyed by name, and each entry is a `TimeSeries` (or a subtype such as `ElectricalSeries`) with its data, its unit and its timing information.[2] Processed results live under `nwbfile.processing`, one module per analysis step, and metadata about the session, the subject and the devices is on the file object itself.[3]',
  '',
  'If you only need the metadata, open the file, read the attributes you care about, and close it again: nothing in `data` is loaded until you index into it.',
].join('\n');

const CITATIONS = [1, 2, 3].map((marker) => ({
  marker, source: `https://pynwb.readthedocs.io/en/latest/page-${marker}.html`, title: `PyNWB page ${marker}`, cited_text: 'x',
}));

const THINK_MS = 1500; // silent, as a reasoning model is before its answer
const SAMPLE_MS = 25; // how often the page samples what the reader sees (see MEASURE)
// What a slow runner may add to a time below, at most: a run stalled for longer than this
// has measured the runner, and the bounds are not stretched to fit it.
const MAX_STALL_MS = 2000;
// A burst: the stream's whole text in well under the two seconds that a reveal is for.
const BURST_MS = 2000;
const sse = (event) => `data: ${JSON.stringify(event)}\n\n`;

function chatStream() {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    async start(controller) {
      const send = (event) => controller.enqueue(encoder.encode(sse(event)));
      send({ event: 'session', session_id: 'paced-check' });
      const beat = setInterval(() => send({ event: 'thinking' }), 500);
      await Bun.sleep(THINK_MS);
      clearInterval(beat);
      for (const marker of [1, 2, 3]) send({ event: 'citation', ...CITATIONS[marker - 1] });
      // The burst: small chunks, a few milliseconds apart, the whole reply in under a second.
      for (let i = 0; i < REPLY.length; i += 7) {
        send({ event: 'content', content: REPLY.slice(i, i + 7) });
        if ((i / 7) % 8 === 0) await Bun.sleep(2);
      }
      send({ event: 'done', session_id: 'paced-check', content: REPLY, citations: CITATIONS });
      controller.close();
    },
  }), { headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' } });
}

const PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>paced reveal</title></head>
<body><script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: 'test', storageKey: 'paced-reveal-check-' + Math.random() });
  OSAChatWidget.init();
</script></body></html>`;

function startServer() {
  return Bun.serve({
    port: 0,
    hostname: '127.0.0.1',
    fetch(request) {
      const { pathname } = new URL(request.url);
      if (pathname === '/') return new Response(PAGE, { headers: { 'content-type': 'text/html' } });
      if (pathname === '/osa-chat-widget.js') return new Response(WIDGET, { headers: { 'content-type': 'text/javascript' } });
      if (pathname === '/api/health') return Response.json({ status: 'healthy' });
      if (pathname === '/api/test/chat') return chatStream();
      if (pathname === '/api/test') {
        return Response.json({ default_model: 'm', offered_models: [], widget: { title: 'Paced check' }, client_tools: [], runtime: null });
      }
      return new Response('not found', { status: 404 });
    },
  });
}

// ------------------------------------------------------------------ in the page

// Send one question and record what the reader saw and when the stream's text arrived.
const MEASURE = `(async () => {
  const widget = document.querySelector('.osa-chat-widget');
  const arrivals = [];
  const t0 = performance.now();
  const realFetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await realFetch.apply(this, args);
    if (/\\/chat(\\?|$)/.test(String(args[0])) && (res.headers.get('content-type') || '').includes('event-stream')) {
      const reader = res.clone().body.getReader();
      const decoder = new TextDecoder();
      (async () => {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          let chars = 0;
          for (const m of decoder.decode(value, { stream: true }).matchAll(/data: (\\{.*\\})/g)) {
            try { const e = JSON.parse(m[1]); if (e.event === 'content') chars += e.content.length; } catch (err) { /* a split event */ }
          }
          if (chars) arrivals.push([performance.now() - t0, chars]);
        }
      })();
    }
    return res;
  };
  widget.querySelector('.osa-chat-button').click();
  await new Promise((r) => setTimeout(r, 300));
  const input = widget.querySelector('.osa-chat-input input');
  input.value = 'How do I read an NWB file?';
  const assistantBefore = widget.querySelectorAll('.osa-message.assistant').length;
  const sentAt = performance.now();
  widget.querySelector('.osa-send-btn').click();

  const frames = [];
  let redrawSeen = 0;
  const messages = widget.querySelector('.osa-chat-messages');
  const observer = new MutationObserver((records) => { redrawSeen += records.length ? 1 : 0; });
  observer.observe(messages, { childList: true });
  let lastChange = performance.now();
  let lastLen = -1;
  for (;;) {
    await new Promise((r) => setTimeout(r, 25));
    const all = [...messages.querySelectorAll('.osa-message.assistant')];
    const reply = all.length > assistantBefore ? all[all.length - 1] : null;
    const content = reply && reply.querySelector('.osa-message-content');
    const len = content ? content.textContent.length : 0;
    const codeLens = reply ? [...reply.querySelectorAll('pre code')].map((c) => c.textContent.length) : [];
    const sources = reply ? reply.querySelectorAll('.osa-message-sources li').length : 0;
    frames.push([performance.now() - sentAt, len, codeLens, sources]);
    if (len !== lastLen) { lastLen = len; lastChange = performance.now(); }
    const idle = !widget.querySelector('.osa-loading') && !widget.querySelector('.osa-chat-input input').disabled;
    if (len > 0 && idle && performance.now() - lastChange > 1500) break;
    if (performance.now() - sentAt > 60000) break;
  }
  observer.disconnect();
  const sentOffset = sentAt - t0;
  return {
    visibility: document.visibilityState,
    frames,
    arrivals: arrivals.map(([t, n]) => [t - sentOffset, n]),
    redraws: redrawSeen,
    reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
    assistantBefore,
  };
})()`;

function summarize(run) {
  const { frames, arrivals } = run;
  const shown = frames.filter(([, len]) => len > 0);
  const full = frames.at(-1)[1];
  const at = (p) => (shown.find(([, len]) => len >= full * p) || shown.at(-1))[0];
  const distinct = [...new Set(frames.map(([, len]) => len))].filter((n) => n > 0);
  const codeLens = new Set(frames.flatMap(([, , lens]) => lens));
  const sourcesAhead = frames.filter(([, , , sources]) => sources > 3).length;
  const firstArrival = arrivals[0][0];
  // The page's own main thread, as the sampler saw it, between the first text arriving and
  // the last frame of the reveal: the longest a sample came later than its 25 ms, which is
  // how late a timer of the widget's could have run too. Nothing on a quiet machine, and
  // what a busy runner adds to every time here.
  const inWindow = frames.filter(([t]) => t >= firstArrival - 100 && t <= at(1) + 100);
  const stall = inWindow.slice(1).reduce((worst, [t], i) => Math.max(worst, t - inWindow[i][0] - SAMPLE_MS), 0);
  return {
    stall: Math.min(MAX_STALL_MS, Math.max(0, Math.round(stall))),
    firstArrival,
    lastArrival: arrivals.at(-1)[0],
    arrivedChars: arrivals.reduce((n, [, c]) => n + c, 0),
    firstText: shown[0][0],
    p25: at(0.25), p50: at(0.5), p75: at(0.75), p100: at(1),
    distinctLengths: distinct.length,
    codeLens: [...codeLens],
    sourcesAhead,
    full,
  };
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

async function openPage(cdp, base, { reducedMotion }) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  await cdp.send('Page.enable', {}, sessionId);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
  if (reducedMotion) {
    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] }, sessionId);
  }
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
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-paced-reveal-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { product } = await cdp.send('Browser.getVersion');
    console.log(`Chrome: ${product}`);

    // ---- paced
    const page = await openPage(cdp, base, { reducedMotion: false });
    // A screenshot part-way through the reveal, when a directory is given.
    const measuring = evaluate(cdp, page.sessionId, MEASURE);
    if (screenshotDir) {
      await Bun.sleep(THINK_MS + 1300);
      await screenshot(cdp, page.sessionId, screenshotDir, 'mid-reveal.png');
    }
    const paced = await measuring;
    await screenshot(cdp, page.sessionId, screenshotDir, 'after-reveal.png');
    const p = summarize(paced);
    console.log('--- paced (a reader with default motion)');
    console.log(JSON.stringify({ ...p, redraws: paced.redraws }));

    report(paced.visibility === 'visible', 'control: the page is visible, so its timers are not throttled', paced.visibility);
    report(p.arrivedChars >= REPLY.length - 5 && p.lastArrival - p.firstArrival < BURST_MS + p.stall,
      `control: the stream was a burst (${p.arrivedChars} characters in ${Math.round(p.lastArrival - p.firstArrival)} ms, under ${BURST_MS + p.stall})`);
    report(p.firstArrival >= THINK_MS - 100, `control: silent before it (first text at ${Math.round(p.firstArrival)} ms)`);
    report(paced.assistantBefore === 1, `control: a fresh conversation, so the reply measured is this one (${paced.assistantBefore} assistant message before it)`, paced.assistantBefore);
    report(p.firstText >= p.firstArrival - 5, `control: nothing was shown before the stream's first text (${Math.round(p.firstText)} against ${Math.round(p.firstArrival)} ms)`);
    report(p.full >= REPLY.length * 0.9, `the whole reply is on the page at the end (${p.full} characters shown)`);
    report(p.firstText - p.firstArrival < 50 + p.stall, `the first words are drawn the moment the first chunk arrives (${Math.round(p.firstText - p.firstArrival)} ms after, under ${50 + p.stall}; a tick would be 80 or more)`);
    report(p.p100 - p.firstText >= 150, `a burst is spread over a window, not one flash (${Math.round(p.p100 - p.firstText)} ms)`);
    report(p.p100 - p.lastArrival <= 650 + p.stall, `the whole reply is on screen within about half a second of the last chunk (${Math.round(p.p100 - p.lastArrival)} ms, within ${650 + p.stall})`);
    report(p.p100 - p.firstText <= 700 + p.stall, `so the reveal adds little (${Math.round(p.p100 - p.firstText)} ms from the first words to the last, within ${700 + p.stall})`);
    report(p.distinctLengths >= 4, `the reader saw the reply grow through ${p.distinctLengths} different lengths`);
    // Quartiles are read off sampled frames, and two can land on one frame where frames are far apart: so in order, and the first and third apart.
    report(p.p25 < p.p75 && p.p25 <= p.p50 && p.p50 <= p.p75 && p.p75 <= p.p100, `25%, 50%, 75% and 100% arrive in order (${[p.p25, p.p50, p.p75, p.p100].map(Math.round).join(', ')} ms)`);
    const codeChars = CODE.split('\n').slice(1, -1).join('\n').length;
    report(p.codeLens.length === 1 && Math.abs(p.codeLens[0] - codeChars) <= 2,
      `the code block was drawn once and whole (${JSON.stringify(p.codeLens)}, expected about ${codeChars})`);
    report(p.sourcesAhead === 0, 'no source was listed beyond the three the reply cites');

    // ---- reduced motion, as the browser emulates it
    const still = await openPage(cdp, base, { reducedMotion: true });
    const reduced = await evaluate(cdp, still.sessionId, MEASURE);
    const r = summarize(reduced);
    console.log('--- prefers-reduced-motion: reduce');
    console.log(JSON.stringify({ ...r, redraws: reduced.redraws }));
    report(reduced.reducedMotion === true, 'control: the browser reports prefers-reduced-motion: reduce', reduced.reducedMotion);
    report(reduced.assistantBefore === 1, `control: a fresh conversation, so the reply measured is this one (${reduced.assistantBefore} assistant message before it)`, reduced.assistantBefore);
    report(r.firstText >= r.firstArrival - 5, `control: nothing was shown before the stream's first text (${Math.round(r.firstText)} against ${Math.round(r.firstArrival)} ms)`);
    report(r.full >= REPLY.length * 0.9, 'the whole reply is on the page at the end');
    report(r.p100 - r.lastArrival < 500 + r.stall, `it is all shown within a moment of the last chunk (${Math.round(r.p100 - r.lastArrival)} ms after, under ${500 + r.stall})`);
    report(r.p100 - r.firstText < (p.p100 - p.firstText) / 2, `so the paced reveal above is the widget's doing, not the network's (${Math.round(r.p100 - r.firstText)} ms against ${Math.round(p.p100 - p.firstText)})`);
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
