#!/usr/bin/env bun
/**
 * Real-Chrome check of the widget's first paint (#475): every animation frame in
 * which the reader can see the launcher shows the community's own look, never the
 * built-in defaults first.
 *
 * A recorder installed before any page script runs samples the chat button on
 * every animation frame (whether it is visible, its width and its background), so a
 * default look drawn for even one frame is caught; transitions run, as they do for a
 * reader. Each frame also records whether the widget is dark. The community
 * config request is held for 600 ms, about what it takes over the network on
 * test.nemar.org, so the window in which the defaults could show is as wide as a
 * reader's. Each run is two loads in one profile: a first visit, which must keep
 * the launcher hidden until the config arrives, then a reload, which must draw it
 * in the community's look at once, before the config arrives.
 *
 * Three runs: NEMAR (a capsule, its theme color, color_scheme auto) on a light
 * device and on a dark one, whose first frame must already be dark; and the
 * harness's own bubble community with color_scheme auto on a dark device, so the
 * bubble's path is drawn and timed in a real browser too. Each NEMAR run then
 * opens and closes the panel, sampling the capsule's chat circle on every frame
 * as it goes from its resting 58px to 46px and back (#490).
 *
 * Usage:
 *   bun frontend/browser-harness/first-paint-check.mjs --serve
 *     starts `widget_e2e.py --nemar` and `widget_e2e.py --color-scheme auto` on
 *     free ports, runs the check, and stops them; what CI runs
 *   bun frontend/browser-harness/first-paint-check.mjs http://127.0.0.1:PORT
 *     checks NEMAR's runs against a `widget_e2e.py PORT --nemar` already running
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
        const widget = document.querySelector('.osa-chat-widget');
        window.__osaFrames.push({ t: Math.round(performance.now()), w: Math.round(button.getBoundingClientRect().width), bg: style.backgroundColor, dark: !!(widget && widget.classList.contains('osa-dark')) });
      }
    }
    if (performance.now() < ${WATCH_MS}) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})();`;

// The capsule's chat circle across a click (#490): drawn at 58px at rest, it settles
// to its 46px box as the panel opens and grows back as it closes, with its
// bottom-right corner 20px from the window's edges in every frame, where the
// bubble's is. Sampled on every animation frame from just before the click, so a
// snap (no frame between the two sizes) or a corner that drifts is caught.
const SAMPLE_CLICK = `(async () => {
  const button = document.querySelector('.osa-launcher-capsule .osa-chat-button');
  const samples = [];
  const sample = (t0) => {
    const r = button.getBoundingClientRect();
    samples.push({ t: Math.round(performance.now() - t0), w: +r.width.toFixed(2),
      right: +(innerWidth - r.right).toFixed(2), bottom: +(innerHeight - r.bottom).toFixed(2) });
  };
  const t0 = performance.now();
  sample(t0);
  button.click();
  await new Promise((resolve) => {
    const tick = () => {
      sample(t0);
      if (performance.now() - t0 < 700) requestAnimationFrame(tick); else resolve();
    };
    requestAnimationFrame(tick);
  });
  const panel = document.querySelector('.osa-chat-window').getBoundingClientRect();
  const indicator = document.querySelector('.osa-capsule-indicator').getBoundingClientRect();
  const r = button.getBoundingClientRect();
  return { samples, open: document.querySelector('.osa-chat-window').classList.contains('open'),
    panelRight: +(innerWidth - panel.right).toFixed(2),
    indicator: { dx: +(indicator.left - r.left).toFixed(2), dy: +(indicator.top - r.top).toFixed(2), w: +indicator.width.toFixed(2) } };
})()`;

async function checkOpenAndClose(evaluate, label) {
  const near = (a, b) => Math.abs(a - b) <= 0.6;
  for (const step of ['open', 'close']) {
    const result = await evaluate(SAMPLE_CLICK);
    const { samples } = result;
    const [from, to] = step === 'open' ? [58, 46] : [46, 58];
    report(result.open === (step === 'open'), `${label}, ${step}: the click ${step === 'open' ? 'opens' : 'closes'} the panel`, result.open);
    report(near(samples[0].w, from) && near(samples.at(-1).w, to), `${label}, ${step}: the chat circle goes from ${from}px to ${to}px`, [samples[0], samples.at(-1)]);
    const between = samples.filter((s) => s.w > 46.6 && s.w < 57.4);
    report(between.length > 0, `${label}, ${step}: through ${between.length} frames in between, not a snap`, samples.slice(0, 6));
    const drifted = samples.filter((s) => !near(s.right, 20) || !near(s.bottom, 20));
    report(drifted.length === 0, `${label}, ${step}: its bottom-right corner stays 20px from the window's edges in every one of ${samples.length} frames`, drifted.slice(0, 5));
    if (step === 'open') {
      report(near(result.panelRight, 20 + 46 + 7 + 12), `${label}, open: the panel sits beside the 46px capsule, where it always has`, result.panelRight);
      report(near(result.indicator.dx, 0) && near(result.indicator.dy, 0) && near(result.indicator.w, 46), `${label}, open: the indicator is exactly behind the chat circle`, result.indicator);
    }
  }
}

// One run: a first visit and a reload in a fresh profile. `community` names the
// config's community (the request held is `${origin}/api/<community>`); `capsule`
// says which launcher it has; `device` is the emulated color scheme.
async function check(base, { label, community, capsule, device }) {
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
      const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, s);
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    };
    await cdp.send('Runtime.enable', {}, s);
    await cdp.send('Page.enable', {}, s);
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, s);
    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: device }] }, s);
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: RECORDER }, s);

    // Hold the community config (`${origin}/api/<community>`, the harness page's
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
    await cdp.send('Fetch.enable', { patterns: [{ urlPattern: `*/api/${community}`, requestStage: 'Request' }] }, s);

    for (const load of ['first visit', 'reload']) {
      const pass = `${label}, ${load}`;
      await cdp.send('Page.navigate', { url: page }, s);
      await Bun.sleep(WATCH_MS + 300);
      const frames = await evaluate('window.__osaFrames');
      const final = await evaluate(`(() => {
        const b = document.querySelector('.osa-launcher-capsule .osa-chat-button') || document.querySelector('.osa-chat-button');
        const widget = document.querySelector('.osa-chat-widget');
        return b ? { w: Math.round(b.getBoundingClientRect().width), bg: getComputedStyle(b).backgroundColor, dark: widget.classList.contains('osa-dark'), capsule: widget.classList.contains('osa-capsule') } : null;
      })()`);
      report(!!final, `${pass}: the launcher is there once the config has arrived`, final);
      if (!final) continue;
      report(final.capsule === capsule && final.w === (capsule ? 58 : 56), `${pass}: ${capsule ? 'the capsule, its chat circle drawn at its resting 58px' : 'the bubble, at 56px'}`, final);
      report(final.dark === (device === 'dark'), `${pass}: ${device === 'dark' ? 'dark, following the dark device' : 'light'}`, final.dark);
      report(frames.length > 0, `${pass}: the launcher was visible in ${frames.length} frames`);
      const wrong = frames.filter((f) => f.w !== final.w || f.bg !== final.bg || f.dark !== final.dark);
      report(wrong.length === 0, `${pass}: every visible frame shows the community's look (${final.bg}${final.dark ? ', dark' : ''}), none the defaults or a fade from them`,
        wrong.slice(0, 5));
      const first = frames[0]?.t;
      if (load === 'first visit') {
        report(first >= CONFIG_DELAY_MS, `${pass}: the launcher stayed hidden until the held config arrived (first shown at ${first} ms, config held ${CONFIG_DELAY_MS} ms)`, first);
        const remembered = await evaluate(`localStorage.getItem('osa-widget-config-${community}')`);
        // A bubble's launcher arrives as null, which the widget treats as unset.
        report(!!remembered && (JSON.parse(remembered).widget.launcher || 'bubble') === (capsule ? 'capsule' : 'bubble'), `${pass}: the community's widget config is remembered`, remembered && remembered.slice(0, 120));
      } else {
        report(first < CONFIG_DELAY_MS, `${pass}: the launcher was drawn in the remembered look before the config arrived (first shown at ${first} ms)`, first);
      }
    }
    report(heldCount >= 2, `${label}: the config request was held on both loads (${heldCount})`);
    if (capsule) await checkOpenAndClose(evaluate, label);
    await cdp.send('Target.closeTarget', { targetId });
  } finally {
    cdp?.close();
    chrome?.kill();
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
}

// The community a harness server serves, as its page configures the widget.
async function communityOf(base) {
  const script = await (await fetch(`${base}/browser-harness/widget-e2e-config.js`)).text();
  const match = script.match(/"communityId":\s*"([^"]+)"/);
  if (!match) throw new Error(`no communityId in ${base}'s widget-e2e-config.js`);
  return match[1];
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === '--serve') {
    const servers = { nemar: startServer(['--nemar']), bubble: startServer(['--color-scheme', 'auto']) };
    try {
      for (const [name, server] of Object.entries(servers)) {
        if (!(await waitForServer(server))) {
          report(false, `the ${name} widget_e2e.py server on port ${server.port} did not start`, server.output.slice(-2000));
        }
      }
      if (failed === 0) {
        const nemar = `http://127.0.0.1:${servers.nemar.port}`;
        await check(nemar, { label: 'NEMAR, light device', community: 'nemar', capsule: true, device: 'light' });
        await check(nemar, { label: 'NEMAR, dark device', community: 'nemar', capsule: true, device: 'dark' });
        const bubbleCommunity = await communityOf(`http://127.0.0.1:${servers.bubble.port}`);
        await check(`http://127.0.0.1:${servers.bubble.port}`, { label: 'a bubble community, dark device', community: bubbleCommunity, capsule: false, device: 'dark' });
      }
    } finally {
      for (const server of Object.values(servers)) server.proc.kill();
    }
  } else if (args.length === 1 && /^https?:\/\//.test(args[0])) {
    const base = args[0].replace(/\/$/, '');
    await check(base, { label: 'NEMAR, light device', community: 'nemar', capsule: true, device: 'light' });
    await check(base, { label: 'NEMAR, dark device', community: 'nemar', capsule: true, device: 'dark' });
  } else {
    console.error('usage: bun frontend/browser-harness/first-paint-check.mjs --serve | http://127.0.0.1:PORT');
    process.exit(2);
  }
  return failed > 0 ? 1 : 0;
}

process.exit(await main());
