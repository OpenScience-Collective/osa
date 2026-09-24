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

## References

- Issues #470 (the notebook tab) and #453 (the site); ADR [0011](0011-the-notebook-site.md).
- `notebook/osa-bridge.js`, `scripts/build_notebook_site.py` (`embed_origins`, `inject_bridge`, `NOTEBOOK_SETTINGS_OVERRIDES`), `notebook/e2e-check.js` (the framed runs).
- `docs/community-notebook.md` (the adopter-facing how-to).
