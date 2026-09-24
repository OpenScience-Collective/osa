# notebook.osc.earth/osa

The static site behind `notebook.osc.earth/osa` (`develop-notebook.osc.earth/osa` on `develop`): a JupyterLite instance, pinned to Pyodide 0.29.5, that a widget's own button (built separately) opens into for a specific dataset.
The `/osa` path follows OSC's naming rule: a subdomain is a plane serving several projects, and the project itself is the path (`api.osc.earth/osa`, `widget.osc.earth/osa`, ...).
See `docs/community-notebook.md` for how a community adds a starter, and `docs/adr/0011-the-notebook-site.md` for why this exists and how it is built.

## What lives in this folder

- `open.html` / `open.js`: the same-origin bootstrap page. JupyterLite 0.8.4 has no `?fromURL=` content-loading parameter, so this page fills a community's starter notebook in with the requested dataset id and writes it directly into JupyterLite's own browser storage, then redirects into it.
- `osa-bridge.js`: added by the build to the notebook page (`notebooks/index.html`). It runs a starter's cells tagged `osa-autorun` when the notebook opens and after a kernel restart, and, when the notebook is the chat widget's tab, reports ready and setup status to the widget and applies the widget's light or dark theme (`docs/adr/0012-the-notebook-as-a-widget-tab.md`).
  Before the setup cells, it sends each new kernel a rejection guard, silently: without it, a request that fails in Safari never returns, and the kernel stays busy for good (#496). The guard is the chat runtime's own, `buildRejectionGuardSource()` in `frontend/osa-egress.js`, which says why it works.
- `localforage.min.js`: vendored, unmodified, version 1.10.0 (the exact version JupyterLite itself bundles), Apache License 2.0.
  Fetched from `https://cdn.jsdelivr.net/npm/localforage@1.10.0/dist/localforage.min.js`, sha256 `cc168d95fb927d46b1043726cfe13998e08902ff63f24330e2bb2290109ed145` as downloaded; this repo's own pre-commit hook (`fix end of files`) added the single trailing newline the upstream file lacked, so the byte-identical committed copy is `b60ef9b887b994187a44438c182dba772741611a46f6dab79d0908c70a17cc18` -- a whitespace-only difference, not a code change.
  Its own license header is intact at the top of the file; do not strip it.
- `_headers`: a TEMPLATE for Cloudflare Pages headers (immutable caching for wheels, no-cache for everything a reader's session depends on being fresh, and the site-wide Content Security Policy (CSP)/Referrer-Policy). The build (`write_headers`) fills in the environment's `frame-ancestors` list (`embed_origins`), prefixes every path pattern with the site's own path (`/osa`), and writes the result at the true publish root, since Cloudflare Pages only reads `_headers` from exactly there, never a subdirectory.
- `test-open.js`: `open.js`'s pure logic (input validation, token filling, entry shape), run under Bun -- no browser, no network.
- `test-bridge.js`: the bridge's rejection guard, run under Bun against the real Pyodide from npm: it is the chat runtime's guard exactly, and under it a failed `pyfetch` raises where it otherwise hangs (Bun's fetch rejects the way Safari's does). No browser; needs `bun install`.
- `e2e-check.js`: the full flow in a real, headless Chrome: builds the site, serves it with its own `_headers`, opens a dataset link, and checks that the setup cell runs by itself, every cell runs, edits save and autosave, a rejection shaped like Safari's failed fetch raises in the kernel rather than hanging it, a kernel restart reruns setup, the notebook works framed on an allowed site (theme messages included), and a site not on the list is refused. Needs the network (reads `zarr.nemar.org`, or `zarr-test.nemar.org` with `--environment develop`).

The actual build script lives at the repository root, `scripts/build_notebook_site.py` (Python, alongside every other community-config-reading script), not in this folder: it reads every community's `config.yaml` under `src/assistants/`, which is easiest from the same place `scripts/build_runtime_lock.py` already reads them from.

## Building the site

```bash
uv run python scripts/build_notebook_site.py \
  --site-url https://notebook.osc.earth/osa \
  --environment production \
  --output-dir dist/notebook-site
```

`--environment` is required, `production` or `develop`.
It picks each community's `zarr_base` and `dataset_page_base` from its `notebook:` block, so a develop build's starter reads the staging data host (for NEMAR, `zarr-test.nemar.org`) and links to the staging website.
There is no default, because a build aimed at the wrong data host still builds and only fails in a reader's browser.

Needs no secrets. Fetches: JupyterLite's own build tooling from PyPI (via `uv tool run`, pinned to `jupyterlite-core==0.8.4` / `jupyterlite-pyodide-kernel==0.8.0`), and Pyodide 0.29.5's own lock from jsDelivr (pinned and sha256-verified; the build fails loudly on a mismatch rather than silently building against an unreviewed lock).
Every build exposes JupyterLite's `Application` instance as `window.jupyterapp`, because `osa-bridge.js` drives it; `e2e-check.js` uses it too, so the check runs the same build a deployment ships.

`--site-url`'s path (`/osa`) becomes a subdirectory of `--output-dir`:
the JupyterLite build, lock, wheels, starters and bootstrap files all land at `dist/notebook-site/osa/`, while `_headers` (path-prefixed) and, when there is a path, a `_redirects` sending `/` to `/osa/` are written at `dist/notebook-site/` itself --
the exact root Cloudflare Pages reads them from.
A bare-host `--site-url` (no path) skips all of that and publishes directly at `--output-dir`, which is what a quick local test without the production path prefix looks like.
**Building for one origin and serving from another silently breaks package installs**: the merged lock's wheel URLs are absolute, so a build made for `https://notebook.osc.earth/osa` will try to fetch wheels from that real host even when served locally on loopback, and `%pip install`/`micropip.install` swallow that failure rather than raising (found by `e2e-check.js`; see `.context/notebook-surface-measurements.md`). Always build with `--site-url` matching wherever you are about to serve from.

## Testing locally

```bash
# open.js's pure logic, no browser needed
bun notebook/test-open.js

# the bridge's rejection guard, against the real Pyodide from npm (after bun install)
bun notebook/test-bridge.js

# the full flow in real, headless Chrome -- builds the site, serves it,
# opens a dataset link, and checks the result end to end. Reads the real,
# live zarr.nemar.org, so it needs the network and is slower (10+ seconds).
bun notebook/e2e-check.js
# the same against a develop build, which reads zarr-test.nemar.org and opens
# the staging fixture xx099903
bun notebook/e2e-check.js --environment develop
```

`e2e-check.js` reuses `frontend/browser-harness/chrome.js`'s Chrome-driving primitives (`findChrome`, `launch`, `connect`) rather than duplicating them, the same way `frontend/browser-harness/notebook-bench.js` already does for the surface-comparison measurements behind ADR 0010.
It is not wired into `test.yml`/`tests.yml` as a required gate (it reads live, third-party-adjacent data over the network, the same reason `frontend/browser-harness/widget_e2e.py` is a manual/`workflow_dispatch` check rather than a required one); run it by hand before a release that touches this site, and see `.context/notebook-surface-measurements.md` for the last recorded run's numbers.

## Serving a preview

Any static file server works; the built site is fully static.
`e2e-check.js` includes a small one (`serveStatic`) that sets `Content-Type: application/wasm` for `.wasm` files, since Pyodide's own interpreter loads from jsDelivr rather than this server, but a locally-served `.wasm` needs the right type wherever one is served from a plain static server that guesses by extension incorrectly.
