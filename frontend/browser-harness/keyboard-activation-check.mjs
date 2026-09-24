#!/usr/bin/env bun
/**
 * Real-Chrome check for the capsule notebook button's keyboard activation
 * (#436 review finding 5): happy-dom does not turn a trusted Enter or Space
 * keypress into a click on a focused <button> (that is native browser
 * behavior, not something the widget's own JS implements), so this cannot be
 * proven in the Bun suite. It IS proven here, over the real DevTools
 * protocol, reusing chrome.js's own findChrome/launch/connect rather than a
 * second way of driving Chrome.
 *
 * Usage (against a running widget_e2e.py --nemar server):
 *   bun frontend/browser-harness/keyboard-activation-check.mjs http://127.0.0.1:8791/browser-harness/widget-e2e.html
 */

import { connect, findChrome, launch } from './chrome.js';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const url = process.argv[2];
if (!url) {
  console.error('usage: bun frontend/browser-harness/keyboard-activation-check.mjs <url>');
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

async function evaluate(cdp, sessionId, expression) {
  const { result, exceptionDetails } = await cdp.send('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise: true,
  }, sessionId);
  if (exceptionDetails) throw new Error(`page exception: ${exceptionDetails.text}`);
  return result.value;
}

/** A real, trusted key press (down + up), the way physical hardware reports one. */
async function pressKey(cdp, sessionId, { key, code, windowsVirtualKeyCode, text }) {
  await cdp.send('Input.dispatchKeyEvent', {
    type: text ? 'keyDown' : 'rawKeyDown', key, code, windowsVirtualKeyCode, text,
  }, sessionId);
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode }, sessionId);
}

const ENTER = { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' };
const SPACE = { key: ' ', code: 'Space', windowsVirtualKeyCode: 32, text: ' ' };

async function main() {
  const chromePath = findChrome();
  if (!chromePath) {
    console.log('SKIP: no Chrome found');
    return 0;
  }
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-keyboard-check-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    await cdp.send('Runtime.enable', {}, sessionId);
    await cdp.send('Page.enable', {}, sessionId);
    await cdp.send('Page.navigate', { url }, sessionId);

    // Wait for the capsule (the NEMAR config sets launcher: capsule).
    const deadline = Date.now() + 20_000;
    let hasCapsule = false;
    while (Date.now() < deadline) {
      hasCapsule = await evaluate(cdp, sessionId, `!!document.querySelector('.osa-launcher-capsule')`);
      if (hasCapsule) break;
      await Bun.sleep(200);
    }
    report(hasCapsule, 'the capsule exists (NEMAR config resolved)');
    if (!hasCapsule) return 1;

    // Open the chat (a plain, non-trusted click is fine here: addEventListener
    // click handlers do not care about the trusted flag, only the browser's
    // OWN native "Enter/Space activates a focused button" behavior does,
    // which is the one thing under test below).
    await evaluate(cdp, sessionId, `document.querySelector('.osa-chat-button').click()`);

    async function checkActivation(label, { zarr, key, keyName, expectOpen }) {
      await evaluate(cdp, sessionId, `window.OSAChatWidget.setDataset({ id: 'nm000103', zarr: ${zarr} })`);
      await evaluate(cdp, sessionId, `
        window.__captured = null;
        window.open = (url, target, features) => { window.__captured = { url, target, features }; return null; };
        document.querySelector('.osa-notebook-btn').focus();
        document.activeElement === document.querySelector('.osa-notebook-btn');
      `);
      await pressKey(cdp, sessionId, key);
      const captured = await evaluate(cdp, sessionId, `window.__captured`);
      if (expectOpen) {
        report(!!captured, `${label}: ${keyName} opens the URL when active`, captured);
        if (captured) {
          report(
            captured.url === 'https://notebook.osc.earth/osa/open.html?community=nemar&dataset=nm000103',
            `${label}: ${keyName} opens the exact contract URL`,
            captured.url
          );
        }
      } else {
        report(!captured, `${label}: ${keyName} opens nothing when inactive`, captured);
      }
    }

    await checkActivation('active (zarr: true)', { zarr: 'true', key: ENTER, keyName: 'Enter', expectOpen: true });
    await checkActivation('active (zarr: true)', { zarr: 'true', key: SPACE, keyName: 'Space', expectOpen: true });
    await checkActivation('inactive (zarr: false)', { zarr: 'false', key: ENTER, keyName: 'Enter', expectOpen: false });
    await checkActivation('inactive (zarr: false)', { zarr: 'false', key: SPACE, keyName: 'Space', expectOpen: false });

    await cdp.send('Target.closeTarget', { targetId });
  } finally {
    cdp?.close();
    chrome?.kill();
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
  return failed > 0 ? 1 : 0;
}

process.exit(await main());
