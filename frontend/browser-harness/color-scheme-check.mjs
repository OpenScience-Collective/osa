#!/usr/bin/env bun
/**
 * Real-Chrome check for the widget's light and dark appearance (#469).
 *
 * The bug this exists for is the browser's own: on a host page that declares
 * `color-scheme: dark`, Chrome draws a form control with no colors of its own in
 * its dark field colors, so the widget's chat input came out dark inside a light
 * panel. happy-dom has no such stylesheet, so only a real browser can show the
 * bug or its fix. Every check starts from a dark host page, and the first one is a
 * control: a plain input on that page must come out dark, or the page is not
 * reproducing the bug and nothing after it proves anything.
 *
 * Usage, against a running widget_e2e.py server (see its docstring):
 *   bun frontend/browser-harness/color-scheme-check.mjs <url> light   # the default config: a community that never set color_scheme
 *   bun frontend/browser-harness/color-scheme-check.mjs <url> nemar   # --nemar: color_scheme: auto
 * where <url> is http://127.0.0.1:PORT/browser-harness/widget-e2e.html
 *
 * The pop-out step runs on a second load of the page, under the harness's own
 * policy with 'unsafe-inline' added to script-src, which is what nemar.org's live
 * policy allows and the harness's stricter one does not: the pop-out is written
 * into about:blank, inherits its opener's policy, and runs its scripts inline, so
 * under the harness's policy it stays blank (a limitation of today's pop-out on
 * any host page without 'unsafe-inline', recorded for the pop-out redesign, #470).
 */

import { connect, findChrome, launch } from './chrome.js';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [url, mode] = process.argv.slice(2);
if (!url || !['light', 'nemar'].includes(mode)) {
  console.error('usage: bun frontend/browser-harness/color-scheme-check.mjs <url> light|nemar');
  process.exit(2);
}

let failed = 0;
function report(ok, label, detail) {
  if (ok) {
    console.log(`ok: ${label}`);
  } else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}

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
  report(false, `timed out waiting: ${label}`);
  return false;
}

// The device's own setting, as Chrome's media emulation reports it to the page.
async function setDevice(cdp, sessionId, scheme) {
  await cdp.send('Emulation.setEmulatedMedia', {
    features: [{ name: 'prefers-color-scheme', value: scheme }],
  }, sessionId);
}

// A host page in its own dark theme: what nemar.org's dark mode, or any dark
// site, declares. Set through the CSSOM, which the harness's CSP allows.
const DARK_HOST = `
  document.documentElement.style.colorScheme = 'dark';
  document.body.style.background = '#0b0f14';
  document.body.style.color = '#e6edf3';
  true;
`;

// The widget's community config request has finished (the browser's own resource
// timing, so it holds for a pop-out whose title was already copied from its
// opener), and a moment has passed for the widget to apply it.
async function waitForCommunityConfig(cdp, sessionId, label) {
  const loaded = await waitFor(cdp, sessionId,
    `performance.getEntriesByType('resource').some((e) => /\\/api\\/[a-z0-9-]+$/.test(e.name) && e.responseEnd > 0)`,
    label);
  await Bun.sleep(300);
  return loaded;
}

const computed = (selector, property) =>
  `getComputedStyle(document.querySelector(${JSON.stringify(selector)}))[${JSON.stringify(property)}]`;

const WHITE = 'rgb(255, 255, 255)';
const WIDGET_TEXT = 'rgb(31, 41, 55)';   // #1f2937
const DARK_PANEL = 'rgb(17, 24, 39)';    // #111827
const DARK_TEXT = 'rgb(229, 231, 235)';  // #e5e7eb

async function checkLightCommunity(cdp, sessionId) {
  await setDevice(cdp, sessionId, 'dark');
  report(!(await evaluate(cdp, sessionId, `document.querySelector('.osa-chat-widget').classList.contains('osa-dark')`)),
    'a community that never set color_scheme stays light on a dark device');
  for (const [selector, label] of [
    ['.osa-chat-input input', 'the chat input'],
    ['#osa-settings-api-key', 'the Settings key field'],
    ['.osa-settings-select', 'the Settings model menu'],
  ]) {
    const [bg, color] = await Promise.all([
      evaluate(cdp, sessionId, computed(selector, 'backgroundColor')),
      evaluate(cdp, sessionId, computed(selector, 'color')),
    ]);
    report(bg === WHITE, `${label} keeps the light panel's white background on a dark host page`, bg);
    report(color === WIDGET_TEXT, `${label} keeps the widget's own text color`, color);
  }
  report(await evaluate(cdp, sessionId, computed('.osa-chat-window', 'color')) === WIDGET_TEXT,
    'panel text does not inherit the dark host page\'s light text');
  report(await evaluate(cdp, sessionId, computed('.osa-chat-widget', 'colorScheme')) === 'light',
    'the widget tells the browser its parts are light');
}

async function checkAuto(cdp, sessionId) {
  const isDark = `document.querySelector('.osa-chat-widget').classList.contains('osa-dark')`;
  await setDevice(cdp, sessionId, 'light');
  report(!(await evaluate(cdp, sessionId, isDark)), 'auto on a light device: the light panel');
  await setDevice(cdp, sessionId, 'dark');
  report(await waitFor(cdp, sessionId, isDark, 'dark after the device switched'), 'the device switching to dark darkens the open panel');
  report(await evaluate(cdp, sessionId, computed('.osa-chat-window', 'backgroundColor')) === DARK_PANEL, 'the panel is the dark background');
  report(await evaluate(cdp, sessionId, computed('.osa-chat-input input', 'backgroundColor')) === DARK_PANEL, 'the chat input is dark too');
  report(await evaluate(cdp, sessionId, computed('.osa-chat-input input', 'color')) === DARK_TEXT, 'with light text');
  report(await evaluate(cdp, sessionId, computed('.osa-chat-widget', 'colorScheme')) === 'dark', 'and the browser\'s own parts are dark');

  // The host page's choice (nemar.org's theme button) outranks the device.
  await evaluate(cdp, sessionId, `window.OSAChatWidget.setColorScheme('light')`);
  report(!(await evaluate(cdp, sessionId, isDark)), 'setColorScheme(\'light\') on a dark device: light');
}

// Serve the page under nemar.org's live script-src (see the header comment):
// every response for the page itself has 'unsafe-inline' added to script-src.
async function allowInlineScript(cdp, sessionId) {
  await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*widget-e2e.html*', requestStage: 'Response' }] }, sessionId);
  cdp.on(async (message) => {
    if (message.method !== 'Fetch.requestPaused' || message.sessionId !== sessionId) return;
    const { requestId, responseStatusCode, responseHeaders = [] } = message.params;
    const headers = responseHeaders.map(({ name, value }) =>
      name.toLowerCase() === 'content-security-policy'
        ? { name, value: value.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'") }
        : { name, value });
    // fulfillRequest with the page's own body, rather than continueResponse:
    // measured, Chrome did not apply headers rewritten by continueResponse to
    // the navigation's own policy.
    const { body, base64Encoded } = await cdp.send('Fetch.getResponseBody', { requestId }, sessionId);
    await cdp.send('Fetch.fulfillRequest', {
      requestId,
      responseCode: responseStatusCode,
      responseHeaders: headers,
      body: base64Encoded ? body : Buffer.from(body).toString('base64'),
    }, sessionId);
  });
}

async function checkPopout(cdp, sessionId, targetIdOfPage) {
  const isDark = `document.querySelector('.osa-chat-widget').classList.contains('osa-dark')`;
  await allowInlineScript(cdp, sessionId);
  await setDevice(cdp, sessionId, 'dark');
  await cdp.send('Page.navigate', { url }, sessionId);
  if (!(await waitFor(cdp, sessionId, `!!document.querySelector('.osa-chat-widget') && !!document.body`, 'the widget, reloaded'))) return;
  // Proved on the document itself, not on a response header: an inline script runs.
  const inlineRan = await evaluate(cdp, sessionId, `(() => {
    const script = document.createElement('script');
    script.textContent = 'window.__inlineRan = true';
    document.head.append(script);
    return window.__inlineRan === true;
  })()`);
  report(inlineRan, 'the reloaded page runs inline script, as nemar.org does');
  await evaluate(cdp, sessionId, DARK_HOST);
  await waitForCommunityConfig(cdp, sessionId, 'the community config, reloaded');
  await evaluate(cdp, sessionId, `document.querySelector('.osa-chat-button').click()`);
  await evaluate(cdp, sessionId, `window.OSAChatWidget.setColorScheme('light')`);
  report(!(await evaluate(cdp, sessionId, isDark)), 'the host chose light, on a dark device');

  // The pop-out: opened now, it starts in the host's light; then the host
  // switching to dark reaches it.
  const before = new Set((await cdp.send('Target.getTargets')).targetInfos.map((t) => t.targetId));
  await evaluate(cdp, sessionId, `document.querySelector('.osa-popout-btn').click()`, { userGesture: true });
  let popup = null;
  const deadline = Date.now() + 15_000;
  while (!popup && Date.now() < deadline) {
    const { targetInfos } = await cdp.send('Target.getTargets');
    popup = targetInfos.find((t) => t.type === 'page' && !before.has(t.targetId) && t.targetId !== targetIdOfPage);
    if (!popup) await Bun.sleep(100);
  }
  report(!!popup, 'the pop-out opened');
  if (!popup) return;
  const { sessionId: popupSession } = await cdp.send('Target.attachToTarget', { targetId: popup.targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, popupSession);
  await setDevice(cdp, popupSession, 'dark');
  const popupReady = await waitFor(cdp, popupSession,
    `!!document.querySelector('.osa-chat-widget.fullscreen') && !!window.OSAChatWidget`, 'the pop-out\'s widget');
  if (!popupReady) return;
  // Its own community config fetch (auto, on a dark device) must not undo the host's light.
  await waitForCommunityConfig(cdp, popupSession, 'the pop-out\'s community config');
  report(!(await evaluate(cdp, popupSession, isDark)),
    'the pop-out keeps the host\'s light, on a dark device, after its own community config arrived');
  await evaluate(cdp, sessionId, `window.OSAChatWidget.setColorScheme('dark')`);
  report(await waitFor(cdp, popupSession, isDark, 'the pop-out following the host to dark'),
    'the host switching to dark reaches the open pop-out');
  await cdp.send('Target.closeTarget', { targetId: popup.targetId });
}

async function main() {
  const chromePath = findChrome();
  if (!chromePath) {
    console.log('SKIP: no Chrome found');
    return 0;
  }
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-color-scheme-check-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    await cdp.send('Runtime.enable', {}, sessionId);
    await cdp.send('Page.enable', {}, sessionId);
    await setDevice(cdp, sessionId, 'light');
    await cdp.send('Page.navigate', { url }, sessionId);
    if (!(await waitFor(cdp, sessionId, `!!document.querySelector('.osa-chat-widget') && !!document.body`, 'the widget'))) return 1;
    await evaluate(cdp, sessionId, DARK_HOST);

    // The control: the host page really does darken a control that has no
    // colors of its own. Without this, a pass below could mean the page never
    // reproduced the bug.
    const control = await evaluate(cdp, sessionId, `(() => {
      const input = document.createElement('input');
      document.body.appendChild(input);
      const bg = getComputedStyle(input).backgroundColor;
      input.remove();
      return bg;
    })()`);
    report(control !== WHITE && /^rgb\((\d+), \1, \1\)$/.test(control) && Number(control.match(/\d+/)[0]) < 128,
      'control: a plain input on this dark host page is drawn dark by the browser', control);

    // Wait for the community config, then open the panel.
    await waitForCommunityConfig(cdp, sessionId, 'the community config');
    await evaluate(cdp, sessionId, `document.querySelector('.osa-chat-button').click()`);

    if (mode === 'light') {
      await checkLightCommunity(cdp, sessionId);
    } else {
      await checkAuto(cdp, sessionId);
      await checkPopout(cdp, sessionId, targetId);
    }

    await cdp.send('Target.closeTarget', { targetId });
  } finally {
    cdp?.close();
    chrome?.kill();
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
  return failed > 0 ? 1 : 0;
}

process.exit(await main());
