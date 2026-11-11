#!/usr/bin/env bun
/**
 * Real-Chrome check of the History sheet and of saved figures in restored chats and
 * in the pop-out (#657, #658, #493), at a phone's 390 x 844.
 *
 * The Bun suite (frontend/test-widget-history-panel.js) holds the logic in happy-dom,
 * over an in-memory stand-in for IndexedDB. This runs the whole path in headless
 * Chrome against the real one: a run that draws a figure goes through a real
 * ClientToolController and WorkspaceStore into IndexedDB, the page is reloaded, and
 * the figure has to come back, three ways: in the chat the reload restores, in the
 * chat opened from the History sheet (whose row also has the figure as its
 * thumbnail), and in the pop-out, a window of its own that reads the same database
 * through its own init.
 *
 * It stands up its own server, so it needs no backend and no network: the widget, the
 * runtime bundle, the test worker (test-workers/executing.js, the same real worker the
 * Bun controller tests drive; its `IMAGE:<base64>` code returns that PNG as the run's
 * figure, here one drawn by the page's own canvas), and a community config that
 * declares the Python tool. The figure is therefore real PNG bytes through the real
 * save and read; the Python that would have made it is run-card-check's and
 * workspace.js's concern, not this one's.
 *
 * Every state carries its checks, so that a pass measures something: the sheet is
 * inside the 390 px window and nothing scrolls sideways, every control is at least
 * 44 px, the thumbnail and the figures are decoded images (naturalWidth > 0), the
 * stored history holds no figure, and the dark scheme paints the sheet with the dark
 * panel's color. Screenshots are written when a directory is given.
 *
 * Usage: bun frontend/browser-harness/history-check.mjs [screenshot-dir]
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

let failed = 0;
function report(ok, label, detail) {
  if (ok) console.log(`ok: ${label}`);
  else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}

const RUNTIME_CONFIG = { pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 60 } };
const CLIENT_TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];
const STORAGE_KEY = `history-check-${Math.random().toString(36).slice(2, 8)}`;
const COMMUNITY = 'historycheck';
const DATASET = 'on002718';
const CALL_ID = 'history-call-1';

const PAGE = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>history</title></head>
<body><script>window.__OSA_TEST__ = true;</script>
<script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setDataset({ id: ${JSON.stringify(DATASET)} });
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: ${JSON.stringify(COMMUNITY)}, storageKey: ${JSON.stringify(STORAGE_KEY)} });
  OSAChatWidget.init();
</script></body></html>`;

function startServer() {
  return Bun.serve({
    port: 0,
    hostname: '127.0.0.1',
    fetch(request) {
      const { pathname } = new URL(request.url);
      const js = (body) => new Response(body, { headers: { 'content-type': 'text/javascript' } });
      if (pathname === '/') return new Response(PAGE, { headers: { 'content-type': 'text/html' } });
      if (pathname === '/osa-chat-widget.js') return js(WIDGET);
      if (pathname === '/osa-runtime.bundle.js') return js(RUNTIME_BUNDLE);
      if (pathname === '/test-workers/executing.js') return js(TEST_WORKER);
      if (pathname === '/api/health') return Response.json({ status: 'healthy' });
      if (pathname === `/api/${COMMUNITY}`) {
        return Response.json({
          default_model: 'm', offered_models: [],
          widget: { title: 'NEMAR Assistant', theme_color: '#5bbad5', theme_text_color: '#04121f', accent_color: '#257a92', color_scheme: 'light' },
          client_tools: CLIENT_TOOLS, runtime: { python: RUNTIME_CONFIG },
        });
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

// Reports a failure itself, once, when it times out.
async function waitFor(cdp, sessionId, expression, label, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(cdp, sessionId, expression)) return true;
    await Bun.sleep(100);
  }
  report(false, `timed out waiting: ${label}`);
  return false;
}

async function screenshot(cdp, sessionId, dir, name) {
  if (!dir) return;
  const clip = await evaluate(cdp, sessionId, `(() => { const r = document.querySelector('.osa-chat-window').getBoundingClientRect(); return { x: Math.max(0, r.x), y: Math.max(0, r.y), width: r.width, height: r.height, scale: 1 }; })()`);
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', clip }, sessionId);
  writeFileSync(join(dir, name), Buffer.from(data, 'base64'));
  console.log(`  screenshot: ${join(dir, name)}`);
}

async function openPage(cdp, base) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  await cdp.send('Page.enable', {}, sessionId);
  // A phone: 390 x 844.
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: false }, sessionId);
  await cdp.send('Page.navigate', { url: `${base}/` }, sessionId);
  return { targetId, sessionId };
}

// The page's widget, ready: its panel open and the runtime bundle (which carries the
// workspace) loaded.
const WIDGET_READY = `(async () => {
  for (let i = 0; i < 400; i++) {
    if (window.OSAChatWidget && document.querySelector('.osa-chat-button') && window.OSAChatWidget.__browser && window.OSAChatWidget.__browser.getBrowserTools() && window.OSARuntime) break;
    await new Promise((r) => setTimeout(r, 25));
  }
  const ready = !!(window.OSARuntime && window.OSAChatWidget.__browser.getBrowserTools());
  const panel = document.querySelector('.osa-chat-window');
  if (ready && !panel.classList.contains('open')) document.querySelector('.osa-chat-button').click();
  return ready;
})()`;

// A figure drawn in the page: an ERP-like plot of three conditions, as base64 PNG.
const DRAW_FIGURE = `(() => {
  const canvas = document.createElement('canvas');
  canvas.width = 640;
  canvas.height = 280;
  const g = canvas.getContext('2d');
  g.fillStyle = '#ffffff';
  g.fillRect(0, 0, 640, 280);
  g.strokeStyle = '#94a3b8';
  g.beginPath(); g.moveTo(50, 20); g.lineTo(50, 240); g.lineTo(620, 240); g.stroke();
  const curve = (color, depth) => {
    g.strokeStyle = color; g.lineWidth = 2.5; g.beginPath();
    for (let x = 0; x <= 570; x += 5) {
      const t = x / 570;
      const y = 130 + depth * Math.exp(-(((t - 0.36) / 0.07) ** 2)) - 18 * Math.exp(-(((t - 0.62) / 0.1) ** 2));
      if (x === 0) g.moveTo(50 + x, y); else g.lineTo(50 + x, y);
    }
    g.stroke();
  };
  curve('#2563eb', 80); curve('#ea580c', 45);
  g.fillStyle = '#334155'; g.font = '13px sans-serif';
  g.fillText('ERP at EEG066 by condition', 60, 16);
  return canvas.toDataURL('image/png').split(',')[1];
})()`;

// Run code that draws a figure, through a real controller and a real WorkspaceStore
// over the page's real IndexedDB, as the reply to the reader's question; keep the
// chat, then start another so the first one is only in History.
const makeChatWithFigure = (png) => `(async () => {
  const api = OSAChatWidget.__browser;
  const container = document.querySelector('.osa-chat-widget');
  const runtime = new OSARuntime.PyodideRuntime({
    runtime: ${JSON.stringify(RUNTIME_CONFIG)},
    workerFactory: () => new Worker('/test-workers/executing.js'),
  });
  const workspace = new OSARuntime.WorkspaceStore({ community: ${JSON.stringify(COMMUNITY)} });
  const controller = new OSARuntime.ClientToolController({
    runtime,
    tools: ${JSON.stringify(CLIENT_TOOLS)},
    gate: async () => OSARuntime.GATE_DECISION.RUN,
    workspace,
  });
  api.setBrowserTools(controller);
  api.setBrowserRuntime(runtime);
  api.setMessages([
    { role: 'assistant', content: 'Hi' },
    { role: 'user', content: 'Plot the ERP for ${DATASET}', dataset: ${JSON.stringify(DATASET)} },
    { role: 'assistant', content: 'The N170 is largest at EEG065 to EEG068.' },
  ]);
  api.setSessionId('history-session');
  await api.answerToolRequest(container, {
    call_id: ${JSON.stringify(CALL_ID)}, tool: 'execute_code', session_id: 'history-session',
    args: { code: 'IMAGE:' + ${JSON.stringify(png)}, description: 'Averaged epochs per condition' },
  }, 2);
  const ran = api.getMessages()[2].executions;
  api.saveHistory();
  const saved = await workspace.figuresForCalls([${JSON.stringify(CALL_ID)}]);
  const started = api.startConversation({ context: api.pageContext() });
  api.renderMessages(container);
  return {
    ran: ran.length, images: ran[0].images.length, status: ran[0].status,
    workspaceFigures: (saved.get(${JSON.stringify(CALL_ID)}) || []).length,
    started: typeof started === 'string',
    stored: localStorage.getItem(${JSON.stringify(STORAGE_KEY)}),
  };
})()`;

const SHEET_MEASURE = `(() => {
  const win = document.querySelector('.osa-chat-window').getBoundingClientRect();
  const sheet = document.querySelector('.osa-history-sheet');
  const box = sheet.getBoundingClientRect();
  const small = (selector) => [...sheet.querySelectorAll(selector)]
    .filter((el) => el.getBoundingClientRect().height > 0)
    .filter((el) => el.getBoundingClientRect().height < 44 || el.getBoundingClientRect().width < 44)
    .map((el) => el.className + ' ' + Math.round(el.getBoundingClientRect().width) + 'x' + Math.round(el.getBoundingClientRect().height));
  const list = sheet.querySelector('.osa-history-list');
  const style = getComputedStyle(sheet);
  return {
    windowWidth: Math.round(win.width),
    inside: box.left >= win.left - 1 && box.right <= win.right + 1 && box.bottom <= win.bottom + 1 && box.top >= win.top - 1,
    sheetVisible: style.display !== 'none' && box.height > 0,
    sideways: list.scrollWidth > list.clientWidth + 1 || document.documentElement.scrollWidth > window.innerWidth + 1,
    smallTargets: small('button'),
    background: style.backgroundColor,
    rows: [...sheet.querySelectorAll('.osa-history-row')].map((row) => row.innerText.replace(/\\s+/g, ' ').trim()),
    pressed: document.querySelector('.osa-history-btn').getAttribute('aria-pressed'),
    inert: !!document.querySelector('.osa-chat-messages').closest('[inert]'),
  };
})()`;

const FIGURE_DECODED = (selector) => `(() => {
  const img = document.querySelector(${JSON.stringify(selector)});
  return !!img && img.complete && img.naturalWidth > 0 && img.getBoundingClientRect().height > 0;
})()`;

async function openPopout(cdp, created, page) {
  const seen = created.length;
  await evaluate(cdp, page.sessionId, `document.querySelector('.osa-popout-btn').click()`, { userGesture: true });
  const deadline = Date.now() + 15_000;
  let target = null;
  while (!target && Date.now() < deadline) {
    target = created.slice(seen).find((t) => t.type === 'page' && t.openerId === page.targetId);
    if (!target) await Bun.sleep(50);
  }
  report(!!target, 'a pop-out window opened, as a new target whose opener is the page');
  if (!target) return null;
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId: target.targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  await cdp.send('Page.enable', {}, sessionId);
  return { targetId: target.targetId, sessionId };
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
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-history-check-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { product } = await cdp.send('Browser.getVersion');
    console.log(`Chrome: ${product}`);
    const created = [];
    cdp.on((message) => {
      if (message.method === 'Target.targetCreated') created.push(message.params.targetInfo);
    });
    await cdp.send('Target.setDiscoverTargets', { discover: true });

    console.log('--- a run that draws a figure');
    const page = await openPage(cdp, base);
    const { sessionId } = page;
    const call = (expression, options) => evaluate(cdp, sessionId, expression, options);
    report(await call(WIDGET_READY), 'the widget is open and the runtime bundle loaded');
    const png = await call(DRAW_FIGURE);
    report(typeof png === 'string' && png.length > 200, 'the page drew a figure', png && png.length);
    const made = await call(makeChatWithFigure(png));
    report(made.status === 'ok' && made.images === 1, 'the code ran and its result carries the figure', made);
    report(made.workspaceFigures === 1, 'the figure is in the real IndexedDB workspace, read back by call id', made.workspaceFigures);
    report(made.started, 'a second, empty chat was started, so the first is only in History');
    report(!made.stored.includes('data_base64') && !made.stored.includes('data:image') && /"images":\[\]/.test(made.stored), 'the stored history holds no figure, only images: []');
    report(/"figureCount":1/.test(made.stored), 'only how many figures the run drew');

    console.log('--- after a reload, History at 390 x 844');
    await cdp.send('Page.reload', {}, sessionId);
    report(await call(WIDGET_READY), 'the reloaded page has the widget open again');
    await call(`document.querySelector('.osa-history-btn').click()`, { userGesture: true });
    report(await waitFor(cdp, sessionId, `document.querySelectorAll('.osa-history-row').length === 1`, 'the sheet lists the saved chat'), 'History lists the chat');
    const sheet = await call(SHEET_MEASURE);
    report(sheet.sheetVisible && sheet.inside, `the sheet is on screen, inside the ${sheet.windowWidth} px window`, sheet);
    report(!sheet.sideways, 'nothing scrolls sideways');
    report(sheet.smallTargets.length === 0, 'every button in the sheet is at least 44 x 44', sheet.smallTargets);
    report(sheet.pressed === 'true' && sheet.inert, 'the History button is pressed and the chat behind the sheet is inert');
    report(/Plot the ERP for on002718/.test(sheet.rows[0]) && /on002718/.test(sheet.rows[0]) && /1 step/.test(sheet.rows[0]) && /1 figure/.test(sheet.rows[0]),
      'the row has the title, the dataset, one step and one figure', sheet.rows);
    report(await waitFor(cdp, sessionId, FIGURE_DECODED('.osa-history-thumb img'), 'the thumbnail is a decoded image'), 'the row\'s thumbnail is the figure, decoded by the browser');
    const thumbSrc = await call(`document.querySelector('.osa-history-thumb img').src`);
    report(thumbSrc.startsWith('blob:'), 'as an object URL', thumbSrc);
    await screenshot(cdp, sessionId, screenshotDir, 'history-sheet-light.png');

    console.log('--- open the chat');
    await call(`document.querySelector('.osa-history-open').click()`, { userGesture: true });
    report(await waitFor(cdp, sessionId, `!document.querySelector('.osa-history-sheet.open')`, 'the sheet closes'), 'opening a chat closes the sheet');
    report(await waitFor(cdp, sessionId, FIGURE_DECODED('.osa-execution-figure img'), 'the figure in the opened chat'), 'the opened chat shows its figure, an image the browser decoded');
    report(await call(`(() => { const raw = localStorage.getItem(${JSON.stringify(STORAGE_KEY)}); return !raw.includes('data_base64') && !raw.includes('data:image'); })()`), 'with no figure written to storage by opening it');
    const stillThere = await call(`fetch(${JSON.stringify(thumbSrc)}).then(() => true, () => false)`);
    report(!stillThere, 'the thumbnail\'s object URL was revoked with the sheet', stillThere);
    await screenshot(cdp, sessionId, screenshotDir, 'history-opened-chat.png');

    console.log('--- the restored chat, after another reload');
    await cdp.send('Page.reload', {}, sessionId);
    report(await call(WIDGET_READY), 'the page is reloaded with the opened chat active');
    report(await waitFor(cdp, sessionId, FIGURE_DECODED('.osa-execution-figure img'), 'the figure in the restored chat'), 'the restored chat shows its figure, with History never opened');
    await screenshot(cdp, sessionId, screenshotDir, 'history-restored-chat.png');

    console.log('--- the pop-out');
    const popup = await openPopout(cdp, created, page);
    if (popup) {
      report(await waitFor(cdp, popup.sessionId, `!!document.querySelector('.osa-chat-widget.fullscreen .osa-chat-window.open')`, 'the pop-out\'s widget'), 'the pop-out\'s widget is on screen');
      report(await waitFor(cdp, popup.sessionId, FIGURE_DECODED('.osa-execution-figure img'), 'the figure in the pop-out'),
        'the pop-out shows the figure too, read from the same IndexedDB by its own init');
      report(await evaluate(cdp, popup.sessionId, `!!document.querySelector('.osa-history-btn')`), 'and has the History button');
      await screenshot(cdp, popup.sessionId, screenshotDir, 'history-popout.png');
      await cdp.send('Target.closeTarget', { targetId: popup.targetId });
    }

    console.log('--- the dark scheme');
    await call(`OSAChatWidget.setColorScheme('dark')`);
    await call(`document.querySelector('.osa-history-btn').click()`, { userGesture: true });
    report(await waitFor(cdp, sessionId, `document.querySelectorAll('.osa-history-row').length === 1`, 'the sheet in the dark scheme'), 'History opens in the dark scheme');
    const dark = await call(SHEET_MEASURE);
    report(dark.background === 'rgb(17, 24, 39)', 'the sheet is the dark panel\'s color', dark.background);
    report(dark.sheetVisible && dark.inside && !dark.sideways && dark.smallTargets.length === 0, 'and fits, with every control at least 44 px', dark);
    await screenshot(cdp, sessionId, screenshotDir, 'history-sheet-dark.png');
    await call(`document.querySelector('.osa-history-sheet').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
    report(await call(`document.activeElement === document.querySelector('.osa-history-btn')`), 'Escape closes it and returns focus to the History button');
  } finally {
    try { cdp?.close?.(); } catch (error) { /* already gone */ }
    chrome?.kill();
    server.stop(true);
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
}

await check(process.argv[2]);
if (failed) process.exit(1);
