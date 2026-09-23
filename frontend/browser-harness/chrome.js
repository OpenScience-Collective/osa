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
const PAGE_TIMEOUT_MS = { nemarlike: 360_000, control: 150_000 };

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

/** Launch Chrome and resolve its browser-level DevTools WebSocket URL. */
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
        },
        close() {
          socket.close();
        },
      });
  });
}

/** Open a page, echo its console, and return window.__harness once it is done. */
async function runPage(cdp, url, timeoutMs) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  cdp.on((message) => {
    if (message.sessionId !== sessionId) return;
    if (message.method === 'Runtime.consoleAPICalled') {
      const text = message.params.args.map((a) => a.value ?? a.description ?? '').join(' ');
      if (text.startsWith('[harness]')) console.log(`    ${text.slice('[harness] '.length)}`);
    } else if (message.method === 'Runtime.exceptionThrown') {
      console.log(`    page exception: ${message.params.exceptionDetails.text}`);
    }
  });
  await cdp.send('Runtime.enable', {}, sessionId);
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
    await cdp.send('Target.closeTarget', { targetId });
  }
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

    console.log('nemarlike: the production policy, where every check must pass');
    const nemarlike = await runPage(cdp, `${base}/nemarlike/browser-harness/index.html`, PAGE_TIMEOUT_MS.nemarlike);
    const failing = nemarlike.results.filter((r) => !r.ok);
    if (!nemarlike.done || nemarlike.error || failing.length > 0 || nemarlike.results.length === 0) {
      failed++;
      console.error(`FAIL: nemarlike ${nemarlike.error || `${failing.length} of ${nemarlike.results.length} checks failed`}`);
      for (const r of failing) console.error(`  - ${r.name}: ${r.detail}`);
    } else {
      console.log(`ok: nemarlike, all ${nemarlike.results.length} checks passed`);
    }

    console.log('control: no wasm grant, where the boot must fail');
    const control = await runPage(cdp, `${base}/control/browser-harness/index.html`, PAGE_TIMEOUT_MS.control);
    const boot = control.results[0];
    if (!control.done || !boot || boot.ok !== false) {
      failed++;
      console.error(`FAIL: control ${control.error || 'booted, so the policy was never applied'}`);
    } else {
      console.log(`ok: control, the boot failed as it must (${boot.detail})`);
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

process.exit(await main());
