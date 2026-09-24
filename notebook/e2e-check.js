#!/usr/bin/env bun
/**
 * The full notebook-site flow, in real, headless Chrome (issue #453,
 * docs/adr/0011-the-notebook-site.md). Reads the real, live zarr.nemar.org, the
 * same reason frontend/browser-harness/widget_e2e.py is a manual/workflow_dispatch
 * check rather than a required gate (see notebook/README.md).
 *
 * Reuses frontend/browser-harness/chrome.js's Chrome-driving primitives
 * (findChrome, launch, connect) rather than duplicating them.
 *
 * Usage: bun notebook/e2e-check.js [screenshot-path]
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { attachWithNetwork, connect, findChrome, launch, NetworkRecorder } from '../frontend/browser-harness/chrome.js';

const COMMUNITY = 'nemar';
const DATASET = 'nm000103';
// OSC's naming rule: a subdomain is a plane serving several projects, and the
// project is the path (api.osc.earth/osa, widget.osc.earth/osa, ...), so the
// real site is notebook.osc.earth/osa, not the bare host -- and this check
// builds and serves under that same prefix so it matches production exactly.
const SITE_SUBDIR = 'osa';
// Never appears in the starter's own source, only in its runtime output
// (window.data.shape, window.unit, window.rate on nm000103's first store).
const SENTINEL = '(4, 500) uV 250.0';

function log(msg) {
  console.log(`[e2e] ${msg}`);
}

/**
 * Builds with `--site-url` set to WHEREVER this is actually served from.
 *
 * The merged lock rewrites every community wheel to an absolute
 * `<site_url>/wheels/<community>/<file>` URL (src.core.config.notebook_lock.
 * merge_site_lock), so a build whose --site-url does not match its own serving
 * origin fetches wheels from the WRONG host: `%pip`/`micropip.install` would try
 * to reach the production domain from a local test server and fail silently
 * (this was found by running this exact check against a build pinned to the
 * production URL while serving on loopback -- see
 * .context/notebook-surface-measurements.md). Building AFTER the static server
 * already has its ephemeral port is what avoids that mismatch here.
 */
async function buildSite(outputDir, siteUrl) {
  log(`building the site into ${outputDir} (--site-url ${siteUrl}, --expose-app)`);
  const proc = Bun.spawn(
    [
      'uv',
      'run',
      'python',
      'scripts/build_notebook_site.py',
      '--site-url',
      siteUrl,
      '--output-dir',
      outputDir,
      '--expose-app',
    ],
    { stdout: 'inherit', stderr: 'inherit' }
  );
  const code = await proc.exited;
  if (code !== 0) throw new Error(`build failed with exit code ${code}`);
}

/** A minimal static file server for the built site: application/wasm for .wasm,
 * since Pyodide's OWN interpreter loads from jsDelivr, but a locally-served
 * .wasm anywhere in the tree needs the right type if a plain guess-by-extension
 * server gets it wrong. */
function serveStatic(root) {
  return Bun.serve({
    hostname: '127.0.0.1',
    port: 0,
    async fetch(request) {
      const { pathname } = new URL(request.url);
      const rel = pathname === '/' ? '/index.html' : pathname;
      const file = new URL(`.${rel}`, `file://${root}/`);
      const found = Bun.file(file);
      if (!(await found.exists())) return new Response('Not Found', { status: 404 });
      const type = rel.endsWith('.wasm') ? 'application/wasm' : found.type;
      return new Response(found, { headers: { 'Content-Type': type, 'Cache-Control': 'no-store' } });
    },
  });
}

/** Evaluate an expression in the page, without waiting for a returned promise
 * to settle: notebook:run-all-cells's own returned promise never settles in
 * this app (learned by trying it), so awaitPromise would hang the whole run. */
async function evaluate(cdp, sessionId, expression) {
  const { result, exceptionDetails } = await cdp.send(
    'Runtime.evaluate',
    { expression, returnByValue: true, awaitPromise: false },
    sessionId
  );
  if (exceptionDetails) throw new Error(`page threw: ${exceptionDetails.text}`);
  return result.value;
}

async function pollUntil(fn, timeoutMs, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await fn();
    if (last) return last;
    await Bun.sleep(intervalMs);
  }
  throw new Error(`timed out after ${timeoutMs}ms waiting for condition; last value: ${JSON.stringify(last)}`);
}

const MAX_LIST_ITEMS = 20;
const MAX_ITEM_CHARS = 1000;

function truncate(text) {
  if (text.length <= MAX_ITEM_CHARS) return text;
  return `${text.slice(0, MAX_ITEM_CHARS)}... [truncated, ${text.length} chars total]`;
}

/**
 * Bounded, best-effort diagnostics for whichever page was active when a step
 * (almost always a pollUntil) failed. Printed to stderr so CI shows it right
 * next to the failure, never thrown from: called from a catch block, so a
 * diagnostic that itself fails must not hide the real error.
 *
 * @param {ReturnType<typeof connect>} cdp
 * @param {{sessionId: string, recorder: import('../frontend/browser-harness/chrome.js').NetworkRecorder} | null} page
 * @param {string[]} consoleErrors - console.error/warn text seen on any page so far.
 * @param {string[]} exceptions - uncaught page exceptions seen on any page so far.
 */
async function diagnose(cdp, page, consoleErrors, exceptions) {
  const lines = ['--- diagnostics (bounded) ---'];
  if (!page) {
    lines.push('no page was open yet when this failed.');
  } else {
    try {
      const url = await evaluate(cdp, page.sessionId, 'window.location.href');
      lines.push(`page url: ${url}`);
    } catch (err) {
      lines.push(`page url: <could not read: ${err.message}>`);
    }

    try {
      const cells = await evaluate(
        cdp,
        page.sessionId,
        `Array.from(document.querySelectorAll('.jp-Cell')).map((cell) => ({
          prompt: (cell.querySelector('.jp-InputPrompt')?.textContent || '').trim(),
          output: (cell.querySelector('.jp-OutputArea')?.innerText || '').trim(),
        }))`
      );
      lines.push(`cells (${cells.length}, showing up to ${MAX_LIST_ITEMS}):`);
      for (const [i, cell] of cells.slice(0, MAX_LIST_ITEMS).entries()) {
        lines.push(`  [${i}] prompt=${JSON.stringify(cell.prompt)}`);
        if (cell.output) lines.push(`      output: ${truncate(cell.output).replace(/\n/g, '\n      ')}`);
      }
    } catch (err) {
      lines.push(`cells: <could not read: ${err.message}>`);
    }

    if (page.recorder) {
      const bad = Array.from(page.recorder.requests.values()).filter(
        (r) => r.failed || (typeof r.status === 'number' && r.status >= 400)
      );
      lines.push(`network requests that failed or returned 4xx/5xx (${bad.length}, showing up to ${MAX_LIST_ITEMS}):`);
      for (const r of bad.slice(0, MAX_LIST_ITEMS)) {
        lines.push(`  - [session ${r.sessionId}] ${r.url} -> status=${r.status ?? '(none)'}${r.failed ? ` failed=${r.failed}` : ''}`);
      }
      if (page.recorder.attachFailures.length > 0) {
        lines.push(`could not watch some workers' network (${page.recorder.attachFailures.length}):`);
        for (const f of page.recorder.attachFailures.slice(0, MAX_LIST_ITEMS)) lines.push(`  - ${f}`);
      }
    }
  }

  lines.push(`console errors/warnings seen this run (${consoleErrors.length}, showing up to ${MAX_LIST_ITEMS}):`);
  for (const line of consoleErrors.slice(0, MAX_LIST_ITEMS)) lines.push(`  - ${truncate(line)}`);

  lines.push(`page exceptions seen this run (${exceptions.length}, showing up to ${MAX_LIST_ITEMS}):`);
  for (const line of exceptions.slice(0, MAX_LIST_ITEMS)) lines.push(`  - ${truncate(line)}`);

  lines.push('--- end diagnostics ---');
  console.error(lines.join('\n'));
}

async function main() {
  const screenshotPath = process.argv[2] || null;
  const chromePath = findChrome();
  if (!chromePath) {
    if (process.env.CI) {
      console.error('FAIL: no Chrome found');
      return 1;
    }
    console.log('SKIP: no Chrome found');
    return 0;
  }

  const buildDir = mkdtempSync(join(tmpdir(), 'osa-notebook-site-'));
  // Serve first (files do not need to exist yet; each request reads the
  // directory fresh), so the build below can be told its own real origin.
  const server = serveStatic(buildDir);
  const origin = `http://127.0.0.1:${server.port}`;
  const base = `${origin}/${SITE_SUBDIR}`;
  await buildSite(buildDir, base);

  let failed = 0;
  const report = (ok, msg) => {
    if (ok) {
      console.log(`ok: ${msg}`);
    } else {
      failed++;
      console.error(`FAIL: ${msg}`);
    }
  };

  const profileDir = mkdtempSync(join(tmpdir(), 'osa-notebook-chrome-'));
  let chrome = null;
  let cdp = null;
  const exceptions = [];
  const consoleErrors = [];
  // Whichever page is currently active, so a failure anywhere in the flow
  // below (almost always a pollUntil) can be diagnosed against the right
  // page and the right worker-inclusive network recording, without every
  // call site having to say which page it was.
  let currentPage = null;

  try {
    const launched = await launch(chromePath, profileDir);
    chrome = launched.chrome;
    cdp = await connect(launched.wsUrl);

    async function openPage(url) {
      const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
      cdp.on((message) => {
        if (message.sessionId !== sessionId) return;
        if (message.method === 'Runtime.exceptionThrown') {
          exceptions.push(message.params.exceptionDetails.text);
        } else if (
          message.method === 'Runtime.consoleAPICalled' &&
          (message.params.type === 'error' || message.params.type === 'warning')
        ) {
          const text = message.params.args.map((a) => a.value ?? a.description ?? '').join(' ');
          consoleErrors.push(`[console.${message.params.type}] ${text}`);
        } else if (
          message.method === 'Log.entryAdded' &&
          (message.params.entry.level === 'error' || message.params.entry.level === 'warning')
        ) {
          consoleErrors.push(`[log:${message.params.entry.level}] ${message.params.entry.text}`);
        }
      });
      await cdp.send('Runtime.enable', {}, sessionId);
      await cdp.send('Log.enable', {}, sessionId);
      await cdp.send('Page.enable', {}, sessionId);
      // Pyodide runs in a Worker, so its own fetches (jsDelivr, this site's
      // wheels, zarr.nemar.org) never reach the page's own Network domain;
      // attachWithNetwork follows every worker the page spawns and folds
      // both into one recorder (frontend/browser-harness/chrome.js).
      const recorder = new NetworkRecorder();
      await attachWithNetwork(cdp, sessionId, recorder);
      await cdp.send('Page.navigate', { url }, sessionId);
      const page = { targetId, sessionId, recorder };
      currentPage = page;
      return page;
    }

    async function waitForNotebookReady(sessionId, timeoutMs) {
      await pollUntil(
        () => evaluate(cdp, sessionId, "typeof window.jupyterapp !== 'undefined' && !!window.jupyterapp.shell.currentWidget"),
        timeoutMs,
        300
      );
    }

    function runAllCells(sessionId) {
      // Fire-and-forget: see evaluate()'s own comment.
      return evaluate(
        cdp,
        sessionId,
        "window.jupyterapp.commands.execute('notebook:run-all-cells'); true"
      );
    }

    async function promptsDone(sessionId) {
      return evaluate(
        cdp,
        sessionId,
        `(() => {
          const prompts = Array.from(document.querySelectorAll('.jp-InputPrompt')).map((el) => el.textContent);
          const codePrompts = prompts.filter((p) => p.includes('['));
          const busy = codePrompts.some((p) => p.includes('*'));
          const numbered = codePrompts.filter((p) => /\\[\\d+\\]/.test(p));
          return !busy && numbered.length >= 3;
        })()`
      );
    }

    async function sentinelPresent(sessionId) {
      return evaluate(cdp, sessionId, `document.body.innerText.includes(${JSON.stringify(SENTINEL)})`);
    }

    // Steps 1-4 below are wrapped so ANY failure in them (almost always a
    // pollUntil timeout, but any thrown error works the same way) prints
    // bounded diagnostics for whichever page was active, before the error
    // still propagates and this process still exits non-zero exactly as
    // before -- this only adds a diagnosis, it never changes the verdict.
    try {
      // --- 1. Cold run: open the dataset link, run all cells, check the result ---
      log('cold run: opening the dataset link');
      const coldStart = Date.now();
      const first = await openPage(`${base}/open.html?community=${COMMUNITY}&dataset=${DATASET}`);
      await pollUntil(
        () => evaluate(cdp, first.sessionId, "window.location.pathname.includes('/notebooks/index.html')"),
        15_000,
        300
      );
      await waitForNotebookReady(first.sessionId, 30_000);
      await runAllCells(first.sessionId);
      await pollUntil(
        async () => (await sentinelPresent(first.sessionId)) && (await promptsDone(first.sessionId)),
        120_000,
        500
      );
      const coldSeconds = (Date.now() - coldStart) / 1000;
      log(`cold: ${coldSeconds.toFixed(1)}s`);

      const indexLineOk = await evaluate(
        cdp,
        first.sessionId,
        "document.body.innerText.includes('recordings with a Zarr copy')"
      );
      report(indexLineOk, 'the index line ("N recordings with a Zarr copy") is present');

      const readLineOk = await sentinelPresent(first.sessionId);
      report(readLineOk, `the read line ("${SENTINEL}") is present`);

      const figureCount = await evaluate(
        cdp,
        first.sessionId,
        "document.querySelectorAll('.jp-OutputArea-output img').length"
      );
      report(figureCount === 1, `exactly one rendered figure (got ${figureCount})`);

      const noTraceback = await evaluate(cdp, first.sessionId, "!document.body.innerText.includes('Traceback')");
      report(noTraceback, 'no error output (no "Traceback" anywhere on the page)');

      if (screenshotPath) {
        // Scroll the figure into view first: a bare captureScreenshot only sees
        // whatever the viewport happened to be scrolled to when the last cell
        // finished, which is usually its own source, not its rendered output.
        await evaluate(
          cdp,
          first.sessionId,
          "document.querySelector('.jp-OutputArea-output img')?.scrollIntoView({block: 'center'})"
        );
        await Bun.sleep(300);
        const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, first.sessionId);
        await Bun.write(screenshotPath, Buffer.from(data, 'base64'));
        log(`screenshot saved to ${screenshotPath}`);
      }

      // --- 2. Edit a cell, save, and prove a second open does not overwrite it ---
      log('editing the intro cell and saving');
      const marker = `EDIT_MARKER_${Date.now()}`;
      await evaluate(
        cdp,
        first.sessionId,
        `(() => {
          const panel = window.jupyterapp.shell.currentWidget;
          const cell = panel.content.model.cells.get(0);
          cell.sharedModel.setSource(cell.sharedModel.getSource() + '\\n\\n${marker}');
          return true;
        })()`
      );
      await evaluate(cdp, first.sessionId, "window.jupyterapp.commands.execute('docmanager:save'); true");
      // docmanager:save's own promise is not awaited either, for the same reason
      // as run-all-cells; poll the model's own dirty flag instead of a fixed sleep.
      await pollUntil(
        () => evaluate(cdp, first.sessionId, '!window.jupyterapp.shell.currentWidget.context.model.dirty'),
        15_000,
        300
      );

      log('warm run: opening the same dataset link again');
      const warmStart = Date.now();
      const second = await openPage(`${base}/open.html?community=${COMMUNITY}&dataset=${DATASET}`);
      await pollUntil(
        () => evaluate(cdp, second.sessionId, "window.location.pathname.includes('/notebooks/index.html')"),
        15_000,
        300
      );
      await waitForNotebookReady(second.sessionId, 30_000);
      const warmSeconds = (Date.now() - warmStart) / 1000;
      log(`warm (open, no re-run): ${warmSeconds.toFixed(1)}s`);

      const editSurvived = await evaluate(
        cdp,
        second.sessionId,
        `document.body.innerText.includes(${JSON.stringify(marker)})`
      );
      report(editSurvived, 'the edit survived opening the same dataset again (no overwrite)');

      // --- 3. Re-run in the warm profile, to measure a genuinely warm cell run too ---
      const rerunStart = Date.now();
      await runAllCells(second.sessionId);
      await pollUntil(
        async () => (await sentinelPresent(second.sessionId)) && (await promptsDone(second.sessionId)),
        60_000,
        500
      );
      const warmRerunSeconds = (Date.now() - rerunStart) / 1000;
      log(`warm (re-run all cells): ${warmRerunSeconds.toFixed(1)}s`);

      // --- 4. Refusals: unknown community and malformed dataset write nothing ---
      log('checking refusals');
      // The placeholder text ("Opening your notebook...") is itself non-empty, so
      // the poll condition has to wait for it to CHANGE, not merely exist.
      const refusalShown = async (targetSessionId) => {
        const text = await evaluate(cdp, targetSessionId, "document.getElementById('app').textContent");
        return text && !text.includes('Opening') ? text : null;
      };

      const unknownCommunity = await openPage(`${base}/open.html?community=doesnotexist&dataset=nm000103`);
      const unknownMsg = await pollUntil(() => refusalShown(unknownCommunity.sessionId), 10_000, 300);
      report(
        unknownMsg.length > 0,
        `an unknown community shows a plain sentence and stays put (got: ${JSON.stringify(unknownMsg)})`
      );

      const badDataset = await openPage(`${base}/open.html?community=${COMMUNITY}&dataset=not-a-real-id`);
      const badMsg = await pollUntil(() => refusalShown(badDataset.sessionId), 10_000, 300);
      report(
        badMsg.length > 0,
        `a malformed dataset id shows a plain sentence and stays put (got: ${JSON.stringify(badMsg)})`
      );

      // Neither refusal should have written anything: the community/dataset pair
      // used above for the real run is the only key that should exist.
      // JupyterLite's own resolved baseUrl at this site's own path prefix (see
      // resolveBaseUrl in open.js and docs/adr/0011-the-notebook-site.md).
      const dbName = await evaluate(cdp, unknownCommunity.sessionId, `'JupyterLite Storage - /${SITE_SUBDIR}/'`);
      const badCommunityWroteNothing = await evaluate(
        cdp,
        unknownCommunity.sessionId,
        `(async () => {
          const req = indexedDB.open(${JSON.stringify(dbName)});
          return await new Promise((resolve) => {
            req.onsuccess = () => {
              const db = req.result;
              if (!db.objectStoreNames.contains('files')) { resolve(true); return; }
              const tx = db.transaction('files', 'readonly');
              const getReq = tx.objectStore('files').get('doesnotexist');
              getReq.onsuccess = () => resolve(getReq.result === undefined);
              getReq.onerror = () => resolve(true);
            };
            req.onerror = () => resolve(true);
          });
        })()`
      );
      report(badCommunityWroteNothing, 'the unknown-community link wrote no directory entry to IndexedDB');

      report(exceptions.length === 0, `no page exceptions (got ${exceptions.length}: ${exceptions.join(' | ')})`);

      console.log('');
      console.log(`Timings: cold ${coldSeconds.toFixed(1)}s, warm-open ${warmSeconds.toFixed(1)}s, warm-rerun ${warmRerunSeconds.toFixed(1)}s`);
      console.log(`${failed === 0 ? 'ALL CHECKS PASSED' : `${failed} CHECK(S) FAILED`}`);
      return failed === 0 ? 0 : 1;
    } catch (err) {
      await diagnose(cdp, currentPage, consoleErrors, exceptions);
      throw err;
    }
  } finally {
    if (cdp) cdp.close();
    if (chrome) {
      chrome.kill();
      await chrome.exited;
    }
    server.stop(true);
    rmSync(profileDir, { recursive: true, force: true });
    rmSync(buildDir, { recursive: true, force: true });
  }
}

if (import.meta.main) {
  process.exit(await main());
}
