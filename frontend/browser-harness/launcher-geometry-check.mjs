#!/usr/bin/env bun
/**
 * Real-Chrome check of the launcher's position, size and offsets (#553): what
 * happy-dom cannot show, that the launcher is where the config puts it, in every
 * animation frame of opening and closing the panel.
 *
 * For each scenario a fresh tab loads the real widget with the settings a page
 * passes to setConfig, then the launcher's chat circle is sampled on every animation
 * frame across a click: its width must go from the closed size to the open one and
 * back through frames in between (an animation, not a snap), and its corner at the
 * anchor (bottom-right, or bottom-left on the left) must stay exactly the configured
 * distance from the window's edges in every frame, so the button never drifts while
 * it resizes. Then the open panel's place beside or above it, the collapsed label's
 * 10px gap and vertical center, and, on the left, a real mouse drag of the panel's
 * top-right resize handle.
 *
 * Scenarios cover the bubble and the capsule, each corner, sizes, offsets, and the
 * phone offsets at 390px and 1440px wide. The community config request is answered
 * by a stub with only a title, so the widget's own geometry is what is drawn.
 *
 * Usage:
 *   bun frontend/browser-harness/launcher-geometry-check.mjs
 * It serves the real widget from frontend/ itself, needs nothing running, and skips
 * where there is no Chrome (and fails under CI, which must have one).
 */

import { connect, findChrome, launch } from './chrome.js';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const WIDGET = new URL('../osa-chat-widget.js', import.meta.url);

let failed = 0;
function report(ok, label, detail) {
  if (ok) console.log(`ok: ${label}`);
  else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}
const near = (a, b, tolerance = 0.6) => Math.abs(a - b) <= tolerance;

const PAGE = `<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>body { margin: 0; height: 100vh; background: #f3f4f6; }</style></head>
<body><script src="/widget.js" data-no-auto-init></script>
<script>
  const cfg = JSON.parse(new URLSearchParams(location.search).get('cfg') || '{}');
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: 'test',
    storageKey: 'launcher-geometry-' + Math.random(), themeColor: '#0a7d5a', ...cfg });
  OSAChatWidget.init();
</script></body></html>`;

function serve() {
  return Bun.serve({
    port: 0,
    async fetch(request) {
      const { pathname } = new URL(request.url);
      if (pathname === '/widget.js') return new Response(Bun.file(WIDGET), { headers: { 'content-type': 'text/javascript' } });
      if (pathname === '/page.html') return new Response(PAGE, { headers: { 'content-type': 'text/html' } });
      if (pathname.endsWith('/health')) return Response.json({ status: 'healthy', version: 'x' });
      if (pathname.startsWith('/api/')) {
        return Response.json({ default_model: 'm', offered_models: [], widget: { title: 'Test Assistant' }, client_tools: [], runtime: null });
      }
      return new Response('not found', { status: 404 });
    },
  });
}

// The chat circle on every animation frame from just before its click, for 700 ms:
// its width, and how far its anchored corner is from the window's edges.
const SAMPLE_CLICK = (anchor) => `(async () => {
  const button = document.querySelector('.osa-launcher-capsule .osa-chat-button') || document.querySelector('.osa-chat-button');
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  const samples = [];
  const sample = (t0) => {
    const r = button.getBoundingClientRect();
    samples.push({ t: Math.round(performance.now() - t0), w: +r.width.toFixed(2),
      edge: +(${anchor === 'left' ? 'r.left' : 'vw - r.right'}).toFixed(2), bottom: +(vh - r.bottom).toFixed(2) });
  };
  const t0 = performance.now();
  sample(t0);
  button.click();
  await new Promise((resolve) => {
    const tick = () => { sample(t0); if (performance.now() - t0 < 700) requestAnimationFrame(tick); else resolve(); };
    requestAnimationFrame(tick);
  });
  const panelEl = document.querySelector('.osa-chat-window');
  const panel = panelEl.getBoundingClientRect();
  const circle = button.getBoundingClientRect();
  const indicator = document.querySelector('.osa-capsule-indicator');
  const ind = indicator && indicator.getBoundingClientRect();
  return { samples, open: panelEl.classList.contains('open'),
    panel: { edge: +(${anchor === 'left' ? 'panel.left' : 'vw - panel.right'}).toFixed(2), bottom: +(vh - panel.bottom).toFixed(2), inside: panel.left >= 0 && panel.right <= vw && panel.top >= 0 },
    indicator: ind ? { dx: +(ind.left - circle.left).toFixed(2), dy: +(ind.top - circle.top).toFixed(2), w: +ind.width.toFixed(2) } : null,
    icons: [...document.querySelectorAll('.osa-launcher-icon')].map((el) => { const r = el.getBoundingClientRect(); return { l: +r.left.toFixed(1), r: +r.right.toFixed(1), t: +r.top.toFixed(1), w: +r.width.toFixed(1) }; }),
    circle: { l: +circle.left.toFixed(1), r: +circle.right.toFixed(1), t: +circle.top.toFixed(1) } };
})()`;

// The collapsed label with its show class forced on: how far it is from the drawn
// circle (10px) and how its vertical center compares with the circle's.
const LABEL = `(() => {
  const button = document.querySelector('.osa-launcher-capsule .osa-chat-button') || document.querySelector('.osa-chat-button');
  const tip = document.querySelector('.osa-chat-tooltip');
  tip.classList.add('visible');
  tip.style.transition = 'none';
  const b = button.getBoundingClientRect(), t = tip.getBoundingClientRect();
  return { gapLeft: +(b.left - t.right).toFixed(2), gapRight: +(t.left - b.right).toFixed(2),
    dy: +((t.top + t.height / 2) - (b.top + b.height / 2)).toFixed(2) };
})()`;

/**
 * anchor: 'right' or 'left'. closed/open: the circle's widths. x/y: its distance from
 * the side and the bottom edge. panelEdge/panelBottom: where the open panel must be.
 */
const SCENARIOS = [
  { label: 'default bubble', cfg: {}, width: 1440, anchor: 'right', closed: 56, open: 56, x: 20, y: 20, panelEdge: 20, panelBottom: 90 },
  { label: 'default capsule', cfg: { launcher: 'capsule' }, width: 1440, anchor: 'right', closed: 58, open: 46, x: 20, y: 20, panelEdge: 85, panelBottom: 20 },
  { label: 'bubble, 80px closed, 64px open', cfg: { launcherSize: 80 }, width: 1440, anchor: 'right', closed: 80, open: 64, x: 20, y: 20, panelEdge: 20, panelBottom: 98 },
  { label: 'bubble on the left, 80px', cfg: { launcherSize: 80, launcherPosition: 'bottom-left' }, width: 1440, anchor: 'left', closed: 80, open: 64, x: 20, y: 20, panelEdge: 20, panelBottom: 98 },
  { label: 'bubble on the left, offsets 32 and 96', cfg: { launcherSize: 72, launcherOpenSize: 60, launcherPosition: 'bottom-left', launcherOffsetX: 32, launcherOffsetY: 96 }, width: 1440, anchor: 'left', closed: 72, open: 60, x: 32, y: 96, panelEdge: 32, panelBottom: 96 + 60 + 14 },
  { label: 'bubble, offsets 40 and 60', cfg: { launcherOffsetX: 40, launcherOffsetY: 60 }, width: 1440, anchor: 'right', closed: 56, open: 56, x: 40, y: 60, panelEdge: 40, panelBottom: 60 + 56 + 14 },
  { label: 'bubble, phone offsets, at 1440px', cfg: { launcherOffsetX: 32, launcherOffsetY: 24, launcherMobileOffsetY: 120 }, width: 1440, anchor: 'right', closed: 56, open: 56, x: 32, y: 24, panelEdge: 32, panelBottom: 24 + 56 + 14 },
  { label: 'bubble, phone offsets, at 390px', cfg: { launcherOffsetX: 32, launcherOffsetY: 24, launcherMobileOffsetY: 120 }, width: 390, anchor: 'right', closed: 56, open: 56, x: 32, y: 120, panelEdge: 32, panelBottom: 120 + 56 + 14 },
  { label: 'capsule on the left, at 1440px', cfg: { launcher: 'capsule', launcherPosition: 'bottom-left' }, width: 1440, anchor: 'left', closed: 58, open: 46, x: 20, y: 20, panelEdge: 85, panelBottom: 20, indicator: true },
  { label: 'capsule on the left, at 390px (a row)', cfg: { launcher: 'capsule', launcherPosition: 'bottom-left' }, width: 390, anchor: 'left', closed: 58, open: 46, x: 20, y: 20, panelEdge: 20, panelBottom: 90, indicator: true, row: true },
  { label: 'capsule, 80px closed, 64px open', cfg: { launcher: 'capsule', launcherSize: 80 }, width: 1440, anchor: 'right', closed: 80, open: 64, x: 20, y: 20, panelEdge: 20 + 64 + 19, panelBottom: 20, indicator: true },
  { label: 'capsule on the left, offsets 30 and 50, at 1440px', cfg: { launcher: 'capsule', launcherPosition: 'bottom-left', launcherOffsetX: 30, launcherOffsetY: 50 }, width: 1440, anchor: 'left', closed: 58, open: 46, x: 30, y: 50, panelEdge: 30 + 46 + 19, panelBottom: 50, indicator: true },
];

async function runScenario(base, cdp, scenario) {
  const { label, cfg, width, anchor, closed, open, x, y, panelEdge, panelBottom } = scenario;
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId: s } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const evaluate = async (expression) => {
    const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, s);
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  try {
    await cdp.send('Runtime.enable', {}, s);
    await cdp.send('Page.enable', {}, s);
    await cdp.send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false }, s);
    await cdp.send('Page.navigate', { url: `${base}/page.html?cfg=${encodeURIComponent(JSON.stringify(cfg))}` }, s);
    const ready = `(() => { const w = document.querySelector('.osa-chat-widget'); return !!w && !w.classList.contains('osa-launcher-waiting') && ${cfg.launcher === 'capsule' ? "!!w.querySelector('.osa-launcher-capsule')" : 'true'}; })()`;
    for (let i = 0; i < 100 && !(await evaluate(ready)); i++) await Bun.sleep(100);
    await Bun.sleep(400);

    const tag = `${label}`;
    const rest = await evaluate(SAMPLE_CLICK(anchor));
    const { samples } = rest;
    report(rest.open, `${tag}, open: the click opens the panel`, rest.open);
    report(near(samples[0].w, closed) && near(samples.at(-1).w, open), `${tag}, open: the chat circle goes from ${closed}px to ${open}px`, [samples[0], samples.at(-1)]);
    if (closed !== open) {
      const between = samples.filter((sample) => sample.w > Math.min(closed, open) + 0.6 && sample.w < Math.max(closed, open) - 0.6);
      report(between.length >= 3, `${tag}, open: through ${between.length} frames in between, not a snap`, samples.slice(0, 6));
    }
    const drifted = samples.filter((sample) => !near(sample.edge, x) || !near(sample.bottom, y));
    report(drifted.length === 0, `${tag}, open: its ${anchor} corner stays ${x}px from the side and ${y}px from the bottom in every one of ${samples.length} frames`, drifted.slice(0, 4));
    report(near(rest.panel.edge, panelEdge) && near(rest.panel.bottom, panelBottom), `${tag}, open: the panel is ${panelEdge}px from the ${anchor} and ${panelBottom}px up`, rest.panel);
    report(rest.panel.inside, `${tag}, open: the panel is inside the window`, rest.panel);
    if (scenario.indicator) {
      report(rest.indicator && near(rest.indicator.dx, 0) && near(rest.indicator.dy, 0) && near(rest.indicator.w, open), `${tag}, open: the indicator is exactly behind the ${open}px chat circle`, rest.indicator);
      const icons = rest.icons;
      report(icons.length === 2 && icons.every((icon) => near(icon.w, open, 0.6)), `${tag}, open: the other circles are ${open}px too`, icons);
      if (scenario.row) {
        report(icons.every((icon) => icon.l > rest.circle.r), `${tag}, open: in the row, the other circles are to the chat circle's right`, { icons, circle: rest.circle });
      } else {
        report(icons.every((icon) => icon.t < rest.circle.t), `${tag}, open: in the column, the other circles are above the chat circle`, { icons, circle: rest.circle });
      }
    }
    await Bun.sleep(300);
    const back = await evaluate(SAMPLE_CLICK(anchor));
    report(!back.open, `${tag}, close: the click closes the panel`, back.open);
    report(near(back.samples[0].w, open) && near(back.samples.at(-1).w, closed), `${tag}, close: the chat circle goes from ${open}px back to ${closed}px`, [back.samples[0], back.samples.at(-1)]);
    const driftedBack = back.samples.filter((sample) => !near(sample.edge, x) || !near(sample.bottom, y));
    report(driftedBack.length === 0, `${tag}, close: its corner stays put in every one of ${back.samples.length} frames`, driftedBack.slice(0, 4));

    const label10 = await evaluate(LABEL);
    const gap = anchor === 'left' ? label10.gapRight : label10.gapLeft;
    report(near(gap, 10) && near(label10.dy, 0, 1.2), `${tag}, label: 10px beside the ${closed}px circle, vertically centered on it`, label10);
  } finally {
    await cdp.send('Target.closeTarget', { targetId });
  }
}

// A real mouse drag of the panel's resize handle, through the browser's own hit
// testing: away from the anchor, so on the left the top-right handle widens the panel
// as it moves right, and on the right the top-left handle widens it as it moves left.
async function checkResizeDrag(base, cdp, position) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId: s } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const evaluate = async (expression) => {
    const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, s);
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const mouse = (type, px, py) => cdp.send('Input.dispatchMouseEvent', { type, x: px, y: py, button: type === 'mouseMoved' ? 'none' : 'left', clickCount: type === 'mouseMoved' ? 0 : 1 }, s);
  try {
    await cdp.send('Runtime.enable', {}, s);
    await cdp.send('Page.enable', {}, s);
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, s);
    const cfg = { launcherPosition: position };
    await cdp.send('Page.navigate', { url: `${base}/page.html?cfg=${encodeURIComponent(JSON.stringify(cfg))}` }, s);
    for (let i = 0; i < 100 && !(await evaluate("!!document.querySelector('.osa-chat-widget') && !document.querySelector('.osa-launcher-waiting')")); i++) await Bun.sleep(100);
    await evaluate("document.querySelector('.osa-chat-button').click()");
    await Bun.sleep(400);
    const before = await evaluate(`(() => { const p = document.querySelector('.osa-chat-window').getBoundingClientRect(); const h = document.querySelector('.osa-resize-handle').getBoundingClientRect(); return { w: p.width, hx: h.left + h.width / 2, hy: h.top + h.height / 2 }; })()`);
    const away = position === 'bottom-left' ? 60 : -60;
    await mouse('mouseMoved', before.hx, before.hy);
    await mouse('mousePressed', before.hx, before.hy);
    await mouse('mouseMoved', before.hx + away / 2, before.hy);
    await mouse('mouseMoved', before.hx + away, before.hy);
    await mouse('mouseReleased', before.hx + away, before.hy);
    await Bun.sleep(100);
    const after = await evaluate(`(() => { const p = document.querySelector('.osa-chat-window').getBoundingClientRect(); return { w: p.width, left: p.left, right: document.documentElement.clientWidth - p.right }; })()`);
    report(near(after.w, before.w + 60), `${position}: a real drag of the handle 60px away from the anchor widens the panel by 60px`, { before: before.w, after });
    report(position === 'bottom-left' ? near(after.left, 20) : near(after.right, 20), `${position}: and its anchored edge stays 20px from the window's`, after);
  } finally {
    await cdp.send('Target.closeTarget', { targetId });
  }
}

async function main() {
  const chromePath = findChrome();
  if (!chromePath) {
    if (process.env.CI) {
      report(false, 'no Chrome found, and CI must run this check');
      return 1;
    }
    console.log('SKIP: no Chrome found');
    return 0;
  }
  const server = serve();
  const base = `http://127.0.0.1:${server.port}`;
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-launcher-geometry-check-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    for (const scenario of SCENARIOS) await runScenario(base, cdp, scenario);
    await checkResizeDrag(base, cdp, 'bottom-right');
    await checkResizeDrag(base, cdp, 'bottom-left');
  } finally {
    cdp?.close();
    chrome?.kill();
    server.stop(true);
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
  console.log(failed ? `\n${failed} check(s) FAILED` : '\nall launcher geometry checks passed');
  return failed ? 1 : 0;
}

process.exit(await main());
