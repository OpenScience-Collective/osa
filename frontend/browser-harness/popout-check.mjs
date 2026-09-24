#!/usr/bin/env bun
/**
 * Real-Chrome check of the widget's pop-out window (#470), under the harness's copy
 * of nemar.org's policy, whose script-src has no 'unsafe-inline'.
 *
 * The pop-out is an about:blank window of the page's own origin, so it inherits the
 * page's Content Security Policy (CSP). It used to write the widget's source into
 * itself as inline script, and opened blank on such a page. It now loads the widget
 * by its address, with the page tag's integrity and crossorigin, and this checks
 * that it renders there: from the capsule's chat tab and from its notebook tab,
 * each opening on the tab it came from, the notebook's with a frame of its own at
 * the notebook site's contract address.
 *
 * Every run carries its own controls. An inline script must be refused by the page
 * and by the pop-out, or the policy was not in force and a pass proves nothing. On
 * the pinned page (the widget's tag with integrity and crossorigin, as nemar.org has
 * it), the pop-out must carry both and render; then, with the tag's integrity made
 * wrong, the next pop-out must be refused by the browser and say so, which shows the
 * browser checks the pin in the pop-out rather than the check reading attributes.
 * A pop-out opened on the notebook tab must start no transition on its views or
 * titles (it is drawn there at once), and a later switch in the same window must
 * start one, so the record of transitions is known to see them.
 *
 * A pop-out is a new DevTools target: this discovers targets and attaches to the one
 * whose opener is the page. The notebook's address points at the harness server
 * itself, so the frame loads nothing from the network; its address is what is
 * checked. With --live-notebook it is the develop notebook instead
 * (develop-notebook.osc.earth/osa, which admits loopback pages to frame it), and
 * the pop-out's notebook must report "Python ready" through the bridge, which needs
 * the network, so CI does not pass it.
 *
 * Usage:
 *   bun frontend/browser-harness/popout-check.mjs --serve [--live-notebook] [screenshot-dir]
 *     starts `widget_e2e.py --nemar` on a free port, runs the check, and stops it;
 *     what CI runs, without --live-notebook
 *   bun frontend/browser-harness/popout-check.mjs http://127.0.0.1:PORT [--live-notebook] [screenshot-dir]
 *     checks a `widget_e2e.py PORT --nemar` already running
 */

import { connect, findChrome, launch } from './chrome.js';
import { startServer, waitForServer } from './harness-server.js';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const LIVE_NOTEBOOK_URL = 'https://develop-notebook.osc.earth/osa/';

let failed = 0;
function report(ok, label, detail) {
  if (ok) console.log(`ok: ${label}`);
  else {
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

// Reports a failure itself, once, when it times out.
async function waitFor(cdp, sessionId, expression, label, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(cdp, sessionId, expression)) return true;
    await Bun.sleep(100);
  }
  report(false, `timed out waiting: ${label}`);
  return false;
}

// An inline script, run or refused: proved on the document itself, since a
// response header says what was sent, not what is in force.
const INLINE_RUNS = `(() => {
  const script = document.createElement('script');
  script.textContent = 'window.__osaInlineRan = true';
  document.head.append(script);
  return window.__osaInlineRan === true;
})()`;

const visible = (selector) => `(() => {
  const el = document.querySelector(${JSON.stringify(selector)});
  if (!el) return false;
  const box = el.getBoundingClientRect();
  return getComputedStyle(el).visibility === 'visible' && box.width > 0 && box.height > 0;
})()`;

// The pop-out the page opens next: a new page target whose opener is the page.
async function openPopout(cdp, created, page, clickExpression) {
  const seen = created.length;
  await evaluate(cdp, page.sessionId, clickExpression, { userGesture: true });
  const deadline = Date.now() + 15_000;
  let target = null;
  while (!target && Date.now() < deadline) {
    target = created.slice(seen).find((t) => t.type === 'page' && t.openerId === page.targetId);
    if (!target) await Bun.sleep(50);
  }
  report(!!target, 'a pop-out window opened, as a new target whose opener is the page');
  if (!target) return null;
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId: target.targetId, flatten: true });
  await cdp.send('Runtime.enable', {}, sessionId);
  return { targetId: target.targetId, sessionId };
}

// Instrumentation in the page, not a change to what it does: every window it opens
// gets a recorder of the transitions it starts, installed the moment the widget
// has written the window's document (its close()), in the same task and so before
// the widget's script element is even added. A recorder added after the pop-out
// target is attached can miss the widget's first transition (measured: it did).
const RECORD_POPOUT_TRANSITIONS = `(() => {
  if (window.__osaOpenRecorded) return true;
  window.__osaOpenRecorded = true;
  const realOpen = window.open;
  window.open = function (...args) {
    const popup = realOpen.apply(this, args);
    if (!popup) return popup;
    const doc = popup.document;
    const realClose = doc.close;
    doc.close = function () {
      const result = realClose.call(doc);
      popup.__osaTransitions = [];
      popup.__osaRecordedBeforeWidget = !popup.OSAChatWidget && !doc.querySelector('script');
      doc.addEventListener('transitionrun', (event) => {
        const name = event.target.className;
        popup.__osaTransitions.push({ target: String(name && name.baseVal !== undefined ? name.baseVal : name), property: event.propertyName });
      }, true);
      return result;
    };
    return popup;
  };
  return true;
})()`;

async function screenshot(cdp, sessionId, dir, name) {
  if (!dir) return;
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, sessionId);
  writeFileSync(join(dir, name), Buffer.from(data, 'base64'));
  console.log(`  screenshot: ${join(dir, name)}`);
}

// What every pop-out that rendered must show: the widget on screen, its policy in
// force, its document free of inline script, and the widget's own script by address.
async function checkRendered(cdp, page, popup, { label, pageScript, pageUrl, title }) {
  if (!(await waitFor(cdp, popup.sessionId,
    `!!window.OSAChatWidget && !!document.querySelector('.osa-chat-widget.fullscreen .osa-chat-window.open')`,
    `${label}: the pop-out's widget`))) return false;
  if (!(await waitFor(cdp, popup.sessionId, `!!document.querySelector('.osa-tab-strip')`,
    `${label}: the pop-out's community config (a capsule), and its tab strip`))) return false;
  report(await evaluate(cdp, popup.sessionId, visible('.osa-chat-window')) &&
    await evaluate(cdp, popup.sessionId, visible('.osa-tab-strip')), `${label}: the pop-out's panel and tab strip are on screen`);
  const scripts = await evaluate(cdp, popup.sessionId, `[...document.scripts].map((s) => ({
    src: s.src, integrity: s.getAttribute('integrity'), crossorigin: s.getAttribute('crossorigin'),
  }))`);
  report(scripts.every((s) => s.src), `${label}: every script in the pop-out's document has a src, none is inline`, scripts);
  report(!(await evaluate(cdp, popup.sessionId, INLINE_RUNS)), `${label}: control: the pop-out refuses an inline script, so the page's policy is in force there`);
  const widget = scripts.find((s) => s.src === pageScript.src);
  report(!!widget, `${label}: the widget's script, by the page tag's address`, scripts.map((s) => s.src));
  if (widget) {
    report(widget.integrity === pageScript.integrity && widget.crossorigin === pageScript.crossorigin,
      `${label}: with the page tag's integrity and crossorigin (${pageScript.integrity ? 'pinned' : 'none'})`,
      { widget, pageScript });
  }
  const popupState = await evaluate(cdp, popup.sessionId, `({
    href: location.href, origin: self.origin, title: OSAChatWidget.getConfig().title,
    marker: localStorage.getItem('osa-popout-check'),
  })`);
  report(popupState.title === title, `${label}: the pop-out's widget has the page's community title`, popupState.title);
  report(popupState.href === pageUrl, `${label}: the pop-out reports the page's address, for the page context it sends`, popupState.href);
  // The page's origin, and so the page's storage: a value only the page wrote
  // (load() below; the widget never writes that key) is there in the pop-out.
  const pageStorage = await evaluate(cdp, page.sessionId, `({ origin: self.origin, marker: localStorage.getItem('osa-popout-check') })`);
  report(popupState.origin === pageStorage.origin && !!pageStorage.marker && popupState.marker === pageStorage.marker,
    `${label}: the pop-out has the page's origin, and reads the page's own storage`, { popup: popupState, page: pageStorage });
  return true;
}

async function check(base, screenshotDir, { liveNotebook = false } = {}) {
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
  const notebookUrl = liveNotebook ? LIVE_NOTEBOOK_URL : `${base}/notebook-stub/`;
  const query = `?dataset=nm000103&zarr=true&notebookUrl=${encodeURIComponent(notebookUrl)}`;
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-popout-check-'));
  let chrome, cdp;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { product } = await cdp.send('Browser.getVersion');
    console.log(`Chrome: ${product}`);
    const created = [];
    cdp.on((message) => {
      if (message.method === 'Target.targetCreated') created.push(message.params.targetInfo);
    });
    await cdp.send('Target.setDiscoverTargets', { discover: true });
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    const page = { targetId, sessionId };
    await cdp.send('Runtime.enable', {}, sessionId);
    await cdp.send('Page.enable', {}, sessionId);
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);

    // Loads the page, waits for NEMAR's capsule, and runs the page's own control.
    const load = async (path) => {
      const url = `${base}/browser-harness/${path}${query}`;
      await cdp.send('Page.navigate', { url }, sessionId);
      if (!(await waitFor(cdp, sessionId,
        `!!document.querySelector('.osa-launcher-capsule .osa-notebook-btn[aria-disabled="false"]')`,
        `${path}: NEMAR's capsule, with the notebook available`))) return null;
      report(!(await evaluate(cdp, sessionId, INLINE_RUNS)), `${path}: control: the page refuses an inline script (no 'unsafe-inline')`);
      await evaluate(cdp, sessionId, RECORD_POPOUT_TRANSITIONS);
      await evaluate(cdp, sessionId, `localStorage.setItem('osa-popout-check', ${JSON.stringify(`${path} ${Date.now()}`)})`);
      const pageScript = await evaluate(cdp, sessionId, `(() => {
        const s = document.querySelector('script[src*="osa-chat-widget"]');
        return { src: s.src, integrity: s.getAttribute('integrity'), crossorigin: s.getAttribute('crossorigin') };
      })()`);
      const title = await evaluate(cdp, sessionId, 'OSAChatWidget.getConfig().title');
      return { url, pageScript, title };
    };
    const close = async (popup) => {
      await cdp.send('Target.closeTarget', { targetId: popup.targetId });
      // The page forgets a closed pop-out on its next once-a-second check.
      await Bun.sleep(1200);
    };

    console.log('--- from the chat tab');
    const plain = await load('widget-e2e.html');
    if (!plain) return;
    report(!plain.pageScript.integrity, 'sanity: this page\'s widget tag has no integrity');
    await evaluate(cdp, sessionId, `document.querySelector('.osa-launcher-capsule .osa-chat-button').click()`);
    let popup = await openPopout(cdp, created, page, `document.querySelector('.osa-popout-btn').click()`);
    if (!popup) return;
    if (await checkRendered(cdp, page, popup, { label: 'chat', pageScript: plain.pageScript, pageUrl: plain.url, title: plain.title })) {
      const tabs = await evaluate(cdp, popup.sessionId, `({
        chat: document.querySelector('.osa-strip-chat').getAttribute('aria-pressed'),
        notebook: document.querySelector('.osa-strip-notebook').getAttribute('aria-pressed'),
      })`);
      report(tabs.chat === 'true' && tabs.notebook === 'false', 'chat: the pop-out opens on the chat tab', tabs);
      report(await evaluate(cdp, popup.sessionId, visible('.osa-view-chat .osa-chat-input input')), 'chat: its chat input is on screen');
      report(await evaluate(cdp, popup.sessionId, `!document.querySelector('.osa-notebook-frame')`), 'chat: no notebook frame until its notebook tab opens');
      await screenshot(cdp, popup.sessionId, screenshotDir, 'popout-chat.png');
    }
    await close(popup);

    console.log('--- from the notebook tab');
    await evaluate(cdp, sessionId, `document.querySelector('.osa-notebook-btn').click()`);
    if (!(await waitFor(cdp, sessionId, `document.querySelector('.osa-chat-widget').classList.contains('osa-tab-notebook')`, 'the page\'s panel on the notebook tab'))) return;
    report(await evaluate(cdp, sessionId, visible('.osa-popout-btn')), 'notebook: the page\'s pop-out button is on screen on the notebook tab');
    popup = await openPopout(cdp, created, page, `document.querySelector('.osa-popout-btn').click()`);
    if (!popup) return;
    if (await checkRendered(cdp, page, popup, { label: 'notebook', pageScript: plain.pageScript, pageUrl: plain.url, title: plain.title })) {
      const state = await evaluate(cdp, popup.sessionId, `({
        chat: document.querySelector('.osa-strip-chat').getAttribute('aria-pressed'),
        notebook: document.querySelector('.osa-strip-notebook').getAttribute('aria-pressed'),
        frame: document.querySelector('.osa-view-notebook .osa-notebook-frame')?.getAttribute('src') ?? null,
        heading: document.querySelector('.osa-notebook-heading')?.textContent ?? null,
      })`);
      report(state.notebook === 'true' && state.chat === 'false', 'notebook: the pop-out opens on the notebook tab', state);
      const expected = `${notebookUrl}open.html?community=nemar&dataset=nm000103`;
      report(state.frame === expected, 'notebook: its frame is at the notebook site\'s contract address, from the page\'s notebookUrl', state.frame);
      report(await evaluate(cdp, popup.sessionId, visible('.osa-notebook-frame')), 'notebook: the frame is on screen');
      report(await evaluate(cdp, popup.sessionId, visible('.osa-notebook-heading')) && state.heading === 'Notebook', 'notebook: the header shows the notebook\'s title');
      report(!(await evaluate(cdp, popup.sessionId, visible('.osa-view-chat .osa-chat-input input'))), 'notebook: the chat is not on screen');
      report(await evaluate(cdp, popup.sessionId, 'window.__osaRecordedBeforeWidget === true'),
        'notebook: sanity: the pop-out\'s transitions were recorded from before its widget script was added');
      const faded = await evaluate(cdp, popup.sessionId, `(window.__osaTransitions || []).filter((t) => /\\bosa-(view|ttl)\\b/.test(t.target))`);
      report(faded.length === 0, 'notebook: it was drawn on the notebook tab at once, with no fade from chat', faded.slice(0, 4));
      report(await evaluate(cdp, sessionId, `!!document.querySelector('.osa-notebook-frame')?.isConnected`), 'notebook: the page keeps its own frame');
      await screenshot(cdp, popup.sessionId, screenshotDir, 'popout-notebook.png');
      // The strip switches tabs in the pop-out as the circles do in the panel, with
      // their fade: which the transition record above must be able to see.
      await evaluate(cdp, popup.sessionId, `document.querySelector('.osa-strip-chat').click()`);
      if (await waitFor(cdp, popup.sessionId, `${visible('.osa-view-chat .osa-chat-input input')} && !${visible('.osa-notebook-frame')}`,
        'notebook: the chat, and the notebook hidden, after the strip\'s Chat tab', 3000)) {
        report(true, 'notebook: the strip\'s Chat tab shows the chat, the notebook hidden');
      }
      const fade = await evaluate(cdp, popup.sessionId, `(window.__osaTransitions || []).some((t) => /\\bosa-view\\b/.test(t.target))`);
      report(fade, 'notebook: control: that switch faded, so the record of transitions sees one when there is one');
      await evaluate(cdp, popup.sessionId, `document.querySelector('.osa-strip-notebook').click()`);
      if (await waitFor(cdp, popup.sessionId, visible('.osa-notebook-frame'), 'notebook: the notebook again, after the strip\'s Notebook tab', 3000)) {
        report(await evaluate(cdp, popup.sessionId, `document.querySelector('.osa-notebook-frame').getAttribute('src') === ${JSON.stringify(`${notebookUrl}open.html?community=nemar&dataset=nm000103`)}`),
          'notebook: and its Notebook tab shows the same notebook again');
      }
      if (liveNotebook) {
        // The notebook in the pop-out's own frame, framed by the pop-out and heard by
        // the pop-out's widget: its setup cell runs, as in the panel.
        const started = Date.now();
        if (await waitFor(cdp, popup.sessionId, `/Python ready$/.test(document.querySelector('.osa-notebook-status-text').textContent)`,
          'live notebook: "Python ready" in the pop-out\'s header', 120_000)) {
          report(true, `live notebook: the pop-out's own notebook reported "Python ready", ${((Date.now() - started) / 1000).toFixed(1)} s after its tab showed`);
          report(await evaluate(cdp, popup.sessionId, `document.querySelector('.osa-notebook-fallback').classList.contains('osa-overlay-hidden') && document.querySelector('.osa-notebook-loading').classList.contains('osa-overlay-hidden')`),
            'live notebook: no overlay left over it');
          await screenshot(cdp, popup.sessionId, screenshotDir, 'popout-live-notebook.png');
        }
      }
    }
    await close(popup);

    console.log('--- the pinned page: integrity and crossorigin, as nemar.org has them');
    const pinned = await load('widget-e2e-sri.html');
    if (!pinned) return;
    report(/^sha384-/.test(pinned.pageScript.integrity || '') && pinned.pageScript.crossorigin === 'anonymous',
      'sanity: this page\'s widget tag has integrity and crossorigin', pinned.pageScript);
    popup = await openPopout(cdp, created, page, `document.querySelector('.osa-popout-btn').click()`);
    if (!popup) return;
    await checkRendered(cdp, page, popup, { label: 'pinned', pageScript: pinned.pageScript, pageUrl: pinned.url, title: pinned.title });
    await close(popup);

    // The control: the same tag with a wrong digest. The page's own copy has already
    // run; the next pop-out carries the wrong one, and the browser must refuse it.
    const wrong = `sha384-${'A'.repeat(64)}`;
    await evaluate(cdp, sessionId, `document.querySelector('script[src*="osa-chat-widget"]').setAttribute('integrity', ${JSON.stringify(wrong)})`);
    popup = await openPopout(cdp, created, page, `document.querySelector('.osa-popout-btn').click()`);
    if (!popup) return;
    if (await waitFor(cdp, popup.sessionId, `!!document.querySelector('.osa-popout-failure')`, 'control: the pop-out\'s failure message')) {
      const refused = await evaluate(cdp, popup.sessionId, `({
        widget: !!window.OSAChatWidget,
        integrity: document.querySelector('script[src*="osa-chat-widget"]')?.getAttribute('integrity') ?? null,
        message: document.querySelector('.osa-popout-failure').textContent,
      })`);
      report(refused.integrity === wrong && !refused.widget,
        'control: a pop-out carrying a wrong integrity is refused by the browser, so the pin is checked there', refused);
      report(refused.message.includes('could not load in this window'), 'control: and the pop-out says so, rather than opening blank', refused.message);
      await screenshot(cdp, popup.sessionId, screenshotDir, 'popout-refused.png');
    }
    await close(popup);
  } finally {
    cdp?.close();
    chrome?.kill();
    if (existsSync(profileDir)) rmSync(profileDir, { recursive: true, force: true });
  }
}

async function main() {
  const all = process.argv.slice(2);
  const liveNotebook = all.includes('--live-notebook');
  const args = all.filter((arg) => arg !== '--live-notebook');
  const screenshotDir = args[1];
  if (args[0] === '--serve' && args.length <= 2) {
    const server = startServer(['--nemar']);
    try {
      if (!(await waitForServer(server))) {
        report(false, `the widget_e2e.py --nemar server on port ${server.port} did not start`, server.output.slice(-2000));
      } else {
        await check(`http://127.0.0.1:${server.port}`, screenshotDir, { liveNotebook });
      }
    } finally {
      server.proc.kill();
    }
  } else if (args.length >= 1 && args.length <= 2 && /^https?:\/\//.test(args[0])) {
    await check(args[0].replace(/\/$/, ''), screenshotDir, { liveNotebook });
  } else {
    console.error('usage: bun frontend/browser-harness/popout-check.mjs --serve | http://127.0.0.1:PORT [--live-notebook] [screenshot-dir]');
    return 2;
  }
  return failed > 0 ? 1 : 0;
}

process.exit(await main());
