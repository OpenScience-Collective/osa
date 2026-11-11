#!/usr/bin/env bun
/**
 * Real-Chrome check of the widget's Privacy choices button and dialog (#706), at a
 * laptop's 1280 x 900 and a phone's 390 x 844, in the light and the dark scheme, with the
 * window in the corner and in the center.
 *
 * The Bun suite (frontend/test-widget-privacy-choices.js) holds the logic in happy-dom,
 * which has no layout, no real keyboard and no painted colors. This runs the widget in
 * headless Chrome against a community config shaped like NEMAR's, and checks, for each
 * of the eight combinations:
 *
 * - the footer's Privacy choices button is on screen, at least 24 px each way, and its
 *   text reads at 4.5:1;
 * - the dialog is inside the viewport and inside the chat window, and nothing scrolls
 *   sideways, the dialog's own body included;
 * - every target at least 24 px (the close button, the two checkboxes, the link) and
 *   every row at least 44 px tall;
 * - the title, the two labels, the two helper lines and the link read at 4.5:1, the close
 *   icon at 3:1, where Chrome paints them;
 * - the real Tab key (a trusted key event, which is what moves focus in a browser) never
 *   takes focus out of the dialog, in 20 presses either way, and cycles through exactly
 *   its four controls;
 * - the real Space key toggles the checkbox it is on, and the choice reaches storage;
 * - the real Escape key closes only the dialog, the window stays where it was, and
 *   focus returns to the Privacy choices button; in the center a second Escape docks it.
 *
 * It stands up its own server, so it needs no backend and no network. Screenshots are
 * written when a directory is given, as the argument or as PRIVACY_CHECK_SCREENSHOTS (CI
 * sets it, and uploads them when the check fails).
 *
 * Usage: bun frontend/browser-harness/privacy-choices-check.mjs [screenshot-dir]
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

const COMMUNITY = 'privacycheck';
const STORAGE_KEY = `privacy-check-${Math.random().toString(36).slice(2, 8)}`;
const DATASET = { id: 'xx099904', title: 'A test dataset' };
const PRIVACY_URL = 'https://docs.example.org/privacy/';
const WIDGET_CONFIG = {
  title: 'NEMAR Assistant',
  theme_color: '#5bbad5',
  theme_text_color: '#04121f',
  accent_color: '#257a92',
  user_bubble_color: '#5bbad5',
  user_bubble_text_color: '#04121f',
  color_scheme: 'auto',
  suggested_questions: ['Find EEG datasets with resting state recordings'],
  privacy_url: PRIVACY_URL,
};

const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>privacy</title>
<style>body{margin:0;font-family:system-ui,sans-serif;background:#fff;color:#0f172a}main{padding:24px 80px;min-height:1200px}</style></head>
<body><main id="host-page"><h1>${DATASET.title}</h1><a id="host-link" href="#top">A link on the page</a></main>
<script>window.__OSA_TEST__ = true;</script>
<script src="/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setDataset(${JSON.stringify(DATASET)});
  OSAChatWidget.setConfig({ apiEndpoint: location.origin + '/api', communityId: ${JSON.stringify(COMMUNITY)}, storageKey: ${JSON.stringify(STORAGE_KEY)} });
  OSAChatWidget.init();
</script></body></html>`;

function startServer() {
  return Bun.serve({
    port: 0,
    hostname: '127.0.0.1',
    async fetch(request) {
      const { pathname } = new URL(request.url);
      if (pathname === '/') return new Response(PAGE, { headers: { 'content-type': 'text/html' } });
      if (pathname === '/osa-chat-widget.js') return new Response(WIDGET, { headers: { 'content-type': 'text/javascript' } });
      if (pathname === '/api/health') return Response.json({ status: 'healthy' });
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
async function pressKey(cdp, sessionId, { key, code, windowsVirtualKeyCode, modifiers = 0, text }) {
  // A key that types a character is a keyDown with its text; one that does not is a rawKeyDown.
  await cdp.send('Input.dispatchKeyEvent', { type: text ? 'keyDown' : 'rawKeyDown', key, code, windowsVirtualKeyCode, modifiers, text }, sessionId);
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode, modifiers }, sessionId);
}

const TAB = { key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 };
const SHIFT_TAB = { ...TAB, modifiers: 8 };
const ESCAPE = { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 };
const SPACE = { key: ' ', code: 'Space', windowsVirtualKeyCode: 32, text: ' ' };

async function openPage(cdp, base, { width, height }) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  await cdp.send('Page.enable', {}, sessionId);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, sessionId);
  await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] }, sessionId);
  await cdp.send('Page.navigate', { url: `${base}/` }, sessionId);
  return { targetId, sessionId };
}

const WIDGET_READY = `(async () => {
  for (let i = 0; i < 400; i++) {
    const title = document.querySelector('.osa-chat-title');
    if (window.OSAChatWidget && title && title.firstChild && title.firstChild.textContent.trim() === 'NEMAR Assistant') return true;
    await new Promise((r) => setTimeout(r, 25));
  }
  return false;
})()`;

const SIZE_OF = (selector) => `(() => {
  const el = document.querySelector(${JSON.stringify(selector)});
  if (!el) return null;
  const box = el.getBoundingClientRect();
  return { width: Math.round(box.width * 10) / 10, height: Math.round(box.height * 10) / 10, left: box.left, top: box.top, right: box.right, bottom: box.bottom };
})()`;

const GEOMETRY = `(() => {
  const win = document.querySelector('.osa-chat-window').getBoundingClientRect();
  const overlay = document.querySelector('.osa-privacy-overlay');
  const modal = document.querySelector('.osa-privacy-modal');
  const box = modal.getBoundingClientRect();
  return {
    viewport: { width: window.innerWidth, height: window.innerHeight },
    box: { left: box.left, top: box.top, right: box.right, bottom: box.bottom },
    inViewport: box.left >= 0 && box.top >= 0 && box.right <= window.innerWidth && box.bottom <= window.innerHeight,
    inWindow: box.left >= win.left - 0.5 && box.top >= win.top - 0.5 && box.right <= win.right + 0.5 && box.bottom <= win.bottom + 0.5,
    sideways: document.documentElement.scrollWidth > window.innerWidth + 1
      || overlay.scrollWidth > overlay.clientWidth + 1 || modal.scrollWidth > modal.clientWidth + 1,
    role: modal.getAttribute('role'),
    modal: modal.getAttribute('aria-modal'),
    labelledBy: document.getElementById(modal.getAttribute('aria-labelledby'))?.textContent.trim(),
    link: (() => { const a = document.querySelector('.osa-privacy-link'); return { href: a.getAttribute('href'), target: a.target, rel: a.rel }; })(),
  };
})()`;

// The contrast ratio (WCAG) of an element's text color against what is behind it: its
// own and its ancestors' backgrounds, composited from the first opaque one up, over
// white. Text must reach 4.5:1; a graphic (an icon) 3:1.
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

// Text, at 4.5:1; the close icon, a graphic, at 3:1.
const DIALOG_CONTRASTS = [
  ['.osa-privacy-modal .osa-settings-title', 4.5, 'the title'],
  ['label[for="osa-privacy-page"]', 4.5, '"Use this page for answers"'],
  ['label[for="osa-privacy-usage"]', 4.5, '"Share usage data"'],
  ['#osa-privacy-page-hint', 4.5, 'the page helper text'],
  ['#osa-privacy-usage-hint', 4.5, 'the usage helper text'],
  ['.osa-privacy-link', 4.5, 'the privacy link'],
  ['.osa-privacy-close-btn', 3, 'the close icon'],
];

async function checkTargets(call, label) {
  const sizes = {};
  for (const [name, selector] of [
    ['close', '.osa-privacy-close-btn'],
    ['page box', '#osa-privacy-page'],
    ['usage box', '#osa-privacy-usage'],
    ['page row', '.osa-privacy-page-field .osa-privacy-row'],
    ['usage row', '.osa-privacy-usage-field .osa-privacy-row'],
    ['page label', 'label[for="osa-privacy-page"]'],
    ['usage label', 'label[for="osa-privacy-usage"]'],
    ['link', '.osa-privacy-link'],
  ]) sizes[name] = await call(SIZE_OF(selector));
  for (const name of ['close', 'page box', 'usage box', 'link']) {
    const size = sizes[name];
    report(size && size.width >= 24 && size.height >= 24, `${label}: the ${name} is at least 24 x 24`, size);
  }
  for (const name of ['page row', 'usage row', 'page label', 'usage label']) {
    const size = sizes[name];
    report(size && size.height >= 44, `${label}: the ${name} is at least 44 px tall`, size);
  }
}

async function checkCombination(cdp, base, screenshotDir, viewport, scheme, mode) {
  const { name, width, height } = viewport;
  const label = `${name}, ${scheme}, ${mode}`;
  console.log(`--- ${label}`);
  const page = await openPage(cdp, base, viewport);
  const { sessionId } = page;
  const call = (expression, options) => evaluate(cdp, sessionId, expression, options);
  const press = (key) => pressKey(cdp, sessionId, key);
  report(await call(WIDGET_READY), `${label}: the widget has its community config`);
  if (scheme === 'dark') await call(`OSAChatWidget.setColorScheme('dark')`);
  await call(`OSAChatWidget.open({ mode: ${JSON.stringify(mode)} })`, { userGesture: true });
  report(await waitFor(cdp, sessionId, `document.querySelector('.osa-chat-window.open') !== null`, 'the window opens'), `${label}: the window opens`);
  const panel = await call(`getComputedStyle(document.querySelector('.osa-chat-window')).backgroundColor`);
  report(panel === (scheme === 'dark' ? 'rgb(17, 24, 39)' : 'rgb(255, 255, 255)'), `${label}: on the ${scheme} panel`, panel);

  // The footer's button.
  const button = await call(SIZE_OF('.osa-privacy-btn'));
  report(button && button.width >= 24 && button.height >= 24, `${label}: the Privacy choices button is at least 24 x 24`, button);
  report(button && button.left >= 0 && button.right <= width && button.top >= 0 && button.bottom <= height, `${label}: and on screen`, button);
  const icon = await call(SIZE_OF('.osa-privacy-btn svg'));
  report(icon && Math.round(icon.height) === 14 && Math.round(icon.width) === 30, `${label}: with the opt-out symbol at 30 x 14`, icon);
  const buttonContrast = await call(CONTRAST_OF('.osa-privacy-btn'));
  report(buttonContrast.ratio !== null && buttonContrast.ratio >= 4.5, `${label}: the button's text reads at 4.5:1 or better`, buttonContrast);
  const sideways = await call(`document.documentElement.scrollWidth > window.innerWidth + 1 || document.querySelector('.osa-combined-footer').scrollWidth > document.querySelector('.osa-combined-footer').clientWidth + 1`);
  report(!sideways, `${label}: the footer does not scroll sideways`);
  const iconFills = await call(`[...document.querySelectorAll('.osa-privacy-btn svg path')].map((p) => getComputedStyle(p).fill)`);
  report(JSON.stringify(iconFills) === JSON.stringify(['rgb(255, 255, 255)', 'rgb(0, 102, 255)', 'rgb(255, 255, 255)', 'rgb(0, 102, 255)']),
    `${label}: the symbol keeps its own colors in this scheme`, iconFills);

  // Open it from the button, with a real focus on the button first.
  await call(`document.querySelector('.osa-privacy-btn').focus()`);
  await call(`document.querySelector('.osa-privacy-btn').click()`, { userGesture: true });
  report(await waitFor(cdp, sessionId, `document.querySelector('.osa-privacy-overlay.open') !== null`, 'the dialog opens'), `${label}: the button opens the dialog`);
  const g = await call(GEOMETRY);
  report(g.role === 'dialog' && g.modal === 'true' && g.labelledBy === 'Privacy choices', `${label}: role=dialog, aria-modal, named "Privacy choices"`, g);
  report(g.inViewport, `${label}: the dialog is inside the ${g.viewport.width} x ${g.viewport.height} viewport`, g.box);
  report(g.inWindow, `${label}: and inside the chat window`, g.box);
  report(!g.sideways, `${label}: nothing scrolls sideways`);
  report(g.link.href === PRIVACY_URL && g.link.target === '_blank' && g.link.rel === 'noopener noreferrer', `${label}: the privacy link is the community's, https, new tab, noopener noreferrer`, g.link);
  report(await call(`document.activeElement === document.querySelector('#osa-privacy-page')`), `${label}: focus is on the first choice`);
  const boxes = await call(`[document.querySelector('#osa-privacy-page').checked, document.querySelector('#osa-privacy-usage').checked]`);
  report(boxes[0] && boxes[1], `${label}: both choices are on by default`, boxes);
  await checkTargets(call, label);
  for (const [selector, minimum, what] of DIALOG_CONTRASTS) {
    const { ratio, why } = await call(CONTRAST_OF(selector));
    report(ratio !== null && ratio >= minimum, `${label}: ${what} reads at ${minimum}:1 or better`, ratio === null ? why : ratio);
  }
  await screenshot(cdp, sessionId, screenshotDir, `privacy-${name}-${scheme}-${mode}.png`);

  // The real Tab key, forward and back: only the dialog's own four controls.
  const where = `(() => {
    const el = document.activeElement;
    return { inside: document.querySelector('.osa-privacy-overlay').contains(el), at: (el.id || el.className || el.tagName) };
  })()`;
  let escaped = null;
  const visited = new Set();
  for (let i = 0; i < 20; i++) {
    await press(TAB);
    const now = await call(where);
    if (!now.inside && !escaped) escaped = `Tab ${i + 1}: ${now.at}`;
    visited.add(now.at);
  }
  report(!escaped, `${label}: 20 presses of Tab never leave the dialog`, escaped);
  report(visited.size === 4, `${label}: and cycle through its four controls`, [...visited]);
  let escapedBack = null;
  for (let i = 0; i < 20; i++) {
    await press(SHIFT_TAB);
    const now = await call(where);
    if (!now.inside && !escapedBack) escapedBack = `Shift+Tab ${i + 1}: ${now.at}`;
  }
  report(!escapedBack, `${label}: 20 presses of Shift+Tab never leave it either`, escapedBack);

  // The real Space key toggles the box it is on, and the choice is stored.
  await call(`document.querySelector('#osa-privacy-usage').focus()`);
  await press(SPACE);
  const afterSpace = await call(`({ checked: document.querySelector('#osa-privacy-usage').checked, stored: localStorage.getItem('osa-share-usage-enabled') })`);
  report(afterSpace.checked === false && afterSpace.stored === 'false', `${label}: Space unchecks "Share usage data" and stores the choice`, afterSpace);
  await press(SPACE);
  const backOn = await call(`({ checked: document.querySelector('#osa-privacy-usage').checked, stored: localStorage.getItem('osa-share-usage-enabled') })`);
  report(backOn.checked === true && backOn.stored === 'true', `${label}: and Space again checks it`, backOn);

  // Escape closes only the dialog.
  await press(ESCAPE);
  report(await waitFor(cdp, sessionId, `document.querySelector('.osa-privacy-overlay.open') === null`, 'the dialog closes'), `${label}: Escape closes the dialog`);
  const after = await call(`({
    open: document.querySelector('.osa-chat-window').classList.contains('open'),
    center: document.querySelector('.osa-chat-window').classList.contains('osa-center'),
    onButton: document.activeElement === document.querySelector('.osa-privacy-btn'),
  })`);
  report(after.open && after.center === (mode === 'center'), `${label}: the window stays open, in the ${mode}`, after);
  report(after.onButton, `${label}: and focus is back on the Privacy choices button`, after);
  if (mode === 'center') {
    await press(ESCAPE);
    report(await waitFor(cdp, sessionId, `!document.querySelector('.osa-chat-window').classList.contains('osa-center')`, 'docks'), `${label}: a second Escape docks the center dialog`);
  }
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
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-privacy-check-'));
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
      for (const scheme of ['light', 'dark']) {
        for (const mode of ['corner', 'center']) {
          await checkCombination(cdp, base, screenshotDir, viewport, scheme, mode);
        }
      }
    }
  } finally {
    try { cdp?.close?.(); } catch (error) { /* already gone */ }
    chrome?.kill();
    server.stop(true);
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
}

await check(process.argv[2] || process.env.PRIVACY_CHECK_SCREENSHOTS);
if (failed) process.exit(1);
