# 0011. The notebook site

Date: 2026-09-23

## Status

Accepted.
Amends [0010](0010-the-notebook-surface.md): 0010 decided the notebook surface (JupyterLite, pinned to Pyodide 0.29.5) and deferred hosting it to this follow-up (issue #453).
0010's body is unchanged; only its status line now says it is amended by this record.

## Context

0010's four prerequisites for the follow-up were: a pruned, pinned JupyterLite build; a hosting origin; that origin reachable by `zarr.nemar.org`'s Cross-Origin Resource Sharing (CORS) allowlist; and a content drive receiving the widget's exported workspace.
This record settles the first three now, on the maintainer's direction (2026-09-23): host a JupyterLite notebook site now, on `osc.earth`, in this repository, as the follow-up 0010 deferred.
The fourth prerequisite -- handing the widget's IndexedDB workspace to the notebook -- stays out of scope; see "The workspace hand-off" below.

The maintainer built a working end-to-end spike by hand before this PR:
`uv tool run --from "jupyterlite-core==0.8.4" --with "jupyterlite-pyodide-kernel==0.8.0" --with jupyter-server jupyter lite build` produces a 66 MB, 780-file site (this PR's own from-scratch build, same versions: 768 files, 66.9 MB, sourcemaps not yet stripped -- consistent within measurement noise of a live spike versus a from-scratch rebuild); stripping sourcemaps and adding the merged lock, wheels, starters and bootstrap files brings the deployed site to 501 files, 19.9 MB;
the real NEMAR read (`eegprep_lean.read_index`/`read_window` against `zarr.nemar.org`) completes and renders a figure once `%matplotlib inline` is run, because eegprep-lean's `plot_window` returns a Figure object through the object-oriented application programming interface (API) rather than pyplot's implicit current figure.
This record and its companion measurements (`.context/notebook-surface-measurements.md`, "2026-09-23: the notebook site build") reproduce that spike as committed, tested code, and record what running it revealed that reasoning about it did not.

## Decision

**Host a JupyterLite notebook site at `notebook.osc.earth/osa` (`develop-notebook.osc.earth/osa` on `develop`), built and deployed from this repository** (`notebook/`, `scripts/build_notebook_site.py`, `.github/workflows/deploy-notebook.yml`), the same repository the widget and its community configs already live in, rather than a new repository or service.
The `/osa` path is OSC's own naming rule, not a choice specific to this site:
a subdomain is a PLANE serving several projects, and the project itself is the PATH (`api.osc.earth/osa`, `widget.osc.earth/osa`, `docs.osc.earth/osa/...`), so a notebook site does not get to own its subdomain's root either.
`scripts/build_notebook_site.py` turns `--site-url`'s path component into a subdirectory of the build (`site_subdir`),
and writes `_headers` (path-prefixed) and a root `_redirects` (`/` -> `/osa/`) at the TRUE publish root,
since Cloudflare Pages reads both only from exactly there.
A community opts in with a `notebook:` block in its own `config.yaml` (`starter`, `dataset_pattern`); only NEMAR has one today.

**Pyodide 0.29.5 loads from jsDelivr through `pyodideUrl`, not self-hosted.**
`jupyter lite build --pyodide=<tarball>` was measured, in 0010, at 529-531 MB total because the full Pyodide distribution is extracted and copied into the site rather than referenced.
Patching the built `jupyter-lite.json`'s `litePluginSettings["@jupyterlite/pyodide-kernel-extension:kernel"]` with an explicit `pyodideUrl` and `loadPyodideOptions` (`lockFileURL`, `packageBaseUrl`) keeps the interpreter a content delivery network (CDN) reference: 67 MB versus 530 MB for what is otherwise the identical build.
`--lite-dir`'s own build-time config merge cannot do this safely: reading `jupyterlite_core.addons.base.BaseAddon.merge_jupyter_config_data` shows only `disabledExtensions`/`federated_extensions`/`settingsOverrides` are merged -- every other key, `litePluginSettings` included, is a plain overwrite -- so a `--lite-dir` seed would silently drop the build's own `pipliteUrls` entry.
`scripts/build_notebook_site.py` instead post-processes the built `jupyter-lite.json` directly, and asserts `pipliteUrls` is still present after patching (`patch_root_config`), which is what stops that regression from shipping silently if a future `jupyterlite-pyodide-kernel` release changes how it writes that file.

**Community wheels are bundled and pinned through one merged lock**, the same "an overlay may only ADD a package, never replace the distribution's own" rule `frontend/osa-worker-core.js`'s `mergeLock` already enforces for the chat widget's own runtime, extended across communities in `src.core.config.notebook_lock.merge_site_lock`: two communities that name the same package must be byte-identical, or the build fails rather than silently preferring one.
Only NEMAR ships a notebook overlay today, so this rule is exercised by a synthetic two-community test (`tests/test_core/test_config/test_notebook_lock.py`), not yet by two real communities disagreeing.

**A same-origin bootstrap page (`open.html`/`open.js`) replaces `?fromURL=`**, because JupyterLite 0.8.4 has none: the string is absent from the built site (checked directly).
`open.js` writes a filled-in starter straight into JupyterLite's own `localforage` database (vendored, 1.10.0, the exact version JupyterLite itself bundles) under the same options JupyterLite's own Contents drive uses, then redirects into `notebooks/index.html?path=...`.
It never overwrites an existing notebook at that path, so a reader's own edits survive opening the same dataset again.

**PyPI fallback stays ON here, unlike the sealed chat-widget runtime.**
The widget's runtime (`docs/community-browser-runtime.md`) is sealed: egress is an explicit `fetch_allow` allowlist, and only `allow_install`'s fixed, config-reviewed list is ever installed.
A notebook is different in kind: it runs the READER's own edited code, in their own tab, and the entire point is that they can `%pip install` something the community's starter never anticipated.
Disabling PyPI fallback here would make every unplanned import an opaque failure in a surface whose whole purpose is exploration.
This is a deliberate, stated difference in threat model between the two runtimes, not an oversight: the notebook site has no conversation to protect and no model picking what code runs.

**Version pins:** `jupyterlite-core` 0.8.4, `jupyterlite-pyodide-kernel` 0.8.0, Pyodide 0.29.5.
The kernel pin is not arbitrary: 0.8.0 is the last release line that still pairs with Pyodide 0.29.*;
0.8.6 (current on PyPI as of 0010's measurements) defaults to Pyodide 314.*, the same CDN-only generation marimo was rejected for.
Pyodide's own lock is pinned by sha256 (`14d2c2dba101277999e17135e653d8f15389ad1437f53eae213bf0c3cdff723d`, fetched 2026-09-23) and the build fails loudly on a mismatch, rather than silently building against a lock nobody reviewed.

**The workspace hand-off (0010's fourth prerequisite) is deferred, not built.**
The widget's IndexedDB workspace and the notebook site's `localforage` database are two different origins' storage; nothing bridges them here.
A reader who wants their widget-side runs in the notebook downloads the workspace zip from Settings and uploads it into JupyterLite by hand today.
Bridging them is real, separate scope (a cross-origin transfer mechanism, or a shared drive), intentionally left for a later follow-up.

## Consequences

- **Easier:** a community adds browser-executable exploration with a four-line config block and one `.ipynb` file; no code deploys with it.
  The site itself needs no secrets to build (`scripts/build_notebook_site.py` fetches only public PyPI/jsDelivr resources) and runs in CI.
- **Harder:** a community sharing a package name with another must keep their wheels byte-identical, or the build refuses outright rather than silently picking one -- a real constraint once a second community adopts this, not yet exercised by two real ones.
- **Content Security Policy (CSP) is loose by necessity, for now.**
  `notebook/_headers` sets `frame-ancestors 'none'` and `Referrer-Policy: no-referrer` site-wide (plus `X-Content-Type-Options: nosniff`, which changes nothing this site depends on and is included as a free hardening step).
  A tighter `script-src`/`connect-src` was considered and not attempted: proving it does not break JupyterLite's own webpack-split chunks, its blob Worker, jsDelivr, and `zarr.nemar.org` needs the same kind of headless-Chrome verification the chat widget's own CSP variants went through (`frontend/browser-harness/serve.js`'s `POLICIES`), and that verification is future work, not done in this PR.
- **`cleanup-preview-dns.yml` never touches this site.**
  Read directly: its branch-deletion and periodic-cleanup logic only matches records whose name `endswith("-demo.osc.earth")`, and this site's two domains (`notebook.osc.earth`, `develop-notebook.osc.earth`) are declared directly in `deploy-notebook.yml`, never derived from an arbitrary branch name.
  Confirmed by reading the matcher, not assumed.
- **No PR previews.**
  `deploy-notebook.yml` triggers on push to `main`/`develop` and `workflow_dispatch` only, unlike `deploy-pages.yml`'s per-branch preview subdomains: a notebook-site change is reviewed by running the build locally (`notebook/README.md`) or on `develop` before merging to `main`, not by a public preview URL per PR.
- **Sourcemaps are stripped** (`scripts/build_notebook_site.py`'s `strip_sourcemaps`): a browser fetches one only when devtools is open, so their absence changes nothing a reader's session depends on.
  Checked, not assumed: `notebook/e2e-check.js`'s full run (see `.context/notebook-surface-measurements.md`) is against a build this function has already stripped.
- **A site's `--site-url` must match wherever it is actually served**, discovered by the live Chrome check rather than reasoned about in advance: the merged lock rewrites every community wheel to an absolute `<site_url>/wheels/...` URL, so building for `notebook.osc.earth/osa` while serving on loopback makes `%pip install`/`micropip.install` fetch from the wrong host and fail silently (`ModuleNotFoundError`, no earlier error at all).
  Not a defect in `merge_site_lock`; a real production deploy never has this mismatch, since it always builds with `--site-url` equal to the domain it is about to publish to.
  `notebook/e2e-check.js` avoids it by starting its own static server first and building against that server's own origin; see `.context/notebook-surface-measurements.md` for the full write-up.

## References

- Issue #453; ADR [0010](0010-the-notebook-surface.md) (the surface decision this amends).
- `.context/notebook-surface-measurements.md`, "2026-09-23: the notebook site build" (this record's own measurements: file counts, bytes, Chrome timings, the `?fromURL=` finding).
- `docs/community-notebook.md` (the adopter-facing how-to).
- `src/core/config/notebook_lock.py`, `scripts/build_notebook_site.py`, `notebook/open.js` (the code this record describes).
