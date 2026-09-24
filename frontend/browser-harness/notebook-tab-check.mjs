#!/usr/bin/env bun
/**
 * Real-Chrome check of the notebook as a tab of the widget's panel (#470), with a
 * real notebook: the live develop notebook site, framed by the harness page, with
 * real Pyodide running the NEMAR starter's setup cell.
 *
 * What happy-dom cannot show and this does: the capsule's circles laid out at
 * their real size and the indicator on the real circle; the frame loading the
 * notebook site, following its redirect, and the bridge's own messages arriving;
 * setup running to "Python ready"; the widget's dark scheme applied inside the
 * frame; the frame surviving a switch to chat without reloading; and a drag of the
 * resize handle past the bubble's 600px limit.
 *
 * Usage, against a running `widget_e2e.py PORT --nemar` (NEMAR's config, since the
 * notebook site has a starter for NEMAR only):
 *   bun frontend/browser-harness/notebook-tab-check.mjs http://127.0.0.1:PORT [screenshot-dir]
 *
 * It needs the network: develop-notebook.osc.earth (which admits loopback pages
 * to frame it) and the Pyodide CDN. It is not in CI for that reason; the
 * happy-dom suite, test-widget-notebook-tab.js, is.
 */

import { connect, findChrome, launch } from './chrome.js';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [base, shotDir] = process.argv.slice(2);
if (!base) {
  console.error('usage: bun frontend/browser-harness/notebook-tab-check.mjs http://127.0.0.1:PORT [screenshot-dir]');
  process.exit(2);
}
const NOTEBOOK = 'https://develop-notebook.osc.earth/osa/';
const DATASET = 'xx099903';
const page = `${base}/browser-harness/widget-e2e.html?dataset=${DATASET}&zarr=true&notebookUrl=${encodeURIComponent(NOTEBOOK)}`;

let failed = 0;
function report(ok, label, detail) {
  if (ok) console.log(`ok: ${label}`);
  else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}

// Seconds since the notebook circle was pressed.
let clickedAt = 0;
const seconds = () => ((Date.now() - clickedAt) / 1000).toFixed(1);

async function main() {
  const chromePath = findChrome();
  if (!chromePath) {
    console.log('SKIP: no Chrome found');
    return 0;
  }
  if (shotDir) mkdirSync(shotDir, { recursive: true });
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-notebook-tab-check-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    const evaluate = async (expression) => {
      const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, s);
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    };
    const waitFor = async (expression, label, timeoutMs = 20_000) => {
      const end = Date.now() + timeoutMs;
      while (Date.now() < end) {
        if (await evaluate(expression)) return true;
        await Bun.sleep(200);
      }
      report(false, `timed out after ${timeoutMs / 1000}s: ${label}`);
      return false;
    };
    // A real key press on whatever has focus, as a reader's keyboard sends it.
    const press = async (key) => {
      const code = key === ' ' ? 'Space' : key;
      const keyCode = key === ' ' ? 32 : 13;
      const text = key === ' ' ? ' ' : '\r';
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: keyCode, text }, s);
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: keyCode }, s);
    };
    const shot = async (name) => {
      if (!shotDir) return;
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, s);
      writeFileSync(join(shotDir, `${name}.png`), Buffer.from(data, 'base64'));
    };
    await cdp.send('Runtime.enable', {}, s);
    await cdp.send('Page.enable', {}, s);
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, s);
    await cdp.send('Page.navigate', { url: page }, s);
    if (!(await waitFor(`!!document.querySelector('.osa-launcher-capsule .osa-notebook-btn[aria-disabled="false"]')`, 'the capsule with an available notebook'))) return 1;

    // Record the notebook's own messages and the frame's loads, from the page.
    await evaluate(`window.__nb = { messages: [], loads: 0 }; window.addEventListener('message', (e) => {
      if (e.data && e.data.source === 'osa-notebook') window.__nb.messages.push({ ...e.data, origin: e.origin });
    }); true`);
    await shot('1-closed');

    // Sizes: the capsule's circles against the Send button.
    await evaluate(`document.querySelector('.osa-launcher-capsule .osa-chat-button').click(), true`);
    await Bun.sleep(500);
    const sizes = await evaluate(`(() => {
      const w = (sel) => document.querySelector(sel).getBoundingClientRect().width;
      return { chat: w('.osa-launcher-capsule .osa-chat-button'), notebook: w('.osa-notebook-btn'), send: w('.osa-send-btn') };
    })()`);
    report(sizes.chat === 46 && sizes.notebook === 46, 'the capsule\'s circles are 46px', sizes);
    report(sizes.send === 40 && sizes.chat / sizes.send <= 1.2, 'no more than 20% larger than the 40px Send button', sizes.chat / sizes.send);
    const onChat = await evaluate(`(() => {
      const ind = document.querySelector('.osa-capsule-indicator').getBoundingClientRect();
      const btn = document.querySelector('.osa-launcher-capsule .osa-chat-button').getBoundingClientRect();
      return Math.abs(ind.top - btn.top) < 1 && Math.abs(ind.left - btn.left) < 1;
    })()`);
    report(onChat, 'the filled indicator sits on the chat circle');
    await shot('2-chat-tab');

    // The notebook tab, opened from the keyboard: Enter on the focused circle.
    await evaluate(`document.querySelector('.osa-notebook-btn').focus(), true`);
    clickedAt = Date.now();
    await press('Enter');
    if (!(await waitFor(`!!document.querySelector('.osa-notebook-frame')`, 'the notebook frame'))) return 1;
    report(true, 'Enter on the focused notebook circle opens the notebook tab');
    await evaluate(`document.querySelector('.osa-notebook-frame').addEventListener('load', () => window.__nb.loads++), true`);
    await Bun.sleep(450);
    const onNotebook = await evaluate(`(() => {
      const ind = document.querySelector('.osa-capsule-indicator').getBoundingClientRect();
      const btn = document.querySelector('.osa-notebook-btn').getBoundingClientRect();
      return Math.abs(ind.top - btn.top) < 1 && Math.abs(ind.left - btn.left) < 1;
    })()`);
    report(onNotebook, 'the indicator slid to the notebook circle');
    await shot('3-notebook-loading');
    if (!(await waitFor(`window.__nb.messages.some((m) => m.type === 'ready')`, 'the notebook reports ready', 90_000))) return 1;
    report(true, `the notebook reported ready, ${seconds()}s after the key press`);
    const origins = await evaluate(`[...new Set(window.__nb.messages.map((m) => m.origin))]`);
    report(origins.length === 1 && origins[0] === 'https://develop-notebook.osc.earth', 'every message came from the notebook\'s origin', origins);
    if (!(await waitFor(`window.__nb.messages.some((m) => m.type === 'setup' && m.status === 'done')`, 'setup done', 180_000))) return 1;
    await Bun.sleep(300);
    const status = await evaluate(`document.querySelector('.osa-notebook-status-text').textContent`);
    report(status === `${DATASET} · Python ready`, `the header says Python is ready, ${seconds()}s after the key press`, status);
    const overlays = await evaluate(`[...document.querySelectorAll('.osa-notebook-overlay')].every((o) => o.classList.contains('osa-overlay-hidden'))`);
    report(overlays, 'no overlay covers the notebook');
    await shot('4-notebook-ready');

    // Dark, inside the frame.
    await evaluate(`window.OSAChatWidget.setColorScheme('dark'), true`);
    if (await waitFor(`window.__nb.messages.some((m) => m.type === 'theme' && m.scheme === 'dark' && m.applied === true)`, 'the notebook applies dark', 20_000)) {
      report(true, 'the widget\'s dark scheme was applied inside the notebook');
    }
    await Bun.sleep(600);
    await shot('5-notebook-dark');

    // Back to chat, and back again: the same frame, never reloaded.
    const loadsBefore = await evaluate(`(document.querySelector('.osa-notebook-frame').dataset.mark = 'kept', window.__nb.loads)`);
    await evaluate(`document.querySelector('.osa-launcher-capsule .osa-chat-button').click(), true`);
    await Bun.sleep(80);
    const midFade = await evaluate(`[getComputedStyle(document.querySelector('.osa-view-notebook')).opacity, getComputedStyle(document.querySelector('.osa-view-chat')).opacity].map(Number)`);
    report(midFade[0] < 1 && midFade[1] < 1, 'fade-through: 80ms in, the notebook is fading out and chat has not yet faded in', midFade);
    await Bun.sleep(500);
    await shot('6-chat-dark');
    // Back to the notebook from the keyboard again, with Space this time.
    await evaluate(`document.querySelector('.osa-notebook-btn').focus(), true`);
    await press(' ');
    await Bun.sleep(500);
    report(await evaluate(`document.querySelector('.osa-chat-widget').classList.contains('osa-tab-notebook')`), 'Space on the focused notebook circle goes back to the notebook tab');
    const kept = await evaluate(`({ mark: document.querySelector('.osa-notebook-frame').dataset.mark, loads: window.__nb.loads, frames: document.querySelectorAll('.osa-notebook-frame').length })`);
    report(kept.mark === 'kept' && kept.loads === loadsBefore && kept.frames === 1, 'the same frame, not reloaded, after a switch to chat and back', { kept, loadsBefore });

    // Resize past the bubble's 600px limit, dragging across the frame. Chrome keeps
    // the drag with the page either way, so this proves the capsule's limit and
    // that the frame does not swallow the drag here, not that the widget's
    // pointer-events rule for the frame is needed (happy-dom checks the rule).
    const handle = await evaluate(`(() => { const r = document.querySelector('.osa-resize-handle').getBoundingClientRect(); return { x: r.left + 6, y: r.top + 6 }; })()`);
    const mouse = (type, x, y) => cdp.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1 }, s);
    await mouse('mousePressed', handle.x, handle.y);
    for (let step = 1; step <= 10; step++) await mouse('mouseMoved', handle.x - step * 50, handle.y - step * 10);
    await mouse('mouseReleased', handle.x - 500, handle.y - 100);
    const width = await evaluate(`document.querySelector('.osa-chat-window').getBoundingClientRect().width`);
    report(width > 900, `the capsule's panel resizes past 600px, across the frame (${width}px)`, width);
    await Bun.sleep(400);
    await shot('7-notebook-wide');
    await cdp.send('Target.closeTarget', { targetId });
  } finally {
    cdp?.close();
    chrome?.kill();
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
  return failed > 0 ? 1 : 0;
}

process.exit(await main());
