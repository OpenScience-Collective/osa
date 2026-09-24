#!/usr/bin/env bun
/**
 * The full notebook-site flow, in real, headless Chrome (issue #453,
 * docs/adr/0011-the-notebook-site.md). Reads the environment's real, live Zarr
 * host (zarr.nemar.org, or zarr-test.nemar.org for develop), the same reason
 * frontend/browser-harness/widget_e2e.py is a manual/workflow_dispatch check
 * rather than a required gate (see notebook/README.md).
 *
 * Reuses frontend/browser-harness/chrome.js's Chrome-driving primitives
 * (findChrome, launch, connect) rather than duplicating them.
 *
 * Usage: bun notebook/e2e-check.js [--environment production|develop] [screenshot-path]
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { attachWithNetwork, connect, findChrome, launch, NetworkRecorder } from '../frontend/browser-harness/chrome.js';

const COMMUNITY = 'nemar';
// One dataset per environment, each present only on that environment's Zarr host,
// so a build that fills in the wrong zarr_base fails here rather than in a reader's
// browser. SENTINEL never appears in the starter's source, only in its runtime
// output: window.data.shape, window.unit and window.rate for the first two seconds
// of four channels of the dataset's first store.
const ENVIRONMENTS = {
  production: { dataset: 'nm000103', sentinel: '(4, 500) uV 250.0' },
  develop: { dataset: 'xx099903', sentinel: '(4, 2000) mV 1000.0' },
};

const USAGE = 'usage: bun notebook/e2e-check.js [--environment production|develop] [screenshot-path]';

// Strict on purpose: an unrecognized option (a typo, or a flag this script does
// not have) must fail, never be taken as the screenshot path while the check
// quietly runs against production instead of the environment that was meant.
function parseArgs(argv) {
  let environment = 'production';
  let screenshotPath = null;
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--environment') {
      environment = argv[++i];
    } else if (arg.startsWith('--environment=')) {
      environment = arg.slice('--environment='.length);
    } else if (arg.startsWith('-')) {
      throw new Error(`unrecognized option ${arg}; ${USAGE}`);
    } else if (screenshotPath === null) {
      screenshotPath = arg;
    } else {
      throw new Error(`unexpected extra argument ${arg}; ${USAGE}`);
    }
  }
  if (!Object.hasOwn(ENVIRONMENTS, environment)) {
    throw new Error(`--environment must be one of ${Object.keys(ENVIRONMENTS).join(', ')}, got ${environment}`);
  }
  return { environment, screenshotPath };
}

const ARGS = parseArgs(process.argv.slice(2));
const DATASET = ENVIRONMENTS[ARGS.environment].dataset;
// OSC's naming rule: a subdomain is a plane serving several projects, and the
// project is the path (api.osc.earth/osa, widget.osc.earth/osa, ...), so the
// real site is notebook.osc.earth/osa, not the bare host -- and this check
// builds and serves under that same prefix so it matches production exactly.
const SITE_SUBDIR = 'osa';
const SENTINEL = ENVIRONMENTS[ARGS.environment].sentinel;
// Matches open.js's own notebookPath(community, dataset); this is the one
// file whose save the edit/reopen step below has to confirm.
const NOTEBOOK_PATH = `${COMMUNITY}/${DATASET}.ipynb`;

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
  log(`building the site into ${outputDir} (--site-url ${siteUrl}, --environment ${ARGS.environment})`);
  const proc = Bun.spawn(
    [
      'uv',
      'run',
      'python',
      'scripts/build_notebook_site.py',
      '--site-url',
      siteUrl,
      '--environment',
      ARGS.environment,
      '--output-dir',
      outputDir,
    ],
    { stdout: 'inherit', stderr: 'inherit' }
  );
  const code = await proc.exited;
  if (code !== 0) throw new Error(`build failed with exit code ${code}`);
}

/** The rules in the build's own `_headers` (Cloudflare Pages' format: a path
 * pattern line, then indented `Name: value` lines), so this server sends what a
 * deployment sends. Cache-Control is left out: this server never lets Chrome
 * cache, so a warm run measures storage, not the HTTP cache. */
function parseHeaders(text) {
  const rules = [];
  let current = null;
  for (const line of text.split('\n')) {
    if (line.startsWith('/')) {
      const pattern = line.trim().replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
      current = { match: new RegExp(`^${pattern}$`), headers: {} };
      rules.push(current);
    } else if (current && /^\s+\S/.test(line)) {
      const colon = line.indexOf(':');
      const name = line.slice(0, colon).trim();
      if (name.toLowerCase() !== 'cache-control') current.headers[name] = line.slice(colon + 1).trim();
    }
  }
  return rules;
}

/** A minimal static file server for the built site: application/wasm for .wasm,
 * since Pyodide's OWN interpreter loads from jsDelivr, but a locally-served
 * .wasm anywhere in the tree needs the right type if a plain guess-by-extension
 * server gets it wrong. It applies the build's `_headers` once the build has
 * written them, so frame-ancestors is enforced here as it is when deployed. */
function serveStatic(root) {
  let rules = null;
  return Bun.serve({
    hostname: '127.0.0.1',
    port: 0,
    async fetch(request) {
      const { pathname } = new URL(request.url);
      if (rules === null) {
        const headersFile = Bun.file(join(root, '_headers'));
        if (await headersFile.exists()) rules = parseHeaders(await headersFile.text());
      }
      const rel = pathname === '/' ? '/index.html' : pathname;
      const file = new URL(`.${rel}`, `file://${root}/`);
      const found = Bun.file(file);
      if (!(await found.exists())) return new Response('Not Found', { status: 404 });
      const type = rel.endsWith('.wasm') ? 'application/wasm' : found.type;
      const headers = { 'Content-Type': type, 'Cache-Control': 'no-store' };
      for (const rule of rules ?? []) if (rule.match.test(pathname)) Object.assign(headers, rule.headers);
      return new Response(found, { headers });
    },
  });
}

/** A page on another site that embeds `?src=` in an iframe, the way the chat
 * widget's notebook tab will, and records every message the frame posts in
 * window.__messages. */
function serveHost(hostname) {
  const page = `<!doctype html><html><body style="margin:0">
<iframe id="notebook" style="width:1000px;height:760px;border:0"></iframe>
<script>
  window.__messages = [];
  const frame = document.getElementById('notebook');
  window.addEventListener('message', (event) => {
    if (event.source === frame.contentWindow) window.__messages.push(event.data);
  });
  window.__send = (message) => frame.contentWindow.postMessage(message, '*');
  frame.src = new URLSearchParams(location.search).get('src');
</script></body></html>`;
  return Bun.serve({
    hostname,
    port: 0,
    fetch: () => new Response(page, { headers: { 'Content-Type': 'text/html', 'Cache-Control': 'no-store' } }),
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

/** Like evaluate(), but DOES await a returned promise -- for a call that
 * always settles on its own (unlike notebook:run-all-cells or docmanager:
 * save's own command promises, which this app's session plumbing never
 * resolves; see evaluate()'s own comment). serviceManager.contents.get() is
 * an ordinary REST-shaped call against JupyterLite's own Contents drive and
 * always settles, so this is safe to await here specifically. */
async function evaluateAwaited(cdp, sessionId, expression) {
  const { result, exceptionDetails } = await cdp.send(
    'Runtime.evaluate',
    { expression, returnByValue: true, awaitPromise: true },
    sessionId
  );
  if (exceptionDetails) throw new Error(`page threw: ${exceptionDetails.text}`);
  return result.value;
}

/**
 * The `last_modified` JupyterLite's own Contents drive reports for `path`,
 * via window.jupyterapp.serviceManager.contents -- the SAME contents manager
 * a fresh page's notebook load reads through, so a change here is the
 * authoritative signal that a save actually reached storage, not just that
 * the document model's own dirty flag (a UI-level convenience) cleared.
 * Resolves to null if the path does not exist yet or the call fails.
 */
async function contentsLastModified(cdp, sessionId, path) {
  return evaluateAwaited(
    cdp,
    sessionId,
    `window.jupyterapp.serviceManager.contents.get(${JSON.stringify(path)}, { content: false })
      .then((model) => model.last_modified)
      .catch(() => null)`
  );
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

// window.jupyterapp's current notebook panel's own sessionContext -- the same
// object a person reads the kernel indicator from in the top-right of the
// notebook. `session` is null until a kernel is requested; `session.kernel.
// status` moves through 'starting' (or 'unknown') to 'idle' once the kernel
// has actually come up and can accept execution. Shared between main()'s own
// waitForKernelIdle and diagnose() below, so both ask the page the same
// question the same way.
const KERNEL_INFO_EXPR = `(() => {
  const panel = window.jupyterapp?.shell?.currentWidget;
  const session = panel?.sessionContext?.session ?? null;
  const kernel = session?.kernel ?? null;
  return {
    hasPanel: !!panel,
    hasSession: !!session,
    kernelName: kernel?.name ?? null,
    kernelStatus: kernel?.status ?? null,
  };
})()`;

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
      const kernel = await evaluate(cdp, page.sessionId, KERNEL_INFO_EXPR);
      lines.push(
        `kernel: session=${kernel.hasSession} name=${JSON.stringify(kernel.kernelName)} ` +
          `status=${JSON.stringify(kernel.kernelStatus)}`
      );
    } catch (err) {
      lines.push(`kernel: <could not read: ${err.message}>`);
    }
    lines.push(
      page.workerTargets && page.workerTargets.length > 0
        ? `pyodide worker target(s) seen (${page.workerTargets.length}): ${page.workerTargets.slice(0, MAX_LIST_ITEMS).join(', ')}`
        : 'pyodide worker target: none seen yet'
    );

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
  const screenshotPath = ARGS.screenshotPath;
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
    // Cheap and always useful: a CI failure and a local pass can be two
    // different Chrome builds (the GitHub-hosted runner and a laptop are
    // rarely on the exact same release), so this is worth knowing without
    // cross-referencing the runner image manifest by hand every time.
    const { product: chromeVersion } = await cdp.send('Browser.getVersion');
    log(`Chrome: ${chromeVersion}`);

    // childTypes: which auto-attached children to follow. attachWithNetwork
    // pauses every child until it is followed, so a page that embeds the
    // notebook (a cross-site iframe, its own target) has to follow 'iframe'
    // too, or the frame never runs. Each frame's session lands in page.frames.
    async function openPage(url, { childTypes = ['worker'] } = {}) {
      const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
      // Pyodide's kernel runs in a Worker; whether one has even been spawned
      // yet is itself diagnostic (a kernel stuck 'starting' with no worker at
      // all is a different problem than one with a worker that never reaches
      // 'idle'). Target.attachedToTarget for a child of this page arrives on
      // THIS page's own session, same as attachWithNetwork below relies on.
      const workerTargets = [];
      const frames = [];
      cdp.on((message) => {
        if (message.method === 'Target.attachedToTarget' && message.sessionId === sessionId) {
          if (message.params.targetInfo.type === 'worker') workerTargets.push(message.params.targetInfo.url);
          if (message.params.targetInfo.type === 'iframe') frames.push(message.params.sessionId);
          return;
        }
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
      await attachWithNetwork(cdp, sessionId, recorder, childTypes);
      await cdp.send('Page.navigate', { url }, sessionId);
      const page = { targetId, sessionId, recorder, workerTargets, frames };
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

    async function kernelInfo(sessionId) {
      return evaluate(cdp, sessionId, KERNEL_INFO_EXPR);
    }

    /**
     * The race PR review found in CI (#465): notebook:run-all-cells queues
     * nothing if it fires before the kernel is up, and on a fast machine the
     * kernel is ready before anyone would notice the gap. This is that gate,
     * checked the way a person would -- the same sessionContext the kernel
     * indicator in the notebook's own toolbar reads.
     */
    async function waitForKernelIdle(sessionId, timeoutMs) {
      try {
        await pollUntil(async () => (await kernelInfo(sessionId)).kernelStatus === 'idle', timeoutMs, 300);
      } catch (err) {
        const info = await kernelInfo(sessionId).catch(() => null);
        throw new Error(
          `the notebook's kernel never reached 'idle' within ${timeoutMs / 1000}s ` +
            `(last seen: ${JSON.stringify(info)}): ${err.message}`
        );
      }
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

    /** True once Run All has queued its cells: the LAST code prompt is no
     * longer the never-executed placeholder '[ ]:' (queued '[*]:' and
     * numbered '[n]:' both count). The last one, because the setup cell has
     * already run by itself by now, so "any prompt moved" would be true before
     * Run All did anything. Distinct from promptsDone, which requires EVERY
     * prompt numbered and none busy, so a slow run and a run that queued
     * nothing are told apart. */
    async function runStarted(sessionId) {
      return evaluate(
        cdp,
        sessionId,
        `(() => {
          const prompts = Array.from(document.querySelectorAll('.jp-InputPrompt')).map((el) => el.textContent.trim());
          const codePrompts = prompts.filter((p) => p.includes('['));
          return codePrompts.length > 0 && codePrompts[codePrompts.length - 1] !== '[ ]:';
        })()`
      );
    }

    /** The first code cell (the starter's setup cell): its prompt and output. */
    async function setupCell(sessionId) {
      return evaluate(
        cdp,
        sessionId,
        `(() => {
          const cell = document.querySelector('.jp-CodeCell');
          return cell && {
            prompt: (cell.querySelector('.jp-InputPrompt')?.textContent || '').trim(),
            output: (cell.querySelector('.jp-OutputArea')?.innerText || '').trim(),
          };
        })()`
      );
    }

    async function setupReady(sessionId, prompt) {
      const cell = await setupCell(sessionId);
      return !!cell && cell.prompt === prompt && cell.output.includes('Ready: eegprep-lean');
    }

    /**
     * Fire Run All, then confirm it actually queued something within 20s.
     * Deliberately NOT a retry: a blind re-fire of Run All would hide a real
     * "Run All does nothing" bug behind an apparent pass on the second try.
     * If nothing queued, this fails with that stated outright; only once
     * something has queued does the caller wait out the full run.
     */
    async function runAllCellsAndConfirmQueued(sessionId) {
      await runAllCells(sessionId);
      try {
        await pollUntil(() => runStarted(sessionId), 20_000, 300);
      } catch {
        throw new Error(
          "notebook:run-all-cells did not queue any cell within 20s of firing " +
            "(every code prompt is still the never-executed '[ ]:'); this looks like " +
            'Run All doing nothing, not a slow run, so it was not retried.'
        );
      }
    }

    /** In the notebook's own outputs, not only the page's text: JupyterLab renders
     *  the cells in view, so once the cells after it scroll the read cell out of
     *  view, its output is in the model but no longer in the DOM. */
    /** Per code cell, from the notebook model: how many figures it rendered, and
     *  any error it raised. The page cannot say either reliably once the notebook
     *  is longer than the viewport, for the same reason as the sentinel below. */
    async function cellOutputs(sessionId) {
      return evaluate(
        cdp,
        sessionId,
        `(() => {
          const cells = window.jupyterapp.shell.currentWidget.content.model.cells;
          const found = [];
          for (let i = 0; i < cells.length; i++) {
            const cell = cells.get(i).toJSON();
            if (cell.cell_type !== 'code') continue;
            const outputs = cell.outputs || [];
            found.push({
              index: i,
              images: outputs.filter((o) => o.data && o.data['image/png']).length,
              errors: outputs.filter((o) => o.output_type === 'error').map((o) => o.ename + ': ' + o.evalue),
            });
          }
          return found;
        })()`
      );
    }

    async function sentinelPresent(sessionId) {
      const sentinel = JSON.stringify(SENTINEL);
      return evaluate(
        cdp,
        sessionId,
        `(() => {
          const cells = window.jupyterapp?.shell?.currentWidget?.content?.model?.cells;
          if (cells) {
            for (let i = 0; i < cells.length; i++) {
              if (JSON.stringify(cells.get(i).toJSON().outputs || []).includes(${sentinel})) return true;
            }
          }
          return document.body.innerText.includes(${sentinel});
        })()`
      );
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
      // Nothing has been run yet: osa-bridge.js runs the setup cell by itself.
      const setupRanByItself = await pollUntil(() => setupReady(first.sessionId, '[1]:'), 120_000, 300).catch(() => false);
      const setupSeconds = (Date.now() - coldStart) / 1000;
      report(setupRanByItself, `the setup cell ran by itself and printed its Ready line (${setupSeconds.toFixed(1)}s after opening)`);
      if (!setupRanByItself) throw new Error(`the setup cell never ran by itself: ${JSON.stringify(await setupCell(first.sessionId))}`);
      await waitForKernelIdle(first.sessionId, 90_000);
      await runAllCellsAndConfirmQueued(first.sessionId);
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

      const outputs = await cellOutputs(first.sessionId);
      const figures = outputs.map((cell) => cell.images);
      report(
        figures.some((n) => n > 0) && figures.every((n) => n <= 1),
        `a rendered figure, and no cell rendering one twice (figures per code cell: ${JSON.stringify(figures)})`
      );

      const errors = outputs.flatMap((cell) => cell.errors.map((e) => `cell ${cell.index}: ${e}`));
      const noTraceback = await evaluate(cdp, first.sessionId, "!document.body.innerText.includes('Traceback')");
      report(
        errors.length === 0 && noTraceback,
        `no error output (${errors.length ? errors.join('; ') : noTraceback ? 'none' : 'a "Traceback" on the page'})`
      );

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
      // Captured before the edit, so "the save reached storage" can be told
      // apart from "the file already looked like this" -- last_modified must
      // move, not merely be non-null.
      const beforeSaveModified = await contentsLastModified(cdp, first.sessionId, NOTEBOOK_PATH);
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
      // as run-all-cells. Two confirmations, cheapest first: the document
      // model's own dirty flag (a UI-level convenience, usually already
      // false by the time this is checked) is necessary but was not
      // sufficient under CPU throttling in testing, since it can clear before
      // the underlying storage write is actually visible to a DIFFERENT
      // page's read; the contents manager's own reported last_modified
      // actually moving is the authoritative signal, since that is read
      // through the exact same Contents drive a second page's notebook load
      // uses.
      await pollUntil(
        () => evaluate(cdp, first.sessionId, '!window.jupyterapp.shell.currentWidget.context.model.dirty'),
        15_000,
        300
      );
      await pollUntil(
        async () => {
          const modified = await contentsLastModified(cdp, first.sessionId, NOTEBOOK_PATH);
          return modified !== null && modified !== beforeSaveModified;
        },
        15_000,
        300
      );

      // An edit with no Save reaches storage by itself: the build sets autosave
      // to every 5 seconds (JupyterLab's default is 2 minutes).
      const autosaveMarker = `AUTOSAVE_MARKER_${Date.now()}`;
      const beforeAutosave = await contentsLastModified(cdp, first.sessionId, NOTEBOOK_PATH);
      await evaluate(
        cdp,
        first.sessionId,
        `(() => {
          const cell = window.jupyterapp.shell.currentWidget.content.model.cells.get(0);
          cell.sharedModel.setSource(cell.sharedModel.getSource() + '\\n\\n${autosaveMarker}');
          return true;
        })()`
      );
      const autosaved = await pollUntil(
        async () => {
          const modified = await contentsLastModified(cdp, first.sessionId, NOTEBOOK_PATH);
          return modified !== null && modified !== beforeAutosave;
        },
        20_000,
        300
      ).catch(() => false);
      report(autosaved, 'an edit with no Save reached storage within 20s (autosave)');

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

      // waitForNotebookReady only confirms the notebook WIDGET exists, not
      // that its cell views have finished mounting their editor DOM -- under
      // CPU throttling those can lag behind, so this is a real condition
      // wait (up to 15s), not a single read: a timeout here means the edit
      // genuinely never showed up, reported as a failure like any other,
      // not silently swallowed.
      const editSurvived = await pollUntil(
        () => evaluate(cdp, second.sessionId, `document.body.innerText.includes(${JSON.stringify(marker)})`),
        15_000,
        300
      ).catch(() => false);
      report(editSurvived, 'the edit survived opening the same dataset again (no overwrite)');
      const autosaveSurvived = await evaluate(
        cdp,
        second.sessionId,
        `document.body.innerText.includes(${JSON.stringify(autosaveMarker)})`
      );
      report(autosaveSurvived, 'the autosaved edit survived too');

      // --- 3. Re-run in the warm profile, to measure a genuinely warm cell run too ---
      const rerunStart = Date.now();
      await waitForKernelIdle(second.sessionId, 90_000);
      await runAllCellsAndConfirmQueued(second.sessionId);
      await pollUntil(
        async () => (await sentinelPresent(second.sessionId)) && (await promptsDone(second.sessionId)),
        60_000,
        500
      );
      const warmRerunSeconds = (Date.now() - rerunStart) / 1000;
      log(`warm (re-run all cells): ${warmRerunSeconds.toFixed(1)}s`);

      // A restart starts a fresh Python, so the bridge runs setup again. The
      // setup cell ran twice on this page (by itself, then Run All), so its
      // prompt going back to [1] is the rerun, not the earlier output.
      const beforeRestart = await setupCell(second.sessionId);
      await evaluate(cdp, second.sessionId, 'window.jupyterapp.shell.currentWidget.sessionContext.restartKernel(); true');
      const rerunAfterRestart = await pollUntil(() => setupReady(second.sessionId, '[1]:'), 90_000, 300).catch(() => false);
      report(
        beforeRestart?.prompt !== '[1]:' && rerunAfterRestart,
        `a kernel restart ran the setup cell again (prompt ${beforeRestart?.prompt} before, [1]: after)`
      );

      // --- 4. Refusals: unknown community and malformed dataset write nothing ---
      log('checking refusals');
      // The placeholder text ("Opening your notebook...") is itself non-empty, so
      // the poll condition has to wait for it to CHANGE, not merely exist. And
      // right after Page.navigate, the target can still be showing about:blank
      // (no #app at all yet) before open.html's own document has loaded, so the
      // read itself has to be optional-chained rather than assume the element
      // is already there -- a page that has not loaded yet is exactly what
      // pollUntil is for, not a crash.
      const refusalShown = async (targetSessionId) => {
        const text = await evaluate(cdp, targetSessionId, "document.getElementById('app')?.textContent ?? null");
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

      // --- 5. Embedded: the notebook as the chat widget's tab ---
      // Each host page is served from another site than the notebook's own
      // (127.0.0.1), so the frame is cross-site: frame-ancestors decides whether
      // it loads, and a loaded one runs with third-party (partitioned) storage.
      // Loopback may embed a develop build only, so production checks only that
      // loopback is refused.
      const notebookLink = `${base}/open.html?community=${COMMUNITY}&dataset=${DATASET}`;
      const allowedHost = ARGS.environment === 'develop' ? serveHost('localhost') : null;
      const refusedHost = ARGS.environment === 'develop' ? serveHost('::1') : serveHost('localhost');
      const refusedOrigin =
        ARGS.environment === 'develop' ? `http://[::1]:${refusedHost.port}` : `http://localhost:${refusedHost.port}`;
      const messagesOf = (page) => evaluate(cdp, page.sessionId, 'window.__messages ?? []');
      const hasMessage = async (page, type, fields = {}) =>
        (await messagesOf(page)).some(
          (m) => m?.source === 'osa-notebook' && m.type === type && Object.entries(fields).every(([k, v]) => m[k] === v)
        );
      let embedSeconds = null;
      try {
        if (allowedHost) {
          log('embedded: opening the notebook in a frame on an allowed site');
          const embedStart = Date.now();
          const host = await openPage(
            `http://localhost:${allowedHost.port}/host.html?src=${encodeURIComponent(notebookLink)}`,
            { childTypes: ['worker', 'iframe'] }
          );
          const setupDone = await pollUntil(
            async () => (await hasMessage(host, 'ready')) && (await hasMessage(host, 'setup', { status: 'done' })),
            120_000,
            300
          ).catch(() => false);
          embedSeconds = (Date.now() - embedStart) / 1000;
          report(setupDone, `framed on an allowed site, the notebook reported ready and setup done (${embedSeconds.toFixed(1)}s)`);

          const frameSession = host.frames.at(-1);
          report(!!frameSession, 'the framed notebook is its own cross-site frame');
          if (frameSession) {
            const setDevice = (value) =>
              cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value }] }, frameSession);
            const shows = (scheme) =>
              evaluate(cdp, frameSession, `document.body.dataset.jpThemeLight === '${scheme === 'light' ? 'true' : 'false'}'`);
            const sendTheme = async (scheme) => {
              const before = (await messagesOf(host)).length;
              await evaluate(cdp, host.sessionId, `window.__send({ target: 'osa-notebook', type: 'theme', scheme: '${scheme}' }); true`);
              return pollUntil(
                async () => {
                  const acked = (await messagesOf(host))
                    .slice(before)
                    .some((m) => m?.source === 'osa-notebook' && m.type === 'theme' && m.scheme === scheme && m.applied === true);
                  return acked && (await shows(scheme));
                },
                15_000,
                300
              ).catch(() => false);
            };
            // Opened on its own the notebook follows the device, so on a light
            // device it starts light. The widget's FIRST message is the one
            // JupyterLab drops if "follow the device" is still on (measured:
            // the theme change then only turns that setting off), so the first
            // one sent differs from what the device shows.
            await setDevice('light');
            const followsDevice = await pollUntil(() => shows('light'), 10_000, 300).catch(() => false);
            report(followsDevice, 'before any message, the framed notebook follows the device (light)');
            report(await sendTheme('dark'), "the widget's first theme message (dark, on a light device) was applied inside the frame");
            report(await sendTheme('light'), "the widget's next theme message (light) was applied inside the frame");
            await setDevice('dark');
            await Bun.sleep(2_000);
            report(await shows('light'), 'after the widget chose light, the device switching to dark left the notebook light');
          }
        }

        log(`embedded: a site that may not embed the notebook (${refusedOrigin})`);
        const refused = await openPage(`${refusedOrigin}/host.html?src=${encodeURIComponent(notebookLink)}`, {
          childTypes: ['worker', 'iframe'],
        });
        // Silence only means refusal if the host page itself loaded and pointed
        // its frame at the notebook; a host page that never loaded is silent too.
        const hostLoaded = await pollUntil(
          () =>
            evaluate(
              cdp,
              refused.sessionId,
              "Array.isArray(window.__messages) && document.getElementById('notebook')?.src.includes('/open.html')"
            ),
          15_000,
          300
        ).catch(() => false);
        report(hostLoaded, `the page on ${refusedOrigin} loaded and pointed its frame at the notebook`);
        await Bun.sleep(15_000);
        const refusedMessages = await messagesOf(refused);
        report(
          refusedMessages.length === 0,
          `framed on ${refusedOrigin}, the notebook never loaded (messages: ${JSON.stringify(refusedMessages)})`
        );
      } finally {
        allowedHost?.stop(true);
        refusedHost.stop(true);
      }

      report(exceptions.length === 0, `no page exceptions (got ${exceptions.length}: ${exceptions.join(' | ')})`);

      console.log('');
      console.log(
        `Timings: setup-by-itself ${setupSeconds.toFixed(1)}s, cold ${coldSeconds.toFixed(1)}s, ` +
          `warm-open ${warmSeconds.toFixed(1)}s, warm-rerun ${warmRerunSeconds.toFixed(1)}s` +
          (embedSeconds === null ? '' : `, embedded ${embedSeconds.toFixed(1)}s`)
      );
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
