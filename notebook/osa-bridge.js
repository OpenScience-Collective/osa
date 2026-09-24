/**
 * The notebook page's side of the chat widget's notebook tab (issue #470,
 * docs/adr/0011-the-notebook-site.md). scripts/build_notebook_site.py adds this
 * script to notebooks/index.html; it drives JupyterLite through the app the
 * build exposes as window.jupyterapp.
 *
 * It does three things:
 *
 * 1. Runs a starter's setup cells (code cells tagged "osa-autorun") when the
 *    notebook opens, and again after the kernel restarts, so a reader never
 *    has to know a setup cell exists.
 * 2. Before those, in every new kernel, makes a request that fails raise in
 *    the cell that awaited it. In Safari it otherwise never returns, and the
 *    kernel stays busy for good (#496; see REJECTION_GUARD below).
 * 3. When the notebook is embedded (the widget's tab), talks to the page that
 *    embeds it: it reports when the notebook is ready and how setup went, and
 *    applies the light or dark theme the widget sends.
 *
 * Messages from this page:   { source: 'osa-notebook', type: 'ready' }
 *                            { source: 'osa-notebook', type: 'setup', status: 'running' | 'done' | 'error' | 'none' }
 *                            { source: 'osa-notebook', type: 'theme', scheme: 'light' | 'dark', applied: boolean }
 *                            { source: 'osa-notebook', type: 'error', phase: 'startup' }
 * Messages to this page:     { target: 'osa-notebook', type: 'theme', scheme: 'light' | 'dark' }
 *
 * Only the embedding page itself is listened to (event.source === window.parent).
 * The site's frame-ancestors policy already limits which pages can be that
 * parent, so no origin list is repeated here. What this page posts carries no
 * reader data, so it is sent to any origin.
 */
(() => {
  const AUTORUN_TAG = 'osa-autorun';
  const THEMES = { light: 'JupyterLab Light', dark: 'JupyterLab Dark' };
  const embedded = window.parent !== window;

  function post(message) {
    if (embedded) window.parent.postMessage({ source: 'osa-notebook', ...message }, '*');
  }

  function warn(what, err) {
    console.warn(`[osa-bridge] ${what}:`, err);
  }

  async function waitFor(read, timeoutMs, what) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const value = read();
      if (value) return value;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`${what} did not appear within ${timeoutMs / 1000}s`);
  }

  function autorunIndices(model) {
    const indices = [];
    for (let i = 0; i < model.cells.length; i++) {
      const cell = model.cells.get(i);
      const tags = cell.getMetadata('tags');
      if (cell.type === 'code' && Array.isArray(tags) && tags.includes(AUTORUN_TAG)) indices.push(i);
    }
    return indices;
  }

  function hasError(cell) {
    for (let i = 0; i < cell.outputs.length; i++) {
      if (cell.outputs.get(i).type === 'error') return true;
    }
    return false;
  }

  // Python that makes a rejected JavaScript promise raise in the coroutine
  // awaiting it. Safari's fetch rejects with a TypeError that has no `stack`,
  // which Pyodide 0.29.5 does not recognize as an error, so asyncio refuses it
  // and the await never returns: the cell stays "[*]" and every later cell
  // queues behind it. Measured in WebKit 26.6 on this site, 2026-09-24. Why it
  // works is explained once, at buildRejectionGuardSource() in
  // frontend/osa-egress.js, which the chat's runtime runs too; this copy must
  // equal it, and notebook/test-bridge.js fails when it does not.
  const REJECTION_GUARD = [
    'def _osa_guard_rejections():',
    '    import _pyodide._future_helper as helper',
    '    from pyodide.ffi import JsException',
    '',
    '    original = helper.set_exception',
    '    if getattr(original, "osa_rejection_guard", False):',
    '        return',
    '',
    '    def describe(value):',
    '        if value is None:',
    '            return "Error", "a promise was rejected with no reason"',
    '        try:',
    '            name = getattr(value, "name", None)',
    '            message = getattr(value, "message", None)',
    '            if not isinstance(message, str):',
    '                message = str(value)',
    '        except Exception:',
    '            name, message = None, "a promise was rejected with a value that could not be read"',
    '        return (name if isinstance(name, str) and name else "Error"), message',
    '',
    '    def set_exception(fut, val):',
    '        # asyncio takes an exception instance or class, and refuses the rest.',
    '        if not (isinstance(val, BaseException) or (isinstance(val, type) and issubclass(val, BaseException))):',
    '            val = JsException(*describe(val))',
    '        original(fut, val)',
    '',
    '    set_exception.osa_rejection_guard = True',
    '    helper.set_exception = set_exception',
    '',
    '',
    '_osa_guard_rejections()',
    'del _osa_guard_rejections',
  ].join('\n');

  // Silent, so it takes no execution count and shows nothing, and it leaves no
  // name behind in the reader's namespace. It is the kernel's and not the
  // starter's, so it runs for a notebook with no setup cells too.
  async function guardRejections(panel) {
    await panel.sessionContext.ready;
    const kernel = panel.sessionContext.session?.kernel;
    if (!kernel) throw new Error('the notebook has no kernel');
    const reply = await kernel.requestExecute({ code: REJECTION_GUARD, silent: true, store_history: false }).done;
    if (reply.content.status !== 'ok') throw new Error(`${reply.content.ename}: ${reply.content.evalue}`);
  }

  let setupRun = null;

  // notebook:run-cell runs the active cell of the current notebook and settles
  // when it finishes, so each setup cell is made active in turn and awaited.
  // A cell counts as run only if it has an execution count afterwards, which
  // is cleared first, since a reopened notebook already shows the count it was
  // saved with. Afterwards the cell after the last setup cell is made active,
  // so Shift+Enter carries on from where the starter wants the reader to begin.
  function runSetup(app, panel) {
    if (setupRun) return setupRun;
    setupRun = (async () => {
      // Sent first, so the kernel runs it before any setup cell. A kernel it
      // did not reach still runs the notebook; it only hangs, in Safari, on
      // the first request that fails, so this is a warning and not a failure.
      const guarded = guardRejections(panel).catch((err) =>
        warn('a failed request may hang this kernel in Safari: the rejection guard was not installed', err)
      );
      try {
        const notebook = panel.content;
        const indices = autorunIndices(notebook.model);
        if (indices.length === 0) {
          // Setup is over once the guard is, with or without cells to run.
          await guarded;
          post({ type: 'setup', status: 'none' });
          return;
        }
        post({ type: 'setup', status: 'running' });
        await panel.sessionContext.ready;
        await guarded;
        let ran = true;
        for (const index of indices) {
          if (app.shell.currentWidget !== panel) throw new Error('another widget became current');
          const cell = notebook.model.cells.get(index);
          cell.executionCount = null;
          notebook.activeCellIndex = index;
          await app.commands.execute('notebook:run-cell');
          if (cell.executionCount === null) ran = false;
        }
        const last = indices[indices.length - 1];
        if (notebook.activeCellIndex === last && last + 1 < notebook.model.cells.length) {
          notebook.activeCellIndex = last + 1;
        }
        const failed = !ran || indices.some((index) => hasError(notebook.model.cells.get(index)));
        post({ type: 'setup', status: failed ? 'error' : 'done' });
      } catch (err) {
        warn('the setup cells did not run', err);
        post({ type: 'setup', status: 'error' });
      }
    })().finally(() => {
      setupRun = null;
    });
    return setupRun;
  }

  // An explicit theme turns off "follow the device" (adaptive-theme) first:
  // with it on, apputils:change-theme only turns it off and returns, leaving
  // the current theme in place. The adaptive-theme command starts that
  // settings write without returning it, so the setting is read back until it
  // is off; otherwise change-theme would see it still on and switch it back.
  // The theme counts as applied only once the page shows it.
  async function applyTheme(app, scheme) {
    const theme = THEMES[scheme];
    if (!theme) return;
    const followsDevice = () => app.commands.isToggled('apputils:adaptive-theme');
    try {
      if (followsDevice()) {
        await app.commands.execute('apputils:adaptive-theme');
        await waitFor(() => !followsDevice(), 10_000, '"follow the device" turning off');
      }
      await app.commands.execute('apputils:change-theme', { theme });
      await waitFor(() => document.body.dataset.jpThemeName === theme, 10_000, `the ${theme} theme`);
      post({ type: 'theme', scheme, applied: true });
    } catch (err) {
      warn(`the ${scheme} theme was not applied`, err);
      post({ type: 'theme', scheme, applied: false });
    }
  }

  // One theme change at a time, in the order the widget sent them.
  let themeQueue = Promise.resolve();
  function queueTheme(app, scheme) {
    themeQueue = themeQueue.then(() => applyTheme(app, scheme));
    return themeQueue;
  }

  let appReady = null;
  let pendingScheme = null;

  window.addEventListener('message', (event) => {
    if (!embedded || event.source !== window.parent) return;
    const data = event.data;
    if (!data || data.target !== 'osa-notebook' || data.type !== 'theme') return;
    if (!Object.hasOwn(THEMES, data.scheme)) return;
    if (appReady) queueTheme(appReady, data.scheme);
    else pendingScheme = data.scheme;
  });

  (async () => {
    const app = await waitFor(() => window.jupyterapp, 60_000, 'window.jupyterapp');
    await app.restored;
    const panel = await waitFor(
      () => {
        const widget = app.shell.currentWidget;
        return widget?.content?.model && widget.sessionContext ? widget : null;
      },
      60_000,
      'the notebook panel'
    );
    await panel.context.ready;
    appReady = app;
    if (pendingScheme) await queueTheme(app, pendingScheme);
    post({ type: 'ready' });

    // A restart, or a new kernel, is a fresh Python, so setup runs again.
    let restarting = false;
    panel.sessionContext.statusChanged.connect((_, status) => {
      if (status === 'restarting' || status === 'autorestarting') restarting = true;
      else if (restarting && status === 'idle') {
        restarting = false;
        runSetup(app, panel);
      }
    });
    panel.sessionContext.kernelChanged.connect((_, change) => {
      if (change.oldValue && change.newValue) runSetup(app, panel);
    });
    await runSetup(app, panel);
  })().catch((err) => {
    warn('the notebook bridge did not start', err);
    post({ type: 'error', phase: 'startup' });
  });
})();
