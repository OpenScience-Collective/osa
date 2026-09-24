# Browser runtime harness

Boots the real `buildWorkerSource` output against real Pyodide,
under a real `Content-Security-Policy` header,
and exercises the half of the runtime that no `bun` test can reach.

It runs in CI, in headless Chrome, as a gate:
`frontend/browser-harness/chrome.js` opens the `nemarlike` page and requires every check to pass,
then opens the `control` page and requires its boot to fail,
then opens `cache-boot.html` twice more (see "Wheel caching" below)
to require the browser's own HTTP cache to have done its job the second and third time NEMAR's runtime boots,
then opens `workspace.html` twice (`?phase=write`, then `?phase=read`, see "Workspace" below)
to require the persistent workspace (#433) to actually round-trip through a real IndexedDB, across a reload.
The Python half is ALSO tested under Bun:
`frontend/test-worker-core.js` and `frontend/test-data-lane.js` run the same worker core
against the real Pyodide from npm.
What only a browser can check is what this harness is for:
the Content-Security-Policy, the blob worker, the egress guard installed on a real worker global,
and a lock overlay's wheels loaded by URL with each one's sha256 enforced,
which Pyodide's Node loader does not do.

## Running it

```bash
bun frontend/browser-harness/chrome.js          # headless, as CI runs it
bun frontend/browser-harness/serve.js 8791      # or serve the pages and open one
```

Then open <http://127.0.0.1:8791/nemarlike/browser-harness/index.html>.
`chrome.js` finds Chrome at `CHROME_PATH` or where it installs on macOS and on the GitHub runner;
under `CI` a missing Chrome fails, and elsewhere it skips.

The path prefix selects the policy.
`nemarlike` is nemar.org's production policy verbatim as of website v0.2.16,
plus the jsDelivr entry the loader needs.
`control` grants no `wasm-unsafe-eval` and **must fail**:
a run where the negative control also passes has measured nothing.
That is not hypothetical,
an earlier spike reported a false failure
because no policy granted `'unsafe-inline'`,
so the page's own inline script never ran and that read as "Pyodide failed".

The page reads `harness-config.json` from the server:
the Pyodide version the npm package pins, and NEMAR's shipped runtime config and lock overlay.
The server serves NEMAR's wheels at `runtime/nemar/<file>`, as the API's route does,
and at `tampered/nemar/<file>` a valid wheel one byte longer,
the byte written as the zip's comment.
It has to be valid:
a wheel with a bare byte appended is a broken zip,
and a boot would refuse it whether or not the digest was checked.
Measured 2026-09-22: with `checkIntegrity: false` passed to `loadPackage`,
the tampered runtime boots and `chrome.js` fails,
which it did not do while the tampered wheel was a broken zip.

## What it measures

Recorded on #431, first against Pyodide 0.28.3 and then, on the same day
(2026-09-22), against 0.29.5 in Chrome, with every check passing on both:

- the generated worker boots under the production policy: 7.9 s on 0.29.5 with
  numpy and matplotlib preloaded, served from the browser's HTTP cache
- `micropip`, `js`, `pyodide`, `pyodide_js` and `ctypes` are gone from the
  execution namespace, through the import statement and through
  `importlib.import_module`, and each says why it specifically is blocked
- a package nobody installed is denied rather than installed on demand
- state persists across executions, so the worker is warm rather than per-run
- executed code CAN read a URL inside `fetch_allow`, through `osa.fetch_bytes`
- **a sibling path on the same origin is refused**, which is the one check that
  isolates this runtime's own allowlist from the browser's `connect-src`:
  the CSP verdict is identical for both URLs, so whatever refused it is ours
- the boot CDN, reachable while the runtime was assembling itself, is
  unreachable once sealed
- `print()` reaches the result, a matplotlib figure comes back as a real PNG
  sized within the cap, and a figure left open by one run does not reappear in
  the next
- a failed run still returns the output it produced before it failed, which is
  usually what explains the exception
- the summary describes arrays by facts rather than contents,
  including the wasm32 default integer width (`int32`, since numpy's default
  integer is a C long); an error carries its type, message and offending line
  while the full traceback stays in the browser, readable by `call_id`; and a
  figure is described in words that outlive the image
- NEMAR's runtime boots with its lock overlay's wheels fetched by URL, each
  checked against its sha256; eegprep-lean and zarr import at the versions the
  overlay records, and the prelude made `osa.fetch` eegprep-lean's transport
- **a valid wheel with other bytes stops that runtime from starting**, and the
  same URL loads when fetched without the digest, so the digest alone refused it
- **an infinite loop is stopped on the deadline**, measured at 10670 ms against
  a 10-second limit, and the runtime then reboots and runs the next execution.
  The limit was 3 seconds until 0.29.5, where a fresh instance spends about
  4.3 s importing matplotlib and pyplot, so every first figure timed out.
  Pyodide cannot interrupt its own Python without a `SharedArrayBuffer`, which
  would require cross-origin isolation on every embedding page, so terminating
  the worker from the host is the only way to stop a runaway loop and this is
  the only place it can be checked

### Wheel caching (warm vs cold)

`serve.js` used to send `no-store` on every route, the overlay wheels included,
so this harness never exercised the browser's own HTTP cache,
even though the API and the worker in front of it both serve those wheels as `public, max-age=31536000, immutable`
(a wheel's name is its identity, so the bytes at a given URL never change).
`serve.js` now sends the same header on `/runtime/nemar/*` and `/tampered/nemar/*`,
and `tests/test_api/test_runtime_wheel_cache_control.py` keeps the three copies of that header equal.

`chrome.js` measures against it.
After the `nemarlike` run, it boots NEMAR's overlay again on a fresh page (`cache-boot.html`),
on the SAME server and port and in the SAME browser profile as the cold run:
Chrome partitions its HTTP cache by top-level site, so a different port or a different browser would measure nothing.
It boots a third time with one wheel (eegprep-lean) served under a renamed file,
a real wheel build tag over the same bytes and sha256,
to show the cache is keyed by exact URL rather than by content.
The Pyodide worker is a separate DevTools target,
so `chrome.js` attaches to each worker paused at its start and records its network before letting it run;
a worker it could not attach to fails that run, since its wheels would go unobserved.
Both checks also require the exact set of wheel names the overlay lists,
so a request nobody observed fails rather than passing as "every observed request was cached".

Measured 2026-09-22, Chrome 153.0.8010.53, Pyodide 0.29.5, on this machine's loopback.
Bytes are the over-the-wire `encodedDataLength` DevTools reports;
a cache hit reports 0 because nothing crosses the network, not because the file is small.

| wheel | cold | warm |
|---|---|---|
| `zarr-3.4.0-py3-none-any.whl` | 377,491 B (network) | 0 B, `fromDiskCache=true` |
| `eegprep_lean-0.1.0.dev2-py3-none-any.whl` | 28,852 B (network) | 0 B, `fromDiskCache=true` |

- **warm**: both overlay wheels came from the browser's HTTP cache on the second boot,
  0 bytes over the network for either,
  cross-checked against `encodedDataLength` rather than trusting `fromDiskCache` alone.
- **lock change**: booting a THIRD time with eegprep-lean renamed to `eegprep_lean-0.1.0.dev2-2-py3-none-any.whl`
  sent exactly that one wheel to the network, the same bytes under a different name,
  while zarr, at its unchanged URL, stayed cached (0 B, `fromDiskCache=true`).
  Measured with 0.1.0.dev1 first, where the renamed wheel crossed at the cold row's exact byte count;
  re-measured with 0.1.0.dev2 for the table above.
- **jsDelivr's own assets** (the interpreter, and the stock wheels numpy and matplotlib pull in)
  were ALSO cache hits in the warm run, each sent with `Cache-Control: public, max-age=31536000`.
  This is reported, not asserted on, since jsDelivr's cache behavior is not this project's to enforce.
- **mutation-checked**: reverting `serve.js`'s wheel routes to `no-store` fails the warm and lock-change checks,
  with both overlay wheels reported `fromDiskCache=false` and their full byte count over the network again.
  A worker whose network could not be recorded fails every recorded run, naming the worker,
  and an overlay that lists no wheel stops the harness before either cache check can pass vacuously.

### Workspace

`frontend/test-workspace.js` already proves the storage-independent half
(path validation, manifest derivation, the `.ipynb` builder, the zip writer,
checked against two independent readers) runs correctly under Bun,
where `indexedDB` does not exist.
What only a real browser can check is IndexedDB itself, and `workspace.html` is for that.

`?phase=write` boots a real runtime, runs two real executions through a real `ClientToolController`
with a `WorkspaceStore` attached (calling `osa.save_script`/`osa.save_artifact` for real),
and checks what actually landed:
the result's `artifacts`, the derived manifest, `sizeUsed`, and the exported zip's own entries,
parsed from its real central directory rather than assumed from the writer's own intent.
It leaves what it expects in `localStorage`, on the same origin, for the second load to check against.

`?phase=read` is a FRESH page load: a new `WorkspaceStore` for the same community,
with no execution and no controller, reading back exactly what the first phase wrote,
which is the only way to show IndexedDB itself persisted it rather than one JS object remembering it.
It also exercises `deleteAll` and confirms the store reports empty afterward.

## The widget, end to end

`widget_e2e.py` serves the chat widget and the real community router side by side,
under the same production policy, for a synthetic community that runs Python:

```bash
uv run python frontend/browser-harness/widget_e2e.py 8791
# then open http://127.0.0.1:8791/browser-harness/widget-e2e.html
```

Everything is real except the language model, which needs a key the harness does
not have. It is scripted: it asks for code by keyword ("plot", "loop", "error")
and, once the browser answers, replies with the result it was sent, so the page
shows the round trip rather than asserting it. Unlike the unit-test stand-in, it
streams, because the router assembles the reply text from streamed chunks.

Measured 2026-09-22 in Chrome:

- the runtime bundle loads with its integrity attribute under the policy, and
  the widget declares `execute_code` and `get_full_output` only after it has
- the gate shows the description and the highlighted code; Run executes it, and
  run 2 streams into the same reply, which records what ran, what it printed and
  the figure it drew
- Stop on an infinite loop returns `cancelled`; Don't run returns `denied`; a
  Python exception returns `error` with its type, line and a pointer to the full
  traceback; each reaches the server as the result for the right call
- "Run without asking" skips the gate for the rest of the page, and a reload
  forgets it; the stored conversation keeps every run, and no figure
- **negative control**: with one byte appended to the served bundle, the browser
  refuses it, nothing is declared, and the assistant still answers

### NEMAR's data lane

```bash
uv run python frontend/browser-harness/widget_e2e.py 8791 --nemar [--tamper-wheel] [--widget-open]
```

Serves NEMAR's shipped config through the real router:
its runtime, its lock overlay in `/config`, and the wheels its own route serves.
The MCP servers are dropped from the config, since the scripted model never calls them.
Ask it to "read" (eegprep-lean's `read_window` and a plot of nm000103),
for the "prompt" (the snippet NEMAR's system prompt teaches, read from its config),
or for the "recipe":
the `python_browser` recipe production's `nemar_read_window` hands a model,
fetched from `mcp.nemar.org` when the server starts, with `start_sample` and `end_sample` bound from its `sample_slice`.
"prompt" and "recipe" are not copies kept here, so each runs what a model is actually given,
and a nemar-cli release that changes the recipe changes this page with it.
The policy adds `https://zarr.nemar.org` to `connect-src`, as nemar.org's `*.nemar.org` does.

Measured 2026-09-22 in Chrome, against the live archive:

- "read" returns a (4, 500) window in uV at 250 Hz, labeled E1 to E4, and the
  figure, titled from the group, reaches both the page and the model; under 10 s
  from Run to result, with numpy and matplotlib already in the HTTP cache
- "recipe" reads (129, 500) int16 through `open_array`, with no transport argument,
  because NEMAR's prelude made the runtime's own client eegprep-lean's default;
  re-measured 2026-09-22 with the recipe fetched live, which is nemar-cli 0.10.5's `python_browser` recipe
- "prompt" returns the same (4, 500) window as "read", in uV at 250 Hz,
  labeled E1 to E4, with its figure
- the overlay wheels load from the API's wheel route with their `sha256` as
  `fetch` integrity, through the egress guard; nothing is refused and the console
  is clean
- **negative control**, `--tamper-wheel`: every wheel arrives one byte longer, and
  the run fails with "package zarr did not load ... Failed to fetch". From the same
  page, that URL fetches fine without a digest and is refused with the entry's, so
  the refusal is the browser's integrity check. (This was measured with a bare
  appended byte; `--tamper-wheel` now writes it as the zip's comment, so the wheel
  stays valid and only the digest can refuse it, as in `chrome.js`.)
- `--widget-open`: the runtime stays `idle` until the chat opens, then boots with
  nothing sent, `ready` 2.5 s later

The Bun test for this lane is `frontend/test-data-lane.js`, which cannot load a
wheel by URL or check its digest (Pyodide's Node loader does neither). Those two
run in CI through `chrome.js`; this page adds the widget and the live archive.

### Light and dark

```bash
uv run python frontend/browser-harness/widget_e2e.py 8795            # a community that never set color_scheme
uv run python frontend/browser-harness/widget_e2e.py 8796 --nemar    # color_scheme: auto
bun frontend/browser-harness/color-scheme-check.mjs http://127.0.0.1:8795/browser-harness/widget-e2e.html light
bun frontend/browser-harness/color-scheme-check.mjs http://127.0.0.1:8796/browser-harness/widget-e2e.html nemar
```

The bug behind issue #469 is the browser's own:
on a host page that declares `color-scheme: dark`, Chrome draws a form control with no colors of its own in its dark field colors.
happy-dom has no such stylesheet, so `frontend/test-widget-color-scheme.js` cannot show it; this check can.
It gives the page a dark theme through the CSSOM, emulates the device's setting with `Emulation.setEmulatedMedia`,
and starts with a **control**: a plain input on that page must come out dark, or the page is not reproducing the bug.

Measured 2026-09-24 in Chrome 153:

- `light`: on a dark host page and a dark device, the widget stays light, and the chat input,
  the Settings key field and the model menu keep a white background and `#1f2937` text;
  panel text does not take the host page's color, and the widget's `color-scheme` is `light`
- `nemar`: `auto` follows the device, including a switch while the panel is open;
  `setColorScheme('light')` outranks a dark device;
  a pop-out opened after that keeps the host's light after its own community config arrives,
  and the host switching to dark reaches the open pop-out
- each of those fails when the widget line it depends on is removed (seven mutations)

The pop-out step reloads the page with `'unsafe-inline'` added to `script-src`, which nemar.org's live policy allows and this harness's stricter one does not.
Today's pop-out is written into `about:blank`, inherits its opener's policy and runs its scripts inline,
so on a host page without `'unsafe-inline'` it opens blank: a limitation of the pop-out itself, left for its redesign (#470).

`notebook-bench.js` times any page's boot, cold and warm, reusing `chrome.js`'s own Chrome-driving primitives;
see ADR 0010 (`docs/adr/0010-the-notebook-surface.md`) and `.context/notebook-surface-measurements.md` for what it was built to measure.
