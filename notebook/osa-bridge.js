/**
 * The notebook page's side of the chat widget's notebook tab (issue #470,
 * docs/adr/0011-the-notebook-site.md). scripts/build_notebook_site.py adds this
 * script to notebooks/index.html; it drives JupyterLite through the app the
 * build exposes as window.jupyterapp.
 *
 * It does two things:
 *
 * 1. Runs a starter's setup cells (code cells tagged "osa-autorun") when the
 *    notebook opens, and again after the kernel restarts, so a reader never
 *    has to know a setup cell exists.
 * 2. When the notebook is embedded (the widget's tab), talks to the page that
 *    embeds it: it reports when the notebook is ready and how setup went, and
 *    applies the light or dark theme the widget sends.
 *
 * Messages from this page:   { source: 'osa-notebook', type: 'ready' }
 *                            { source: 'osa-notebook', type: 'setup', status: 'running' | 'done' | 'error' | 'none' }
 *                            { source: 'osa-notebook', type: 'theme', scheme: 'light' | 'dark' }
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

  let setupRun = null;

  // notebook:run-cell runs the active cell and settles when it finishes, so
  // each setup cell is made active in turn and awaited. Afterwards the cell
  // after the last setup cell is made active, so Shift+Enter carries on from
  // where the starter wants the reader to begin.
  function runSetup(app, panel) {
    if (setupRun) return setupRun;
    setupRun = (async () => {
      const notebook = panel.content;
      const indices = autorunIndices(notebook.model);
      if (indices.length === 0) {
        post({ type: 'setup', status: 'none' });
        return;
      }
      post({ type: 'setup', status: 'running' });
      try {
        await panel.sessionContext.ready;
        for (const index of indices) {
          notebook.activeCellIndex = index;
          await app.commands.execute('notebook:run-cell');
        }
        const last = indices[indices.length - 1];
        if (notebook.activeCellIndex === last && last + 1 < notebook.model.cells.length) {
          notebook.activeCellIndex = last + 1;
        }
        const failed = indices.some((index) => hasError(notebook.model.cells.get(index)));
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
  // The theme is reported to the widget only once the page shows it.
  async function applyTheme(app, scheme) {
    const theme = THEMES[scheme];
    if (!theme) return;
    const followsDevice = () => app.commands.isToggled('apputils:adaptive-theme');
    try {
      if (followsDevice()) {
        await app.commands.execute('apputils:adaptive-theme');
        await waitFor(() => !followsDevice(), 5_000, '"follow the device" turning off');
      }
      await app.commands.execute('apputils:change-theme', { theme });
      await waitFor(() => document.body.dataset.jpThemeName === theme, 5_000, `the ${theme} theme`);
      post({ type: 'theme', scheme });
    } catch (err) {
      warn(`the ${scheme} theme was not applied`, err);
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

    let restarting = false;
    panel.sessionContext.statusChanged.connect((_, status) => {
      if (status === 'restarting' || status === 'autorestarting') restarting = true;
      else if (restarting && status === 'idle') {
        restarting = false;
        runSetup(app, panel);
      }
    });
    await runSetup(app, panel);
  })().catch((err) => warn('the notebook bridge did not start', err));
})();
