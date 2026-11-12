#!/usr/bin/env bun
/**
 * Real-Chrome check of the notebook card and the History row menu's notebook items
 * (E4.3, #675), at a 400 px panel, in light and in dark.
 *
 * The Bun suite (frontend/test-widget-notebook-entry.js) holds their logic in
 * happy-dom, which does no layout. This draws them in headless Chrome, where wrapping,
 * the dark stylesheet, the buttons' real sizes and where a tall menu ends up are the
 * browser's own. It stands up its own server, so it needs no backend and no network:
 * the widget, the runtime bundle (which carries the notebook builder), and a capsule
 * community config that declares the Python tool.
 *
 * Its checks, so that a pass measures something: the card sits inside the 400 px panel
 * with nothing scrolling sideways, every control in it is at least 44 px tall, its
 * text and its buttons' labels reach 4.5:1 against what is behind them, the Download
 * code menu opens inside the panel with its two 44 px items and its note, and the last
 * History row's menu (the tallest, with Continue, Download code unfolded and a
 * reason) is wholly inside the sheet's list once opened and unfolded: the list clips
 * what runs past it, so the menu is capped at the list's height and scrolled into view.
 *
 * Usage: bun frontend/browser-harness/notebook-entry-check.mjs [screenshot-dir]
 * Chrome is found at CHROME_PATH or where it installs (see chrome.js). Under CI a
 * missing Chrome fails; elsewhere it skips.
 */

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { connect, findChrome, launch } from './chrome.js';

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

const RUNTIME_CONFIG = { pyodide_version: '0.29.5', preload: [], preload_on: 'first_run', fetch_allow: [], limits: { exec_seconds: 60 } };
const CLIENT_TOOLS = [{ name: 'execute_code', runtime: 'python', requires_permission: true }];
const STORAGE_KEY = `notebook-entry-check-${Math.random().toString(36).slice(2, 8)}`;
const COMMUNITY = 'nbentry';
const DATASET = 'on002718';
const OTHER = 'nm000241';

// A stored chat on a dataset with one successful run, as the widget keeps them.
function chat(index, dataset) {
  const updatedAt = Date.now() - 60_000 - index * 1000;
  return {
    id: `chat-${index}`,
    context: { type: 'dataset', id: dataset },
    title: `Compare the ERPs, take ${index}`,
    sessionId: `session-${index}`,
    createdAt: updatedAt - 1000,
    updatedAt,
    messages: [
      { role: 'assistant', content: 'Hi' },
      { role: 'user', content: `Compare the ERPs, take ${index}`, dataset },
      {
        role: 'assistant',
        content: 'The N170 is largest at EEG065 to EEG068.',
        executions: [
          {
            callId: `call-${index}-1`, tool: 'execute_code', description: 'Average the epochs', status: 'ok',
            code: 'import numpy as np\nprint(np.arange(3).mean())', stdout: '1.0\n', stderr: '', traceback: '',
            local: false, images: [], workspaceNote: '',
          },
          {
            callId: `call-${index}-2`, tool: 'execute_code', description: 'Count the events', status: 'ok',
            code: 'print(882)', stdout: '882\n', stderr: '', traceback: '', local: false, images: [], workspaceNote: '',
          },
        ],
      },
    ],
  };
}

// The open chat (on this page's dataset), then six more: the last rows are on another
// dataset, so their Continue is disabled with its reason, the tallest menu.
function storedRecord() {
  const chats = [chat(0, DATASET), chat(1, DATASET), chat(2, DATASET), chat(3, DATASET), chat(4, OTHER), chat(5, OTHER), chat(6, OTHER)];
  return JSON.stringify({ version: 3, activeId: 'chat-0', conversations: chats });
}

const PAGE = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>notebook entry</title></head>
<body><script>window.__OSA_TEST__ = true;
try { localStorage.setItem(${JSON.stringify(STORAGE_KEY)}, ${JSON.stringify(storedRecord())}); sessionStorage.setItem(${JSON.stringify(`${STORAGE_KEY}-session`)}, '1'); } catch (e) {}
</script>
<script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setDataset({ id: ${JSON.stringify(DATASET)}, zarr: true });
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: ${JSON.stringify(COMMUNITY)}, storageKey: ${JSON.stringify(STORAGE_KEY)}, initialMessage: 'Hi' });
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
      if (pathname === '/api/health') return Response.json({ status: 'healthy' });
      if (pathname === `/api/${COMMUNITY}`) {
        return Response.json({
          default_model: 'm', offered_models: [],
          widget: { title: 'NEMAR Assistant', launcher: 'capsule', theme_color: '#5bbad5', theme_text_color: '#04121f', accent_color: '#257a92', color_scheme: 'auto' },
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

async function waitFor(cdp, sessionId, expression, label, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(cdp, sessionId, expression)) return true;
    await Bun.sleep(100);
  }
  console.error(`  timed out waiting: ${label}`);
  return false;
}

async function screenshot(cdp, sessionId, dir, name) {
  if (!dir) return;
  const clip = await evaluate(cdp, sessionId, `(() => { const r = document.querySelector('.osa-chat-window').getBoundingClientRect(); return { x: Math.max(0, r.x), y: Math.max(0, r.y), width: r.width, height: r.height, scale: 1 }; })()`);
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', clip }, sessionId);
  writeFileSync(join(dir, name), Buffer.from(data, 'base64'));
}

async function openPage(cdp, url) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  await cdp.send('Page.enable', {}, sessionId);
  // A phone-width screen: the panel is the whole 400 px, the width the design is drawn at.
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 400, height: 800, deviceScaleFactor: 2, mobile: false }, sessionId);
  await cdp.send('Page.navigate', { url }, sessionId);
  return { targetId, sessionId };
}

const CONTRAST_OF = (selector) => `(() => {
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
  const fg = parse(getComputedStyle(el).color);
  if (!fg) return { ratio: null, why: getComputedStyle(el).color };
  const flat = over(fg, backdrop);
  const lum = (c) => {
    const f = (v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const a = lum(flat);
  const b = lum(backdrop);
  return { ratio: Math.round(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) * 100) / 100 };
})()`;

// The page's widget, ready: the runtime bundle loaded (the card needs its builder),
// the panel open on the chat, and the card drawn.
const WIDGET_READY = `(async () => {
  for (let i = 0; i < 600; i++) {
    if (window.OSAChatWidget && document.querySelector('.osa-launcher-capsule .osa-chat-button') && window.OSAChatWidget.__browser
        && window.OSAChatWidget.__browser.getBrowserTools() && window.OSARuntime && document.querySelector('.osa-chat-widget.osa-capsule')) break;
    await new Promise((r) => setTimeout(r, 25));
  }
  const widget = document.querySelector('.osa-chat-widget');
  const ready = !!(window.OSARuntime && widget && widget.classList.contains('osa-capsule'));
  if (ready && !document.querySelector('.osa-chat-window').classList.contains('open')) document.querySelector('.osa-launcher-capsule .osa-chat-button').click();
  if (ready) await new Promise((r) => setTimeout(r, 300));
  return ready;
})()`;

const CARD_MEASURE = `(() => {
  const messages = document.querySelector('.osa-chat-messages');
  const panel = messages.getBoundingClientRect();
  const card = messages.querySelector('.osa-nb-entry');
  if (!card) return { card: false };
  const box = card.getBoundingClientRect();
  const controls = [...card.querySelectorAll('button')].filter((b) => b.getBoundingClientRect().height > 0);
  return {
    card: true,
    panelWidth: Math.round(panel.width),
    inside: box.left >= panel.left - 1 && box.right <= panel.right + 1,
    sideways: messages.scrollWidth > messages.clientWidth + 1,
    short: controls.filter((b) => b.getBoundingClientRect().height < 44).map((b) => b.textContent.trim() + ' ' + Math.round(b.getBoundingClientRect().height)),
    labels: controls.map((b) => b.textContent.trim()),
  };
})()`;

const MENU_MEASURE = `(() => {
  const panel = document.querySelector('.osa-chat-window').getBoundingClientRect();
  const menu = document.querySelector('.osa-nb-entry-menu');
  const box = menu.getBoundingClientRect();
  const items = [...menu.querySelectorAll('button')].filter((b) => b.getBoundingClientRect().height > 0);
  return {
    shown: getComputedStyle(menu).display !== 'none' && box.height > 0,
    inside: box.left >= panel.left - 1 && box.right <= panel.right + 1 && box.bottom <= panel.bottom + 1 && box.top >= panel.top - 1,
    items: items.map((b) => b.textContent.trim()),
    short: items.filter((b) => b.getBoundingClientRect().height < 44).map((b) => b.textContent.trim()),
    note: menu.querySelector('.osa-nb-entry-note').getBoundingClientRect().height > 0,
  };
})()`;

// The last row's menu, opened: wholly inside the sheet's list (which clips what runs past it), where a reader can reach it.
const HISTORY_MENU_MEASURE = `(() => {
  // The list clips what runs past it, so it is the list the menu must be inside.
  const sheet = document.querySelector('.osa-history-list').getBoundingClientRect();
  const rows = [...document.querySelectorAll('.osa-history-row')];
  const row = rows[rows.length - 1];
  const menu = row.querySelector('.osa-history-menu');
  const box = menu.getBoundingClientRect();
  const buttons = [...menu.querySelectorAll('button')].filter((b) => b.getBoundingClientRect().height > 0);
  return {
    rows: rows.length,
    shown: !menu.hidden && box.height > 0,
    inside: box.top >= sheet.top - 1 && box.bottom <= sheet.bottom + 1 && box.left >= sheet.left - 1 && box.right <= sheet.right + 1,
    labels: buttons.map((b) => b.textContent.trim()),
    short: buttons.filter((b) => b.getBoundingClientRect().height < 44).map((b) => b.textContent.trim()),
    box: { top: Math.round(box.top), bottom: Math.round(box.bottom), sheetTop: Math.round(sheet.top), sheetBottom: Math.round(sheet.bottom) },
  };
})()`;

async function runScheme(cdp, base, scheme, dir) {
  const page = await openPage(cdp, `${base}/`);
  const { sessionId } = page;
  const call = (expression, options) => evaluate(cdp, sessionId, expression, options);
  await waitFor(cdp, sessionId, `!!window.OSAChatWidget && !!document.querySelector('.osa-chat-widget')`, 'the widget mounts');
  report(await call(WIDGET_READY), `${scheme}: the widget is a capsule, open, with the runtime bundle loaded`);
  report(await call(`OSAChatWidget.setColorScheme(${JSON.stringify(scheme)}), document.querySelector('.osa-chat-widget').classList.contains('osa-dark')`) === (scheme === 'dark'), `${scheme}: the panel is ${scheme}`);
  report(await waitFor(cdp, sessionId, `!!document.querySelector('.osa-nb-entry')`, 'the card'), `${scheme}: the card is under the last reply`);

  const card = await call(CARD_MEASURE);
  report(card.card && card.inside, `${scheme}: the card is inside the ${card.panelWidth} px panel`, card);
  report(!card.sideways, `${scheme}: nothing scrolls sideways`);
  report(card.labels.join('|') === 'Continue in notebook|Download code', `${scheme}: Continue in notebook and Download code`, card.labels);
  report(card.short.length === 0, `${scheme}: every control in the card is at least 44 px tall`, card.short);
  for (const [selector, what] of [
    ['.osa-nb-entry-text', 'the card\'s sentence'],
    ['.osa-nb-entry-continue', 'Continue in notebook'],
    ['.osa-nb-entry-download', 'Download code'],
  ]) {
    const { ratio, why } = await call(CONTRAST_OF(selector));
    report(ratio !== null && ratio >= 4.5, `${scheme}: ${what} reads at 4.5:1 or better`, ratio === null ? why : ratio);
  }
  await screenshot(cdp, sessionId, dir, `${scheme}-card.png`);

  await call(`document.querySelector('.osa-nb-entry-download').click()`, { userGesture: true });
  await Bun.sleep(150);
  const menu = await call(MENU_MEASURE);
  report(menu.shown && menu.inside, `${scheme}: the Download code menu opens inside the panel`, menu);
  report(menu.items.length === 2 && menu.short.length === 0, `${scheme}: with two items, each at least 44 px tall`, menu);
  report(menu.note, `${scheme}: and its note is shown`);
  for (const [selector, what] of [['.osa-nb-entry-item', 'a menu item'], ['.osa-nb-entry-note', 'the menu\'s note']]) {
    const { ratio, why } = await call(CONTRAST_OF(selector));
    report(ratio !== null && ratio >= 4.5, `${scheme}: ${what} reads at 4.5:1 or better`, ratio === null ? why : ratio);
  }
  await screenshot(cdp, sessionId, dir, `${scheme}-card-menu.png`);
  await call(`document.querySelector('.osa-nb-entry-download').click()`, { userGesture: true });

  // History: open the last row's menu and unfold Download code, the tallest it gets.
  await call(`document.querySelector('.osa-history-btn').click()`, { userGesture: true });
  report(await waitFor(cdp, sessionId, `document.querySelectorAll('.osa-history-row').length === 7`, 'the sheet lists the chats'), `${scheme}: History lists the seven chats`);
  await Bun.sleep(300);
  await call(`(() => { const rows = [...document.querySelectorAll('.osa-history-row')]; rows[rows.length - 1].querySelector('.osa-history-more').click(); })()`, { userGesture: true });
  await Bun.sleep(200);
  await call(`(() => { const rows = [...document.querySelectorAll('.osa-history-row')]; rows[rows.length - 1].querySelector('.osa-history-menu-download').click(); })()`, { userGesture: true });
  await Bun.sleep(300);
  const history = await call(HISTORY_MENU_MEASURE);
  report(history.shown && history.inside, `${scheme}: the last row's menu, opened and unfolded, is wholly inside the list that clips it`, history);
  report(history.labels.includes('Continue in notebook') && history.labels.includes('Download code') && history.labels.includes('Delete'), `${scheme}: it holds Continue in notebook, Download code and Delete`, history.labels);
  report(history.short.length === 0, `${scheme}: every item in it is at least 44 px tall`, history.short);
  const reason = await call(CONTRAST_OF('.osa-history-row:last-child .osa-history-menu-reason'));
  report(reason.ratio !== null && reason.ratio >= 4.5, `${scheme}: the reason under the disabled Continue reads at 4.5:1 or better`, reason);
  await screenshot(cdp, sessionId, dir, `${scheme}-history-menu.png`);
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
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-notebook-entry-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { product } = await cdp.send('Browser.getVersion');
    console.log(`Chrome: ${product}`);
    for (const scheme of ['light', 'dark']) {
      console.log(`\n${scheme}`);
      await runScheme(cdp, base, scheme, screenshotDir);
    }
  } finally {
    try { cdp?.close?.(); } catch (error) { /* already gone */ }
    chrome?.kill();
    server.stop(true);
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
}

await check(process.argv[2] || process.env.NOTEBOOK_ENTRY_CHECK_SCREENSHOTS);
if (failed) process.exit(1);
