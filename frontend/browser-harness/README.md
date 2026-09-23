# Browser runtime harness

Boots the real `buildWorkerSource` output against real Pyodide,
under a real `Content-Security-Policy` header,
and exercises the half of the runtime that no `bun` test can reach.

It runs in CI, in headless Chrome, as a gate:
`frontend/browser-harness/chrome.js` opens the `nemarlike` page and requires every check to pass,
then opens the `control` page and requires its boot to fail.
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

Serves NEMAR's shipped config through the real router: its runtime, its lock
overlay in `/config`, and the wheels its own route serves. The MCP servers are
dropped, since the scripted model never calls them. Ask it to "read" (eegprep-lean's
`read_window` and a plot of nm000103) or for the "recipe" (the `python_browser`
shape, `open_array` on the live array URL). The policy adds `https://zarr.nemar.org`
to `connect-src`, as nemar.org's `*.nemar.org` does.

Measured 2026-09-22 in Chrome, against the live archive:

- "read" returns a (4, 500) window in uV at 250 Hz, labeled E1 to E4, and the
  figure, titled from the group, reaches both the page and the model; under 10 s
  from Run to result, with numpy and matplotlib already in the HTTP cache
- "recipe" reads (129, 500) int16 through `open_array`, with no transport argument,
  because NEMAR's prelude made the runtime's own client eegprep-lean's default
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
