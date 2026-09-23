# 0010. The notebook surface

Date: 2026-09-23

## Status

Accepted

## Context

Issue #423 (child of phase issue #433, epic #429) records that marimo was evaluated as the browser-execution epic's notebook surface on 2026-09-16,
in the same advisory review that produced the two-run transport decision (`.context/browser-execution-tool-design.md`),
and rejected for four integration reasons, none of them speed:

1. No `postMessage` API for a widget to hand a notebook its files (marimo issue #8139, open, unanswered at the time).
2. marimo's own IDBFS-backed storage, not storage a widget can write into.
3. CDN-only Pyodide: marimo.app loaded 0.27.7 during the check, not OSA's pinned 0.29.5.
4. A one-definition-per-variable reactive model, so a plain script the assistant wrote needs a conversion pass before it is a marimo notebook.

That review also adopted JupyterLite behind a small custom drive, to be built third (after an editable re-run panel in the chat),
citing measured costs of a 69 MB static site, about 4 MB compressed for the shell, a second Pyodide instance, and 15 to 17 seconds to first output.
Nobody measured marimo's time to first output,
and neither surface was opened in a browser on our own machines during that review.
The verdict was never written down;
#423 exists to settle it with data and record it, one way or the other.

Phase 4a's workspace exports, per session: `scripts/run-NNN.py` (plain scripts, one per assistant turn), `artifacts/`, `manifest.json`,
and `notebook.ipynb` (nbformat 4.5, one code cell per run with outputs).
The widget is embedded on third-party pages (e.g. nemar.org);
any notebook surface opens in a new tab, on its own origin, as a second Pyodide instance.
Sharing a live kernel with the chat worker is unsupported and out of scope
(stated in #433 and already recorded as a non-goal in `.context/browser-execution-tool-design.md`, "Persistence and the notebook surface").

OSA's own runtime figures (`docs/community-browser-runtime.md`, `frontend/browser-harness/README.md`,
both dated 2026-09-22, Chrome 153.0.8010.53, Pyodide 0.29.5) are the yardstick:
interpreter 5.3 MB uncompressed,
NEMAR's four-package `preload` (numpy, matplotlib, zarr, eegprep-lean) resolving to 23 packages / 13.6 MB, 18.9 MB combined;
a cold boot with numpy and matplotlib preloaded measured 7.9 s;
the live NEMAR read (`--nemar`, "read") measured under 10 s from Run to result with numpy and matplotlib already in the browser's HTTP cache.

## Measurements

All measurements below are dated **2026-09-23**, on this machine, in **Chrome 153.0.8010.53** headless, driven over the DevTools protocol.
Tool versions: **marimo 0.24.2** (the current PyPI release;
released 2026-09-11, five days before the original review and still current today —
marimo has not shipped a release since #423 was filed), **jupyterlite-core 0.8.4**,
**jupyterlite-pyodide-kernel 0.8.0** (pinned explicitly; see "Pyodide pinning" below for why), **uv 0.12.15**, **Bun 1.4.2**.
All were installed and run through `uvx`/`uv tool run` and `bun`, never `pip`/`conda`/`npm`.

### Method

`frontend/browser-harness/notebook-bench.js` (committed) drives headless Chrome the way `chrome.js` does:
it launches Chrome with a `--user-data-dir`, opens one DevTools WebSocket,
and records `Network.*` events from the page and every Worker it spawns
(marimo and the JupyterLite Pyodide kernel both run in a Worker) via the same auto-attach pattern `chrome.js`'s `attachWithNetwork` uses.
It times from just before `Page.navigate` to the first poll where `document.body.innerText` contains a sentinel string,
polling every 200 ms.
**Cold** is a fresh profile (`mkdtempSync`);
**warm** is a second navigation in the same Chrome process and profile, same origin —
the same methodology `chrome.js`'s own "warm" check uses.

One bug worth recording because it produced a false measurement before it was caught:
JupyterLite's REPL and notebook UIs echo the submitted code into the DOM **before** execution finishes,
so a sentinel written as a literal substring of the printed message (e.g. `print("OSA_BENCH_TRIVIAL_OK", result)`) matches on the unexecuted source
and reports a bogus sub-second "completion".
Every sentinel below is assembled at runtime from pieces that never appear contiguous in the source (`"OSA_BENCH_TRIVIAL" + "_DONE"`),
so only real output can match.
marimo's `--mode run` export hides source by default and was not affected, but the fix was applied to both for consistency.

The workload is the exact snippet in `src/assistants/nemar/config.yaml`, "Running code in the reader's browser"
(the fenced block `frontend/test-data-lane.js` and `frontend/browser-harness/widget_e2e.py` also extract programmatically),
filled in with `widget_e2e.py`'s own substitution values for nm000103's first recording
(`sub-NDARAA075AMK_task-DespicableMe_eeg.set`, group `eeg_250hz`, `start_sample=2500`, `n_samples=500`, `channels=[0,1,2,3]`).
The benchmark code uses `widget_e2e.py`'s `READ` variant (`index.stores[0]`) rather than re-deriving `store.group(GROUP)`,
which is the same read against the same window and is itself in-repo, not a fabricated stand-in —
see "NEMAR read, exact code" below for what actually ran.
Both notebooks read `eegprep-lean` from a locally re-served copy of the exact vendored wheel
(`src/assistants/nemar/runtime/wheels/eegprep_lean-0.1.0.dev2-py3-none-any.whl`, sha256 matching `nemar-pyodide-lock.json`) and its `zarr` companion wheel,
over loopback;
the NEMAR data read itself is against the live, public `zarr.nemar.org`.

**Loopback vs. CDN caveat.** JupyterLite's self-hosted, pinned build serves its interpreter over loopback (no real network latency);
marimo's interpreter comes from the real jsDelivr CDN.
That is not a fair wall-clock comparison by itself,
so a third JupyterLite configuration is included below —
the **default**, unpinned build, which also fetches its interpreter from jsDelivr — to control for network path.
Under that control, both surfaces fetch the same Pyodide version (314.0.0) from the same CDN.

### Results

| Surface | Config | Pyodide (loaded from) | Workload | Cold s | Cold bytes | Cold reqs | Warm s | Warm bytes | Warm reqs (cache hits) |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| marimo 0.24.2 | `export html-wasm --mode run` | 314.0.0 (jsDelivr CDN) | trivial `1+1` | 3.87 | 19.66 MB | 318 | 3.05 | 51.8 KB | 318 (313) |
| marimo 0.24.2 | same | 314.0.0 (CDN) | NEMAR read | **fails** | 33.7 MB before failing | 345 | **fails** | 54.2 KB | 343 (338) |
| JupyterLite (core 0.8.4, kernel 0.8.0) | default, unpinned | 314.0.0 (jsDelivr CDN) | trivial | 3.46 | 16.14 MB | 115 | 2.64 | 58.9 KB | 115 (101) |
| JupyterLite | pinned, self-hosted | **0.29.5** (loopback, self-hosted) | trivial | 2.46 | 23.35 MB | 116 | 2.23 | 59.0 KB | 116 (102) |
| JupyterLite | pinned, self-hosted | **0.29.5** (loopback, self-hosted) | NEMAR read | 5.29 | 37.65 MB | 146 | 3.04 | 59.0 KB | 145 (131) |

("Cold bytes"/"reqs" are DevTools `encodedDataLength`/request counts across the page and every Worker it spawned;
"warm" reuses the same profile so nearly everything but the navigable HTML comes from the browser's own HTTP cache,
matching `docs/community-browser-runtime.md`'s own cold/warm methodology.)

**Headline reading.** Under the CDN-matched control (row 3 vs. row 1), marimo and JupyterLite differ by well under a second in both directions —
**neither is decisively faster for a trivial cell.**
The "marimo feels faster" impression the original review flagged as unmeasured does not survive a controlled comparison;
if anything JupyterLite was marginally faster here.
The number that actually separates the two surfaces is not in this table as a time:
**marimo could not produce the NEMAR read's output in any configuration tried**, cold or warm, self-hosted-adjacent or CDN.
JupyterLite (pinned to OSA's own 0.29.5) completed it in 5.29 s cold, 3.04 s warm —
inside the same order of magnitude as OSA's own in-widget runtime's "under 10 s" figure for the identical read,
and its 3.04 s warm figure is actually faster than OSA's own widget's 7.9 s cold-cache boot,
though that is not an apples-to-apples comparison (different preload sets, different UI chrome) and is reported, not asserted as a real win.

**Why marimo fails on the real workload** is in "Integration gaps re-checked" below (gap 5) —
it is not a timeout or a missing package,
it is a hard rejection from marimo's own WASM sandbox, discovered by actually running the read, not by reasoning about it.
The original review never ran the real workload against marimo at all.

**Static site size.** `marimo export html-wasm` produces a 27 MB directory regardless of workload
(its shell always bundles the full marimo editor, unused language modes and diagram support included,
since `--mode run` still ships the same asset graph as `--mode edit`).
A JupyterLite build's own shell (`repl`+`notebooks` apps, no bundled Pyodide) is 64-65 MB,
matching the original review's "69 MB" figure closely enough to confirm that figure was the shell without a locally-pinned interpreter.
Pinning JupyterLite to Pyodide 0.29.5 via `--pyodide=<tarball>` (see next section) grows the build to **529-531 MB total**,
because the full, un-pruned Pyodide distribution (464 MB) is extracted and copied into the deployed site, not merely referenced.
`jupyter lite build` accepts `--no-unused-shared-packages` to prune packages nothing in `content/` imports;
**that flag was not tried, so a pruned, still-pinned size is not measured** — recorded as an open item below rather than guessed.

### Reproduction

```bash
# marimo, current release, no version pin needed (nothing to pin against)
uvx marimo export html-wasm <notebook>.py -o <dir> --mode run -f

# JupyterLite, pinned to the exact interpreter OSA's own runtime uses
curl -LO https://github.com/pyodide/pyodide/releases/download/0.29.5/pyodide-0.29.5.tar.bz2
uv tool run --from jupyterlite-core --with "jupyterlite-pyodide-kernel==0.8.0" \
  --with jupyter-server jupyter lite build \
  --contents content --output-dir <dir> \
  --pyodide=./pyodide-0.29.5.tar.bz2 --apps repl --apps notebooks --apps lab

# timing, either surface, reusing chrome.js's own Chrome-driving pattern
bun frontend/browser-harness/notebook-bench.js <url> <sentinel> [timeoutMs]
```

The REPL app's `?kernel=python&code=<url-encoded>&execute=1` URL parameters
(`https://docs.marimo.io`'s equivalent does not exist; this is JupyterLite's own, documented at `quickstart/embed-repl.md`)
gave a controlled, auto-executing entry point comparable to marimo's own auto-run `--mode run` export,
without needing to automate a "Run All" click through either UI.
The exact notebook sources, the wheel-serving stand-in server, and the URL-encoded REPL commands used for the table above
live only under this session's scratchpad (`phase4b/`), per this task's instructions not to commit throwaway builds;
the commands above and the workload code inline in this ADR are what recreate them.

### NEMAR read, exact code

```python
import micropip
await micropip.install([
    "numpy", "matplotlib", "numcodecs", "msgspec", "google-crc32c",
    "donfig", "typing-extensions", "packaging",
    "<wheel route>/zarr-3.4.0-py3-none-any.whl",
    "<wheel route>/eegprep_lean-0.1.0.dev2-py3-none-any.whl",
], deps=False)

import eegprep_lean
index = await eegprep_lean.read_index("nm000103")
store = index.stores[0]
window = await eegprep_lean.read_window(
    index, store, start_sample=2500, n_samples=500, channels=[0, 1, 2, 3]
)
print(window.data.shape, window.unit, window.rate)
```

The explicit package list and `deps=False` is not cosmetic;
it is required, and its absence is itself a finding.
`micropip.install(<eegprep-lean wheel URL>)` alone fails with
`ImportError: read_window needs the zarr extra ... because zarr pins numcodecs and numcodecs publishes no emscripten wheel at any version`
(eegprep-lean's own error message, raised from its `__getattr__` lazy-import guard, measured verbatim in both surfaces).
OSA's own runtime never hits this because its lock overlay's `preload` resolves `zarr`'s Pyodide-native dependencies
(`numcodecs`, `donfig`, `google-crc32c`, `msgspec`, `packaging`, `typing-extensions`) through `loadPackage` before any import runs
(`docs/community-browser-runtime.md`, "The lock overlay").
Neither marimo nor JupyterLite has an equivalent preload-and-lock mechanism for a community-owned wheel;
a person or the widget has to reproduce that install list by hand (as above),
which is itself part of the answer to "what does opening a workspace file take" below.

## Integration gaps re-checked

**1. `postMessage` / an embedding API — still absent, now with a maintainer response but no shipped API.**
marimo issue #8139 (opened 2026-02-05, a Google-Sheets-sidebar embedding request) is **still open**.
A marimo team member (`dmadisetti`) replied the same day:
"It might be possible with an anywidget if it's just data transfer... What's the minimum api you'd require?"
No further comment, no linked PR, and no code exists as of 2026-09-23 — over seven months of silence after that question.
The gap is unchanged in substance:
there is still no documented way for an embedding page to hand a marimo WASM export files or state after load;
the only documented mechanisms remain a bundled `public/` folder baked in at **export time** and `mo.notebook_location()` to read it,
or a GitHub-hosted notebook fetched by molab.
Both are static, pre-export inputs, not a live channel a widget could use after the person is already in a chat session.

**2. Storage — still marimo's own, unchanged in substance, now more clearly documented.**
marimo's WASM guide (`docs.marimo.io/guides/wasm.md`, fetched 2026-09-23) confirms the same two sanctioned patterns as before:
a `public/` folder copied in at export time, and fetching remote files at runtime (subject to CORS; see below).
Nothing reads or writes the origin storage (OPFS/IndexedDB) the widget's workspace (#433's scope) would use,
and nothing in the guide has changed that.
This is the same constraint JupyterLite has —
#433 already calls this a non-goal, not a JupyterLite-only cost,
since "neither notebook surface can reach the origin private file system the widget writes to."

**3. Pyodide pinning and self-hosting — re-checked directly, and the two surfaces now diverge more, not less.**

- **marimo: still CDN-only, and further from 0.29.5 than at the last review.**
  `marimo/_pyodide/pyodide_constraints.py` (in the installed 0.24.2 package) hardcodes `PYODIDE_VERSION = "314.0.0"` —
  the new CPython-3.14-based Pyodide numbering, a full generation past both 0.27.7 (what the original review measured) and OSA's pinned 0.29.5.
  The exported HTML requests it from `https://cdn.jsdelivr.net/pyodide/v314.0.0/full/`, confirmed by reading the generated `index.html` directly.
  **`docs.marimo.io/guides/publishing/self_host_wasm.md` documents a `--offline` export flag**
  ("downloads the Python runtime and notebook packages alongside the exported HTML... via Playwright") that would answer the CDN-only complaint —
  **but `marimo export html-wasm --help`, run against the installed current release, does not list `--offline` as an option**,
  and the installed package's source has no reference to it either.
  The documentation describes a capability the shipped release does not have.
  There is still no way to make a marimo export load Pyodide 0.29.5 specifically;
  the version is marimo's own choice, not a configuration point.
- **JupyterLite: can be pinned to exactly 0.29.5, confirmed by building it, at a real size cost, and pinning is mutually exclusive with staying CDN-hosted.**
  `jupyter lite build --pyodide=<path-or-URL>` (documented via `--help-all` as `PyodideAddon.pyodide_url`) accepts a local tarball path **or a URL**;
  both were tried.
  Passing the official `github.com/pyodide/pyodide/releases/download/0.29.5/pyodide-0.29.5.tar.bz2` release URL
  was expected to let the build reference Pyodide at that URL and stay small;
  it does not;
  measured directly, the build **downloads and extracts the full tarball into the deployed site regardless of whether the source was a local path or a URL**
  (`pyodideUrl` in the built `jupyter-lite.json` always resolves to `./static/pyodide/pyodide.mjs`, a local file, never the source URL).
  So the choice is binary:
  the **default** build (unpinned, 314.0.0, ~64 MB shell, same CDN marimo uses)
  or a **pinned** build (exactly 0.29.5, confirmed via the built `pyodide-lock.json`'s `info.python: "3.13.2"`, matching Pyodide 0.29.5's known Python version)
  that is 529-531 MB unpruned.
  `jupyterlite-pyodide-kernel` must also be pinned to `0.8.0` for this:
  `0.8.6` (the current release on PyPI) defaults to Pyodide `314.*`, same generation as marimo,
  and only the `0.8.0` line still pairs with `0.29.*`.

**4. The one-definition-per-variable model — confirmed, and a real conversion tool exists and was verified to work.**
Two scripts were written to mimic what `osa.save_script()` would actually produce across two assistant turns in one session —
the first defines `index`, `store`, `window`, `fig`;
the second reuses `index`/`store` from the (warm) session and **redefines** `window` and `fig` with a different sample range,
exactly the "redefine a variable across runs" pattern #423 asked to check:

```python
# run-001.py
index = await eegprep_lean.read_index("nm000103")
store = index.stores[0]
window = await eegprep_lean.read_window(index, store, start_sample=2500, n_samples=500, channels=[0,1,2,3])
fig = eegprep_lean.plot_window(window).figure

# run-002.py
window = await eegprep_lean.read_window(index, store, start_sample=5000, n_samples=500, channels=[0,1,2,3])
fig = eegprep_lean.plot_window(window).figure
```

Concatenated into one file (as `marimo convert` would receive when a session's scripts are combined into one notebook)
with `# %%` cell markers and run through `marimo convert`
(requires `jupytext`, an **extra, non-default dependency** —
`marimo export` and `marimo edit` do not need it, only `convert` from py:percent format does;
the CLI fails with a clear message and install instructions if it is missing),
the converter correctly identified `index` and `store` as **shared**
(threaded through the cell signature: `async def _(eegprep_lean, index, store):`)
and privatized the **redefined-and-not-depended-on** `window`/`fig` to cell-local `_window`/`_fig` automatically —
valid marimo, no manual fix-up needed for this pattern.

A second, smaller case that reads its own prior value (`count = count + 1` across two cells, the read-modify-write shape rather than write-only)
converts differently:
the tool **renames** the variable per cell (`count_1 = count + 1`) rather than reusing the name,
because marimo's reactive graph cannot have two cells both defining and reading the same name.
Both conversions were **executed in the browser, not just inspected**:
the write-only case reproduced the exact NEMAR read/plot behavior described above,
and the read-modify-write case printed `OSA_BENCH_RUN1_DONE 1` then `OSA_BENCH_RUN2_DONE 2`, correct in both cells.
So: the conversion pass is real, automatable, and was verified to produce correct output for a realistic two-turn redefinition —
but it needs `jupytext` installed,
is an offline/build-time step (nothing converts a plain script live inside a running marimo WASM notebook),
and a person or generated code that expects a variable to keep its exact name across turns will sometimes see it renamed.

**5. New: marimo's own WASM concurrency sandbox blocks the real NEMAR read outright —
not one of the original four reasons, found only because the actual workload was run.**
Every attempt at the NEMAR read in marimo (all rows in the results table) fails with the same traceback,
reached only after packages load and the store is opened successfully
(`zarr.nemar.org` is reached, discovery 404s happen exactly as they do in a working run):

```
File "numcodecs/blosc.pyx", line 86, in numcodecs.blosc.get_mutex
File ".../marimo/_runtime/_wasm/_concurrency/_mp_context.py", line 54, in _unsupported
    raise UnsupportedWasmConcurrencyError(
marimo._runtime._wasm._concurrency._wait.UnsupportedWasmConcurrencyError:
    multiprocessing.Lock is not supported by the Pyodide WASM process adapter
```

`numcodecs`'s `blosc` codec (used to decompress the real, blosc-compressed Zarr chunks nm000103 is stored as)
allocates a mutex the first time it decodes a chunk
(`numcodecs.blosc.get_mutex`, called from `_get_use_threads`, called from `decompress`).
marimo's own WASM sandbox (`marimo/_runtime/_wasm/_concurrency/_mp_context.py`) intentionally rejects this —
its documented "Concurrency" support levels (`docs.marimo.io/guides/wasm.md`) list native locks as `blocked`:
"marimo rejects the API because the browser cannot provide the native process, synchronization, or shared-memory primitive it requires."
This is not a marimo bug so much as a documented design choice that happens to collide with a real dependency of the real workload.
**JupyterLite applies no such interception** —
the identical Python (same wheels, same install sequence) ran the same read to completion under JupyterLite's plain Pyodide kernel,
confirming the failure is marimo-specific, not a Pyodide-, zarr-, or eegprep-lean-side issue.
`eegprep-lean` itself has no `multiprocessing` usage (checked directly in the vendored wheel);
the `Lock()` call originates inside `numcodecs`, three dependency layers below anything OSA or NEMAR own,
so there is no realistic workaround on OSA's side short of not using marimo.

## What opening a workspace file takes

**A `.py` script.** marimo's own notebook format IS a plain `.py` file,
but not the same plain script `osa.save_script()` writes (per-run scripts with module-level statements, meant to run top-to-bottom):
it needs `@app.cell`-decorated functions with marimo's one-definition rule.
`marimo convert <script>.py -o <out>.py` handles this (needs `jupytext`, verified above) for a single already-percent-formatted or plain script;
multiple per-run scripts from one session need concatenating (with `# %%` markers) before conversion,
which is a step OSA would have to add, not something either tool does automatically across separate files.
JupyterLite has no notebook-native `.py` concept at all:
opening a bare `.py` in its `edit` app is a text editor, not something that runs cell-by-cell;
turning per-run scripts into something JupyterLite can execute means writing (or generating) an `.ipynb` directly, one cell per script —
which is exactly the shape phase 4a's `notebook.ipynb` export already is.

**An `.ipynb`.** JupyterLite is native here:
`jupyter lite build --contents <dir>` indexes any `.ipynb` placed in it and serves it at `notebooks/index.html?path=<name>.ipynb`
through the standard `@jupyterlite/contents` Contents API
(confirmed live: `GET /api/contents/all.json` lists both test notebooks built for this ADR with correct `size`/`path`/`type`).
This is a **build-time import**, not a live bridge:
the files were placed in `content/` before running `jupyter lite build`,
matching exactly what #433 already predicted ("Phase 4 should plan on IndexedDB plus an import step").
Nothing in this build let JupyterLite read a *different* origin's OPFS/IndexedDB directly,
and nothing found while re-checking this expected it to;
the real mechanism stays:
the widget exports a zip, and getting that zip's contents into JupyterLite's own Contents store (its own IndexedDB, at ITS origin) is a separate
upload/import step, e.g. via the documented `?fromURL=` content-loading parameter (not tried here, but documented)
or a drag-and-drop upload into the file browser.
marimo can open an `.ipynb` too, but only by first converting it to a `.py` with `marimo convert`
(same tool, same `jupytext` dependency, same caveats as above) — it never opens `.ipynb` directly.

**Cross-origin reachability.** The notebook surface opens on its own origin (per #433, never the widget's embedding page's origin).
Two live checks, both against real, production endpoints:

- **`zarr.nemar.org` is origin-gated, confirmed live.**
  A GET with `Origin: https://nemar.org` returns `access-control-allow-origin: https://nemar.org` (reflecting that specific allowed origin)
  plus `Range`/`GET, HEAD, OPTIONS`/exposed `ETag, Content-Length, Content-Range, Accept-Ranges` headers a sharded Zarr reader needs.
  A GET with `Origin: https://example-third-party-site.com` gets **no CORS headers at all** — a browser fetch from that origin would be blocked.
  This matches `.context/browser-execution-tool-design.md`'s own note that the S3 bucket
  "admits `nemar.org` and its subdomains, the website Pages previews, `demo.osc.earth` and loopback,"
  measured here directly rather than only cited.
- **OSA's own wheel route (`GET /{community}/runtime/{file_name}`) could not be probed live** —
  production (`api.osc.earth/osa`) is still on **v0.8.13**, which predates this epic's phases 1-3
  (no `client_tools`/`runtime` config is live yet; `/nemar/config` 404s, and the version endpoint confirms this is not yet the epic's code).
  Read from source instead:
  `src/api/routers/community.py`'s `get_runtime_wheel` sets no CORS header itself;
  CORS for the whole community API is `_is_allowed_origin`
  (`community.py:883-924`, platform demo origins plus the community's own `cors_origins`) enforced as `CORSMiddleware` (`src/api/main.py`),
  and independently at the edge by `workers/osa-worker/index.js`'s `getCorsHeaders`/`isAllowedOrigin`,
  whose allowlist is a fixed set of each community's known embed origins
  (for nemar: `nemar.org`, `www.nemar.org`, plus the platform's `*.osc.earth`/`*.pages.dev` demo hosts) —
  an origin not on that list gets `Access-Control-Allow-Origin: https://demo.osc.earth`
  (a **fixed fallback value, not the requester's own origin**), which fails the browser's own CORS check for any other origin.
  **So once the wheel route deploys, it will have the same shape as `zarr.nemar.org`'s:
  reachable only from nemar.org (or the platform demo hosts), not from an arbitrary notebook-surface origin.**
  If the eventual notebook surface is hosted anywhere else,
  both the wheel route's `cors_origins` (`src/assistants/nemar/config.yaml`) and the S3 bucket's CORS policy need that origin added —
  the same requirement embedding the chat widget already has,
  not a new category of work, but a real one to plan for rather than assume away.

## Decision

**Skip marimo as the notebook surface.** The 2026-09-16 verdict is reaffirmed, for a now-five-reason, still entirely integration-based case:
the original four (no `postMessage`/embedding API — still open, unanswered in substance;
its own storage — unchanged;
CDN-only Pyodide, now a full version generation further from 0.29.5, with a documented self-host flag that does not exist in the shipped release;
the one-definition model, whose conversion pass is real but needs an extra dependency and an offline step)
plus a fifth found only by running the actual workload:
**marimo's own WASM concurrency sandbox refuses the `multiprocessing.Lock` `numcodecs`'s blosc codec needs to decompress real Zarr chunks,
so marimo cannot complete the NEMAR read at all, in any configuration tried.**
The speed question #423 asked about is settled and explicitly does **not** favor marimo:
under a CDN-matched control, the two surfaces are within a second of each other for a trivial cell,
and the only workload that actually matters (the real NEMAR read) marimo cannot run to completion regardless of speed.

**Adopt JupyterLite behind a small custom drive, pinned to Pyodide 0.29.5, and still build it third**,
after the editable re-run panel in the chat widget —
nothing here changes that ordering;
the panel covers "keep tinkering" for a fraction of a full notebook surface's cost,
and that argument was about integration cost and phase sequencing, not about which notebook surface wins,
so it stands regardless of this ADR's verdict.
What is new: pinning to exactly 0.29.5 is confirmed possible (unlike marimo, where it is not possible at all today)
but costs a **529-531 MB unpruned** static deploy versus **~64 MB** for the unpinned default
(which drifts to whatever Pyodide version `jupyterlite-pyodide-kernel` ships next, currently 314.0.0, the same generation marimo uses and not OSA's 0.29.5).
That size question — pinned-and-heavy versus unpinned-and-light —
is a real, unresolved decision for whoever builds phase 4's JupyterLite integration,
and is called out explicitly below rather than silently assumed toward the "adopt" side of this ADR's verdict.

## Consequences

- The five-reason case against marimo is written down, sourced, and dated,
  so it no longer depends on a chat transcript no one else can read (the problem #423 was filed to fix).
- A future re-check of marimo does not need to re-litigate speed;
  it does need to re-check gap 5 specifically (has marimo's WASM concurrency sandbox grown an allowance for a codec-internal lock?)
  and gap 3 (has `--offline` shipped in a release?),
  both of which are cheap, dated, falsifiable checks against a specific future release rather than a redo of this whole ADR.
- JupyterLite is confirmed workable end-to-end against the real, live NEMAR read,
  including the same wheel-and-package-list problem OSA's own lock overlay already solves differently
  (`preload` + `loadPackage`, versus this ADR's explicit `micropip.install([...], deps=False)` list) —
  whoever builds phase 4's integration should not have to rediscover that a bare `micropip.install(<eegprep-lean wheel>)` fails;
  the exact list is recorded above.
- **Not resolved by this ADR, and worth a decision before or during phase 4's build:**
  whether JupyterLite ships pinned-to-0.29.5-and-529+ MB or unpinned-and-~64 MB
  (or a pruned pinned build via `--no-unused-shared-packages`, size not measured here).
  This does not change marimo's rejection either way,
  so it was left open rather than forced to a conclusion this ADR's evidence does not actually settle.
- **Not measured, and why:** a pruned (`--no-unused-shared-packages`) pinned JupyterLite build's size
  (ran out of scope for this ADR's measurement budget, not attempted);
  marimo's `--offline` self-host flag's actual cost (not present in the installed release, so there was nothing to measure);
  OSA's own wheel route's live CORS headers
  (production has not deployed phases 1-3 yet; answered from source instead, cited above, and should be re-verified live once it deploys).

## References

- #423, #433, epic #429.
- marimo-team/marimo#8139 (still open, 2026-09-23).
- `docs.marimo.io/guides/wasm.md`, `docs.marimo.io/guides/publishing/self_host_wasm.md` (fetched 2026-09-23).
- `docs.jupyterlite.readthedocs.io` quickstart "Embed a live REPL on a website" (the `?code=&execute=1` mechanism used for the timed measurements).
- `docs/community-browser-runtime.md`, `frontend/browser-harness/README.md` (OSA's own runtime figures, 2026-09-22).
- `.context/browser-execution-tool-design.md`, "Persistence and the notebook surface" and its open questions (updated alongside this ADR).
- `src/assistants/nemar/config.yaml`, `src/assistants/nemar/runtime/`, `frontend/browser-harness/widget_e2e.py`, `frontend/browser-harness/chrome.js`
  (workload, wheels, and the DevTools-protocol pattern `notebook-bench.js` reuses).
- `frontend/browser-harness/notebook-bench.js` (this ADR's measuring script).
