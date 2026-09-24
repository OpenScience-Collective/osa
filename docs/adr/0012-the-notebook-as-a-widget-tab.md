# 0012. The notebook as a widget tab

Date: 2026-09-24

## Status

Accepted.
Amends [0011](0011-the-notebook-site.md): 0011 kept the notebook site from ever being embedded (`frame-ancestors 'none'`), and this record reverses that for the sites the chat widget runs on.
0011's body is unchanged; only its status line now says it is amended by this record.

## Context

The capsule's notebook button (#468) opens the notebook site in a new browser tab.
The notebook should instead open inside the widget, as a second tab next to the chat, so a reader moves between the assistant and the notebook without leaving the page (#470).
That puts the notebook site in a cross-site iframe on nemar.org, test.nemar.org and the other widget hosts, which 0011's policy refuses.

Three more things follow from embedding it.
A reader should not have to know that the starter has a setup cell to run first.
The notebook should match the widget's light or dark theme, which follows the embedding site, not only the device.
And the widget's pop-out window reopens the notebook in a new frame, which only carries a reader's edits if they have already reached storage.

## Decision

**The sites the widget runs on may embed the notebook, and nothing else may.**
`scripts/build_notebook_site.py`'s `embed_origins` writes `frame-ancestors` per environment: `'self'`, the platform's own widget hosts (`https://demo.osc.earth`, `https://osa-demo.pages.dev`), then every notebook community's `cors_origins`, the same origins the application programming interface (API) already allows the widget on.
The develop build adds `https://*.osc.earth`, `https://*.osa-demo.pages.dev` and loopback, for previews and local testing.
A frame-ancestors source cannot take a partial-label wildcard, so develop's `*-demo.osc.earth` previews are covered by `*.osc.earth`; a community origin written that way is refused by the config loader's own `cors_origins` rule, and again by the build, rather than producing a policy the browser ignores.
The site also sends `X-Frame-Options: SAMEORIGIN`, for a browser too old to read frame-ancestors, which then refuses to embed it at all; a browser that reads frame-ancestors ignores `X-Frame-Options` when both are sent (measured in Chrome: the allowed embed still loads).
The widget's pop-out is an `about:blank` window of the embedding page's own origin (`openPopout` writes the widget into it), so its frame's ancestor is the same site and the same list covers it.

**A small script of ours runs inside the notebook page, `notebook/osa-bridge.js`.**
The build adds it to `notebooks/index.html`, the page `open.js` redirects into.
It runs a starter's code cells tagged `osa-autorun` when the notebook opens, and again after a kernel restart or a new kernel, each a fresh Python.
Setup counts as done only if every tagged cell got an execution count (cleared first, because a reopened notebook shows the one it was saved with) and none produced an error.
Embedded, it also tells the widget when the notebook is ready and how setup went, and applies the theme the widget sends:

| Direction | Message |
|---|---|
| notebook to widget | `{source: 'osa-notebook', type: 'ready'}` |
| notebook to widget | `{source: 'osa-notebook', type: 'setup', status: 'running' \| 'done' \| 'error' \| 'none'}` |
| notebook to widget | `{source: 'osa-notebook', type: 'theme', scheme, applied}`, once a theme is applied or has failed |
| notebook to widget | `{source: 'osa-notebook', type: 'error', phase: 'startup'}`, if the script could not start |
| widget to notebook | `{target: 'osa-notebook', type: 'theme', scheme: 'light' \| 'dark'}` |

It listens only to `window.parent`: frame-ancestors has already decided which pages can be that parent, so the script repeats no origin list.
What it posts carries no reader data, so it posts to any origin.
It drives JupyterLite through the app the build exposes as `window.jupyterapp`, so every build now sets `exposeAppInBrowser`; the `--expose-app` build flag is gone, and `notebook/e2e-check.js` checks the same build a deployment ships.
The exposed app is reachable only from the notebook site's own origin, never from the page that embeds it.

It relies on `notebook:run-cell` settling when the cell finishes, unlike `notebook:run-all-cells`, whose promise never settles (measured in Chrome against JupyterLite 0.8.4).
Before applying the widget's theme it turns off "follow the device" (`adaptive-theme`), because with it on, `apputils:change-theme` only turns that setting off and returns, leaving the current theme in place (read in JupyterLab's own command, and measured): the widget's first choice would be dropped whenever it differs from what the device shows, as on a dark site viewed from a light device.
The `apputils:adaptive-theme` command starts its settings write without returning it, so the script reads the setting back until it is off before changing the theme; the first version did not, and `notebook/e2e-check.js` caught the theme command switching the setting straight back on.
The check sends exactly that first message, and checks that a later change of the device's setting leaves the widget's choice in place.

A JupyterLab extension was rejected: building one needs `jupyter labextension build`, which brings Node and webpack in beside this repository's Bun-only JavaScript toolchain, for about 150 lines of code against an app JupyterLite already exposes.

**The widget shows the notebook as a tab of its own panel**, next to the chat, and the capsule's filled indicator marks which tab is open.
It makes one frame, the first time the tab opens, and keeps it while the reader is on chat, so the notebook's Python keeps running; a new dataset on screen replaces it, and a dataset with no Zarr copy drops it.
The frame has no `sandbox` attribute: the notebook needs scripts, its own origin's storage and service worker, downloads (File > Download) and its own dialogs, and the notebook site's separate origin already keeps it from the host page.
Because the bridge posts to any origin, the widget does the filtering: it acts on a message only if it comes from the frame it made and from the notebook site's origin, and it addresses its theme messages to that origin only.
A frame that loads but has not said it is ready within 12 seconds, or has not said so within 45 seconds of being created, or whose bridge reports a startup error before it is ready, is covered by a fallback that retries in a fresh frame or opens the same address in a browser tab.
A page whose own `frame-src` refuses the notebook still fires the frame's load event, for the error page, and then hears nothing (measured in Chrome 153: one load, no messages; an allowed frame loads twice, through `open.html`'s redirect, and speaks), which is why the shorter timeout counts from the load.
A startup error after ready is ignored, because the bridge's error path also covers the work it does after the notebook is on screen.
A failed frame is kept until the reader asks for another with "Try again", so returning to the tab shows the same fallback instead of quietly spending another load, and a notebook that was only slow recovers when it reports ready.
`launcher: capsule` now requires a `notebook` section, because without one the notebook icon could open only an error.

A browser tab as the notebook's primary surface (#468's behavior) was rejected: moving between the assistant and the notebook meant leaving the page.
So was unloading the frame on a switch to chat, which would restart Python, and its setup, on every return.

**Two settings change from JupyterLab's defaults**, through `settingsOverrides` in the built `jupyter-lite.json`.
`adaptive-theme` is on, so a notebook opened on its own follows the device.
`autosaveInterval` is 5 seconds instead of 120, so the pop-out reopens a reader's latest edits.
Measured with a control: with the override, an edit with no Save reached storage within a second; without it, not within 20 seconds.

## Consequences

- **Easier:** a starter's author tags a setup cell once, and every reader gets a notebook that is ready without knowing the cell exists.
  A community that embeds the widget gets the notebook tab by listing its site in `cors_origins`, which it already does for the widget.
- **Storage follows the embedding site.**
  Framed, the notebook is a third-party frame, so the browser keeps its storage separate for each site that embeds it: notebooks edited in nemar.org's tab (and its pop-out) are not the ones a reader sees when opening the notebook site on its own.
  Accepted rather than solved: making them one would need the Storage Access API and a prompt, for a case nobody has asked for.
- **Harder:** the bridge depends on JupyterLab's command names and cell model (`getMetadata('tags')`, `outputs`, `sessionContext.statusChanged`), which a JupyterLite upgrade can change.
  `notebook/e2e-check.js` fails loudly if one does: it checks that the setup cell runs with no clicks, reruns after a restart, and that each theme message is applied inside the frame.
- **Embedders with a Content Security Policy (CSP)** need `frame-src` for the notebook host, `https://notebook.osc.earth` (or `https://develop-notebook.osc.earth` on staging); the widget's documentation says so.
- **Safari is not in the automated check.**
  Chrome's partitioned storage and third-party service worker are exercised by the framed run; Safari's are checked by hand on staging.
- **The pop-out does not carry the notebook yet.**
  Its button is hidden on the notebook tab, and a pop-out shows the chat only, until the pop-out gets its own tabs (#470).
- **A frame kept alive costs memory while the reader is on chat.**
  One Pyodide stays loaded for as long as the page is open once the tab has been used; the tab is opened only by a reader asking for it.

## Update: the pop-out carries the notebook

Date: 2026-09-24

Two sentences above no longer describe the pop-out; they are kept as they were written, and this is what replaced them (#470, its second part).

- **"`openPopout` writes the widget into it."** The pop-out now writes a document with no script in it, sets its presets on its window from the page, and loads the widget by its address, with the page's widget tag's `integrity` and `crossorigin`.
  An `about:blank` window inherits the embedding page's Content Security Policy, and the inline copy it used to write was refused on a page without `script-src 'unsafe-inline'`, where the pop-out opened blank (measured in Chrome with the harness's copy of nemar.org's policy).
  It is still an `about:blank` window of the embedding page's own origin, so the frame-ancestors reasoning above holds: a notebook frame in the pop-out has the same site as its ancestor.
- **"The pop-out does not carry the notebook yet."** A capsule community's pop-out has the panel's Chat and Notebook tabs, as a strip under its header, since it has no launcher, and opens on the tab the reader was on; the pop-out button stays on the notebook tab.

The pop-out's notebook is a frame of its own, and so a fresh notebook session: Python and the setup cell start again there, and the notebook reopens with what had reached storage, which is what the five-second autosave above is for.
Moving the page's frame into the pop-out was rejected: an iframe taken out of its document is unloaded, so the pop-out would start a fresh session anyway, and the page would lose its own.
The page's frame is left running, so both windows have the same stored notebook open; the widget's documentation says to edit it in one of them at a time.

## References

- Issues #470 (the notebook tab) and #453 (the site); ADR [0011](0011-the-notebook-site.md).
- `notebook/osa-bridge.js`, `scripts/build_notebook_site.py` (`embed_origins`, `inject_bridge`, `NOTEBOOK_SETTINGS_OVERRIDES`), `notebook/e2e-check.js` (the framed runs).
- `frontend/osa-chat-widget.js` (`ensureNotebookFrame`, `handleNotebookMessage`, `setTab`), `frontend/test-widget-notebook-tab.js`, `frontend/browser-harness/notebook-tab-check.mjs` (the widget's half, in Chrome against the develop notebook).
- `docs/community-notebook.md` (the adopter-facing how-to) and `docs/community-widget.md`, "The notebook tab" and "The pop-out window".
- For the update: `frontend/osa-chat-widget.js` (`openPopout`, `buildTabStrip`, `showTabAtOnce`), `frontend/test-widget-popout.js`, and `frontend/browser-harness/popout-check.mjs` (the pop-out in Chrome under a policy without `'unsafe-inline'`).
