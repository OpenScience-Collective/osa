#!/usr/bin/env bun
/**
 * Real-Chrome check of the widget's first paint (#475): every animation frame in
 * which the reader can see the launcher shows the community's own look, never the
 * built-in defaults first.
 *
 * A recorder installed before any page script runs samples the chat button on
 * every animation frame (whether it is visible, its width and its background), so a
 * default look drawn for even one frame is caught; transitions run, as they do for a
 * reader. The community config request is held for 600 ms, about what it takes
 * over the network on test.nemar.org, so the window in which the defaults could
 * show is as wide as a reader's. Run twice in one profile: a first visit, which
 * must keep the launcher hidden until the config arrives, then a reload, which
 * must draw it in the community's look at once, before the config arrives.
 *
 * Usage:
 *   bun frontend/browser-harness/first-paint-check.mjs --serve
 *     starts `widget_e2e.py --nemar` on a free port, runs the check, and stops it
 *     (NEMAR's config, for its capsule and theme color); what CI runs
 *   bun frontend/browser-harness/first-paint-check.mjs http://127.0.0.1:PORT
 *     checks a `widget_e2e.py PORT --nemar` already running
 */

import { connect, findChrome, launch } from './chrome.js';
import { startServer, waitForServer } from './harness-server.js';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CONFIG_DELAY_MS = 600;
const WATCH_MS = 3000;

let failed = 0;
function report(ok, label, detail) {
  if (ok) console.log(`ok: ${label}`);
  else {
    failed++;
    console.error(`FAIL: ${label}${detail !== undefined ? ` (got ${JSON.stringify(detail)})` : ''}`);
  }
}

// Installed before the page's own scripts: one sample per frame in which the chat
// button is visible, until WATCH_MS.
const RECORDER = `(() => {
  window.__osaFrames = [];
  const tick = () => {
    const button = document.querySelector('.osa-launcher-capsule .osa-chat-button') || document.querySelector('.osa-chat-button');
    if (button) {
      const style = getComputedStyle(button);
      if (style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity) > 0) {
        window.__osaFrames.push({ t: Math.round(performance.now()), w: Math.round(button.getBoundingClientRect().width), bg: style.backgroundColor });
      }
    }
    if (performance.now() < ${WATCH_MS}) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})();`;

async function check(base) {
  const chromePath = findChrome();
  if (!chromePath) {
    if (process.env.CI) {
      report(false, 'no Chrome found, and CI must run this check');
      return;
    }
    console.log('SKIP: no Chrome found');
    return;
  }
  const page = `${base}/browser-harness/widget-e2e.html`;
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-first-paint-check-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    const evaluate = async (expression) => {
      const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true }, s);
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    };
    await cdp.send('Runtime.enable', {}, s);
    await cdp.send('Page.enable', {}, s);
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, s);
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: RECORDER }, s);

    // Hold the community config (`${origin}/api/nemar`, the harness page's
    // endpoint) as the network would.
    let heldCount = 0;
    cdp.on(async (message) => {
      if (message.method !== 'Fetch.requestPaused' || message.sessionId !== s) return;
      heldCount++;
      await Bun.sleep(CONFIG_DELAY_MS);
      try {
        await cdp.send('Fetch.continueRequest', { requestId: message.params.requestId }, s);
      } catch {
        // the page went away first
      }
    });
    await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*/api/nemar', requestStage: 'Request' }] }, s);

    for (const pass of ['first visit', 'reload']) {
      await cdp.send('Page.navigate', { url: page }, s);
      await Bun.sleep(WATCH_MS + 300);
      const frames = await evaluate('window.__osaFrames');
      const final = await evaluate(`(() => {
        const b = document.querySelector('.osa-launcher-capsule .osa-chat-button');
        return b ? { w: Math.round(b.getBoundingClientRect().width), bg: getComputedStyle(b).backgroundColor } : null;
      })()`);
      report(!!final, `${pass}: the capsule is there once the config has arrived`, final);
      if (!final) continue;
      report(final.w === 46, `${pass}: at the capsule's 46px`, final.w);
      report(frames.length > 0, `${pass}: the launcher was visible in ${frames.length} frames`);
      const wrong = frames.filter((f) => f.w !== final.w || f.bg !== final.bg);
      report(wrong.length === 0, `${pass}: every visible frame shows the community's look (${final.bg}), none the defaults or a fade from them`,
        wrong.slice(0, 5));
      const first = frames[0]?.t;
      if (pass === 'first visit') {
        report(first >= CONFIG_DELAY_MS, `first visit: the launcher stayed hidden until the held config arrived (first shown at ${first} ms, config held ${CONFIG_DELAY_MS} ms)`, first);
        const remembered = await evaluate(`localStorage.getItem('osa-widget-config-nemar')`);
        report(!!remembered && JSON.parse(remembered).widget.launcher === 'capsule', 'first visit: the community\'s widget config is remembered', remembered && remembered.slice(0, 120));
      } else {
        report(first < CONFIG_DELAY_MS, `reload: the launcher was drawn in the remembered look before the config arrived (first shown at ${first} ms)`, first);
      }
    }
    report(heldCount >= 2, `the config request was held on both loads (${heldCount})`);
    await cdp.send('Target.closeTarget', { targetId });
  } finally {
    cdp?.close();
    chrome?.kill();
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === '--serve') {
    const server = startServer(['--nemar']);
    try {
      if (!(await waitForServer(server))) {
        report(false, `the widget_e2e.py server on port ${server.port} did not start`, server.output.slice(-2000));
      } else {
        await check(`http://127.0.0.1:${server.port}`);
      }
    } finally {
      server.proc.kill();
    }
  } else if (args.length === 1 && /^https?:\/\//.test(args[0])) {
    await check(args[0].replace(/\/$/, ''));
  } else {
    console.error('usage: bun frontend/browser-harness/first-paint-check.mjs --serve | http://127.0.0.1:PORT');
    process.exit(2);
  }
  return failed > 0 ? 1 : 0;
}

process.exit(await main());
