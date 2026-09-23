#!/usr/bin/env bun
/**
 * Time-to-first-output measurement for ADR 0010 (the notebook surface).
 *
 * Drives headless Chrome over the DevTools protocol, reusing `chrome.js`'s
 * own helpers directly (`findChrome`, `launch`, `connect`, `NetworkRecorder`,
 * `attachWithNetwork`) rather than a second copy of them. This script is
 * narrower than that harness: it does not gate CI, it only times "navigation
 * start" to "a sentinel appears", cold (fresh profile) and warm (second
 * navigation, same profile and origin), and sums the bytes, request count,
 * and failed-request count the page and its Worker targets transferred.
 *
 * `attachWithNetwork`'s auto-attach follows Worker targets by default, which
 * is what marimo and JupyterLite's Pyodide kernel both run in. A page that
 * instead loads its content in an `iframe` (not used by this ADR's
 * measurements, but supported for a future one) can be followed too by
 * passing `['worker', 'iframe']` as `attachWithNetwork`'s fourth argument.
 *
 * Usage:
 *   bun frontend/browser-harness/notebook-bench.js <url> <sentinel> [timeoutMs]
 *
 * `<sentinel>` is normally a string to find in `document.body.innerText`.
 * The special value `__harness__` instead polls `window.__harness.done`
 * (the shape `frontend/browser-harness/cache-boot.js` and the rest of this
 * harness use), so OSA's own runtime can be measured with this same script,
 * on the same `nemarlike` page `chrome.js` gates CI with.
 *
 * Prints one JSON line per run (cold, then warm) to stdout. Chrome is
 * relaunched with a fresh --user-data-dir for "cold" and reused for "warm",
 * exactly as chrome.js's own warm check reuses the profile from its cold run.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { attachWithNetwork, connect, findChrome, launch, NetworkRecorder } from './chrome.js';

const HARNESS_SENTINEL = '__harness__';

/**
 * Navigate to `url` and poll until the sentinel condition is true, or
 * `timeoutMs` elapses. Returns elapsed milliseconds from just before
 * `Page.navigate` to the first poll that saw it, plus network totals across
 * the page and every attached Worker (bytes transferred, request count, and
 * how many of those requests failed outright, e.g. a CORS refusal or a
 * dropped connection, which a byte-count alone reports as a silent zero).
 */
async function timeToSentinel(cdp, url, sentinel, timeoutMs) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const recorder = new NetworkRecorder();
  const unsubscribeNetwork = await attachWithNetwork(cdp, sessionId, recorder);
  await cdp.send('Runtime.enable', {}, sessionId);
  const consoleLines = [];
  const exceptions = [];
  const stopListening = cdp.on((message) => {
    if (message.sessionId !== sessionId) return;
    if (message.method === 'Runtime.consoleAPICalled') {
      consoleLines.push(message.params.args.map((a) => a.value ?? a.description ?? '').join(' '));
    } else if (message.method === 'Runtime.exceptionThrown') {
      exceptions.push(message.params.exceptionDetails.text);
    }
  });
  const expression =
    sentinel === HARNESS_SENTINEL
      ? 'window.__harness && window.__harness.done ? JSON.stringify(window.__harness) : null'
      : `document.body && document.body.innerText.includes(${JSON.stringify(sentinel)}) ? "1" : null`;
  try {
    const start = Date.now();
    await cdp.send('Page.navigate', { url }, sessionId);
    const deadline = start + timeoutMs;
    let foundAtMs = null;
    let harness = null;
    let polls = 0;
    while (Date.now() < deadline) {
      polls++;
      let value = null;
      try {
        const { result } = await cdp.send('Runtime.evaluate', { expression, returnByValue: true }, sessionId);
        value = result.value ?? null;
      } catch (err) {
        // A navigation mid-poll (a redirect, a reload the page itself
        // triggers) tears down the execution context Runtime.evaluate was
        // sent to; that one poll is lost, not the whole run. Anything else
        // still surfaces on the next iteration or the final timeout.
        const message = (err && err.message) || String(err);
        if (!/context/i.test(message)) throw err;
        await Bun.sleep(200);
        continue;
      }
      if (value) {
        foundAtMs = Date.now();
        if (sentinel === HARNESS_SENTINEL) harness = JSON.parse(value);
        break;
      }
      await Bun.sleep(200);
    }
    const all = Array.from(recorder.requests.values());
    const totals = {
      requestCount: all.length,
      bytesTransferred: all.reduce((sum, r) => sum + r.encodedDataLength, 0),
      cacheHits: all.filter((r) => r.fromDiskCache || r.servedFromCache).length,
      failedRequests: all.filter((r) => r.failed).length,
    };
    if (foundAtMs === null) {
      return {
        ok: false,
        elapsedMs: null,
        error: `sentinel not seen within ${timeoutMs}ms`,
        exceptions,
        consoleTail: consoleLines.slice(-10),
        ...totals,
      };
    }
    // A match on the very first poll (before the page could plausibly have
    // done real work) is the signature of the false-positive this script
    // once had: JupyterLite's REPL echoes submitted code into the DOM
    // before running it, so a sentinel that is also a literal substring of
    // the source matches instantly. Warn rather than silently reporting a
    // number nobody should trust.
    if (polls === 1) {
      console.warn(`warning: sentinel matched on the first poll (${foundAtMs - start}ms) for ${url} -- verify it cannot match unexecuted source`);
    }
    const harnessOk = sentinel === HARNESS_SENTINEL ? harness.ok !== false : true;
    return {
      ok: harnessOk && exceptions.length === 0,
      elapsedMs: foundAtMs - start,
      ...(harness ? { harness } : {}),
      exceptions,
      ...totals,
    };
  } finally {
    stopListening();
    unsubscribeNetwork();
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  }
}

async function main() {
  const [url, sentinel, timeoutArg] = process.argv.slice(2);
  if (!url || !sentinel) {
    console.error('usage: bun notebook-bench.js <url> <sentinel|__harness__> [timeoutMs]');
    return 1;
  }
  const timeoutMs = Number(timeoutArg || 180_000);
  const chromePath = findChrome();
  if (!chromePath) {
    console.error('no Chrome found (set CHROME_PATH)');
    return 1;
  }

  const profileDir = mkdtempSync(join(tmpdir(), 'osa-notebook-bench-'));
  let chrome = null;
  let cdp = null;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const { product: chromeVersion } = await cdp.send('Browser.getVersion');

    const cold = await timeToSentinel(cdp, url, sentinel, timeoutMs);
    console.log(JSON.stringify({ run: 'cold', url, chromeVersion, ...cold }));

    // warm: same profile (same disk cache), fresh page, same origin.
    const warm = await timeToSentinel(cdp, url, sentinel, timeoutMs);
    console.log(JSON.stringify({ run: 'warm', url, chromeVersion, ...warm }));

    return cold.ok && warm.ok ? 0 : 1;
  } finally {
    if (cdp) cdp.close();
    if (chrome) {
      chrome.kill();
      await chrome.exited;
    }
    rmSync(profileDir, { recursive: true, force: true });
  }
}

if (import.meta.main) {
  process.exit(await main());
}
