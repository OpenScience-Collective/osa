#!/usr/bin/env bun
/**
 * Time-to-first-output measurement for ADR 0010 (the notebook surface).
 *
 * Drives headless Chrome over the DevTools protocol, the same way
 * `chrome.js` does (see that file for the fuller pattern this borrows:
 * launching Chrome, a minimal CDP client, and a Network.* recorder that
 * follows a page into its Worker targets). This script is narrower: it does
 * not gate CI, it only times "navigation start" to "a sentinel string appears
 * in the page", cold (fresh profile) and warm (second navigation, same
 * profile and origin), and sums the bytes and request count the page and its
 * workers transferred.
 *
 * Usage:
 *   bun frontend/browser-harness/notebook-bench.js <url> <sentinel> [timeoutMs]
 *
 * Prints one JSON line per run (cold, then warm) to stdout. Chrome is
 * relaunched with a fresh --user-data-dir for "cold" and reused for "warm",
 * exactly as chrome.js's own warm check reuses the profile from its cold run.
 */

import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CANDIDATES = [
  process.env.CHROME_PATH,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean);

function findChrome() {
  return CANDIDATES.find((path) => existsSync(path)) || null;
}

async function launch(chromePath, profileDir) {
  const args = [
    '--headless=new',
    '--remote-debugging-port=0',
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    'about:blank',
  ];
  if (process.platform === 'linux') args.unshift('--no-sandbox');
  const chrome = Bun.spawn([chromePath, ...args], { stdout: 'ignore', stderr: 'pipe' });
  let seen = '';
  const endpoint = new Promise((resolve) => {
    (async () => {
      const decoder = new TextDecoder();
      for await (const chunk of chrome.stderr) {
        if (seen.length < 20_000) seen += decoder.decode(chunk);
        const match = /DevTools listening on (ws:\/\/\S+)/.exec(seen);
        if (match) resolve(match[1]);
      }
      resolve(null);
    })();
  });
  const wsUrl = await Promise.race([endpoint, Bun.sleep(30_000).then(() => null)]);
  if (!wsUrl) {
    chrome.kill();
    throw new Error(`Chrome did not report a DevTools endpoint:\n${seen.slice(-800)}`);
  }
  return { chrome, wsUrl };
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl);
    const pending = new Map();
    const listeners = [];
    let nextId = 0;
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== undefined && pending.has(message.id)) {
        const { resolve: done, reject: fail } = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) fail(new Error(`${message.error.message} (${message.error.code})`));
        else done(message.result);
      } else if (message.method) {
        for (const listener of listeners) listener(message);
      }
    };
    socket.onerror = () => reject(new Error(`could not connect to ${wsUrl}`));
    socket.onopen = () =>
      resolve({
        send(method, params = {}, sessionId) {
          const id = ++nextId;
          socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
          return new Promise((done, fail) => pending.set(id, { resolve: done, reject: fail }));
        },
        on(listener) {
          listeners.push(listener);
          return () => {
            const index = listeners.indexOf(listener);
            if (index >= 0) listeners.splice(index, 1);
          };
        },
        close() {
          socket.close();
        },
      });
  });
}

/** Network.* traffic for a page and every worker it spawns (see chrome.js's NetworkRecorder). */
class NetworkRecorder {
  constructor() {
    this.requests = new Map();
    this.attachFailures = [];
  }
  handle(message) {
    const { method, params, sessionId } = message;
    if (typeof method !== 'string' || !method.startsWith('Network.') || !params || params.requestId === undefined) return;
    const key = `${sessionId}:${params.requestId}`;
    if (method === 'Network.requestWillBeSent') {
      if (!this.requests.has(key)) {
        this.requests.set(key, { url: params.request.url, encodedDataLength: 0, fromDiskCache: false, servedFromCache: false });
      }
    } else if (method === 'Network.responseReceived') {
      const r = this.requests.get(key);
      if (r) r.fromDiskCache = Boolean(params.response.fromDiskCache);
    } else if (method === 'Network.requestServedFromCache') {
      const r = this.requests.get(key);
      if (r) r.servedFromCache = true;
    } else if (method === 'Network.loadingFinished') {
      const r = this.requests.get(key);
      if (r) r.encodedDataLength = params.encodedDataLength;
    }
  }
  totals() {
    const all = Array.from(this.requests.values());
    const bytes = all.reduce((sum, r) => sum + r.encodedDataLength, 0);
    const cacheHits = all.filter((r) => r.fromDiskCache || r.servedFromCache).length;
    return { requestCount: all.length, bytesTransferred: bytes, cacheHits };
  }
}

async function attachWithNetwork(cdp, pageSessionId, recorder) {
  const tracked = new Set([pageSessionId]);
  const unsubscribe = cdp.on((message) => {
    if (message.method === 'Target.attachedToTarget' && message.sessionId === pageSessionId) {
      const { sessionId: childSessionId, targetInfo } = message.params;
      if (targetInfo.type !== 'worker' && targetInfo.type !== 'iframe') return;
      tracked.add(childSessionId);
      cdp
        .send('Network.enable', {}, childSessionId)
        .then(() => cdp.send('Runtime.runIfWaitingForDebugger', {}, childSessionId))
        .catch((err) => recorder.attachFailures.push(`${targetInfo.url}: ${err && err.message}`));
      return;
    }
    if (tracked.has(message.sessionId)) recorder.handle(message);
  });
  await cdp.send('Network.enable', {}, pageSessionId);
  await cdp.send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: true, flatten: true }, pageSessionId);
  return unsubscribe;
}

/**
 * Navigate to `url` and poll until `document.body.innerText` contains
 * `sentinel`, or `timeoutMs` elapses. Returns elapsed milliseconds from just
 * before Page.navigate to the first poll that saw the sentinel, plus network
 * totals across the page and every worker it spawned.
 */
async function timeToSentinel(cdp, url, sentinel, timeoutMs) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const recorder = new NetworkRecorder();
  const unsubscribe = await attachWithNetwork(cdp, sessionId, recorder);
  await cdp.send('Runtime.enable', {}, sessionId);
  const consoleLines = [];
  const stopConsole = cdp.on((message) => {
    if (message.sessionId !== sessionId) return;
    if (message.method === 'Runtime.consoleAPICalled') {
      consoleLines.push(message.params.args.map((a) => a.value ?? a.description ?? '').join(' '));
    }
  });
  try {
    const start = Date.now();
    await cdp.send('Page.navigate', { url }, sessionId);
    const deadline = start + timeoutMs;
    let foundAtMs = null;
    while (Date.now() < deadline) {
      const { result } = await cdp.send(
        'Runtime.evaluate',
        { expression: `document.body && document.body.innerText.includes(${JSON.stringify(sentinel)})`, returnByValue: true },
        sessionId
      );
      if (result.value === true) {
        foundAtMs = Date.now();
        break;
      }
      await Bun.sleep(200);
    }
    const totals = recorder.totals();
    if (foundAtMs === null) {
      return { ok: false, elapsedMs: null, error: `sentinel not seen within ${timeoutMs}ms`, consoleTail: consoleLines.slice(-10), ...totals };
    }
    return { ok: true, elapsedMs: foundAtMs - start, ...totals };
  } finally {
    stopConsole();
    unsubscribe();
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  }
}

async function main() {
  const [url, sentinel, timeoutArg] = process.argv.slice(2);
  if (!url || !sentinel) {
    console.error('usage: bun notebook-bench.js <url> <sentinel> [timeoutMs]');
    return 1;
  }
  const timeoutMs = Number(timeoutArg || 180_000);
  const chromePath = findChrome();
  if (!chromePath) {
    console.error(`no Chrome found (set CHROME_PATH; looked in ${CANDIDATES.join(', ')})`);
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

process.exit(await main());
