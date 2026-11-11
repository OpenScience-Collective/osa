#!/usr/bin/env bun
/**
 * Real-Chrome check of the run card (#664, #672), at 400 px, in light and in dark.
 *
 * The Bun suite (frontend/test-widget-run-card.js) holds the card's markup and logic in
 * happy-dom; this draws every state of it in headless Chrome, where layout, wrapping,
 * the dark stylesheet and the buttons' real sizes are the browser's own, and writes a
 * screenshot of each so a reader can look at them. It stands up its own server, so it
 * needs no backend and no network: the widget, the runtime bundle (for highlighting and
 * Edit and run), and a community config that declares the Python tool.
 *
 * The states: consent; running, with and without a bar; done with a figure (and with
 * its code open); done without a figure; a KeyError, a SyntaxError and an error with no
 * line (all from stderr recorded off real Pyodide, frontend/test-support); stderr cut by
 * the worker; output cut by the worker; a run that worked and warned (closed, then open);
 * each non-ok status; the retry line; and the same cards restored from storage.
 *
 * Every state carries its checks, so that a pass measures something: the card is inside
 * the 400 px panel, nothing scrolls sideways, every button is at least 36 px tall, the
 * failing line is marked, the code is hidden until Show code is pressed and shown after,
 * and the restored card has no figure.
 *
 * Usage: bun frontend/browser-harness/run-card-check.mjs [screenshot-dir]
 * Chrome is found at CHROME_PATH or where it installs (see chrome.js). Under CI a
 * missing Chrome fails; elsewhere it skips.
 */

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { connect, findChrome, launch } from './chrome.js';
import { RUN_CARD_FIXTURES } from '../test-support/run-card-fixtures.js';

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

const PAGE = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>run card</title></head>
<body><script>window.__OSA_TEST__ = true;</script>
<script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: 'runcard', storageKey: 'run-card-check-' + Math.random() });
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
      if (pathname === '/api/runcard') {
        return Response.json({
          default_model: 'm', offered_models: [], widget: { title: 'NEMAR Assistant', theme_color: '#5bbad5', theme_text_color: '#04121f', accent_color: '#257a92', color_scheme: 'auto' },
          client_tools: CLIENT_TOOLS, runtime: { python: RUNTIME_CONFIG },
        });
      }
      return new Response('not found', { status: 404 });
    },
  });
}

// ------------------------------------------------------------------ in the page

// Everything the page does, as one expression. `window.__card` is its small API:
// show(name) puts one state on screen and returns what to check about it.
const SETUP = `(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const FIXTURES = ${JSON.stringify(RUN_CARD_FIXTURES)};
  const widget = document.querySelector('.osa-chat-widget');
  // The panel is a fixed height; a card needs room to be seen whole, so the check makes
  // the panel as tall as the screen. Its width stays the 400 px of the design.
  const tall = document.createElement('style');
  tall.textContent = '.osa-chat-widget .osa-chat-window { height: 1000px !important; max-height: 1000px !important; }';
  document.head.appendChild(tall);
  widget.querySelector('.osa-chat-button').click();
  const api = OSAChatWidget.__browser;
  for (let i = 0; i < 400 && !(api.getBrowserTools() && window.OSARuntime); i++) await sleep(25);
  if (!(api.getBrowserTools() && window.OSARuntime)) return { ready: false };
  const runtime = new OSARuntime.PyodideRuntime({
    runtime: ${JSON.stringify(RUNTIME_CONFIG)},
    workerFactory: () => new Worker('/test-workers/executing.js'),
  });
  const controller = new OSARuntime.ClientToolController({
    runtime,
    tools: ${JSON.stringify(CLIENT_TOOLS)},
    gate: async () => OSARuntime.GATE_DECISION.RUN,
  });
  api.setBrowserTools(controller);
  api.setBrowserRuntime(runtime);

  // A figure drawn in the page, so the card has a real image to lay out: an ERP-like
  // plot of three conditions.
  const canvas = document.createElement('canvas');
  canvas.width = 640;
  canvas.height = 280;
  const g = canvas.getContext('2d');
  g.fillStyle = '#ffffff';
  g.fillRect(0, 0, 640, 280);
  g.strokeStyle = '#94a3b8';
  g.beginPath(); g.moveTo(50, 20); g.lineTo(50, 240); g.lineTo(620, 240); g.stroke();
  g.beginPath(); g.moveTo(50, 130); g.lineTo(620, 130); g.stroke();
  const curve = (color, depth, dash) => {
    g.strokeStyle = color; g.lineWidth = 2.5; g.setLineDash(dash || []);
    g.beginPath();
    for (let x = 0; x <= 570; x += 5) {
      const t = x / 570;
      const y = 130 + depth * Math.exp(-(((t - 0.36) / 0.07) ** 2)) - 18 * Math.exp(-(((t - 0.62) / 0.1) ** 2));
      if (x === 0) g.moveTo(50 + x, y); else g.lineTo(50 + x, y);
    }
    g.stroke();
  };
  curve('#2563eb', 80); curve('#ea580c', 45); curve('#475569', 62, [6, 4]);
  g.setLineDash([]);
  g.fillStyle = '#334155'; g.font = '13px sans-serif';
  g.fillText('ERP at EEG066 by condition', 60, 16);
  g.fillText('0 ms', 215, 258); g.fillText('600 ms', 580, 258);
  const PNG = canvas.toDataURL('image/png').split(',')[1];

  const CODE = [
    'import numpy as np',
    'from nemar import read_window',
    '',
    'epochs = read_window(index, store, tmin=-0.2, tmax=0.6)',
    'erp = {c: epochs[c].mean(0) for c in ("Famous", "Scrambled", "Unfamiliar")}',
    'fig, ax = plt.subplots()',
    'for name, curve in erp.items():',
    '    ax.plot(times, curve[65], label=name)',
    'ax.legend()',
    'display(fig)',
  ].join('\\n');
  const record = (over) => Object.assign({
    callId: 'c' + Math.random().toString(36).slice(2, 8), tool: 'execute_code', description: 'Averaged epochs per condition',
    code: CODE, status: 'ok', stdout: '', stderr: '', traceback: '', images: [], local: false, workspaceNote: '',
  }, over);
  const fromFixture = (name, over) => {
    const f = FIXTURES[name];
    return Object.assign(api.executionRecord(
      { call_id: 'f-' + name, tool: 'execute_code', args: { code: f.code, description: 'Averaged epochs per condition' } },
      { status: f.status, stdout: f.stdout, stderr: f.stderr, images: [] }, f.traceback), over || {});
  };
  const figure = [{ mime: 'image/png', data_base64: PNG, width: 640, height: 280 }];
  const reply = (executions, content) => [
    { role: 'assistant', content: 'Hi' },
    { role: 'user', content: 'Plot the ERP for on002718', dataset: 'on002718' },
    { role: 'assistant', content: content === undefined ? 'The N170 is largest at EEG065 to EEG068.' : content, executions },
  ];
  const container = widget;
  const messagesEl = widget.querySelector('.osa-chat-messages');

  // What to check about whatever is on screen: the card is inside the panel, nothing
  // scrolls sideways, and every button is a real target.
  function measure() {
    const panel = messagesEl.getBoundingClientRect();
    const cards = [...messagesEl.querySelectorAll('.osa-run-card')];
    const buttons = [...messagesEl.querySelectorAll('.osa-run-card button.osa-run-card-button')]
      .filter((b) => b.getBoundingClientRect().height > 0);
    return {
      panelWidth: Math.round(panel.width),
      cards: cards.length,
      outside: cards.filter((c) => { const r = c.getBoundingClientRect(); return r.left < panel.left - 1 || r.right > panel.right + 1; }).length,
      sideways: messagesEl.scrollWidth > messagesEl.clientWidth + 1,
      shortButtons: buttons.filter((b) => b.getBoundingClientRect().height < 36).map((b) => b.textContent.trim()),
      buttons: buttons.map((b) => b.textContent.trim()),
      visibleText: cards.map((c) => c.innerText),
    };
  }
  const visible = (selector) => { const el = messagesEl.querySelector(selector); return !!el && el.getBoundingClientRect().height > 0; };

  window.__card = {
    api,
    async show(name) {
      api.setToolActivity(null);
      const base = (executions, content) => { api.setMessages(reply(executions, content)); };
      const extra = {};
      if (name === 'consent') {
        base([]);
        api.setToolActivity({ phase: 'asking', prompt: { code: CODE, description: 'Average the epochs per condition and plot them' }, decide() {} });
      } else if (name === 'running') {
        base([]);
        api.setToolActivity({ phase: 'running', prompt: { code: CODE, description: 'Average the epochs per condition and plot them' }, progress: { text: 'Loading numpy (2 of 4)', step: 2, steps: 4 } });
      } else if (name === 'done-figure') {
        base([record({ images: figure, stdout: 'epochs: 882\\nmean: 1.5\\nmax: 4.1\\n' })]);
      } else if (name === 'done-figure-code-open') {
        base([record({ images: figure, stdout: 'epochs: 882\\nmean: 1.5\\nmax: 4.1\\n' })]);
      } else if (name === 'done-no-figure') {
        base([record({ stdout: 'Counted 882 face onsets in 3 conditions\\n' })], 'There are 882 face onsets in three conditions.');
      } else if (name === 'failed-keyerror') {
        base([fromFixture('keyErrorWithLine')], '');
      } else if (name === 'failed-syntax') {
        base([fromFixture('syntaxError')], '');
      } else if (name === 'failed-no-line') {
        base([fromFixture('errorWithoutLine', { traceback: '' })], '');
      } else if (name === 'failed-cut-stderr') {
        base([fromFixture('cutStderr')], '');
      } else if (name === 'cut-output') {
        base([fromFixture('cutOutput')]);
      } else if (name === 'warnings') {
        base([fromFixture('okWithWarnings')]);
      } else if (name === 'warnings-open') {
        base([fromFixture('okWithWarnings')]);
      } else if (name === 'statuses') {
        base([
          record({ status: 'timeout', stderr: '[runtime] the code ran longer than 60s and was stopped.' }),
          record({ status: 'oom', stderr: '[runtime] the Python runtime ran out of memory and was restarted. Try working on less data at a time.' }),
          record({ status: 'cancelled', stderr: '[runtime] stopped by the reader.' }),
          record({ status: 'denied', stderr: '[runtime] the reader declined to run this code.' }),
        ], '');
      } else if (name === 'local') {
        base([record({ local: true, stdout: 'mine\\n', description: 'Your edit' })], '');
      } else if (name === 'retry') {
        base([], '');
        await api.answerToolRequest(container, { call_id: 'retry-1', tool: 'execute_code', args: { code: 'print(1)', description: 'Averaged epochs per condition' } }, 2);
        const failedRun = fromFixture('keyErrorWithLine');
        api.getMessages()[2].executions = [failedRun];
      } else if (name === 'restored') {
        base([record({ images: figure, stdout: 'epochs: 882\\n', _codeOpen: true }), fromFixture('keyErrorWithLine')]);
        api.renderMessages(container);
        api.saveHistory();
        api.setMessages([]);
        api.loadHistory();
        extra.stored = window.localStorage.getItem(api.getConfig().storageKey);
      } else {
        throw new Error('no such state: ' + name);
      }
      api.renderMessages(container);
      if (name === 'done-figure-code-open') messagesEl.querySelector('.osa-run-card-code-toggle').click();
      if (name === 'warnings-open') messagesEl.querySelector('.osa-run-card-warnings-toggle').click();
      await sleep(150);
      messagesEl.scrollTop = name === 'consent' || name === 'running' ? messagesEl.scrollHeight : 0;
      await sleep(50);
      return Object.assign(measure(), extra);
    },
    visible,
    query(selector) { const el = messagesEl.querySelector(selector); return el ? { text: el.textContent.trim(), expanded: el.getAttribute('aria-expanded') } : null; },
    click(selector) { const el = messagesEl.querySelector(selector); if (el) el.click(); return !!el; },
    colors(selector) { const el = messagesEl.querySelector(selector); if (!el) return null; const s = getComputedStyle(el); return { color: s.color, background: s.backgroundColor, border: s.borderTopColor }; },
    figureShown() { const img = messagesEl.querySelector('.osa-execution-figure img'); return !!img && img.getBoundingClientRect().height > 0; },
    scheme(value) { OSAChatWidget.setColorScheme(value); return widget.classList.contains('osa-dark'); },
  };
  return { ready: true, version: document.visibilityState };
})()`;

// ------------------------------------------------------------------ the driver

async function evaluate(cdp, sessionId, expression) {
  const { result, exceptionDetails } = await cdp.send('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise: true,
  }, sessionId);
  if (exceptionDetails) throw new Error(`page exception: ${exceptionDetails.exception?.description || exceptionDetails.text}`);
  return result.value;
}

async function screenshot(cdp, sessionId, dir, name) {
  if (!dir) return;
  // Just the panel: the page around it is empty.
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
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 400, height: 1500, deviceScaleFactor: 2, mobile: false }, sessionId);
  await cdp.send('Page.navigate', { url }, sessionId);
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (await evaluate(cdp, sessionId, `!!document.querySelector('.osa-chat-button') && !!window.OSAChatWidget`)) break;
    await Bun.sleep(100);
  }
  return { targetId, sessionId };
}

const STATES = [
  'consent', 'running', 'done-figure', 'done-figure-code-open', 'done-no-figure', 'failed-keyerror', 'failed-syntax',
  'failed-no-line', 'failed-cut-stderr', 'cut-output', 'warnings', 'warnings-open', 'statuses', 'local', 'restored', 'retry',
];

function checkState(scheme, name, m) {
  const where = `${scheme} ${name}`;
  report(m.cards >= 1 && m.outside === 0, `${where}: every card is inside the ${m.panelWidth} px panel`, [m.cards, m.outside]);
  report(!m.sideways, `${where}: nothing scrolls sideways`);
  report(m.shortButtons.length === 0, `${where}: every button is at least 36 px tall`, m.shortButtons);
}

async function runScheme(cdp, base, scheme, dir) {
  // A page of its own for each scheme: the retry line's state (a reply waiting on the
  // assistant) is not something the page lets go of, so it must not reach the next pass.
  const page = await openPage(cdp, `${base}/`);
  const setup = await evaluate(cdp, page.sessionId, SETUP);
  report(setup.ready === true, `${scheme}: the widget is open and the runtime bundle loaded`, setup);
  if (!setup.ready) return;
  const { sessionId } = page;
  const call = (expression) => evaluate(cdp, sessionId, expression);
  const dark = await call(`window.__card.scheme(${JSON.stringify(scheme)})`);
  report(dark === (scheme === 'dark'), `${scheme}: the panel is ${scheme}`, dark);
  const measured = {};
  for (const name of STATES) {
    measured[name] = await call(`window.__card.show(${JSON.stringify(name)})`);
    checkState(scheme, name, measured[name]);
    await screenshot(cdp, sessionId, dir, `${scheme}-${name}.png`);
  }
  const text = (name) => measured[name].visibleText.join('\n');

  report(/Run this Python in your browser\?/.test(text('consent')) && /import numpy as np/.test(text('consent')), `${scheme}: the consent card asks, with the code in view`);
  report(/Running Python/.test(text('running')), `${scheme}: the running card says so`);
  report(await call(`window.__card.show('done-figure').then(() => window.__card.figureShown())`), `${scheme}: the figure is visible without opening anything`);
  const closed = await call(`window.__card.query('.osa-run-card-code-toggle')`);
  report(closed && closed.expanded === 'false' && /^Show code/.test(closed.text), `${scheme}: Show code starts collapsed`, closed);
  report(!(await call(`window.__card.visible('.osa-code-panel')`)), `${scheme}: and the code is not on screen`);
  await call(`window.__card.click('.osa-run-card-code-toggle')`);
  await Bun.sleep(100);
  const opened = await call(`window.__card.query('.osa-run-card-code-toggle')`);
  report(opened.expanded === 'true' && /^Hide code/.test(opened.text) && await call(`window.__card.visible('.osa-code-panel pre')`), `${scheme}: a click shows the code`, opened);
  const colors = await call(`window.__card.colors('.osa-run-card-code-toggle')`);
  report(colors && colors.color === colors.border && colors.color !== colors.background, `${scheme}: Show code is drawn in the accent, with a border of it`, colors);

  report(/KeyError: 'Scrambled'/.test(text('failed-keyerror')), `${scheme}: the KeyError card says Type: message`);
  report(await call(`window.__card.show('failed-keyerror').then(() => window.__card.visible('.osa-run-card-line-marked'))`), `${scheme}: with the failing line marked, in view`);
  report(['All code', 'Full traceback', 'Copy error'].every((label) => measured['failed-keyerror'].buttons.some((b) => b.startsWith(label))), `${scheme}: All code, Full traceback and Copy error`, measured['failed-keyerror'].buttons);
  report(/SyntaxError: '\[' was never closed/.test(text('failed-syntax')), `${scheme}: the SyntaxError card`);
  report(!/Full traceback/.test(text('failed-no-line')) && !/line \d/.test(text('failed-no-line').split('\n').slice(0, 3).join(' ')), `${scheme}: no traceback, no line, none invented`);
  report(/ValueError: no recording had a usable event file/.test(text('failed-cut-stderr')) && /characters not shown/.test(text('failed-cut-stderr')), `${scheme}: the error survives cut stderr, and the cut is said`);
  report(/\d+ characters not shown/.test(text('cut-output')), `${scheme}: cut output says how much`);
  report(/2 warnings/.test(text('warnings')) && !/UserWarning/.test(text('warnings')), `${scheme}: warnings are counted and collapsed`);
  report(/UserWarning/.test(text('warnings-open')), `${scheme}: and open on request`);
  const statuses = text('statuses');
  report(['Stopped: took too long', 'Stopped: out of memory', 'Stopped', 'Not run'].every((label) => statuses.includes(label)), `${scheme}: each non-ok status names itself`);
  report(/The assistant is looking at this error and may run the code again \(1 of \d+ runs used\)/.test(text('retry')), `${scheme}: the retry line`);
  report(!measured.restored.stored.includes('data:image') && !/"_[A-Za-z]/.test(measured.restored.stored), `${scheme}: nothing transient and no figure is stored`);
  report(!(await call(`window.__card.show('restored').then(() => window.__card.figureShown())`)), `${scheme}: the restored card has no figure`);
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
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-run-card-'));
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

await check(process.argv[2]);
if (failed) process.exit(1);
