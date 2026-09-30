#!/usr/bin/env bun
/**
 * Real-Chrome check of the launcher's position, size and offsets (#553): what
 * happy-dom cannot show, that the launcher is where the config puts it, in every
 * animation frame of opening and closing the panel.
 *
 * For each scenario a fresh tab loads the real widget with the settings a page
 * passes to setConfig, then the launcher's chat circle is sampled on every animation
 * frame across a click: its width must go from the closed size to the open one and
 * back, and, when the sizes differ, through at least three frames in between on the
 * way open (an animation, not a snap). Its corner at the anchor (bottom-right, or
 * bottom-left on the left) must stay within 0.6px of the configured distance from the
 * window's edges in every frame, so the button never drifts while it resizes.
 * Then, with a real pointer, hovering the resting circle must grow it 5% with that
 * corner held. Then the open panel's place beside or above it, and that it is inside
 * the window (the scenarios include the smallest phone windows and the largest
 * offsets and sizes the config accepts), the collapsed label's 10px gap and vertical
 * center, and, for a capsule, that its circles are in the order they are seen in,
 * which is the Tab order.
 * Two more checks follow the scenarios: on each side, a real mouse drag of the
 * panel's resize handle away from its anchor, and the left side's details (the
 * label's arrow, the pill's origin, the panel's transition, the dark handle) and a
 * size change made while the panel is open.
 *
 * Any exception on the page, and any `[OSA] Ignoring` warning, fails the scenario it
 * happened in. The community config request is answered by a stub with only a title,
 * so the widget's own geometry is what is drawn.
 *
 * Usage:
 *   bun frontend/browser-harness/launcher-geometry-check.mjs
 * It serves the real widget from frontend/ itself, needs nothing running, gives up
 * after four minutes, and skips where there is no Chrome (and fails under CI, which
 * must have one).
 */

import { connect, findChrome, launch } from './chrome.js';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const WIDGET = new URL('../osa-chat-widget.js', import.meta.url);
const TOLERANCE = 0.6;
const MIN_FRAMES = 20;

const watchdog = setTimeout(() => {
  console.error('\nFAIL: the check did not finish within four minutes');
  process.exit(1);
}, 240_000);
watchdog.unref?.();

let failed = 0;
let reports = 0;
function report(ok, label, detail) {
  reports++;
  if (ok) console.log(`ok: ${label}`);
  else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}
const near = (a, b, tolerance = TOLERANCE) => Math.abs(a - b) <= tolerance;

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

const CIRCLE = `(document.querySelector('.osa-launcher-capsule .osa-chat-button') || document.querySelector('.osa-chat-button'))`;

// The chat circle on every animation frame for `ms` from now (and, when `click` is
// set, from just before it is clicked): its width, and how far its anchored corner is
// from the window's edges.
const SAMPLE = (anchor, ms, click) => `(async () => {
  const button = ${CIRCLE};
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  const samples = [];
  const sample = (t0) => {
    const r = button.getBoundingClientRect();
    samples.push({ t: Math.round(performance.now() - t0), w: +r.width.toFixed(2),
      edge: +(${anchor === 'left' ? 'r.left' : 'vw - r.right'}).toFixed(2), bottom: +(vh - r.bottom).toFixed(2) });
  };
  const t0 = performance.now();
  sample(t0);
  ${click ? 'button.click();' : ''}
  await new Promise((resolve) => {
    const tick = () => { sample(t0); if (performance.now() - t0 < ${ms}) requestAnimationFrame(tick); else resolve(); };
    requestAnimationFrame(tick);
  });
  const panelEl = document.querySelector('.osa-chat-window');
  const panel = panelEl.getBoundingClientRect();
  const circle = button.getBoundingClientRect();
  const indicator = document.querySelector('.osa-capsule-indicator');
  const ind = indicator && indicator.getBoundingClientRect();
  return { samples, open: panelEl.classList.contains('open'),
    panel: { edge: +(${anchor === 'left' ? 'panel.left' : 'vw - panel.right'}).toFixed(2), bottom: +(vh - panel.bottom).toFixed(2),
      inside: panel.left >= -0.5 && panel.right <= vw + 0.5 && panel.top >= -0.5 && panel.bottom <= vh + 0.5,
      rect: { l: +panel.left.toFixed(1), t: +panel.top.toFixed(1), r: +panel.right.toFixed(1), b: +panel.bottom.toFixed(1), vw, vh } },
    indicator: ind ? { dx: +(ind.left - circle.left).toFixed(2), dy: +(ind.top - circle.top).toFixed(2), w: +ind.width.toFixed(2) } : null,
    icons: [...document.querySelectorAll('.osa-launcher-icon')].map((el) => { const r = el.getBoundingClientRect(); return { l: +r.left.toFixed(1), r: +r.right.toFixed(1), t: +r.top.toFixed(1), w: +r.width.toFixed(1) }; }),
    circle: { l: +circle.left.toFixed(1), r: +circle.right.toFixed(1), t: +circle.top.toFixed(1), cx: circle.left + circle.width / 2, cy: circle.top + circle.height / 2 } };
})()`;

// The collapsed label with its show class forced on: how far it is from the drawn
// circle (10px), how its vertical center compares with the circle's, and its arrow.
const LABEL = `(() => {
  const button = ${CIRCLE};
  const tip = document.querySelector('.osa-chat-tooltip');
  tip.classList.add('visible');
  tip.style.transition = 'none';
  const b = button.getBoundingClientRect(), t = tip.getBoundingClientRect();
  const arrow = getComputedStyle(tip, '::after');
  return { gapLeft: +(b.left - t.right).toFixed(2), gapRight: +(t.left - b.right).toFixed(2),
    dy: +((t.top + t.height / 2) - (b.top + b.height / 2)).toFixed(2),
    arrow: { left: arrow.borderLeftWidth, right: arrow.borderRightWidth } };
})()`;

// The capsule's three circles in DOM order, which is the Tab order, with where each
// is on the screen.
const CAPSULE_ORDER = `(() => [...document.querySelectorAll('.osa-launcher-capsule > button')].map((el) => {
  const r = el.getBoundingClientRect();
  return { name: el.classList.contains('osa-chat-button') ? 'chat' : el.classList.contains('osa-notebook-btn') ? 'notebook' : 'hpc', l: r.left, t: r.top };
}))()`;

/**
 * anchor: 'right' or 'left'. closed/open: the circle's widths. x/y: its distance from
 * the side and the bottom edge. panelEdge/panelBottom: where the open panel must be.
 * height: the window's (900 by default). resizeTo: a width to narrow or widen to at the
 * end, and the order the capsule's circles must then be in.
 */
const SCENARIOS = [
  { label: 'default bubble', cfg: {}, width: 1440, anchor: 'right', closed: 56, open: 56, x: 20, y: 20, panelEdge: 20, panelBottom: 90 },
  { label: 'default capsule', cfg: { launcher: 'capsule' }, width: 1440, anchor: 'right', closed: 58, open: 46, x: 20, y: 20, panelEdge: 85, panelBottom: 20, capsule: true },
  { label: 'bubble, 80px closed, 64px open', cfg: { launcherSize: 80 }, width: 1440, anchor: 'right', closed: 80, open: 64, x: 20, y: 20, panelEdge: 20, panelBottom: 98 },
  { label: 'bubble on the left, 80px', cfg: { launcherSize: 80, launcherPosition: 'bottom-left' }, width: 1440, anchor: 'left', closed: 80, open: 64, x: 20, y: 20, panelEdge: 20, panelBottom: 98 },
  { label: 'bubble on the left, offsets 32 and 96', cfg: { launcherSize: 72, launcherOpenSize: 60, launcherPosition: 'bottom-left', launcherOffsetX: 32, launcherOffsetY: 96 }, width: 1440, anchor: 'left', closed: 72, open: 60, x: 32, y: 96, panelEdge: 32, panelBottom: 96 + 60 + 14 },
  { label: 'bubble, offsets 40 and 60', cfg: { launcherOffsetX: 40, launcherOffsetY: 60 }, width: 1440, anchor: 'right', closed: 56, open: 56, x: 40, y: 60, panelEdge: 40, panelBottom: 60 + 56 + 14 },
  { label: 'bubble, phone offsets, at 1440px', cfg: { launcherOffsetX: 32, launcherOffsetY: 24, launcherMobileOffsetY: 120 }, width: 1440, anchor: 'right', closed: 56, open: 56, x: 32, y: 24, panelEdge: 32, panelBottom: 24 + 56 + 14 },
  { label: 'bubble, phone offsets, at 390px', cfg: { launcherOffsetX: 32, launcherOffsetY: 24, launcherMobileOffsetY: 120 }, width: 390, anchor: 'right', closed: 56, open: 56, x: 32, y: 120, panelEdge: 32, panelBottom: 120 + 56 + 14 },
  { label: 'capsule on the left, at 1440px', cfg: { launcher: 'capsule', launcherPosition: 'bottom-left' }, width: 1440, anchor: 'left', closed: 58, open: 46, x: 20, y: 20, panelEdge: 85, panelBottom: 20, capsule: true, resizeTo: { width: 390, order: ['chat', 'notebook', 'hpc'] } },
  { label: 'capsule on the left, at 390px (a row)', cfg: { launcher: 'capsule', launcherPosition: 'bottom-left' }, width: 390, anchor: 'left', closed: 58, open: 46, x: 20, y: 20, panelEdge: 20, panelBottom: 90, capsule: true, row: true, resizeTo: { width: 1440, order: ['hpc', 'notebook', 'chat'] } },
  { label: 'capsule on the right, at 390px (a row)', cfg: { launcher: 'capsule' }, width: 390, anchor: 'right', closed: 58, open: 46, x: 20, y: 20, panelEdge: 20, panelBottom: 90, capsule: true, row: true },
  { label: 'capsule, 80px closed, 64px open', cfg: { launcher: 'capsule', launcherSize: 80 }, width: 1440, anchor: 'right', closed: 80, open: 64, x: 20, y: 20, panelEdge: 20 + 64 + 19, panelBottom: 20, capsule: true },
  { label: 'capsule on the left, offsets 30 and 50, at 1440px', cfg: { launcher: 'capsule', launcherPosition: 'bottom-left', launcherOffsetX: 30, launcherOffsetY: 50 }, width: 1440, anchor: 'left', closed: 58, open: 46, x: 30, y: 50, panelEdge: 30 + 46 + 19, panelBottom: 50, capsule: true },
  // The smallest windows, and the largest values the config accepts: the panel must
  // stay inside the window whatever the offsets are.
  { label: 'phone 320x568, mobile offset 30', cfg: { launcherMobileOffsetX: 30 }, width: 320, height: 568, anchor: 'right', closed: 56, open: 56, x: 30, y: 20, panelEdge: 30, panelBottom: 90 },
  { label: 'phone 320x568, on the left, offset 30', cfg: { launcherPosition: 'bottom-left', launcherOffsetX: 30 }, width: 320, height: 568, anchor: 'left', closed: 56, open: 56, x: 30, y: 20, panelEdge: 30, panelBottom: 90 },
  { label: 'the largest values, at 390x640', cfg: { launcherSize: 96, launcherOffsetX: 200, launcherOffsetY: 200, launcherMobileOffsetX: 200, launcherMobileOffsetY: 200 }, width: 390, height: 640, anchor: 'right', closed: 96, open: 77, x: 200, y: 200, panelEdge: 200, panelBottom: 200 + 77 + 14 },
  { label: 'capsule, the largest values, at 601px', cfg: { launcher: 'capsule', launcherSize: 96, launcherOpenSize: 96, launcherOffsetX: 200 }, width: 601, anchor: 'right', closed: 96, open: 96, x: 200, y: 20, panelEdge: 200 + 96 + 19, panelBottom: 20, capsule: true },
];

// One tab per scenario. Collects what the page throws or warns about as it runs.
async function withTab(cdp, { width, height = 900 }, body) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId: s } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const problems = [];
  const unsubscribe = cdp.on((message) => {
    if (message.sessionId !== s) return;
    if (message.method === 'Runtime.exceptionThrown') {
      const d = message.params.exceptionDetails;
      problems.push(`exception: ${d.exception?.description || d.text}`);
    } else if (message.method === 'Runtime.consoleAPICalled') {
      const text = message.params.args.map((a) => a.value ?? a.description ?? '').join(' ');
      if (message.params.type === 'error' || text.includes('[OSA] Ignoring')) problems.push(`${message.params.type}: ${text}`);
    }
  });
  const evaluate = async (expression) => {
    const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, s);
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const waitFor = async (expression, label, timeoutMs = 10_000) => {
    const end = Date.now() + timeoutMs;
    while (Date.now() < end) {
      if (await evaluate(expression)) return true;
      await Bun.sleep(100);
    }
    report(false, `timed out after ${timeoutMs / 1000}s waiting for ${label}`);
    return false;
  };
  const mouse = (type, x, y) => cdp.send('Input.dispatchMouseEvent', { type, x, y, button: type === 'mouseMoved' ? 'none' : 'left', clickCount: type === 'mouseMoved' ? 0 : 1 }, s);
  try {
    await cdp.send('Runtime.enable', {}, s);
    await cdp.send('Page.enable', {}, s);
    await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, s);
    return await body({ evaluate, waitFor, mouse, send: (method, params) => cdp.send(method, params, s), problems });
  } finally {
    unsubscribe?.();
    await cdp.send('Target.closeTarget', { targetId });
  }
}

async function load(base, cfg, { evaluate, waitFor, send }) {
  await send('Page.navigate', { url: `${base}/page.html?cfg=${encodeURIComponent(JSON.stringify(cfg))}` });
  const ready = `(() => { const w = document.querySelector('.osa-chat-widget'); return !!w && !w.classList.contains('osa-launcher-waiting') && ${cfg.launcher === 'capsule' ? "!!w.querySelector('.osa-launcher-capsule')" : 'true'}; })()`;
  if (!(await waitFor(ready, 'the widget to be ready'))) return false;
  await Bun.sleep(400);
  return true;
}

async function runScenario(base, cdp, scenario) {
  const { label, cfg, width, height, anchor, closed, open, x, y, panelEdge, panelBottom } = scenario;
  await withTab(cdp, { width, height }, async (tab) => {
    const { evaluate, mouse, problems } = tab;
    if (!(await load(base, cfg, tab))) return;

    const opened = await evaluate(SAMPLE(anchor, 700, true));
    const { samples } = opened;
    report(opened.open, `${label}, open: the click opens the panel`, opened.open);
    report(samples.length >= MIN_FRAMES, `${label}, open: sampled on ${samples.length} animation frames, at least ${MIN_FRAMES}`);
    report(near(samples[0].w, closed) && near(samples.at(-1).w, open), `${label}, open: the chat circle goes from ${closed}px to ${open}px`, [samples[0], samples.at(-1)]);
    if (closed !== open) {
      const between = samples.filter((sample) => sample.w > Math.min(closed, open) + TOLERANCE && sample.w < Math.max(closed, open) - TOLERANCE);
      report(between.length >= 3, `${label}, open: through ${between.length} frames in between, not a snap`, samples.slice(0, 6));
    }
    const drifted = samples.filter((sample) => !near(sample.edge, x) || !near(sample.bottom, y));
    report(drifted.length === 0, `${label}, open: its ${anchor} corner stays ${x}px from the side and ${y}px from the bottom in every one of ${samples.length} frames`, drifted.slice(0, 4));
    report(near(opened.panel.edge, panelEdge) && near(opened.panel.bottom, panelBottom), `${label}, open: the panel is ${panelEdge}px from the ${anchor} and ${panelBottom}px up`, opened.panel);
    report(opened.panel.inside, `${label}, open: the panel is inside the window`, opened.panel.rect);

    if (scenario.capsule) {
      report(opened.indicator && near(opened.indicator.dx, 0) && near(opened.indicator.dy, 0) && near(opened.indicator.w, open), `${label}, open: the indicator is behind the ${open}px chat circle, within ${TOLERANCE}px`, opened.indicator);
      const icons = opened.icons;
      report(icons.length === 2 && icons.every((icon) => near(icon.w, open)), `${label}, open: the other circles are ${open}px too`, icons);
      const circles = await evaluate(CAPSULE_ORDER);
      const ordered = scenario.row
        ? circles.every((c, i) => i === 0 || c.l > circles[i - 1].l)
        : circles.every((c, i) => i === 0 || c.t > circles[i - 1].t);
      report(circles.length === 3 && ordered, `${label}, open: the circles are in the order they are seen in (${scenario.row ? 'left to right' : 'top to bottom'}), so Tab follows the eye`, circles.map((c) => c.name));
    }

    await Bun.sleep(300);
    const closedAgain = await evaluate(SAMPLE(anchor, 700, true));
    report(!closedAgain.open, `${label}, close: the click closes the panel`, closedAgain.open);
    report(near(closedAgain.samples[0].w, open) && near(closedAgain.samples.at(-1).w, closed), `${label}, close: the chat circle goes from ${open}px back to ${closed}px`, [closedAgain.samples[0], closedAgain.samples.at(-1)]);
    const driftedBack = closedAgain.samples.filter((sample) => !near(sample.edge, x) || !near(sample.bottom, y));
    report(driftedBack.length === 0, `${label}, close: its corner stays put in every one of ${closedAgain.samples.length} frames`, driftedBack.slice(0, 4));

    const tip = await evaluate(LABEL);
    const gap = anchor === 'left' ? tip.gapRight : tip.gapLeft;
    report(near(gap, 10) && near(tip.dy, 0, 1.2), `${label}, label: 10px beside the ${closed}px circle, vertically centered on it`, tip);
    const [arrowSide, arrowOther] = anchor === 'left' ? [tip.arrow.right, tip.arrow.left] : [tip.arrow.left, tip.arrow.right];
    report(arrowSide === '6px' && arrowOther === '0px', `${label}, label: its arrow points at the circle, from the ${anchor === 'left' ? 'left' : 'right'} side of the label`, tip.arrow);

    // Hovered with a real pointer, a circle drawn larger than its box grows 5% more with
    // its corner held; a bubble the same size in both states uses the shared transform,
    // which grows it around its center, so it is not held to this.
    if (closed !== open) {
      const rest = closedAgain.circle;
      const pending = evaluate(SAMPLE(anchor, 500, false));
      await mouse('mouseMoved', rest.cx, rest.cy);
      const hovered = (await pending).samples;
      report(near(hovered.at(-1).w, closed * 1.05), `${label}, hover: the resting circle ends at ${(closed * 1.05).toFixed(1)}px`, hovered.at(-1));
      const driftedHover = hovered.filter((sample) => !near(sample.edge, x) || !near(sample.bottom, y));
      report(driftedHover.length === 0, `${label}, hover: its corner stays put in every one of ${hovered.length} frames`, driftedHover.slice(0, 4));
      const pendingLeave = evaluate(SAMPLE(anchor, 500, false));
      await mouse('mouseMoved', 5, 5);
      const left = (await pendingLeave).samples;
      report(near(left.at(-1).w, closed), `${label}, leave: back to ${closed}px`, left.at(-1));
      const driftedLeave = left.filter((sample) => !near(sample.edge, x) || !near(sample.bottom, y));
      report(driftedLeave.length === 0, `${label}, leave: its corner stays put in every one of ${left.length} frames`, driftedLeave.slice(0, 4));
    }

    // A window resized across the 600px line reorders a capsule anchored on the left.
    if (scenario.resizeTo) {
      await tab.send('Emulation.setDeviceMetricsOverride', { width: scenario.resizeTo.width, height: height ?? 900, deviceScaleFactor: 1, mobile: false });
      await Bun.sleep(400);
      const order = (await evaluate(CAPSULE_ORDER)).map((c) => c.name);
      report(JSON.stringify(order) === JSON.stringify(scenario.resizeTo.order), `${label}, resized to ${scenario.resizeTo.width}px wide: the circles are ${scenario.resizeTo.order.join(', ')} in the DOM, so Tab follows the eye`, order);
    }

    report(problems.length === 0, `${label}: nothing on the page threw or warned`, problems.slice(0, 3));
  });
}

// A real mouse drag of the panel's resize handle, through the browser's own hit
// testing: away from the anchor, so on the left the top-right handle widens the panel
// as it moves right, and on the right the top-left handle widens it as it moves left.
async function checkResizeDrag(base, cdp, position) {
  await withTab(cdp, { width: 1440 }, async (tab) => {
    const { evaluate, mouse } = tab;
    if (!(await load(base, { launcherPosition: position }, tab))) return;
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
  });
}

// What only the left side has, or only a live change shows.
async function checkLeftDetails(base, cdp) {
  // The dark scheme's handle, and the capsule's pill and panel transition, anchored on the left.
  await withTab(cdp, { width: 390 }, async (tab) => {
    const { evaluate } = tab;
    if (!(await load(base, { launcher: 'capsule', launcherPosition: 'bottom-left' }, tab))) return;
    await evaluate(`OSAChatWidget.setColorScheme('dark')`);
    await evaluate(`${CIRCLE}.click()`);
    await Bun.sleep(500);
    const details = await evaluate(`(() => ({
      handle: getComputedStyle(document.querySelector('.osa-resize-handle'), '::before').borderRightColor,
      handleLeft: getComputedStyle(document.querySelector('.osa-resize-handle'), '::before').borderLeftStyle,
      pillOrigin: getComputedStyle(document.querySelector('.osa-launcher-capsule'), '::before').transformOrigin,
    }))()`);
    report(details.handle === 'rgba(255, 255, 255, 0.3)' && details.handleLeft === 'none', 'left, dark: the top-right resize handle is drawn in the dark scheme\'s color, on its right edge', details);
    report(parseFloat(details.pillOrigin) === 0, 'left, 390px: the pill grows out of the chat circle on the left, from its left edge', details.pillOrigin);
  });
  await withTab(cdp, { width: 1440 }, async (tab) => {
    const { evaluate } = tab;
    if (!(await load(base, { launcher: 'capsule', launcherPosition: 'bottom-left' }, tab))) return;
    const transition = await evaluate(`getComputedStyle(document.querySelector('.osa-chat-window')).transitionProperty`);
    report(transition.includes('left'), 'left, 1440px: the capsule\'s panel eases its side as well as its bottom', transition);
  });

  // A size change made while the panel is open moves the indicator to where the layout puts the chat circle now.
  await withTab(cdp, { width: 1440 }, async (tab) => {
    const { evaluate, problems } = tab;
    if (!(await load(base, { launcher: 'capsule' }, tab))) return;
    await evaluate(`${CIRCLE}.click()`);
    await Bun.sleep(500);
    await evaluate(`OSAChatWidget.setConfig({ launcherSize: 72 })`);
    await Bun.sleep(500);
    const now = await evaluate(`(() => { const c = ${CIRCLE}.getBoundingClientRect(); const i = document.querySelector('.osa-capsule-indicator').getBoundingClientRect(); return { w: +c.width.toFixed(2), dx: +(i.left - c.left).toFixed(2), dy: +(i.top - c.top).toFixed(2), iw: +i.width.toFixed(2) }; })()`);
    report(near(now.w, 58) && near(now.dx, 0) && near(now.dy, 0) && near(now.iw, 58), 'live: with the panel open, a launcherSize of 72 opens at 58px, and the indicator is behind the chat circle again', now);
    report(problems.length === 0, 'live: nothing on the page threw or warned', problems.slice(0, 3));
  });
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
  if (SCENARIOS.length === 0) {
    report(false, 'there are no scenarios to run');
    return 1;
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
    await checkLeftDetails(base, cdp);
  } finally {
    cdp?.close();
    if (chrome) {
      chrome.kill();
      await chrome.exited;
    }
    server.stop(true);
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
  report(reports > 0, `${reports} checks ran`);
  console.log(failed ? `\n${failed} check(s) FAILED` : '\nall launcher geometry checks passed');
  return failed ? 1 : 0;
}

process.exit(await main());
