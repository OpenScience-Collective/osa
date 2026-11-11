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
 * 4. When embedded, keeps the notebooks of the dataset (the starter, notebooks
 *    imported from a chat, blank ones) as named tabs and writes an import to
 *    storage: the page is cross-origin to the widget, so only this script can
 *    (#673, #676, docs/adr/0017-notebook-import-and-tabs.md; the pure rules are
 *    in osa-notebook-files.js).
 *
 * Messages from this page:   { source: 'osa-notebook', type: 'ready' }
 *                            { source: 'osa-notebook', type: 'setup', status: 'running' | 'done' | 'error' | 'none' }
 *                            { source: 'osa-notebook', type: 'theme', scheme: 'light' | 'dark', applied: boolean }
 *                            { source: 'osa-notebook', type: 'error', phase: 'startup' }
 *                            { source: 'osa-notebook', type: 'result', requestId, ok, path?, error? }
 *                            { source: 'osa-notebook', type: 'notebooks', items: [{path, title, kind, open, undoable?}], active }
 * Messages to this page:     { target: 'osa-notebook', type: 'theme', scheme: 'light' | 'dark' }
 *                            { target: 'osa-notebook', type: 'import-notebook' | 'run-all' | 'undo-import' | 'new-notebook'
 *                              | 'open-notebook' | 'rename-notebook' | 'close-notebook', requestId, ... }
 *                            (the fields of each are in notebook/README.md)
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
  // Where osa-notebook-files.js is, resolved now: document.currentScript is
  // only set while this script first runs.
  const FILES_URL = new URL('./osa-notebook-files.js', document.currentScript?.src || window.location.href).href;

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
    '    from pyodide.ffi import JsException, jsnull',
    '',
    '    original = helper.set_exception',
    '    if getattr(original, "osa_rejection_guard", False):',
    '        return',
    '',
    '    def describe(value):',
    '        # A reason left out arrives as None, and a null one as jsnull.',
    '        if value is None or value is jsnull:',
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

  const guardMissing = (err) =>
    warn('a failed request may hang this kernel in Safari: the rejection guard was not installed', err);

  // Sends the guard to the panel's kernel, and resolves once it is SENT, which
  // is all the ordering needs. JupyterLite 0.8.4 runs a kernel's requests one at
  // a time, in the order they arrive: every message waits on one async-mutex
  // per kernel, whose queue is first in, first out (processMsg in
  // packages/services/src/kernel/client.ts), and the page's kernel connection
  // queues what it sends first in, first out while it connects. So every
  // request sent after this one, the setup cells included, runs after it.
  //
  // Its reply is watched only for a warning. Awaiting it could hang setup:
  // when a request fails, JupyterLite cancels every request queued behind it,
  // and a cancelled request never gets a reply.
  //
  // Silent, so it takes no execution count and shows nothing, and it leaves no
  // name behind in the reader's namespace. It is the kernel's and not the
  // starter's, so it runs for a notebook with no setup cells too.
  async function sendRejectionGuard(panel) {
    await panel.sessionContext.ready;
    // From here to the request is one synchronous hop, so nothing the reader
    // does can reach the kernel in between. What could come first is a cell the
    // reader ran before this point. On opening there is none to speak of: the
    // guard is sent as the session becomes ready, and a probe sent at the first
    // moment the kernel's connection existed (polled every 5 ms) ran after it,
    // in Chrome 153 and WebKit 26.6 (measured 2026-09-24). After a restart there
    // can be: the guard goes out again once the kernel reports idle, so a cell
    // the reader ran while it restarted runs before the guard, unguarded, and
    // hangs in Safari if a request it makes fails.
    const kernel = panel.sessionContext.session?.kernel;
    if (!kernel) throw new Error('the notebook has no kernel');
    kernel.requestExecute({ code: REJECTION_GUARD, silent: true, store_history: false }).done.then((reply) => {
      if (reply.content.status !== 'ok') guardMissing(new Error(`${reply.content.ename}: ${reply.content.evalue}`));
    }, guardMissing);
  }

  let setupRun = null;

  // notebook:run-cell runs the active cell of the current notebook and settles
  // when it finishes, so each setup cell is made active in turn and awaited.
  // A cell counts as run only if it has an execution count afterwards, which
  // is cleared first, since a reopened notebook already shows the count it was
  // saved with. Afterwards the cell after the last setup cell is made active,
  // so Shift+Enter carries on from where the starter wants the reader to begin.
  //
  // `guardSent` is the guard the caller already sent to this kernel, if any.
  function runSetup(app, panel, guardSent = null) {
    if (setupRun) return setupRun;
    setupRun = (async () => {
      // Sent before any setup cell, so the kernel runs it first. A kernel it
      // did not reach still runs the notebook; it only hangs, in Safari, on
      // the first request that fails, so this is a warning and not a failure.
      const guarded = guardSent || sendRejectionGuard(panel).catch(guardMissing);
      try {
        const notebook = panel.content;
        const indices = autorunIndices(notebook.model);
        if (indices.length === 0) {
          // Setup is over once the guard is sent, with or without cells to run.
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

  // A restart, or a new kernel, is a fresh Python, so setup runs again. Called
  // for every notebook the page shows, since a tab switch shows another panel.
  function watchKernel(app, panel) {
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
  }

  // Each panel's guard, and its setup if it has setup cells that have not run
  // in this page yet, as a promise a caller can wait on. Setup runs once per
  // path: the kernel of a notebook the reader tabbed away from keeps running,
  // and the panel that comes back reconnects to it.
  const adopted = new WeakMap();
  const setupPaths = new Set();

  function adoptPanel(app, panel) {
    watchKernel(app, panel);
    const guard = sendRejectionGuard(panel).catch(guardMissing);
    const path = panel.context.path;
    let ready = guard;
    if (autorunIndices(panel.content.model).length > 0 && !setupPaths.has(path)) {
      setupPaths.add(path);
      ready = runSetup(app, panel, guard);
    }
    adopted.set(panel, ready);
    return ready;
  }

  // --- Notebook tabs and import (#673, #676; docs/adr/0017-notebook-import-and-tabs.md) ---
  //
  // JupyterLite's notebooks page is a single-document shell: it shows one
  // notebook, and `docmanager:open` would put another in a browser tab of its
  // own. A tab switch therefore closes the panel on screen (its kernel keeps
  // running, so Python state survives) and opens the next one in its place.

  const NOTEBOOK_OPEN_TIMEOUT_MS = 30_000;

  const resultMessage = (requestId, outcome) => ({
    type: 'result',
    requestId,
    ok: outcome.ok,
    ...(outcome.path === undefined ? {} : { path: outcome.path }),
    ...(outcome.error === undefined ? {} : { error: outcome.error }),
  });

  async function createTabs(app, files, firstPanel) {
    const response = await fetch(new URL('./starters/index.json', FILES_URL), { cache: 'no-store' });
    const context = files.tabContext(firstPanel.context.path, await response.json());
    if (!context.ok) throw new Error(`no tabs for ${firstPanel.context.path}: ${context.error}`);
    const { community, datasetId, datasetPattern } = context;
    const playground = firstPanel.context.path;
    const contents = app.serviceManager.contents;
    const storageKey = `osa-notebook-tabs:${community}:${datasetId}`;

    // The open tabs, in order; the starter is always first. Kept in this
    // browser's storage so a reload brings them back; a closed notebook is
    // still a file, so it is listed (open: false) and can be reopened.
    const readOpen = () => {
      try {
        const saved = JSON.parse(window.localStorage.getItem(storageKey));
        return Array.isArray(saved?.open) ? saved.open.filter((path) => typeof path === 'string') : [];
      } catch {
        return [];
      }
    };
    let openList = [playground, ...readOpen().filter((path) => path !== playground)];
    const writeOpen = () => {
      try {
        window.localStorage.setItem(storageKey, JSON.stringify({ open: openList }));
      } catch (err) {
        warn('the open notebooks were not remembered', err);
      }
    };
    const addOpen = (path) => {
      if (openList.includes(path)) return;
      openList = [...openList, path];
      writeOpen();
    };

    const kinds = new Map([[playground, 'playground']]);
    // The latest import and what its source was, so Undo is offered only until
    // the reader edits it.
    let lastImport = null;
    let runningAll = null;

    const activePath = () => app.shell.currentWidget?.context?.path ?? null;

    async function listFiles() {
      const directory = await contents.get(community, { content: true });
      return (directory.content || [])
        .filter((model) => files.belongsToDataset(model.name, datasetId))
        .map((model) => ({ path: model.path || `${community}/${model.name}`, lastModified: model.last_modified || '' }));
    }

    async function kindOf(path) {
      if (kinds.has(path)) return kinds.get(path);
      let kind = 'blank';
      try {
        kind = files.kindOf((await contents.get(path, { content: true })).content);
      } catch (err) {
        warn(`could not read the kind of ${path}`, err);
      }
      kinds.set(path, kind);
      return kind;
    }

    async function isEdited(record) {
      const panel = app.shell.currentWidget;
      const content =
        panel?.context?.path === record.path && panel.context.model
          ? panel.context.model.toJSON()
          : (await contents.get(record.path, { content: true })).content;
      return files.sourceSignature(content) !== record.signature;
    }

    // The notebooks of this dataset: the open ones in tab order, then the
    // closed ones, newest first.
    async function listNotebooks() {
      const present = await listFiles();
      const exists = new Set(present.map((file) => file.path));
      const active = activePath();
      if (active && exists.has(active)) addOpen(active);
      const closed = present
        .filter((file) => !openList.includes(file.path))
        .sort((a, b) => (a.lastModified < b.lastModified ? 1 : a.lastModified > b.lastModified ? -1 : 0))
        .map((file) => file.path);
      const order = [...openList.filter((path) => exists.has(path)), ...closed];
      const items = [];
      for (const path of order) {
        items.push({
          path,
          title: files.titleFromPath(path, datasetId),
          kind: await kindOf(path),
          open: openList.includes(path),
        });
      }
      if (lastImport) {
        const item = items.find((candidate) => candidate.path === lastImport.path);
        if (item) item.undoable = !(await isEdited(lastImport));
        else lastImport = null;
      }
      return items;
    }

    let lastPosted = '';
    let postChain = Promise.resolve();
    // One list at a time, and not again when nothing changed: an autosave moves
    // nothing the widget shows.
    function postNotebooks(force = false) {
      postChain = postChain
        .then(async () => {
          const items = await listNotebooks();
          const active = activePath();
          const message = { type: 'notebooks', items, active: items.some((item) => item.path === active) ? active : null };
          const serialized = JSON.stringify(message);
          if (!force && serialized === lastPosted) return;
          lastPosted = serialized;
          post(message);
        })
        .catch((err) => warn('the list of notebooks was not sent', err));
      return postChain;
    }

    let scheduled = null;
    function scheduleNotebooks() {
      clearTimeout(scheduled);
      scheduled = setTimeout(() => postNotebooks(), 150);
    }

    // The panel on screen goes: what it holds unsaved is saved first, and a
    // file nobody changed is left as it is.
    async function retire(panel) {
      try {
        if (panel.context.model?.dirty) await panel.context.save();
      } catch (err) {
        warn('the notebook was not saved before it closed', err);
      }
      panel.dispose();
    }

    // Shows `path`: the one panel the page holds is replaced. Waits for the
    // starter's setup first, which runs on the panel that is on screen.
    async function show(path) {
      await setupRun;
      const current = app.shell.currentWidget;
      if (current?.context?.path === path) return current;
      if (current) await retire(current);
      await app.commands.execute('docmanager:open', { path, factory: 'Notebook', options: { ref: '_noref' } });
      const panel = await waitFor(
        () => {
          const widget = app.shell.currentWidget;
          return widget?.context?.path === path && widget.content?.model && widget.sessionContext ? widget : null;
        },
        NOTEBOOK_OPEN_TIMEOUT_MS,
        `the notebook ${path}`
      );
      await panel.context.ready;
      adoptPanel(app, panel);
      return panel;
    }

    async function writeNotebook(path, content, kind) {
      await contents.save(path, { type: 'notebook', format: 'json', content: files.withKind(content, kind) });
      kinds.set(path, kind);
    }

    async function importNotebook(request) {
      if (request.datasetId !== datasetId) {
        return { ok: false, error: `this notebook is for ${datasetId}, not ${request.datasetId}` };
      }
      const taken = (await listFiles()).map((file) => file.path);
      const path = files.importedPath(community, datasetId, request.title, taken);
      const content = files.withKind(request.notebook, 'chat');
      await writeNotebook(path, content, 'chat');
      lastImport = { path, signature: files.sourceSignature(content) };
      addOpen(path);
      await show(path);
      return { ok: true, path };
    }

    async function newNotebook() {
      const taken = (await listFiles()).map((file) => file.path);
      const path = files.importedPath(community, datasetId, files.BLANK_TITLE, taken);
      await writeNotebook(path, files.blankNotebook(), 'blank');
      addOpen(path);
      await show(path);
      return { ok: true, path };
    }

    async function openNotebook(request) {
      addOpen(request.path);
      await show(request.path);
      return { ok: true, path: request.path };
    }

    async function renameNotebook(request) {
      const taken = (await listFiles()).map((file) => file.path);
      const next = files.renamedPath(request.path, request.title, taken);
      if (next === request.path) return { ok: true, path: next };
      await contents.rename(request.path, next);
      openList = openList.map((path) => (path === request.path ? next : path));
      writeOpen();
      if (kinds.has(request.path)) {
        kinds.set(next, kinds.get(request.path));
        kinds.delete(request.path);
      }
      if (lastImport?.path === request.path) lastImport = { ...lastImport, path: next };
      return { ok: true, path: next };
    }

    // The tab to land on when `path` goes: the one before it.
    const neighbour = (path) => openList[Math.max(0, openList.indexOf(path) - 1)] ?? playground;

    async function closeNotebook(request) {
      if (activePath() === request.path) await show(neighbour(request.path));
      openList = openList.filter((path) => path !== request.path);
      writeOpen();
      return { ok: true, path: request.path };
    }

    async function undoImport(request) {
      if (lastImport?.path !== request.path) return { ok: false, error: 'only the latest import can be undone' };
      if (await isEdited(lastImport)) return { ok: false, error: 'it has been edited, so it was kept' };
      if (activePath() === request.path) await show(neighbour(request.path));
      openList = openList.filter((path) => path !== request.path);
      writeOpen();
      await contents.delete(request.path);
      kinds.delete(request.path);
      lastImport = null;
      return { ok: true, path: request.path };
    }

    // Runs the notebook's cells one at a time, as setup does: run-all-cells
    // never settles (docs/adr/0012). Stops at the first cell that fails.
    async function runAll(request) {
      if (runningAll) return { ok: false, path: request.path, error: 'the cells are already running' };
      runningAll = request.path;
      try {
        const panel = await show(request.path);
        await panel.sessionContext.ready;
        await adopted.get(panel);
        const notebook = panel.content;
        const model = notebook.model;
        for (let index = 0; index < model.cells.length; index++) {
          if (app.shell.currentWidget !== panel) throw new Error('the notebook was closed before every cell ran');
          const cell = model.cells.get(index);
          const isCode = cell.type === 'code';
          if (isCode && cell.sharedModel.getSource().trim() === '') continue;
          if (isCode) cell.executionCount = null;
          notebook.activeCellIndex = index;
          await app.commands.execute('notebook:run-cell');
          if (isCode && (cell.executionCount === null || hasError(cell))) {
            return { ok: false, path: request.path, error: `cell ${index + 1} did not finish without an error` };
          }
        }
        return { ok: true, path: request.path };
      } finally {
        runningAll = null;
      }
    }

    // What each request does; Run all is the one that is not waited for.
    const HANDLERS = {
      'import-notebook': importNotebook,
      'new-notebook': newNotebook,
      'open-notebook': openNotebook,
      'rename-notebook': renameNotebook,
      'close-notebook': closeNotebook,
      'undo-import': undoImport,
    };

    async function handle(data) {
      let checked;
      try {
        checked = files.validateTabMessage(data, { community, datasetPattern, items: await listNotebooks() });
      } catch (err) {
        // The listing the check needs (the contents, or a read of a file's
        // edits) failed: a request that is one gets an answer, not a wait.
        warn('a request from the widget could not be checked', err);
        const requestId = files.tabRequestId(data);
        if (requestId !== null) post(resultMessage(requestId, { ok: false, error: err.message || String(err) }));
        return;
      }
      if (!checked.ok) {
        if (checked.ignored) warn('a message from the widget was ignored', checked.error || data?.type);
        else post(resultMessage(checked.requestId, checked));
        return;
      }
      const { request } = checked;
      if (request.type === 'run-all') {
        runAll(request)
          .catch((err) => ({ ok: false, path: request.path, error: err.message }))
          .then((outcome) => {
            post(resultMessage(request.requestId, outcome));
            return postNotebooks();
          });
        return;
      }
      let outcome;
      try {
        const handler = HANDLERS[request.type];
        outcome = handler ? await handler(request) : { ok: false, error: `${request.type} is not supported here` };
      } catch (err) {
        warn(`${request.type} failed`, err);
        outcome = { ok: false, error: err.message || String(err) };
      }
      post(resultMessage(request.requestId, outcome));
      await postNotebooks();
    }

    // Anything that changes the list: a save, a rename, another panel on screen.
    contents.fileChanged.connect(scheduleNotebooks);
    app.shell.currentChanged?.connect(scheduleNotebooks);
    await postNotebooks(true);
    return { handle };
  }

  let appReady = null;
  let pendingScheme = null;
  // Requests from the widget wait here until the tabs have started (or failed
  // to), and then run one at a time, in the order they came.
  let tabs = null;
  let releaseTabs = () => {};
  let tabQueue = new Promise((resolve) => {
    releaseTabs = resolve;
  });

  // The id a reply goes under, or null for a message that gets none: the same
  // rule as validateTabMessage (osa-notebook-files.js). If that module did not
  // load there is no list of types to check, so any message of this target
  // with a well-formed id is answered, so the widget does not wait on it.
  async function answerableId(data) {
    const files = await filesModule;
    if (files) return files.tabRequestId(data);
    const id = data.requestId;
    return typeof id === 'string' && /^[A-Za-z0-9._:-]{1,64}$/.test(id) ? id : null;
  }

  function queueTabRequest(data) {
    tabQueue = tabQueue.then(async () => {
      if (tabs) {
        try {
          await tabs.handle(data);
        } catch (err) {
          warn('a request from the widget failed', err);
        }
        return;
      }
      const requestId = await answerableId(data);
      if (requestId !== null) post({ type: 'result', requestId, ok: false, error: 'the notebook tabs are not available' });
    });
  }

  window.addEventListener('message', (event) => {
    if (!embedded || event.source !== window.parent) return;
    const data = event.data;
    if (!data || data.target !== 'osa-notebook') return;
    if (data.type === 'theme') {
      if (!Object.hasOwn(THEMES, data.scheme)) return;
      if (appReady) queueTheme(appReady, data.scheme);
      else pendingScheme = data.scheme;
      return;
    }
    // Tab and import requests; osa-notebook-files.js says which types it knows.
    queueTabRequest(data);
  });

  // Never throws: a page whose tabs cannot start still runs its notebook.
  async function startTabs(app, panel) {
    try {
      const files = await filesModule;
      if (!files) throw new Error('osa-notebook-files.js did not load');
      tabs = await createTabs(app, files, panel);
    } catch (err) {
      warn('the notebook tabs did not start', err);
    } finally {
      releaseTabs();
    }
  }

  // Started now, in parallel with JupyterLite: it is needed only once the notebook is on screen.
  const filesModule = embedded
    ? import(FILES_URL).catch((err) => {
        warn('osa-notebook-files.js did not load', err);
        return null;
      })
    : Promise.resolve(null);

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
    // As soon as the panel exists, before its document and the theme, so the
    // guard is the first request its kernel gets (see sendRejectionGuard).
    const firstGuard = sendRejectionGuard(panel).catch(guardMissing);
    await panel.context.ready;
    appReady = app;
    if (pendingScheme) await queueTheme(app, pendingScheme);
    post({ type: 'ready' });

    watchKernel(app, panel);
    setupPaths.add(panel.context.path);
    const firstSetup = runSetup(app, panel, firstGuard);
    adopted.set(panel, firstSetup);
    if (embedded) await startTabs(app, panel);
    await firstSetup;
  })().catch((err) => {
    warn('the notebook bridge did not start', err);
    post({ type: 'error', phase: 'startup' });
  });
})();
