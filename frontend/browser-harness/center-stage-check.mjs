#!/usr/bin/env bun
/**
 * Real-Chrome check of the center-stage AI Mode dialog (E6.1, #701), at a laptop's
 * 1280 x 900 and a phone's 390 x 844, in the light and the dark scheme.
 *
 * The Bun suite (frontend/test-widget-host-api.js) holds the logic in happy-dom, which
 * has no layout, no real Tab key and no painted colors. This runs the dialog in
 * headless Chrome: a page with a long body of its own and a button that opens AI Mode,
 * the widget against a community config shaped like NEMAR's (cards, a note, a docs link,
 * dataset questions, the teal theme), and these checks, per viewport and scheme:
 *
 * - the dialog is inside the viewport (a margin of 36 and 80 px on the laptop, the
 *   whole screen on the phone), and nothing scrolls sideways, the dialog's own start
 *   screen included;
 * - the page behind it is inert and does not scroll;
 * - the question field is 60 px tall, and every pill and the send button at least 44 px;
 * - the real Tab key (a trusted key event, which is what moves focus in a browser) never
 *   takes focus out of the dialog, in 40 presses either way, and wraps at both ends;
 * - Escape (a real key) docks the same window to the corner, with the thread, the
 *   session and what was typed untouched, and Close gives focus back to the page's
 *   own button;
 * - the colors likeliest to fall short reach their contrast ratio where Chrome paints
 *   them: text at 4.5:1, an icon at 3:1;
 * - under prefers-reduced-motion the pills' transition is immediate.
 *
 * And, with an approved plan in the conversation (board C3, E6.1): the plan rail beside
 * the thread, on a bubble and a capsule widget at 1280 x 900 (280 px wide, header across
 * the top, thread and input in the right column, the footer across both, nothing
 * scrolling sideways, rows and its "change" at least 36 px tall, its text at 4.5:1 in the
 * light and the dark scheme, the thread's strip hidden while the bar stays, a row taking
 * a click to its run card without touching the address, "change" opening Settings on the
 * memory choices, the header's Continue in notebook) and at a phone's 390 x 844 (no rail,
 * the strip showing, nothing scrolling sideways, the header's button icon only).
 *
 * It stands up its own server, so it needs no backend and no network. Screenshots are
 * written when a directory is given, as the argument or as CENTER_CHECK_SCREENSHOTS
 * (CI sets it, and uploads them when the check fails).
 *
 * Usage: bun frontend/browser-harness/center-stage-check.mjs [screenshot-dir]
 * Chrome is found at CHROME_PATH or where it installs (see chrome.js). Under CI a
 * missing Chrome fails; elsewhere it skips.
 */

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { connect, findChrome, launch } from './chrome.js';
import { run } from '../test-support/notebook-conversations.js';

const WIDGET = readFileSync(new URL('../osa-chat-widget.js', import.meta.url), 'utf8');
const RUNTIME_BUNDLE = readFileSync(new URL('../osa-runtime.bundle.js', import.meta.url), 'utf8');

let failed = 0;
function report(ok, label, detail) {
  if (ok) console.log(`ok: ${label}`);
  else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}

const COMMUNITY = 'centercheck';
const STORAGE_KEY = `center-check-${Math.random().toString(36).slice(2, 8)}`;
const DATASET = { id: 'on002718', title: 'Face processing EEG dataset for EEGLAB' };
const DOCS_URL = 'https://docs.example.org/web/analyze-in-browser/';
const WIDGET_CONFIG = {
  title: 'NEMAR Assistant',
  theme_color: '#5bbad5',
  theme_text_color: '#04121f',
  accent_color: '#257a92',
  user_bubble_color: '#5bbad5',
  user_bubble_text_color: '#04121f',
  color_scheme: 'auto',
  suggested_questions: ['Find EEG datasets with resting state recordings'],
  dataset_suggested_questions: [
    { text: 'What was recorded in {dataset_id}, and how?' },
    { text: 'Which event types are in the recordings of {dataset_id}?' },
    { text: 'How do I download {dataset_id}?' },
  ],
  center_start: {
    capabilities: [
      { title: 'Describe', text: "Summaries, methods and citations, from the dataset's own files." },
      { title: 'Look closer', text: 'Events, channels and recordings, read live from the archive.' },
      { title: 'Analyze', text: 'Plans the steps with you, then runs Python in your browser.' },
    ],
    note: "Code runs in your browser on the archive's streaming copy, which is lossy. Nothing you run is uploaded. AI can make mistakes.",
  },
  analysis_docs: { title: 'Analyze data in your browser', url: DOCS_URL },
};

const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>center</title>
<style>body{margin:0;font-family:system-ui,sans-serif;background:#fff;color:#0f172a}main{padding:24px 80px;min-height:2400px}h1{font-size:44px;max-width:640px;margin:60px 0 24px}</style></head>
<body><main id="host-page"><div style="font-size:26px;font-weight:700">NEMAR</div><h1>${DATASET.title}</h1>
<button id="host-pill" type="button" style="min-height:44px;padding:0 20px;border-radius:999px;border:0;background:#5bbad5;color:#04121f;font:600 15px system-ui">AI Mode</button>
<a id="host-link" href="#top">A link on the page</a></main>
<script>window.__OSA_TEST__ = true;</script>
<script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setDataset(${JSON.stringify(DATASET)});
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: ${JSON.stringify(COMMUNITY)}, storageKey: ${JSON.stringify(STORAGE_KEY)} });
  OSAChatWidget.init();
</script></body></html>`;

// A second community, with a runtime (the plan rail needs the bundle, which is only loaded
// for a community with client tools) and a conversation stored with an approved plan, three
// of its five steps done: the state of board C3. `preload_on: first_run` keeps Python from
// booting, so no network is needed.
const PLAN_COMMUNITY = 'centerplan';
const PLAN_STORAGE_KEY = `center-plan-check-${Math.random().toString(36).slice(2, 8)}`;
const PLAN_STEPS = [
  'Count face onsets per condition',
  'Cut epochs, -200 to 600 ms',
  'Average epochs per condition',
  'Plot famous vs scrambled ERPs',
  'Summarize, offer all 18 subjects',
];
const PLAN_CONFIG = {
  ...WIDGET_CONFIG,
  suggested_questions: [],
  dataset_suggested_questions: [],
};
// The same conversation in three states: `paused` (three of five done, the default), `failed`
// (two done, step 3 failed with no attempt left) and `stopped` (two done, the plan stopped
// before step 3). The last two paint the rows the first never does.
function planChat(variant) {
  const done = variant === 'paused' ? 3 : 2;
  const executions = PLAN_STEPS.slice(0, done).map((title, i) => run({
    callId: `plan-run-${i + 1}`,
    step: i + 1,
    attempt: 1,
    stepTitle: title,
    elapsedMs: 3400,
    description: title,
    code: `print(${i + 1})`,
    stdout: `${i + 1}\n`,
  }));
  if (variant === 'failed') {
    for (const attempt of [1, 2]) {
      executions.push(run({
        callId: `plan-run-3-${attempt}`,
        step: 3,
        attempt,
        stepTitle: PLAN_STEPS[2],
        elapsedMs: 900,
        description: PLAN_STEPS[2],
        code: 'raise ValueError("no epochs")',
        status: 'error',
        stderr: 'ValueError: no epochs',
        traceback: 'ValueError: no epochs',
      }));
    }
  }
  return JSON.stringify({
    version: 3,
    activeId: 'chat-plan',
    conversations: [{
      id: 'chat-plan',
      context: { type: 'dataset', id: DATASET.id },
      title: 'Compare ERPs for famous vs scrambled faces',
      sessionId: 'session-plan',
      createdAt: Date.now() - 120_000,
      updatedAt: Date.now() - 60_000,
      messages: [
        { role: 'assistant', content: 'Hello' },
        { role: 'user', content: 'Compare ERPs for famous vs scrambled faces' },
        {
          role: 'assistant',
          content: 'The plan is under way.',
          citations: [],
          plan: {
            callId: 'plan-1',
            findings: 'Three conditions, 18 subjects.',
            estPeakMb: 300,
            runMode: 'pause',
            status: variant === 'stopped' ? 'stopped' : 'approved',
            steps: PLAN_STEPS.map((title) => ({ title, detail: '' })),
          },
          executions,
        },
      ],
    }],
  });
}

function planPage(launcher, variant) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>center plan</title>
<style>body{margin:0;font-family:system-ui,sans-serif;background:#fff;color:#0f172a}main{padding:24px 80px;min-height:2400px}</style></head>
<body><main id="host-page"><button id="host-pill" type="button">AI Mode</button></main>
<script>
  window.__OSA_TEST__ = true;
  localStorage.setItem(${JSON.stringify(PLAN_STORAGE_KEY)}, ${JSON.stringify(planChat(variant))});
  sessionStorage.setItem(${JSON.stringify(`${PLAN_STORAGE_KEY}-session`)}, '1');
</script>
<script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setDataset({ id: ${JSON.stringify(DATASET.id)}, title: ${JSON.stringify(DATASET.title)}, zarr: true });
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: ${JSON.stringify(PLAN_COMMUNITY)}, storageKey: ${JSON.stringify(PLAN_STORAGE_KEY)}, launcher: ${JSON.stringify(launcher)} });
  OSAChatWidget.init();
</script></body></html>`;
}

function startServer() {
  const encoder = new TextEncoder();
  return Bun.serve({
    port: 0,
    hostname: '127.0.0.1',
    async fetch(request) {
      const { pathname } = new URL(request.url);
      const js = (body) => new Response(body, { headers: { 'content-type': 'text/javascript' } });
      if (pathname === '/') return new Response(PAGE, { headers: { 'content-type': 'text/html' } });
      if (pathname === '/osa-chat-widget.js') return js(WIDGET);
      if (pathname === '/osa-runtime.bundle.js') return js(RUNTIME_BUNDLE);
      if (pathname === '/plan') {
        const launcher = new URL(request.url).searchParams.get('launcher') === 'bubble' ? 'bubble' : 'capsule';
        const asked = new URL(request.url).searchParams.get('variant');
        const variant = asked === 'failed' || asked === 'stopped' ? asked : 'paused';
        return new Response(planPage(launcher, variant), { headers: { 'content-type': 'text/html' } });
      }
      if (pathname === `/api/${PLAN_COMMUNITY}`) {
        return Response.json({
          default_model: 'm',
          offered_models: [],
          widget: PLAN_CONFIG,
          client_tools: [{ name: 'execute_code', runtime: 'python', requires_permission: true }],
          runtime: { python: { pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 60 } } },
        });
      }
      if (pathname === '/api/health') return Response.json({ status: 'healthy' });
      if (pathname === `/api/${PLAN_COMMUNITY}/chat`) {
        const body = await request.json();
        // Held back, so the page can be read while the step is sent and not yet answered.
        await Bun.sleep(1500);
        const events = [
          { event: 'session', session_id: body.session_id || 'session-plan' },
          { event: 'content', content: 'Step 4 is next.' },
          { event: 'done' },
        ];
        return new Response(new ReadableStream({
          start(controller) {
            for (const event of events) controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
            controller.close();
          },
        }), { headers: { 'content-type': 'text/event-stream' } });
      }
      if (pathname === `/api/${COMMUNITY}/chat`) {
        const body = await request.json();
        const events = [
          { event: 'session', session_id: body.session_id || 'center-check-session' },
          { event: 'content', content: 'The N170 is largest at EEG065 to EEG068.' },
          { event: 'done' },
        ];
        return new Response(new ReadableStream({
          start(controller) {
            for (const event of events) controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
            controller.close();
          },
        }), { headers: { 'content-type': 'text/event-stream' } });
      }
      if (pathname === `/api/${COMMUNITY}`) {
        return Response.json({ default_model: 'm', offered_models: [], widget: WIDGET_CONFIG, client_tools: [], runtime: null });
      }
      return new Response('not found', { status: 404 });
    },
  });
}

// ------------------------------------------------------------------ the driver

async function evaluate(cdp, sessionId, expression, { userGesture = false } = {}) {
  const { result, exceptionDetails } = await cdp.send('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise: true, userGesture,
  }, sessionId);
  if (exceptionDetails) throw new Error(`page exception: ${exceptionDetails.exception?.description || exceptionDetails.text}`);
  return result.value;
}

async function waitFor(cdp, sessionId, expression, label, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(cdp, sessionId, expression)) return true;
    await Bun.sleep(50);
  }
  console.error(`  timed out waiting: ${label}`);
  return false;
}

async function screenshot(cdp, sessionId, dir, name) {
  if (!dir) return;
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, sessionId);
  writeFileSync(join(dir, name), Buffer.from(data, 'base64'));
  console.log(`  screenshot: ${join(dir, name)}`);
}

/** A real, trusted key press (down + up), the way physical hardware reports one. */
async function pressKey(cdp, sessionId, { key, code, windowsVirtualKeyCode, modifiers = 0 }) {
  await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key, code, windowsVirtualKeyCode, modifiers }, sessionId);
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode, modifiers }, sessionId);
}

const TAB = { key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 };
const SHIFT_TAB = { ...TAB, modifiers: 8 };
const ESCAPE = { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 };

async function openPage(cdp, base, { width, height }, path = '/') {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  await cdp.send('Page.enable', {}, sessionId);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, sessionId);
  // The community follows the device ('auto'): the "light" run is a light device, whatever
  // this machine's own setting is, and the dark run is the host page's choice.
  await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] }, sessionId);
  await cdp.send('Page.navigate', { url: `${base}${path}` }, sessionId);
  return { targetId, sessionId };
}

// The page's widget has its community config, and the page's own button has focus, as
// it does when a reader has just pressed it.
const WIDGET_READY = `(async () => {
  for (let i = 0; i < 400; i++) {
    const title = document.querySelector('.osa-chat-title');
    if (window.OSAChatWidget && title && title.firstChild && title.firstChild.textContent.trim() === 'NEMAR Assistant') return true;
    await new Promise((r) => setTimeout(r, 25));
  }
  return false;
})()`;

const GEOMETRY = `(() => {
  const win = document.querySelector('.osa-chat-window');
  const box = win.getBoundingClientRect();
  const sizes = (selector) => [...document.querySelectorAll(selector)]
    .filter((el) => el.getBoundingClientRect().height > 0)
    .map((el) => ({ width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height }));
  const body = document.querySelector('.osa-center-start-body');
  return {
    viewport: { width: window.innerWidth, height: window.innerHeight },
    box: { left: box.left, top: box.top, right: box.right, bottom: box.bottom },
    inside: box.left >= 0 && box.top >= 0 && box.right <= window.innerWidth && box.bottom <= window.innerHeight,
    sideways: document.documentElement.scrollWidth > window.innerWidth + 1 || win.scrollWidth > win.clientWidth + 1
      || (body ? body.scrollWidth > body.clientWidth + 1 : false),
    field: sizes('.osa-center-input')[0],
    pills: sizes('.osa-center-suggestion'),
    send: sizes('.osa-center-send')[0],
    cards: document.querySelectorAll('.osa-center-card').length,
    startShown: !!body && body.getBoundingClientRect().height > 0,
    headerButtons: sizes('.osa-chat-header button'),
    pageInert: document.getElementById('host-page').inert === true,
    pageOverflow: getComputedStyle(document.documentElement).overflow,
    role: win.getAttribute('role'),
    modal: win.getAttribute('aria-modal'),
    label: win.getAttribute('aria-label'),
  };
})()`;

// The contrast ratio (WCAG) of an element's text color against what is behind it: its
// own and its ancestors' backgrounds, composited from the first opaque one up, over
// white. Text must reach 4.5:1; a graphic (an icon) 3:1.
const CONTRAST_OF = (selector, property = 'color') => `(() => {
  const el = document.querySelector(${JSON.stringify(selector)});
  if (!el) return { ratio: null, why: 'no such element' };
  const parse = (value) => {
    const m = /rgba?\\(([^)]+)\\)/.exec(value);
    if (!m) return null;
    const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (top, under) => ({
    r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1,
  });
  const layers = [];
  for (let n = el; n; n = n.parentElement) {
    const bg = parse(getComputedStyle(n).backgroundColor);
    if (bg && bg.a > 0) layers.push(bg);
    if (bg && bg.a >= 1) break;
  }
  let backdrop = { r: 255, g: 255, b: 255, a: 1 };
  for (const layer of layers.reverse()) backdrop = over(layer, backdrop);
  const fg = parse(getComputedStyle(el)[${JSON.stringify(property)}]);
  if (!fg) return { ratio: null, why: getComputedStyle(el)[${JSON.stringify(property)}] };
  const flat = over(fg, backdrop);
  const lum = (c) => {
    const f = (v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const a = lum(flat);
  const b = lum(backdrop);
  return { ratio: Math.round(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) * 100) / 100 };
})()`;

// Text, at 4.5:1; the send arrow, a graphic, at 3:1.
const CONTRASTS = [
  ['.osa-center-heading', 4.5, 'the heading'],
  ['.osa-center-pill', 4.5, 'the AI Mode pill'],
  ['.osa-center-context code', 4.5, 'the dataset id'],
  ['.osa-center-context', 4.5, 'the dataset title'],
  ['.osa-history-btn', 4.5, 'History'],
  ['.osa-center-dock-btn', 4.5, 'Move to corner'],
  ['.osa-close-btn', 3, 'Close'],
  ['.osa-center-input', 4.5, 'the question field'],
  ['.osa-center-send', 3, 'the send arrow'],
  ['.osa-center-suggestion', 4.5, 'a pill'],
  ['.osa-center-card-title', 4.5, 'a card\'s heading'],
  ['.osa-center-card-text', 4.5, 'a card\'s text'],
  ['.osa-center-note', 4.5, 'the footer note'],
  ['.osa-center-docs-link', 4.5, 'the "How it works" link'],
];

async function checkContrast(call, scheme, viewport) {
  for (const [selector, minimum, what] of CONTRASTS) {
    const { ratio, why } = await call(CONTRAST_OF(selector));
    report(ratio !== null && ratio >= minimum, `${viewport}, ${scheme}: ${what} reads at ${minimum}:1 or better`, ratio === null ? why : ratio);
  }
}

async function checkViewport(cdp, base, screenshotDir, viewport) {
  const { name, width, height } = viewport;
  console.log(`--- ${name}: ${width} x ${height}`);
  const page = await openPage(cdp, base, viewport);
  const { sessionId } = page;
  const call = (expression, options) => evaluate(cdp, sessionId, expression, options);
  const press = (key) => pressKey(cdp, sessionId, key);
  report(await call(WIDGET_READY), `${name}: the widget has its community config`);

  // The page's own button has focus, as after a click; the dialog opens from it.
  await call(`document.getElementById('host-pill').focus()`);
  await call(`OSAChatWidget.open({ mode: 'center' })`, { userGesture: true });
  report(await waitFor(cdp, sessionId, `document.querySelector('.osa-chat-window.open.osa-center') !== null`, 'the dialog opens'), `${name}: open({mode: 'center'}) shows the dialog`);
  const g = await call(GEOMETRY);
  report(g.role === 'dialog' && g.modal === 'true' && g.label === `AI Mode for ${DATASET.id}`, `${name}: role=dialog, aria-modal and the dataset in its name`, g);
  report(g.inside, `${name}: the dialog is inside the ${g.viewport.width} x ${g.viewport.height} viewport`, g.box);
  if (width > 600) {
    report(Math.round(g.box.left) === 80 && Math.round(g.box.top) === 36 && Math.round(g.viewport.width - g.box.right) === 80 && Math.round(g.viewport.height - g.box.bottom) === 36,
      `${name}: with the board's 80 and 36 px margins`, g.box);
  } else {
    report(g.box.left === 0 && g.box.top === 0 && g.box.right === g.viewport.width && g.box.bottom === g.viewport.height, `${name}: it fills the screen`, g.box);
  }
  report(!g.sideways, `${name}: nothing scrolls sideways`);
  report(g.pageInert && g.pageOverflow === 'hidden', `${name}: the page behind it is inert and does not scroll`, [g.pageInert, g.pageOverflow]);
  report(g.startShown && g.cards === 3, `${name}: the start screen shows, with the three cards`, [g.startShown, g.cards]);
  report(Math.round(g.field.height) === 60, `${name}: the question field is 60 px tall`, g.field);
  report(g.pills.length === 3 && g.pills.every((p) => p.height >= 44), `${name}: the three pills are at least 44 px tall`, g.pills);
  report(g.send.width >= 44 && g.send.height >= 44, `${name}: the send button is at least 44 x 44`, g.send);
  report(g.headerButtons.every((b) => b.width >= 36 && b.height >= 36), `${name}: and the header's buttons at least 36 x 36`, g.headerButtons);
  const inField = await call(`document.activeElement === document.querySelector('.osa-center-input')`);
  report(inField, `${name}: focus is in the question field`);
  const scheme = await call(`getComputedStyle(document.querySelector('.osa-chat-window')).backgroundColor`);
  report(scheme === 'rgb(255, 255, 255)', `${name}: the light run is on the light panel`, scheme);
  await screenshot(cdp, sessionId, screenshotDir, `center-start-${name}-light.png`);

  // The real Tab key: forward and back, never out of the dialog.
  const where = `(() => {
    const el = document.activeElement;
    return { inside: document.querySelector('.osa-chat-window').contains(el), at: (el.className || el.tagName) + '|' + (el.textContent || '').slice(0, 24) };
  })()`;
  let escaped = null;
  const visited = new Set();
  for (let i = 0; i < 40; i++) {
    await press(TAB);
    const now = await call(where);
    if (!now.inside && !escaped) escaped = `Tab ${i + 1}: ${now.at}`;
    visited.add(now.at);
  }
  report(!escaped, `${name}: 40 presses of Tab never leave the dialog`, escaped);
  if (escaped) {
    // What the widget's trap thinks the controls are: the last of them is where Tab wraps.
    console.error('  the trap\'s controls:', JSON.stringify(await call(`OSAChatWidget.__center.focusables(document.querySelector('.osa-chat-window')).map((el) => (el.className || el.tagName) + '|' + (el.textContent || '').trim().slice(0, 20))`)));
  }
  report(visited.size >= 8 && visited.size < 40, `${name}: and cycle through its controls, wrapping (${visited.size} distinct)`, visited.size);
  let escapedBack = null;
  for (let i = 0; i < 40; i++) {
    await press(SHIFT_TAB);
    const now = await call(where);
    if (!now.inside && !escapedBack) escapedBack = `Shift+Tab ${i + 1}: ${now.at}`;
  }
  report(!escapedBack, `${name}: 40 presses of Shift+Tab never leave it either`, escapedBack);
  const onPage = await call(`document.activeElement === document.getElementById('host-pill') || document.activeElement === document.getElementById('host-link')`);
  report(!onPage, `${name}: and focus never reached the page's own controls`);

  await checkContrast(call, 'light', name);

  // A question from a pill: the thread takes the start screen's place, in a column.
  await call(`document.querySelector('.osa-center-suggestion').click()`, { userGesture: true });
  report(await waitFor(cdp, sessionId, `document.querySelectorAll('.osa-chat-messages .osa-message').length >= 3 && !document.querySelector('.osa-chat-input input').disabled`, 'the reply'), `${name}: a pill sends its question and the reply arrives`);
  const thread = await call(`(() => {
    const first = document.querySelector('.osa-chat-messages .osa-message').getBoundingClientRect();
    const messages = document.querySelector('.osa-chat-messages').getBoundingClientRect();
    return {
      startHidden: document.querySelector('.osa-center-start').hidden,
      sendHeight: document.querySelector('.osa-send-btn').getBoundingClientRect().height,
      inputHeight: document.querySelector('.osa-chat-input input').getBoundingClientRect().height,
      column: Math.max(...[...document.querySelectorAll('.osa-chat-messages .osa-message')].map((m) => m.getBoundingClientRect().width)),
      left: first.left - messages.left,
    };
  })()`);
  report(thread.startHidden, `${name}: the start screen is gone`);
  report(thread.sendHeight >= 44 && thread.inputHeight >= 44, `${name}: the chat's send button and field are at least 44 px`, thread);
  report(thread.column <= 760, `${name}: the thread is a column of at most 760 px`, thread.column);
  await screenshot(cdp, sessionId, screenshotDir, `center-thread-${name}-light.png`);

  // Escape docks: the same window, the same thread, session and draft.
  await call(`(() => {
    window.__nodes = [...document.querySelectorAll('.osa-chat-messages .osa-message')];
    document.querySelector('.osa-chat-input input').value = 'A draft';
    document.querySelector('.osa-chat-input input').focus();
    window.__session = OSAChatWidget.__browser.getSessionId();
  })()`);
  await press(ESCAPE);
  report(await waitFor(cdp, sessionId, `!document.querySelector('.osa-chat-window').classList.contains('osa-center')`, 'docks'), `${name}: Escape docks the window`);
  const docked = await call(`(() => {
    const win = document.querySelector('.osa-chat-window');
    const box = win.getBoundingClientRect();
    const nodes = [...document.querySelectorAll('.osa-chat-messages .osa-message')];
    return {
      open: win.classList.contains('open'),
      nodes: nodes.length === window.__nodes.length && nodes.every((n, i) => n === window.__nodes[i]),
      session: OSAChatWidget.__browser.getSessionId() === window.__session,
      draft: document.querySelector('.osa-chat-input input').value,
      inert: document.querySelectorAll('[inert]').length,
      overflow: getComputedStyle(document.documentElement).overflow,
      corner: box.right <= window.innerWidth - 8 && box.bottom <= window.innerHeight - 8 && box.width < window.innerWidth - 100 || window.innerWidth <= 600,
      focusIn: win.contains(document.activeElement),
      role: win.getAttribute('role'),
    };
  })()`);
  report(docked.open && docked.nodes && docked.session && docked.draft === 'A draft', `${name}: it stays open, with the same message nodes, session and draft`, docked);
  report(docked.inert === 0 && docked.overflow !== 'hidden' && docked.role === null, `${name}: the page is live again`, docked);
  report(docked.corner && docked.focusIn, `${name}: the window is in the corner and focus is in it`, docked);
  await screenshot(cdp, sessionId, screenshotDir, `center-docked-${name}.png`);

  // Back to center, and Close: focus goes back to the page's own button.
  await call(`document.getElementById('host-pill').focus()`);
  await call(`OSAChatWidget.setMode('center')`);
  report(await waitFor(cdp, sessionId, `document.querySelector('.osa-chat-window').classList.contains('osa-center')`, 'center again'), `${name}: setMode('center') brings the same conversation back to the middle`);
  await call(`document.querySelector('.osa-close-btn').click()`, { userGesture: true });
  const closed = await call(`({
    open: document.querySelector('.osa-chat-window').classList.contains('open'),
    onPill: document.activeElement === document.querySelector('.osa-chat-button'),
  })`);
  report(!closed.open, `${name}: Close closes the window`, closed);

  // Open from the page's own button, close, and check the page's button has focus again.
  await call(`document.getElementById('host-pill').focus()`);
  await call(`OSAChatWidget.open({ mode: 'center' })`, { userGesture: true });
  await call(`document.querySelector('.osa-close-btn').click()`, { userGesture: true });
  report(await call(`document.activeElement === document.getElementById('host-pill')`), `${name}: Close gives focus back to the page's own button`);

  // The dark scheme, on the start screen of a new chat; and reduced motion.
  await call(`OSAChatWidget.setColorScheme('dark')`);
  await call(`OSAChatWidget.open({ mode: 'center' })`, { userGesture: true });
  await call(`document.querySelector('.osa-history-btn').click()`, { userGesture: true });
  await call(`document.querySelector('.osa-history-new').click()`, { userGesture: true });
  report(await waitFor(cdp, sessionId, `!document.querySelector('.osa-center-start').hidden`, 'the start screen again'), `${name}: a new chat shows the start screen again`);
  const dark = await call(GEOMETRY);
  const panel = await call(`getComputedStyle(document.querySelector('.osa-chat-window')).backgroundColor`);
  report(panel === 'rgb(17, 24, 39)', `${name}: the dialog is the dark panel's color`, panel);
  report(dark.inside && !dark.sideways && dark.pills.every((p) => p.height >= 44) && dark.send.height >= 44, `${name}: and fits, with every pill and the send button at least 44 px`, dark);
  await checkContrast(call, 'dark', name);
  await screenshot(cdp, sessionId, screenshotDir, `center-start-${name}-dark.png`);
  await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }, { name: 'prefers-reduced-motion', value: 'reduce' }] }, sessionId);
  const reduced = await call(`parseFloat(getComputedStyle(document.querySelector('.osa-center-suggestion')).transitionDuration) <= 0.001`);
  report(reduced, `${name}: under prefers-reduced-motion the pills' transition is immediate`);
  await cdp.send('Target.closeTarget', { targetId: page.targetId });
}

// ------------------------------------------------------------------ the plan rail (board C3)

const RAIL_GEOMETRY = `(() => {
  const win = document.querySelector('.osa-chat-window');
  const winBox = win.getBoundingClientRect();
  const box = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height, display: style.display };
  };
  const heights = (selector) => [...document.querySelectorAll(selector)].map((el) => Math.round(el.getBoundingClientRect().height * 10) / 10);
  const railEl = document.querySelector('.osa-plan-rail');
  const threadEl = document.querySelector('.osa-chat-messages');
  const headingEl = document.querySelector('.osa-plan-rail-heading');
  return {
    win: { left: winBox.left, top: winBox.top, right: winBox.right, bottom: winBox.bottom, width: winBox.width },
    rail: box('.osa-plan-rail'),
    header: box('.osa-chat-header'),
    thread: box('.osa-chat-messages'),
    input: box('.osa-chat-input'),
    disclaimer: box('.osa-ai-disclaimer'),
    footer: box('.osa-combined-footer'),
    strip: box('.osa-plan-strip'),
    bar: box('.osa-plan-bar'),
    notebookBtn: box('.osa-center-notebook-btn'),
    notebookHidden: document.querySelector('.osa-center-notebook-btn').hidden,
    notebookLabel: box('.osa-center-notebook-btn .osa-center-label'),
    dock: box('.osa-center-dock-btn'),
    rowHeights: heights('.osa-plan-rail-step'),
    changeHeight: heights('.osa-plan-rail-change')[0],
    rows: [...document.querySelectorAll('.osa-plan-rail ol > li')].map((li) => ({ cls: li.className.replace('osa-plan-rail-row ', ''), button: !!li.querySelector('button') })),
    heading: headingEl ? headingEl.textContent : null,
    headingCase: headingEl ? getComputedStyle(headingEl).textTransform : null,
    sideways: document.documentElement.scrollWidth > window.innerWidth + 1 || win.scrollWidth > win.clientWidth + 1
      || (railEl ? railEl.scrollWidth > railEl.clientWidth + 1 : false)
      || threadEl.scrollWidth > threadEl.clientWidth + 1,
    barButtons: [...document.querySelectorAll('.osa-plan-bar button')].map((b) => b.textContent.trim()),
  };
})()`;

// Text on the rail, at 4.5:1; the check mark, a graphic, at 3:1.
const RAIL_CONTRASTS = [
  ['.osa-plan-rail-heading', 4.5, 'the heading'],
  ['.osa-plan-rail-done .osa-plan-rail-title', 4.5, 'a done row'],
  ['.osa-plan-rail-current .osa-plan-rail-title', 4.5, 'the current row'],
  ['.osa-plan-rail-pending .osa-plan-rail-title', 4.5, 'a pending row'],
  ['.osa-plan-rail-mode', 4.5, 'the run mode'],
  ['.osa-plan-rail-memory', 4.5, 'the memory budget'],
  ['.osa-plan-rail-change', 4.5, 'the change button'],
  ['.osa-plan-rail-done .osa-plan-rail-icon', 3, 'the check mark'],
  // The rings, graphics too: the current step's, and a pending step's.
  ['.osa-plan-rail-dot-current', 3, 'the current ring', 'borderTopColor'],
  ['.osa-plan-rail-pending .osa-plan-rail-dot', 3, 'a pending ring', 'borderTopColor'],
];
// What only a failed or a stopped plan paints, which the paused fixture never shows.
const RAIL_STATE_CONTRASTS = {
  failed: [
    ['.osa-plan-rail-failed .osa-plan-rail-title', 4.5, 'the failed row'],
    ['.osa-plan-rail-failed .osa-plan-rail-icon', 3, 'the failed mark'],
    ['.osa-plan-rail-done .osa-plan-rail-icon', 3, 'the check mark'],
    ['.osa-plan-rail-pending .osa-plan-rail-dot', 3, 'a pending ring', 'borderTopColor'],
  ],
  stopped: [
    ['.osa-plan-rail-stopped .osa-plan-rail-title', 4.5, 'the stopped row'],
    ['.osa-plan-rail-stopped .osa-plan-rail-icon', 3, 'the stopped mark'],
    ['.osa-plan-rail-heading', 4.5, 'the "Plan stopped" heading'],
    ['.osa-plan-rail-pending .osa-plan-rail-dot', 3, 'a pending ring', 'borderTopColor'],
  ],
};
const RAIL_NOTEBOOK_CONTRAST = ['.osa-center-notebook-btn', 4.5, 'Continue in notebook'];

async function checkPlanRail(cdp, base, screenshotDir, viewport, launcher) {
  const { name, width, height } = viewport;
  const label = `${name} ${launcher} plan`;
  console.log(`--- ${label}: ${width} x ${height}`);
  const page = await openPage(cdp, base, viewport, `/plan?launcher=${launcher}`);
  const { sessionId } = page;
  const call = (expression, options) => evaluate(cdp, sessionId, expression, options);
  report(await call(WIDGET_READY), `${label}: the widget has its community config`);
  await call(`document.getElementById('host-pill').focus()`);
  await call(`OSAChatWidget.open({ mode: 'center' })`, { userGesture: true });
  report(
    await waitFor(cdp, sessionId, `document.querySelector('.osa-chat-window.open.osa-center') !== null && OSAChatWidget.__browser.getBrowserTools() !== null && document.querySelector('.osa-chat-messages .osa-run-card') !== null`, 'the dialog, the runtime and the chat'),
    `${label}: the dialog opens on the stored conversation`
  );
  if (width <= 900) {
    // A phone: the strip and the bar carry the plan, and no rail is drawn.
    const g = await call(RAIL_GEOMETRY);
    report(g.rail === null || (g.rail.display === 'none' && g.rail.width === 0), `${label}: the rail is not drawn`, g.rail);
    report(g.strip !== null && g.strip.height > 0 && g.strip.display !== 'none', `${label}: the strip in the thread shows`, g.strip);
    report(g.bar !== null && g.bar.height > 0 && g.barButtons.includes('Run step 4'), `${label}: and so does the bar, with Run step 4`, g.barButtons);
    report(!g.sideways, `${label}: nothing scrolls sideways`);
    report(g.win.width === width, `${label}: the dialog is the whole screen`, g.win);
    report(!g.notebookHidden && g.notebookBtn.width >= 36 && g.notebookBtn.height >= 36, `${label}: Continue in notebook is in the header, at least 36 x 36`, g.notebookBtn);
    report(g.notebookLabel === null || g.notebookLabel.display === 'none' || g.notebookLabel.width === 0, `${label}: as an icon only`, g.notebookLabel);
    report(g.notebookBtn.right <= g.dock.left + 1, `${label}: before Move to corner`, [g.notebookBtn, g.dock]);
    await screenshot(cdp, sessionId, screenshotDir, `center-plan-${name}-${launcher}-light.png`);
    await call(`OSAChatWidget.setColorScheme('dark')`);
    // The header's buttons fade their background over 200 ms; read the colors after.
    await Bun.sleep(400);
    const dark = await call(RAIL_GEOMETRY);
    report(!dark.sideways && (dark.rail === null || dark.rail.display === 'none'), `${label}, dark: still no rail and nothing scrolls sideways`);
    const c = await call(CONTRAST_OF('.osa-center-notebook-btn'));
    report(c.ratio !== null && c.ratio >= 4.5, `${label}, dark: the header button reads at 4.5:1`, c.ratio);
    await screenshot(cdp, sessionId, screenshotDir, `center-plan-${name}-${launcher}-dark.png`);
    await cdp.send('Target.closeTarget', { targetId: page.targetId });
    return;
  }

  const g = await call(RAIL_GEOMETRY);
  report(g.rail !== null && Math.round(g.rail.width) === 280, `${label}: the rail is 280 px wide`, g.rail && g.rail.width);
  report(g.rail !== null && g.rail.display === 'flex', `${label}: and drawn`, g.rail && g.rail.display);
  if (!g.rail) {
    await cdp.send('Target.closeTarget', { targetId: page.targetId });
    return;
  }
  report(Math.abs(g.rail.left - g.win.left) < 1.5, `${label}: at the window's left edge`, [g.rail.left, g.win.left]);
  report(g.header.width >= g.win.width - 1 && g.header.bottom <= g.rail.top + 1, `${label}: the header runs across the top, above the rail`, [g.header, g.rail]);
  report(g.thread.left >= g.rail.right - 1 && g.input.left >= g.rail.right - 1, `${label}: the thread and its input are in the right column`, [g.thread.left, g.input.left, g.rail.right]);
  report(g.rail.bottom <= g.disclaimer.top + 1 || g.disclaimer.height === 0, `${label}: the rail ends above the disclaimer`, [g.rail.bottom, g.disclaimer]);
  report(g.footer.width >= g.win.width - 1 && g.footer.left <= g.win.left + 1, `${label}: the footer runs across both columns`, g.footer);
  report(g.disclaimer.height === 0 || g.disclaimer.width >= g.win.width - 1, `${label}: and so does the disclaimer`, g.disclaimer);
  report(!g.sideways, `${label}: nothing scrolls sideways`);
  report(g.rowHeights.length === 5 && g.rowHeights.every((h) => h >= 36), `${label}: the five rows are at least 36 px tall`, g.rowHeights);
  report(g.changeHeight >= 36, `${label}: and "change" is`, g.changeHeight);
  report(g.heading === 'Plan · 3 of 5 done' && g.headingCase === 'uppercase', `${label}: the heading reads PLAN · 3 OF 5 DONE`, [g.heading, g.headingCase]);
  report(JSON.stringify(g.rows.map((r) => [r.cls, r.button])) === JSON.stringify([
    ['osa-plan-rail-done', true], ['osa-plan-rail-done', true], ['osa-plan-rail-done', true], ['osa-plan-rail-current', false], ['osa-plan-rail-pending', false],
  ]), `${label}: three done rows that are buttons, then the current and a pending row as text`, g.rows);
  report(g.strip !== null && g.strip.display === 'none', `${label}: the strip in the thread is hidden`, g.strip);
  report(g.bar !== null && g.bar.height > 0 && g.barButtons.includes('Run step 4') && g.barButtons.includes('Change step 4'), `${label}: the paused bar stays, with Run step 4 and Change step 4`, g.barButtons);
  if (launcher === 'capsule') {
    report(!g.notebookHidden && g.notebookBtn.height >= 36 && g.notebookLabel.width > 0, `${label}: Continue in notebook is in the header, with its label`, [g.notebookHidden, g.notebookBtn]);
    report(g.notebookBtn.right <= g.dock.left + 1, `${label}: before Move to corner`, [g.notebookBtn, g.dock]);
  } else {
    report(g.notebookHidden && g.notebookBtn.display === 'none', `${label}: no notebook tab, so no Continue in notebook`, [g.notebookHidden, g.notebookBtn]);
  }
  await screenshot(cdp, sessionId, screenshotDir, `center-plan-${name}-${launcher}-light.png`);

  for (const [selector, minimum, what, property] of launcher === 'capsule' ? [...RAIL_CONTRASTS, RAIL_NOTEBOOK_CONTRAST] : RAIL_CONTRASTS) {
    const { ratio, why } = await call(CONTRAST_OF(selector, property));
    report(ratio !== null && ratio >= minimum, `${label}, light: ${what} reads at ${minimum}:1 or better`, ratio === null ? why : ratio);
  }

  // The real Tab key: from the header's last button, the rail, then the thread, then the input.
  await call(`document.querySelector('.osa-close-btn').focus()`);
  const seen = [];
  for (let i = 0; i < 60; i++) {
    await pressKey(cdp, sessionId, TAB);
    seen.push(await call(`(() => { const el = document.activeElement; return el.closest('.osa-plan-rail') ? 'rail' : el.closest('.osa-chat-messages') ? 'thread' : el.closest('.osa-chat-input') ? 'input' : el.closest('.osa-chat-header') ? 'header' : 'other'; })()`));
  }
  const places = seen.filter((place, i) => i === 0 || place !== seen[i - 1]);
  report(JSON.stringify(places.slice(0, 3)) === JSON.stringify(['rail', 'thread', 'input']), `${label}: from the header's last button, Tab goes to the rail, then the thread, then the input`, places);

  // A row: a click takes you to its run card, without changing the address.
  const before = await call(`({ hash: location.hash, href: location.href })`);
  await call(`document.querySelector('.osa-plan-rail-done button[data-plan-rail-step="2"]').click()`, { userGesture: true });
  const after = await call(`({
    focused: document.activeElement === document.querySelector('.osa-run-card[data-plan-step="2"]'),
    hash: location.hash,
    href: location.href,
  })`);
  report(after.focused, `${label}: a done row's click focuses that step's run card`, after);
  report(after.hash === before.hash && after.href === before.href, `${label}: and the address is as it was`, [before, after]);

  // change opens Settings on the memory choices.
  await call(`document.querySelector('.osa-plan-rail-change').click()`, { userGesture: true });
  const settings = await call(`({
    open: !!document.querySelector('.osa-settings-overlay.open'),
    focus: !!document.activeElement && document.activeElement.classList.contains('osa-memory-radio') && document.activeElement.checked,
  })`);
  report(settings.open && settings.focus, `${label}: change opens Settings with focus on the memory choice that is on`, settings);
  await pressKey(cdp, sessionId, ESCAPE);
  report(await call(`!document.querySelector('.osa-settings-overlay.open') && document.querySelector('.osa-chat-window').classList.contains('osa-center')`), `${label}: Escape closes Settings first, and the dialog stays`);

  // Docking: the rail goes, the thread stays the same node.
  await call(`window.__thread = document.querySelector('.osa-chat-messages'); window.__cards = [...document.querySelectorAll('.osa-run-card')]; 0`);
  await call(`OSAChatWidget.setMode('corner')`);
  const docked = await call(`({
    rail: !!document.querySelector('.osa-plan-rail'),
    sameThread: document.querySelector('.osa-chat-messages') === window.__thread,
    sameCards: document.querySelectorAll('.osa-run-card').length === window.__cards.length
      && [...document.querySelectorAll('.osa-run-card')].every((c, i) => c === window.__cards[i]),
  })`);
  report(!docked.rail && docked.sameThread && docked.sameCards, `${label}: docking removes the rail and keeps the thread and its cards`, docked);
  await screenshot(cdp, sessionId, screenshotDir, `center-plan-docked-${launcher}.png`);
  await call(`OSAChatWidget.setMode('center')`);
  report(await call(`!!document.querySelector('.osa-plan-rail') && document.querySelector('.osa-chat-messages') === window.__thread`), `${label}: raising it puts the rail back`);

  // The dark scheme.
  await call(`OSAChatWidget.setColorScheme('dark')`);
  await Bun.sleep(400);
  const dark = await call(RAIL_GEOMETRY);
  report(!dark.sideways && Math.round(dark.rail.width) === 280 && dark.rowHeights.every((h) => h >= 36), `${label}, dark: the rail is 280 px, its rows at least 36 px, nothing scrolls sideways`, dark.rowHeights);
  for (const [selector, minimum, what, property] of launcher === 'capsule' ? [...RAIL_CONTRASTS, RAIL_NOTEBOOK_CONTRAST] : RAIL_CONTRASTS) {
    const { ratio, why } = await call(CONTRAST_OF(selector, property));
    report(ratio !== null && ratio >= minimum, `${label}, dark: ${what} reads at ${minimum}:1 or better`, ratio === null ? why : ratio);
  }
  await screenshot(cdp, sessionId, screenshotDir, `center-plan-${name}-${launcher}-dark.png`);
  await cdp.send('Target.closeTarget', { targetId: page.targetId });
}

// A failed plan and a stopped one on the laptop rail, which the paused fixture does not
// paint: the rows say their state, and their colors read in both schemes.
async function checkPlanRailState(cdp, base, screenshotDir, variant) {
  const label = `laptop capsule ${variant} plan`;
  console.log(`--- ${label}: 1280 x 900`);
  const page = await openPage(cdp, base, { name: 'laptop', width: 1280, height: 900 }, `/plan?launcher=capsule&variant=${variant}`);
  const { sessionId } = page;
  const call = (expression, options) => evaluate(cdp, sessionId, expression, options);
  report(await call(WIDGET_READY), `${label}: the widget has its community config`);
  await call(`document.getElementById('host-pill').focus()`);
  await call(`OSAChatWidget.open({ mode: 'center' })`, { userGesture: true });
  report(
    await waitFor(cdp, sessionId, `document.querySelector('.osa-chat-window.open.osa-center .osa-plan-rail') !== null && document.querySelector('.osa-chat-messages .osa-run-card') !== null`, 'the dialog and its rail'),
    `${label}: the dialog opens with the rail`
  );
  const g = await call(RAIL_GEOMETRY);
  const expected = variant === 'failed'
    ? ['osa-plan-rail-done', 'osa-plan-rail-done', 'osa-plan-rail-failed', 'osa-plan-rail-pending', 'osa-plan-rail-pending']
    : ['osa-plan-rail-done', 'osa-plan-rail-done', 'osa-plan-rail-stopped', 'osa-plan-rail-pending', 'osa-plan-rail-pending'];
  report(JSON.stringify(g.rows.map((r) => r.cls)) === JSON.stringify(expected), `${label}: the rows are done, done, ${variant}, pending, pending`, g.rows);
  report(!g.sideways && g.rowHeights.length === 5 && g.rowHeights.every((h) => h >= 36), `${label}: five rows, at least 36 px tall, nothing scrolls sideways`, g.rowHeights);
  for (const scheme of ['light', 'dark']) {
    if (scheme === 'dark') {
      await call(`OSAChatWidget.setColorScheme('dark')`);
      await Bun.sleep(400);
    }
    for (const [selector, minimum, what, property] of RAIL_STATE_CONTRASTS[variant]) {
      const { ratio, why } = await call(CONTRAST_OF(selector, property));
      report(ratio !== null && ratio >= minimum, `${label}, ${scheme}: ${what} reads at ${minimum}:1 or better`, ratio === null ? why : ratio);
    }
    await screenshot(cdp, sessionId, screenshotDir, `center-plan-${variant}-${scheme}.png`);
  }
  await cdp.send('Target.closeTarget', { targetId: page.targetId });
}

// Run step N in the bar replaces the button that was pressed, and the strip it used to
// send focus to is hidden while the rail shows: focus must land in the dialog, on the rail,
// never fall to the page. happy-dom cannot see this; focus() on a display:none element is
// only a no-op in a browser.
async function checkRunStepFocus(cdp, base) {
  const label = 'laptop capsule plan, Run step 4';
  console.log(`--- ${label}: 1280 x 900`);
  const page = await openPage(cdp, base, { name: 'laptop', width: 1280, height: 900 }, '/plan?launcher=capsule');
  const { sessionId } = page;
  const call = (expression, options) => evaluate(cdp, sessionId, expression, options);
  report(await call(WIDGET_READY), `${label}: the widget has its community config`);
  await call(`document.getElementById('host-pill').focus()`);
  await call(`OSAChatWidget.open({ mode: 'center' })`, { userGesture: true });
  report(
    await waitFor(cdp, sessionId, `document.querySelector('.osa-chat-window.open.osa-center .osa-plan-rail') !== null && document.querySelector('.osa-plan-bar-run') !== null`, 'the rail and the bar'),
    `${label}: the dialog opens with the rail and the paused bar`
  );
  report(await call(`getComputedStyle(document.querySelector('.osa-plan-strip')).display === 'none'`), `${label}: the strip is hidden, so it cannot take focus`);
  // The click sends "Continue with step 4."; the test server answers it after a pause.
  await call(`document.querySelector('.osa-plan-bar-run').click()`, { userGesture: true });
  report(
    await waitFor(cdp, sessionId, `[...document.querySelectorAll('.osa-chat-messages .osa-message')].some((m) => m.textContent.includes('Continue with step 4.'))`, 'the sent step'),
    `${label}: the step is sent`
  );
  const where = await call(`(() => {
    const el = document.activeElement;
    return {
      tag: el ? el.tagName : null,
      inDialog: !!el && !!el.closest('.osa-chat-window'),
      rail: !!el && el.classList.contains('osa-plan-rail'),
      answered: [...document.querySelectorAll('.osa-chat-messages .osa-message')].some((m) => m.textContent.includes('Step 4 is next.')),
    };
  })()`);
  report(!where.answered, `${label}: read before the answer arrives`, where);
  report(where.tag !== 'BODY' && where.inDialog, `${label}: focus is inside the dialog, not on the page`, where);
  report(where.rail, `${label}: on the rail, which says what the hidden strip would`, where);
  report(
    await waitFor(cdp, sessionId, `[...document.querySelectorAll('.osa-chat-messages .osa-message')].some((m) => m.textContent.includes('Step 4 is next.'))`, 'the reply'),
    `${label}: the step is answered`
  );
  const after = await call(`(() => { const el = document.activeElement; return { tag: el && el.tagName, inDialog: !!el && !!el.closest('.osa-chat-window') }; })()`);
  report(after.tag !== 'BODY' && after.inDialog, `${label}: and focus is still in the dialog once it is`, after);
  await cdp.send('Target.closeTarget', { targetId: page.targetId });
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
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-center-check-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { product } = await cdp.send('Browser.getVersion');
    console.log(`Chrome: ${product}`);
    for (const viewport of [
      { name: 'laptop', width: 1280, height: 900 },
      { name: 'phone', width: 390, height: 844 },
    ]) {
      await checkViewport(cdp, base, screenshotDir, viewport);
    }
    // The plan rail, on the capsule launcher NEMAR uses and on the bubble, at the laptop;
    // and on the phone, where it is not drawn.
    await checkPlanRail(cdp, base, screenshotDir, { name: 'laptop', width: 1280, height: 900 }, 'capsule');
    await checkPlanRail(cdp, base, screenshotDir, { name: 'laptop', width: 1280, height: 900 }, 'bubble');
    await checkPlanRail(cdp, base, screenshotDir, { name: 'phone', width: 390, height: 844 }, 'capsule');
    await checkPlanRailState(cdp, base, screenshotDir, 'failed');
    await checkPlanRailState(cdp, base, screenshotDir, 'stopped');
    await checkRunStepFocus(cdp, base);
  } finally {
    try { cdp?.close?.(); } catch (error) { /* already gone */ }
    chrome?.kill();
    server.stop(true);
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
}

await check(process.argv[2] || process.env.CENTER_CHECK_SCREENSHOTS);
if (failed) process.exit(1);
