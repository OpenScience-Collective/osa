# 0017. Import a chat's notebook, and name the notebooks as tabs

Date: 2026-11-10

## Status

Accepted.
Amends [0012](0012-the-notebook-as-a-widget-tab.md): 0012's bridge only reported to the widget and took its theme; it now also writes notebooks to the frame's storage and shows them one at a time, on the widget's request.
0012's body is unchanged.

## Context

The chat can now turn its successful runs into a notebook (`buildConversationNotebook`, #667), and the next steps are to hand it to the notebook (#673) and to let the Notebook surface hold several named notebooks instead of the one starter (#676).
The notebook is a frame on another origin (the widget on nemar.org, the notebook on notebook.osc.earth), so the widget cannot write its storage and cannot reach its application object; only a script running inside the notebook page can.
That script is `notebook/osa-bridge.js` (0012), which already drives JupyterLite through `window.jupyterapp`: `serviceManager.contents` for files (as `notebook/e2e-check.js` already does) and `app.commands` for the rest.

Three facts about the JupyterLite notebooks page decide the shape of this:

- **It is a single-document shell.**
  `NotebookShell.add` returns without adding when its main area already holds a widget, and the page's document opener sends `docmanager:open` to a new browser tab unless the call passes `options: {ref: '_noref'}` (read in JupyterLite 0.8.4's built bundle, and measured).
  Showing another notebook means closing the panel on screen and opening the next in its place.
- **A closed panel does not stop its kernel.**
  The kernel session of the notebook that was tabbed away from keeps running, and `docmanager:open` on that path reconnects to it (measured: after the starter was tabbed away from and back, `eegprep_lean` was still imported in its kernel).
  So Python state survives a tab switch, and setup is run once per notebook per page, not at every switch.
- **`notebook:run-all-cells` never settles** (0012), so Run all runs the cells one at a time with `notebook:run-cell`, as setup does.

## Decision

**The widget asks, the page does, and the page answers.**
Every message from the widget is `{target: 'osa-notebook', type, requestId, ...}`; the bridge keeps its check that a message comes from `window.parent`.

| Direction | Message |
|---|---|
| widget to notebook | `import-notebook {datasetId, title, notebook, stats}`, `run-all {path}`, `undo-import {path}` |
| widget to notebook | `new-notebook`, `open-notebook {path}`, `rename-notebook {path, title}`, `close-notebook {path}` |
| notebook to widget | `{source: 'osa-notebook', type: 'result', requestId, ok, path?, error?}`, once per request |
| notebook to widget | `{source: 'osa-notebook', type: 'notebooks', items: [{path, title, kind, open, undoable?}], active}`, on ready and after every change, including a notebook the reader opens inside JupyterLite |

`kind` is `playground` (the starter), `chat` (an import) or `blank`.
`undoable` is on the latest import only, and false once the reader has edited it.

**The page trusts nothing in a message.**
`notebook/osa-notebook-files.js` is a pure module the bridge loads with a dynamic `import()` (the bridge is a classic script) and `notebook/test-notebook-files.js` runs under Bun:

- A path from a message is acted on only if it is one the page itself listed under `<community>/` for this dataset, and the starter cannot be renamed or closed.
- A dataset must pass `datasetProblem` (the check a notebook link gets, `notebook/open.js`) against the pattern in `starters/index.json`.
- An import is nbformat 4 with only markdown and code cells, at most 200 cells and 2 MB; the `osa-autorun` tag is stripped, so an import never runs by itself, and code cells' outputs and execution counts are cleared.
- A title becomes a file name by removing `/ \ : * ? " < > |`, control and direction-override characters, and dots and spaces at its edges, at most 64 characters, "Notebook" if nothing is left.
  A name that exists gets " 2", " 3", and nothing is ever overwritten.
- A message with another `target`, a type the module does not know, or no usable `requestId` is ignored and not answered.
  A known type with a bad path or a bad import is answered `ok: false` with a reason.

**Files.**
An import is `<community>/<dataset> · <title>.ipynb`, a blank notebook `<dataset> · Untitled.ipynb`, and the starter stays `<dataset>.ipynb`, which no message writes.
A notebook records its kind in its own metadata (`metadata.osa.kind`), so the kind stays with the file; the playground is decided by its path alone.
A rename keeps the `<dataset> · ` prefix.

**Tabs.**
The open list is kept in the page's `localStorage` per community and dataset, starts with the starter, and is restored after a reload with the starter on screen, the page's first notebook.
A closed notebook is still a file and is listed with `open: false`, which is what "Reopen" lists.
Switching waits for the starter's setup to finish, since setup runs cell by cell on the panel on screen, and saves a panel with unsaved changes before closing it, so a file nobody changed is not rewritten (the starter's `last_modified` stays put across an import).

**Undo** deletes the imported file and closes its tab, if the cells' source is still what was imported: running the cells is not an edit, typing in them is.

**The widget** (`frontend/osa-chat-widget.js`, the block "Notebook tabs") opens the notebook tab and the panel, queues a message until the notebook has reported ready, posts it once and only to the notebook's origin, and draws the strip, the banner ("Imported N steps from your chat", the runs left out from `stats`, Run all and Undo) and the "+" menu from the notebook's messages, so it shows what the page has.
`importNotebookToTab` is the one entry point; the "Continue in notebook" card (E4.3) will call it.

## Consequences

- **Easier:** a chat's notebook is one call away from being on screen, named, runnable and undoable; and the reader can keep several notebooks per dataset without leaving the widget.
  The notebook page is the only place that writes storage, so a widget bug cannot write a file, and a hostile page that frames the notebook can do no more than the request list above allows.
- **Harder:** the bridge now depends on more of JupyterLite's internals: `docmanager:open` with `ref: '_noref'`, the single-document shell, `contents.fileChanged`, the kernel outliving its panel.
  `notebook/e2e-check.js` covers each (import, open, the starter unchanged, undo, create, rename, close, a reload and a reopen, a second import, a foreign message) and fails loudly if a JupyterLite upgrade changes one.
- **Each notebook opened is a kernel, and so a Pyodide.**
  A notebook the reader tabs away from keeps its Python running; closing a tab does not stop it.
  Accepted rather than solved: stopping it would lose state the reader may want back, and the same trade is made for the chat's frame in 0012.
- **The pop-out has its own frame**, so a notebook imported from the page is a file the pop-out's frame lists once it next sees the storage change; the widget's documentation already says to edit one window at a time.
- **After a reload, the starter is the notebook on screen**, not the last one the reader used; the open tabs return, and each is a click away.
  Restoring the last active notebook would run its kernel and the starter's setup at once on every reload.
- **A notebook the reader changes outside the page** (another browser tab on the same storage, or a file added to the community's directory by hand) is listed as a blank notebook if it has this dataset's prefix, and not at all otherwise.
- **A blank notebook is empty**, with no setup: it is a Python (Pyodide) kernel and nothing else, so a reader who wants the data in it runs the starter's setup cell themselves.

## References

- Issues #673 (import) and #676 (tabs); the plan is `.context/nemar-ai-ux-plan.md`, E4.2 and E4.4, with board B1, `.context/designs/nemar-ai-ux/Notebook.dc.html`.
- ADR [0011](0011-the-notebook-site.md) (the site), [0012](0012-the-notebook-as-a-widget-tab.md) (the bridge and the frame).
- `notebook/osa-bridge.js`, `notebook/osa-notebook-files.js`, `notebook/test-notebook-files.js`, `notebook/e2e-check.js`, `notebook/README.md` (the protocol).
- `frontend/osa-chat-widget.js` (`importNotebookToTab`, `renderNotebookTabs`, `renderNotebookBanner`), `frontend/test-widget-notebook-import.js`, `frontend/test-widget-notebook-strip.js`.
