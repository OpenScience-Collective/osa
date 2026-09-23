#!/usr/bin/env bun
/**
 * Run the browser runtime harness in headless Chrome, and fail unless it passes.
 *
 * What only a browser can check, and so what no Bun suite does: Pyodide booting
 * under a real Content-Security-Policy, the egress guard on a real worker global,
 * and a lock overlay's wheels loaded by URL with their sha256 enforced. This turns
 * the harness from a page someone remembers to open into a CI gate.
 *
 * Two pages, both required:
 *   /nemarlike/  every check passes, NEMAR's overlay and its tampered control included
 *   /control/    the boot FAILS, because the policy grants no wasm; a control that
 *                passes would mean the policy was never applied
 *
 * Chrome is driven over the DevTools protocol on its own WebSocket, so nothing is
 * installed for this. It is found at CHROME_PATH, or where Chrome installs on
 * macOS and on the GitHub runner. Under CI a missing Chrome fails; elsewhere it
 * skips with a message, since a laptop without Chrome has nothing to report.
 *
 * Usage: bun frontend/browser-harness/chrome.js
 */

import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startServer } from './serve.js';

// A cold run downloads Pyodide, numpy and matplotlib, boots three runtimes and
// waits out a 10-second deadline once; the control waits out its 45-second boot
// deadline. Both bounds are generous so that a slow runner is not a failure.
// warm and lockchange boot NEMAR's overlay once more, on the SAME origin, so
// everything but at most one wheel comes from the browser's own HTTP cache;
// generous for the same reason, not because either is expected to be slow.
const PAGE_TIMEOUT_MS = { nemarlike: 360_000, control: 150_000, warm: 120_000, lockchange: 120_000 };

const CANDIDATES = [
  process.env.CHROME_PATH,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean);

export function findChrome() {
  return CANDIDATES.find((path) => existsSync(path)) || null;
}

/** Launch Chrome and resolve its browser-level DevTools WebSocket URL. */
export async function launch(chromePath, profileDir) {
  const args = [
    '--headless=new',
    '--remote-debugging-port=0',
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    'about:blank',
  ];
  // The runner's kernel refuses the user namespaces Chrome's sandbox needs; the
  // pages are this repository's own, served on loopback.
  if (process.platform === 'linux') args.unshift('--no-sandbox');
  const chrome = Bun.spawn([chromePath, ...args], { stdout: 'ignore', stderr: 'pipe' });

  // Read stderr for the endpoint, and keep draining it after: a pipe nobody reads
  // fills, and a Chrome blocked writing its log stops answering.
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

/** A minimal DevTools protocol client over one browser-level WebSocket. */
export function connect(wsUrl) {
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
        /** Returns a function that removes the listener again. */
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

/**
 * Every Network.* event CDP delivers, from every session fed into it, keyed
 * by session so two requests that happen to share a requestId (a different
 * worker, a different page) are never merged into one record.
 */
export class NetworkRecorder {
  constructor() {
    this.requests = new Map();
    // Workers whose network could not be watched. A run with any is a failed
    // measurement, whatever the requests that were seen say.
    this.attachFailures = [];
  }

  handle(message) {
    const { method, params, sessionId } = message;
    if (typeof method !== 'string' || !method.startsWith('Network.') || !params || params.requestId === undefined) return;
    const key = `${sessionId}:${params.requestId}`;
    if (method === 'Network.requestWillBeSent') {
      // The first sighting wins: a request that gets a redirect resends this
      // event for the SAME requestId, and what we care about is the URL
      // whoever asked for a wheel actually asked for.
      if (!this.requests.has(key)) {
        this.requests.set(key, {
          sessionId,
          requestId: params.requestId,
          url: params.request.url,
          startedAtMs: Date.now(),
          status: null,
          fromDiskCache: false,
          servedFromCache: false,
          encodedDataLength: 0,
          headers: null,
          failed: null,
          finishedAtMs: null,
        });
      }
    } else if (method === 'Network.responseReceived') {
      const r = this.requests.get(key);
      if (r) {
        r.status = params.response.status;
        r.fromDiskCache = Boolean(params.response.fromDiskCache);
        r.headers = params.response.headers || {};
      }
    } else if (method === 'Network.requestServedFromCache') {
      // Fires with NO responseReceived at all for some cache hits (e.g. the
      // memory cache), which is why this is checked as well as fromDiskCache
      // rather than instead of it.
      const r = this.requests.get(key);
      if (r) r.servedFromCache = true;
    } else if (method === 'Network.loadingFinished') {
      const r = this.requests.get(key);
      if (r) {
        r.encodedDataLength = params.encodedDataLength;
        r.finishedAtMs = Date.now();
      }
    } else if (method === 'Network.loadingFailed') {
      const r = this.requests.get(key);
      if (r) r.failed = params.errorText;
    }
  }

  /** Every recorded request whose URL contains `needle`, in the order first seen. */
  byUrl(needle) {
    return Array.from(this.requests.values())
      .filter((r) => r.url.includes(needle))
      .sort((a, b) => a.startedAtMs - b.startedAtMs);
  }
}

/**
 * Auto-attach to every worker target a page spawns (Pyodide runs in a blob
 * Worker, so this is where its own fetches happen and the page's own Network
 * domain never sees them), and feed the page's and each worker's Network.*
 * events into `recorder`.
 *
 * @param {ReturnType<typeof connect>} cdp
 * @param {string} pageSessionId
 * @param {NetworkRecorder} recorder
 * @param {string[]} [targetTypes] - Target.type values to auto-attach to,
 *   beyond the page itself. Defaults to `['worker']` (Pyodide's own case);
 *   a caller whose page loads its content in an `iframe` (rather than a
 *   Worker) passes `['worker', 'iframe']` to follow that too.
 */
export async function attachWithNetwork(cdp, pageSessionId, recorder, targetTypes = ['worker']) {
  const tracked = new Set([pageSessionId]);
  const unsubscribe = cdp.on((message) => {
    // Target.attachedToTarget for a CHILD of this page arrives on the PAGE's
    // own session (the one that called setAutoAttach); the child's own
    // session id is inside params, not the message's outer sessionId.
    if (message.method === 'Target.attachedToTarget' && message.sessionId === pageSessionId) {
      const { sessionId: childSessionId, targetInfo } = message.params;
      if (!targetTypes.includes(targetInfo.type)) return;
      tracked.add(childSessionId);
      // Paused on start (waitForDebuggerOnStart), so nothing the worker does
      // is missed between it existing and Network being enabled on it.
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
 * Open a page, echo its console, and return window.__harness once it is done.
 *
 * @param {ReturnType<typeof connect>} cdp
 * @param {string} url
 * @param {number} timeoutMs
 * @param {{recorder?: NetworkRecorder}} [options] - When given, every worker
 *   the page starts is auto-attached and its (and the page's own) network
 *   traffic is recorded into it; see NetworkRecorder and attachWithNetwork.
 */
async function runPage(cdp, url, timeoutMs, { recorder } = {}) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  const unsubscribers = [];
  unsubscribers.push(cdp.on((message) => {
    if (message.sessionId !== sessionId) return;
    if (message.method === 'Runtime.consoleAPICalled') {
      const text = message.params.args.map((a) => a.value ?? a.description ?? '').join(' ');
      if (text.startsWith('[harness]')) console.log(`    ${text.slice('[harness] '.length)}`);
    } else if (message.method === 'Runtime.exceptionThrown') {
      console.log(`    page exception: ${message.params.exceptionDetails.text}`);
    }
  }));
  await cdp.send('Runtime.enable', {}, sessionId);
  if (recorder) unsubscribers.push(await attachWithNetwork(cdp, sessionId, recorder));
  await cdp.send('Page.navigate', { url }, sessionId);

  const deadline = Date.now() + timeoutMs;
  try {
    while (Date.now() < deadline) {
      const { result } = await cdp.send(
        'Runtime.evaluate',
        { expression: 'window.__harness && window.__harness.done ? JSON.stringify(window.__harness) : null', returnByValue: true },
        sessionId
      );
      if (result.value) return JSON.parse(result.value);
      await Bun.sleep(1000);
    }
    return { done: false, results: [], error: `the page did not finish within ${timeoutMs / 1000}s` };
  } finally {
    for (const unsubscribe of unsubscribers) unsubscribe();
    await cdp.send('Target.closeTarget', { targetId });
  }
}

/**
 * The wheel requests a NetworkRecorder saw under a path, reduced to what the
 * cache measurement and the report both need.
 *
 * @param {NetworkRecorder} recorder
 * @param {string} pathSubstring - e.g. '/runtime/nemar/'.
 * @returns {{fileName: string, url: string, fromDiskCache: boolean, servedFromCache: boolean, cacheHit: boolean, encodedDataLength: number, status: number|null, failed: string|null, startedAtMs: number, finishedAtMs: number|null}[]}
 */
function wheelRequests(recorder, pathSubstring) {
  return recorder.byUrl(pathSubstring).map((r) => ({
    fileName: decodeURIComponent(r.url.split('/').pop()),
    url: r.url,
    fromDiskCache: r.fromDiskCache,
    servedFromCache: r.servedFromCache,
    cacheHit: r.fromDiskCache || r.servedFromCache,
    encodedDataLength: r.encodedDataLength,
    status: r.status,
    failed: r.failed,
    startedAtMs: r.startedAtMs,
    finishedAtMs: r.finishedAtMs,
  }));
}

async function main() {
  const chromePath = findChrome();
  if (!chromePath) {
    const message = `no Chrome found (set CHROME_PATH; looked in ${CANDIDATES.join(', ')})`;
    if (process.env.CI) {
      console.error(`FAIL: ${message}`);
      return 1;
    }
    console.log(`SKIP: ${message}`);
    return 0;
  }

  const server = await startServer({ port: 0 });
  const profileDir = mkdtempSync(join(tmpdir(), 'osa-harness-chrome-'));
  let chrome = null;
  let cdp = null;
  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);
    const base = `http://127.0.0.1:${server.port}`;
    let failed = 0;
    /** Prints one check's verdict; a failure prints its details and counts. */
    const report = (ok, failMessage, okMessage, details = []) => {
      if (ok) {
        console.log(`ok: ${okMessage}`);
        return;
      }
      failed++;
      console.error(`FAIL: ${failMessage}`);
      for (const line of details) console.error(`  - ${line}`);
    };
    /**
     * Runs a page with its workers' network recorded. A worker whose network
     * could not be watched fails the run: its wheels would go unobserved.
     */
    const runRecorded = async (url, timeoutMs) => {
      const recorder = new NetworkRecorder();
      const page = await runPage(cdp, url, timeoutMs, { recorder });
      return { page, recorder, attachFailures: recorder.attachFailures.map((f) => `could not watch a worker's network: ${f}`) };
    };
    const describeWheel = (w) =>
      `${w.fileName}: fromDiskCache=${w.fromDiskCache} servedFromCache=${w.servedFromCache} encodedDataLength=${w.encodedDataLength} status=${w.status} failed=${w.failed || ''}`;

    const { product: chromeVersion } = await cdp.send('Browser.getVersion');
    console.log(`Chrome: ${chromeVersion}`);

    console.log('nemarlike: the production policy, where every check must pass');
    const cold = await runRecorded(`${base}/nemarlike/browser-harness/index.html`, PAGE_TIMEOUT_MS.nemarlike);
    const nemarlike = cold.page;
    const failing = nemarlike.results.filter((r) => !r.ok);
    report(
      nemarlike.done && !nemarlike.error && failing.length === 0 && nemarlike.results.length > 0 && cold.attachFailures.length === 0,
      `nemarlike ${nemarlike.error || `${failing.length} of ${nemarlike.results.length} checks failed`}`,
      `nemarlike, all ${nemarlike.results.length} checks passed`,
      [...failing.map((r) => `${r.name}: ${r.detail}`), ...cold.attachFailures]
    );

    console.log('control: no wasm grant, where the boot must fail');
    const control = await runPage(cdp, `${base}/control/browser-harness/index.html`, PAGE_TIMEOUT_MS.control);
    const boot = control.results[0];
    report(
      control.done && boot && boot.ok === false,
      `control ${control.error || 'booted, so the policy was never applied'}`,
      `control, the boot failed as it must (${boot && boot.detail})`
    );

    // The overlay's own wheel names, from the config the harness pages boot
    // from, so a request that went unobserved (a worker the recorder missed)
    // fails the cache checks rather than passing as "every observed request
    // was cached". An empty list would make both checks vacuous, so it throws.
    const harnessConfig = await (await fetch(`${base}/browser-harness/harness-config.json`)).json();
    const overlayFiles = Object.values(harnessConfig.nemar.packages).map((entry) => entry.file_name).sort();
    if (overlayFiles.length === 0) throw new Error('harness-config.json names no overlay wheel, so the cache checks would prove nothing');
    const sameFiles = (wheels, expected) =>
      JSON.stringify([...new Set(wheels.map((w) => w.fileName))].sort()) === JSON.stringify([...expected].sort());

    // warm: NEMAR's runtime, booted a SECOND time on a fresh page. Same
    // server, same port, same browser (Chrome partitions its HTTP cache by
    // top-level site), and the same /nemarlike/ path prefix as the cold run
    // above, so this is the same origin and the same cache partition.
    console.log('warm: NEMAR\'s runtime booted again, where every overlay wheel must come from cache');
    const warmRun = await runRecorded(`${base}/nemarlike/browser-harness/cache-boot.html?variant=nemar`, PAGE_TIMEOUT_MS.warm);
    const warm = warmRun.page;
    const warmWheels = wheelRequests(warmRun.recorder, '/runtime/nemar/');
    const warmMisses = warmWheels.filter((w) => !w.cacheHit || w.encodedDataLength > 0);
    const warmIncomplete = !sameFiles(warmWheels, overlayFiles);
    report(
      warm.done && warm.ok && !warmIncomplete && warmMisses.length === 0 && warmRun.attachFailures.length === 0,
      `warm ${warm.error || (warmIncomplete ? `observed ${warmWheels.map((w) => w.fileName).join(', ') || 'no wheel'}, expected ${overlayFiles.join(', ')}` : '')}`,
      `warm, all ${warmWheels.length} overlay wheel requests were served from cache (0 bytes over the network)`,
      [...warmMisses.map(describeWheel), ...warmRun.attachFailures]
    );

    // lock change: ONE overlay wheel (eegprep-lean) renamed with a build tag,
    // same bytes and sha256, in the SAME browser. That new URL has never been
    // fetched before, so it alone must be a real network fetch; everything
    // else (zarr, and the interpreter and stock wheels) must still be cached.
    console.log('lock change: one renamed wheel goes to the network, the rest stay cached');
    const lockRun = await runRecorded(
      `${base}/nemarlike/browser-harness/cache-boot.html?variant=nemarLockChanged`,
      PAGE_TIMEOUT_MS.lockchange
    );
    const lockchange = lockRun.page;
    const lockWheels = wheelRequests(lockRun.recorder, '/runtime/nemar/');
    const changed = lockWheels.filter((w) => w.fileName.includes('eegprep_lean') && /-\d+-py3-none-any\.whl$/.test(w.fileName));
    const unchanged = lockWheels.filter((w) => !changed.includes(w));
    const changedProblem = changed.length !== 1 || changed[0].cacheHit || changed[0].encodedDataLength === 0;
    const unchangedProblem =
      !sameFiles(unchanged, overlayFiles.filter((name) => !name.startsWith('eegprep_lean-'))) ||
      unchanged.some((w) => !w.cacheHit || w.encodedDataLength > 0);
    report(
      lockchange.done && lockchange.ok && !changedProblem && !unchangedProblem && lockRun.attachFailures.length === 0,
      `lock change ${lockchange.error || ''}`,
      `lock change, only ${changed[0]?.fileName} went to the network; ${unchanged.length} other wheel(s) stayed cached`,
      [...lockWheels.map(describeWheel), ...lockRun.attachFailures]
    );

    // jsDelivr's own assets (the interpreter and the stock wheels numpy and
    // matplotlib): reported, not gated. jsDelivr's cache behavior is not
    // ours to enforce; this is only as far as its headers let us say anything.
    console.log('jsDelivr (interpreter and stock wheels), reported only:');
    for (const r of warmRun.recorder.byUrl('cdn.jsdelivr.net')) {
      const cacheControl = (r.headers && (r.headers['cache-control'] || r.headers['Cache-Control'])) || '(no header)';
      console.log(`  - ${r.url.split('/').pop()}: fromDiskCache=${r.fromDiskCache} bytes=${r.encodedDataLength} cache-control=${cacheControl}`);
    }

    console.log('\ncold vs warm, per overlay wheel (bytes over the network, milliseconds):');
    // The nemarlike page fetches each overlay wheel more than once (the
    // overlay boot, then the tampered-digest checks re-fetching the same
    // committed URL by plain fetch()); the FIRST sighting is the genuinely
    // cold one; later ones are already this page's own warm hits.
    const firstByFileName = (wheels) => {
      const seen = new Map();
      for (const w of wheels) if (!seen.has(w.fileName)) seen.set(w.fileName, w);
      return [...seen.values()];
    };
    const coldWheels = firstByFileName(wheelRequests(cold.recorder, '/runtime/nemar/'));
    for (const first of coldWheels) {
      const w = warmWheels.find((x) => x.fileName === first.fileName);
      console.log(
        `  - ${first.fileName}: cold ${first.encodedDataLength}B in ${(first.finishedAtMs ?? first.startedAtMs) - first.startedAtMs}ms` +
          (w ? `, warm ${w.encodedDataLength}B (cacheHit=${w.cacheHit})` : ', warm (not observed)')
      );
    }

    return failed === 0 ? 0 : 1;
  } finally {
    if (cdp) cdp.close();
    if (chrome) {
      chrome.kill();
      await chrome.exited;
    }
    server.stop(true);
    rmSync(profileDir, { recursive: true, force: true });
  }
}

if (import.meta.main) {
  process.exit(await main());
}
