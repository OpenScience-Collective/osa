# Notebook surface measurements, for ADR 0010

This note holds the method, full results, reproduction commands, exact code, integration-gap re-check,
and file-opening analysis behind [`docs/adr/0010-the-notebook-surface.md`](../docs/adr/0010-the-notebook-surface.md) ("The notebook surface").
That ADR states the decision;
this note is the evidence for it, kept out of the ADR itself so the ADR stays to the four sections every record in `docs/adr/` uses.

All measurements are dated **2026-09-23**, on one machine, in **Chrome 153.0.8010.53** headless, driven over the DevTools protocol.
Tool versions: **marimo 0.24.2** (the current release on the Python Package Index (PyPI);
released 2026-09-11, five days before the original 2026-09-16 review and still current today,
marimo has not shipped a release since #423 was filed), **jupyterlite-core 0.8.4**,
**jupyterlite-pyodide-kernel 0.8.0** (pinned explicitly; see "Pyodide pinning" below for why), **uv 0.12.15**, **Bun 1.4.2**.
All were installed and run through `uvx`/`uv tool run` and `bun`, never `pip`/`conda`/`npm`.

## Method

`frontend/browser-harness/notebook-bench.js` (committed) times a page from just before navigation to the first poll where a completion condition is true,
cold (fresh browser profile) and warm (second navigation, same profile and origin).
It imports its Chrome-driving primitives directly from `frontend/browser-harness/chrome.js`
(`findChrome`, `launch`, `connect`, `NetworkRecorder`, `attachWithNetwork`) rather than duplicating them,
so a fix to one benefits both;
an earlier version of this script did carry its own copy, which is how it briefly dropped `NetworkRecorder`'s `Network.loadingFailed` handling
(a failed request then counted as a zero-byte success rather than a failure; both scripts now share the fixed version).
`chrome.js` itself gained four `export` keywords and one optional parameter for this
(`attachWithNetwork`'s auto-attach now takes a target-type list, defaulting to `['worker']` as before);
`bun frontend/browser-harness/chrome.js` was re-run after that change and still passes every one of its checks, unchanged.

Completion is normally text: the script polls `document.body.innerText` for a sentinel string.
The special sentinel `__harness__` instead polls `window.__harness.done`,
the shape `frontend/browser-harness/cache-boot.js` and the rest of this harness already use,
so OSA's own runtime can be measured with this same script on its own `nemarlike` page, rather than a separate ad hoc one.

Two robustness fixes came from using the bench in anger:

- **A false-positive sentinel match.** JupyterLite's Read-Eval-Print Loop (REPL) and notebook interfaces echo submitted code into the page
  before execution finishes,
  so a sentinel written as a literal substring of the printed message (for example `print("OSA_BENCH_TRIVIAL_OK", result)`)
  matched on the unexecuted source and reported a bogus sub-second "completion".
  Every sentinel below is assembled at runtime from pieces that never appear contiguous in the source
  (`"OSA_BENCH_TRIVIAL" + "_DONE"`), so only real output can match.
  marimo's `--mode run` export hides source by default and was not affected, but the fix was applied to both surfaces for consistency.
  The bench now also warns on stderr if a sentinel matches on the very first poll,
  since that is the same signature a reviewer should treat as suspect.
- **A transient poll failure aborting the whole run.** A page-triggered reload or redirect mid-poll tears down the JavaScript execution context
  `Runtime.evaluate` was sent to;
  the bench now catches that one error, skips that single poll, and continues, rather than failing the run outright.
  `Runtime.exceptionThrown` is also now recorded and folded into a failed run's result, matching `chrome.js`'s own `runPage`.

The workload is the exact snippet in `src/assistants/nemar/config.yaml`, "Running code in the reader's browser"
(the fenced block `frontend/test-data-lane.js` and `frontend/browser-harness/widget_e2e.py` also extract programmatically),
filled in with `widget_e2e.py`'s own substitution values for nm000103's first recording
(`sub-NDARAA075AMK_task-DespicableMe_eeg.set`, group `eeg_250hz`, `start_sample=2500`, `n_samples=500`, `channels=[0,1,2,3]`).
The benchmark code uses `widget_e2e.py`'s `READ` variant (`index.stores[0]`) rather than re-deriving `store.group(GROUP)`,
which is the same read against the same window and is itself in-repo, not a fabricated stand-in;
see "NEMAR read, exact code" below for what actually ran.
Both notebooks read `eegprep-lean` from a locally re-served copy of the exact vendored wheel
(`src/assistants/nemar/runtime/wheels/eegprep_lean-0.1.0.dev2-py3-none-any.whl`,
its cryptographic hash matching `nemar-pyodide-lock.json`) and its `zarr` companion wheel, over loopback;
the NEMAR data read itself is against the live, public `zarr.nemar.org`.

**Loopback versus content delivery network (CDN) caveat.** JupyterLite's self-hosted, pinned build serves its interpreter over loopback
(no real network latency);
marimo's interpreter comes from the real jsDelivr CDN.
That is not a fair wall-clock comparison by itself,
so a third JupyterLite configuration is included below, the **default**, unpinned build,
which also fetches its interpreter from jsDelivr, to control for network path.
Under that control, both surfaces fetch the same Pyodide version (314.0.0) from the same CDN.

## Results

Cold is a fresh browser profile; warm is a second navigation in the same profile, same origin (`chrome.js`'s own "warm" methodology).
"Bytes"/"reqs" are DevTools `encodedDataLength`/request counts across the page and every attached Worker;
"failed" is the count of requests the browser itself never completed (a dropped connection, a refused cross-origin fetch),
which a byte total alone reports identically to a genuine zero-byte cache hit.

| Surface | Config | Pyodide (loaded from) | Workload | Cold s | Cold bytes / reqs / failed | Warm s | Warm bytes / reqs / failed |
|---|---|---|---|---:|---:|---:|---:|
| marimo 0.24.2 | `export html-wasm --mode run` | 314.0.0 (jsDelivr CDN) | trivial `1+1` | 4.46 | 19.67 MB / 318 / 0 | 3.65 | 51.8 KB / 318 / 0 |
| marimo 0.24.2 | same | 314.0.0 (CDN) | NEMAR read | fails | 33.67 MB / 345 / 2 | fails | 54.2 KB / 343 / 2 |
| JupyterLite (core 0.8.4, kernel 0.8.0) | default, unpinned | 314.0.0 (jsDelivr CDN) | trivial | 3.49 | 16.14 MB / 116 / 0 | 2.83 | 58.9 KB / 115 / 0 |
| JupyterLite | pinned, self-hosted | **0.29.5** (loopback) | trivial | 2.69 | 23.35 MB / 116 / 0 | 2.43 | 59.0 KB / 116 / 0 |
| JupyterLite | pinned, self-hosted | **0.29.5** (loopback) | NEMAR read | 5.50 | 37.65 MB / 146 / 2 | 3.25 | 58.8 KB / 144 / 2 |
| OSA's own runtime | `cache-boot.html?variant=nemar`, boot and import check only, not a full data read | **0.29.5** (loopback) | boot | 2.23 | 19.09 MB / 37 / 0 | 1.82 | 126.3 KB / 36 / 0 |

The 2 failed requests on both NEMAR-read rows are the same in both surfaces:
`eegprep-lean`'s store discovery probes a `.zarray`/`.zattrs` path that does not exist before finding the one that does,
and that probe's response arrives as `net::ERR_ABORTED` rather than a plain 404, which the fixed `NetworkRecorder` now counts explicitly.
This is expected discovery behavior, not an instability signal;
it was previously invisible, folded into "0 bytes" indistinguishably from a genuine cache hit.

**OSA's own runtime, measured directly with this same bench, not cited from an older figure.**
`frontend/browser-harness/README.md` separately reports "the generated worker boots under the production policy: 7.9 s on 0.29.5 with numpy and
matplotlib preloaded, served from the browser's HTTP cache".
That is a different, smaller measurement than the row above: it times `harness.js`'s own generic boot check
(`preload: ['numpy', 'matplotlib']`, two packages, no `zarr` or `eegprep-lean`), not NEMAR's actual four-package overlay, and it runs earlier,
in a different position in that page's own test sequence.
The row above times NEMAR's real, shipped overlay (`numpy`, `matplotlib`, `zarr`, `eegprep-lean`, about 19 MB uncompressed,
matching `docs/community-browser-runtime.md`'s own combined figure of 18.9 MB closely enough to confirm it is measuring the same weight)
on `cache-boot.html?variant=nemar`, the exact page `chrome.js`'s own warm-cache check already boots, with this same bench script.
The two figures are not measuring the same thing and should not be read as contradicting each other;
this note uses the freshly measured, same-bench figure for comparison, and states plainly that it differs from, and does not replace,
the README's own generic-boot figure.

**Not re-measured with this bench: OSA's own full NEMAR read (data fetched, not just packages imported).**
`cache-boot.js` only imports `eegprep_lean` and `zarr` as a boot proof (`import eegprep_lean, zarr\n"ok"`);
it does not fetch or plot a window.
Timing OSA's own widget through an actual `zarr.nemar.org` read with this bench would mean driving `widget_e2e.py`'s real chat flow,
which was out of this note's time budget.
`frontend/browser-harness/README.md`'s own citation stands in its place: "read" returns a result "under 10 s from Run to result,
with numpy and matplotlib already in the browser's HTTP cache", a warm-oriented, non-bench measurement, not directly comparable in methodology
to the cold/warm pairs above, and reported here as a separate, differently scoped number rather than folded into the table.

**Headline reading.** Under the CDN-matched control (JupyterLite's default row versus marimo's row), marimo and JupyterLite differ by well under
a second in both directions; **neither is decisively faster for a trivial cell.**
The "marimo feels faster" impression the original review flagged as unmeasured does not survive a controlled comparison;
if anything JupyterLite was marginally faster here.
The number that actually separates the two surfaces is not a time at all:
**marimo could not produce the NEMAR read's output in any configuration tried**, cold or warm, self-hosted-adjacent or CDN.
JupyterLite, pinned to OSA's own 0.29.5, completed it in 5.50 s cold, 3.25 s warm,
close to OSA's own boot-only figure's order of magnitude and inside the "under 10 s" figure cited for OSA's own full read above,
though that last comparison is explicitly not apples to apples (different preload sets, different UI, and a non-bench citation), and is reported as
context, not as a claimed win.

**Why marimo fails on the real workload** is in "Integration gaps re-checked" below (gap 5);
it is not a timeout or a missing package, it is a hard rejection from marimo's own WebAssembly (WASM) sandbox,
discovered by actually running the read, not by reasoning about it.
The original review never ran the real workload against marimo at all.

**Static site size.** `marimo export html-wasm` produces a 27 MB directory regardless of workload
(its shell always bundles the full marimo editor, unused language modes and diagram support included,
since `--mode run` still ships the same asset graph as `--mode edit`).
A JupyterLite build's own shell (`repl`+`notebooks` apps, no bundled Pyodide) is 64 to 65 MB,
matching the original review's "69 MB" figure closely enough to confirm that figure was the shell without a locally pinned interpreter.
Pinning JupyterLite to Pyodide 0.29.5 via `--pyodide=<tarball>` (see "Pyodide pinning and self-hosting" below) grows the build to
**529 to 531 MB total**, because the full, unpruned Pyodide distribution (464 MB) is extracted and copied into the deployed site, not merely
referenced.
`jupyter lite build` accepts `--no-unused-shared-packages` to prune packages nothing in `content/` imports;
**that flag was not tried, so a pruned, still-pinned size is not measured**, recorded as an open item in the ADR rather than guessed.

## Reproduction

```bash
# marimo, current release, no version pin needed (nothing to pin against)
uvx marimo export html-wasm <notebook>.py -o <dir> --mode run -f

# JupyterLite, pinned to the exact interpreter OSA's own runtime uses
curl -LO https://github.com/pyodide/pyodide/releases/download/0.29.5/pyodide-0.29.5.tar.bz2
uv tool run --from jupyterlite-core --with "jupyterlite-pyodide-kernel==0.8.0" \
  --with jupyter-server jupyter lite build \
  --contents content --output-dir <dir> \
  --pyodide=./pyodide-0.29.5.tar.bz2 --apps repl --apps notebooks --apps lab

# timing, either surface, or OSA's own runtime on serve.js's nemarlike page
bun frontend/browser-harness/serve.js 8794 &
bun frontend/browser-harness/notebook-bench.js \
  "http://127.0.0.1:8794/nemarlike/browser-harness/cache-boot.html?variant=nemar" \
  __harness__ 60000
bun frontend/browser-harness/notebook-bench.js <marimo-or-jupyterlite-url> <sentinel> [timeoutMs]
```

The REPL app's `?kernel=python&code=<url-encoded>&execute=1` uniform resource locator (URL) parameters
(documented at `quickstart/embed-repl.md` on `jupyterlite.readthedocs.io`) gave a controlled, auto-executing entry point comparable to marimo's own
auto-run `--mode run` export, without needing to automate a "Run All" click through either interface.
The exact notebook sources, the wheel-serving stand-in server, and the URL-encoded REPL commands used for the table above live only under this
session's scratchpad, per this task's instructions not to commit throwaway builds;
the commands above and the workload code inline in this note are what recreate them.

## NEMAR read, exact code

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

The explicit package list and `deps=False` is not cosmetic; it is required, and its absence is itself a finding.
`micropip.install(<eegprep-lean wheel URL>)` alone fails with
`ImportError: read_window needs the zarr extra ... because zarr pins numcodecs and numcodecs publishes no emscripten wheel at any version`
(eegprep-lean's own error message, raised from its `__getattr__` lazy-import guard, measured verbatim in both surfaces).
OSA's own runtime never hits this because its lock overlay's `preload` resolves `zarr`'s Pyodide-native dependencies
(`numcodecs`, `donfig`, `google-crc32c`, `msgspec`, `packaging`, `typing-extensions`) through `loadPackage` before any import runs
(`docs/community-browser-runtime.md`, "The lock overlay").
Neither marimo nor JupyterLite has an equivalent preload-and-lock mechanism for a community-owned wheel;
a person or the widget has to reproduce that install list by hand (as above),
which is itself part of the answer to "what opening a workspace file takes" below.

## Integration gaps re-checked

**1. The `postMessage` embedding application programming interface (API) is still absent, and the maintainer's response was fuller than
"unanswered".**
marimo issue #8139 (opened 2026-02-05, a Google-Sheets-sidebar embedding request) is **still open**.
A marimo team member (`dmadisetti`) left two comments the same day:
first, "It might be possible with an anywidget if it's just data transfer... What's the minimum api you'd require?";
second, "Also `&show-chrome=false` to hide the sidebar in an iframe" (a display-only tip about the existing embed URL, not a data channel).
**Neither answers the `postMessage` request**: the first asks a scoping question that was never answered by the issue's author,
and the second addresses only how the notebook looks in an iframe, not how it would receive a widget's files or state.
No further comment, no linked pull request, and no code exists as of 2026-09-23, over seven months of silence after that exchange.
The only documented mechanisms for getting files into a marimo export remain a bundled `public/` folder baked in at **export time** and
`mo.notebook_location()` to read it, or a GitHub-hosted notebook fetched by molab.
Both are static, pre-export inputs, not a live channel a widget could use after the person is already in a chat session.

**2. Storage is still marimo's own, unchanged in substance, now more clearly documented.**
marimo's WASM guide (`docs.marimo.io/guides/wasm.md`, fetched 2026-09-23) confirms the same two sanctioned patterns as before:
a `public/` folder copied in at export time, and fetching remote files at runtime (subject to Cross-Origin Resource Sharing, CORS; see below).
Nothing reads or writes the origin storage, the browser's Origin Private File System (OPFS) or IndexedDB, that the widget's workspace
(#433's scope) would use, and nothing in the guide has changed that.
This is the same constraint JupyterLite has;
#433 already calls this a non-goal, not a JupyterLite-only cost, since "neither notebook surface can reach the origin private file system
the widget writes to."

**3. Pyodide pinning and self-hosting were re-checked directly, and the two surfaces now diverge more, not less.**

- **marimo: still CDN-only, and further from 0.29.5 than at the last review.**
  `marimo/_pyodide/pyodide_constraints.py` (in the installed 0.24.2 package) hardcodes `PYODIDE_VERSION = "314.0.0"`,
  the new CPython-3.14-based Pyodide numbering, a full generation past both 0.27.7 (what the original review measured) and OSA's pinned 0.29.5.
  The exported HyperText Markup Language (HTML) requests it from `https://cdn.jsdelivr.net/pyodide/v314.0.0/full/`,
  confirmed by reading the generated `index.html` directly.
  `docs.marimo.io/guides/publishing/self_host_wasm.md` documents a `--offline` export flag
  ("downloads the Python runtime and notebook packages alongside the exported HTML... via Playwright") that would answer the CDN-only complaint,
  but `marimo export html-wasm --help`, run against the installed current release, does not list `--offline` as an option,
  and the installed package's source has no reference to it either.
  The documentation describes a capability the shipped release does not have.
  There is still no way to make a marimo export load Pyodide 0.29.5 specifically;
  the version is marimo's own choice, not a configuration point.
- **JupyterLite: can be pinned to exactly 0.29.5, confirmed by building it, at a real size cost, and pinning is mutually exclusive with staying
  CDN-hosted.**
  `jupyter lite build --pyodide=<path-or-URL>` (documented as `PyodideAddon.pyodide_url`) accepts a local tarball path **or a URL**;
  both were tried.
  Passing the official `github.com/pyodide/pyodide/releases/download/0.29.5/pyodide-0.29.5.tar.bz2` release URL was expected to let the build
  reference Pyodide at that URL and stay small;
  it does not.
  Measured directly, the build **downloads and extracts the full tarball into the deployed site regardless of whether the source was a local
  path or a URL** (`pyodideUrl` in the built `jupyter-lite.json` always resolves to `./static/pyodide/pyodide.mjs`, a local file, never the
  source URL).
  So the choice is binary: the **default** build (unpinned, 314.0.0, about 64 MB shell, the same CDN marimo uses)
  or a **pinned** build (exactly 0.29.5, confirmed via the built `pyodide-lock.json`'s `info.python: "3.13.2"`, matching Pyodide 0.29.5's known
  Python version) that is 529 to 531 MB unpruned.
  `jupyterlite-pyodide-kernel` must also be pinned to `0.8.0` for this: `0.8.6` (the current release on PyPI) defaults
  to Pyodide `314.*`, the same generation as marimo, and only the `0.8.0` line still pairs with `0.29.*`.

**4. The one-definition-per-variable model is confirmed, and a real conversion tool exists and was verified to work.**
Two scripts were written to mimic what `osa.save_script()` would actually produce across two assistant turns in one session;
the first defines `index`, `store`, `window`, `fig`;
the second reuses `index`/`store` from the warm session and **redefines** `window` and `fig` with a different sample range,
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

Concatenated into one file (as `marimo convert` would receive when a session's scripts are combined into one notebook) with `# %%` cell markers
and run through `marimo convert` (requires `jupytext`, an **extra, non-default dependency**;
`marimo export` and `marimo edit` do not need it, only `convert` from percent-format source does;
the command-line interface (CLI) fails with a clear message and install instructions if it is missing),
the converter correctly identified `index` and `store` as **shared** (threaded through the cell signature:
`async def _(eegprep_lean, index, store):`) and privatized the **redefined-and-not-depended-on** `window`/`fig` to cell-local `_window`/`_fig`
automatically, valid marimo, no manual fix-up needed for this pattern.

A second, smaller case that reads its own prior value (`count = count + 1` across two cells, the read-modify-write shape rather than write-only)
converts differently: the tool **renames** the variable per cell (`count_1 = count + 1`) rather than reusing the name,
because marimo's reactive graph cannot have two cells both defining and reading the same name.
Both conversions were **executed in the browser, not just inspected**: the write-only case reproduced the exact NEMAR read and plot behavior
described above, and the read-modify-write case printed `OSA_BENCH_RUN1_DONE 1` then `OSA_BENCH_RUN2_DONE 2`, correct in both cells.
So: the conversion pass is real, automatable, and was verified to produce correct output for a realistic two-turn redefinition,
but it needs `jupytext` installed, is an offline, build-time step (nothing converts a plain script live inside a running marimo WASM notebook),
and a person or generated code that expects a variable to keep its exact name across turns will sometimes see it renamed.

**5. New: marimo's own WASM concurrency sandbox blocks the real NEMAR read outright, not one of the original four reasons, found only because
the actual workload was run.**
Every attempt at the NEMAR read in marimo (every marimo row in the results table) fails with the same traceback,
reached only after packages load and the store is opened successfully (`zarr.nemar.org` is reached, discovery probes 404 exactly as they do in
a working run):

```
File "numcodecs/blosc.pyx", line 86, in numcodecs.blosc.get_mutex
File ".../marimo/_runtime/_wasm/_concurrency/_mp_context.py", line 54, in _unsupported
    raise UnsupportedWasmConcurrencyError(
marimo._runtime._wasm._concurrency._wait.UnsupportedWasmConcurrencyError:
    multiprocessing.Lock is not supported by the Pyodide WASM process adapter
```

`numcodecs`'s `blosc` codec (used to decompress the real, blosc-compressed Zarr chunks nm000103 is stored as) allocates a mutex the first time it
decodes a chunk (`numcodecs.blosc.get_mutex`, called from `_get_use_threads`, called from `decompress`).
marimo's own WASM sandbox (`marimo/_runtime/_wasm/_concurrency/_mp_context.py`) intentionally rejects this;
its documented "Concurrency" support levels (`docs.marimo.io/guides/wasm.md`) list native locks as `blocked`:
"marimo rejects the API because the browser cannot provide the native process, synchronization, or shared-memory primitive it requires."
This is not a marimo bug so much as a documented design choice that happens to collide with a real dependency of the real workload.
**JupyterLite applies no such interception**: the identical Python (same wheels, same install sequence) ran the same read to completion under
JupyterLite's plain Pyodide kernel, confirming the failure is marimo-specific, not a Pyodide, zarr, or eegprep-lean issue.
`eegprep-lean` itself has no `multiprocessing` usage (checked directly in the vendored wheel);
the `Lock()` call originates inside `numcodecs`, three dependency layers below anything OSA or NEMAR own,
so there is no realistic workaround on OSA's side short of not using marimo.

## What opening a workspace file takes

**A `.py` script.** marimo's own notebook format IS a plain `.py` file, but not the same plain script `osa.save_script()` writes (per-run scripts
with module-level statements, meant to run top to bottom): it needs `@app.cell`-decorated functions under marimo's one-definition rule.
`marimo convert <script>.py -o <out>.py` handles this (needs `jupytext`, verified above) for a single already-percent-formatted or plain script;
multiple per-run scripts from one session need concatenating (with `# %%` markers) before conversion,
which is a step OSA would have to add, not something either tool does automatically across separate files.
JupyterLite has no notebook-native `.py` concept at all: opening a bare `.py` in its `edit` app is a text editor, not something that runs
cell by cell;
turning per-run scripts into something JupyterLite can execute means writing (or generating) an `.ipynb` directly, one cell per script,
which is exactly the shape phase 4a's `notebook.ipynb` export already is.

**An `.ipynb`.** JupyterLite is native here: `jupyter lite build --contents <dir>` indexes any `.ipynb` placed in it and serves it at
`notebooks/index.html?path=<name>.ipynb` through the standard `@jupyterlite/contents` Contents API
(confirmed live: `GET /api/contents/all.json` lists both test notebooks built for this note with correct `size`/`path`/`type`).
This is a **build-time import**, not a live bridge: the files were placed in `content/` before running `jupyter lite build`,
matching exactly what #433 already predicted ("Phase 4 should plan on IndexedDB plus an import step").
Nothing in this build let JupyterLite read a *different* origin's storage directly, and nothing found while re-checking this expected it to;
the real mechanism stays: the widget exports a zip, and getting that zip's contents into JupyterLite's own Contents store
(its own IndexedDB, at its own origin) is a separate upload or import step, for example via the documented `?fromURL=` content-loading
parameter (not tried here, but documented) or a drag-and-drop upload into the file browser.
marimo can open an `.ipynb` too, but only by first converting it to a `.py` with `marimo convert`
(same tool, same `jupytext` dependency, same caveats as above); it never opens `.ipynb` directly.

**Cross-origin reachability.** The notebook surface opens on its own origin (per #433, never the widget's embedding page's origin).
Two live checks, both against real, production endpoints:

- **`zarr.nemar.org` is origin-gated, confirmed live.**
  A GET with `Origin: https://nemar.org` returns `access-control-allow-origin: https://nemar.org` (reflecting that specific allowed origin) plus
  `Range`/`GET, HEAD, OPTIONS`/exposed `ETag, Content-Length, Content-Range, Accept-Ranges` headers a sharded Zarr reader needs.
  A GET with `Origin: https://example-third-party-site.com` gets **no CORS headers at all**, so a browser fetch from that origin would be
  blocked.
  It also admits `osc.earth` and every `*.osc.earth` subdomain:
  `Origin: https://osc.earth`, `https://notebook.osc.earth` and `https://demo.osc.earth` each get their own origin reflected
  (checked 2026-09-23; `isOscOrigin` in `nemarOrg/nemar-cli`'s `backend/src/services/cors-origins.ts`).
  This matches `.context/browser-execution-tool-design.md`'s own note that the S3 bucket "admits `nemar.org` and its subdomains, the website
  Pages previews, `demo.osc.earth` and loopback," measured here directly rather than only cited.
- **OSA's own wheel route (`GET /{community}/runtime/{file_name}`) could not be probed live.**
  Production (`api.osc.earth/osa`) is still on **v0.8.13**, which predates this epic's phases 1 to 3 (no `client_tools`/`runtime` config is live
  yet; `/nemar/config` returns a 404, and the version endpoint confirms this is not yet the epic's code).
  Read from source instead: `src/api/routers/community.py`'s `get_runtime_wheel` sets no CORS header itself;
  CORS for the whole community API is `_is_authorized_origin` (`community.py:868-924`, platform demo origins plus the community's own
  `cors_origins`) enforced as `CORSMiddleware` (`src/api/main.py`), and independently at the edge by `workers/osa-worker/index.js`'s
  `getCorsHeaders`/`isAllowedOrigin`, whose allowlist is a fixed set of each community's known embed origins
  (for nemar: `nemar.org`, `www.nemar.org`, plus the platform's demo hosts: the bare `osc.earth`, `demo.osc.earth`,
  `*-demo.osc.earth` and the legacy `*.pages.dev` previews).
  An origin not on that list gets `Access-Control-Allow-Origin: https://demo.osc.earth`, a **fixed fallback value, not the requester's own
  origin**, which fails the browser's own CORS check for any other origin.
  **So once the wheel route deploys, it will be narrower than `zarr.nemar.org`:
  reachable from nemar.org and the platform's demo hosts, not from an arbitrary `osc.earth` subdomain.**
  A notebook surface at, say, `notebook.osc.earth` would read the archive through `zarr.nemar.org` today,
  and still be refused the `eegprep-lean` wheel by both the edge and the API.
  Wherever it is hosted, its origin needs adding to the edge's `isAllowedOrigin` and the community's `cors_origins`
  (`src/assistants/nemar/config.yaml`), and to the S3 bucket's CORS policy,
  which admits `demo.osc.earth` but no other `osc.earth` host.
  That is the same requirement embedding the chat widget already has: not a new category of work,
  but a real one to plan for rather than assume away.

## References

- #423, #433, epic #429.
- marimo-team/marimo#8139 (still open, 2026-09-23).
- `docs.marimo.io/guides/wasm.md`, `docs.marimo.io/guides/publishing/self_host_wasm.md` (fetched 2026-09-23).
- `jupyterlite.readthedocs.io` quickstart "Embed a live REPL on a website" (the `?code=&execute=1` mechanism used for the timed measurements).
- `docs/community-browser-runtime.md`, `frontend/browser-harness/README.md` (OSA's own runtime figures, 2026-09-22, and see "OSA's own
  runtime" above for how this note's directly measured figure differs from and does not replace the README's generic-boot figure).
- `.context/browser-execution-tool-design.md`, "Persistence and the notebook surface" and its open questions.
- `src/assistants/nemar/config.yaml`, `src/assistants/nemar/runtime/`, `frontend/browser-harness/widget_e2e.py`,
  `frontend/browser-harness/chrome.js`, `frontend/browser-harness/serve.js` (workload, wheels, and the DevTools-protocol primitives
  `notebook-bench.js` imports).
- `frontend/browser-harness/notebook-bench.js` (this note's measuring script).
