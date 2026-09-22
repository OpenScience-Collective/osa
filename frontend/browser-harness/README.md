# Browser runtime harness

Boots the real `buildWorkerSource` output against real Pyodide,
under a real `Content-Security-Policy` header,
and exercises the half of the runtime that no `bun` test can reach.

This is a manual check, not a CI gate.
There is no browser in CI.
Most of the Python half is now ALSO tested in CI:
`frontend/test-worker-core.js` runs the same worker core
against the real Pyodide from npm under Bun.
What only this harness can check is the browser itself:
the Content-Security-Policy, the blob worker, and the egress guard
installed on a real worker global.
and the protocol tests deliberately stop at the message boundary:
the workers under `frontend/test-workers/` speak the message protocol and nothing more,
because Pyodide needs a browser and asserting the Python half against a stand-in
would be asserting the stand-in.

## Running it

```bash
python3 frontend/browser-harness/serve.py 8791
```

Then open <http://127.0.0.1:8791/nemarlike/browser-harness/index.html>.

The path prefix selects the policy.
`nemarlike` is nemar.org's production policy verbatim as of website v0.2.16,
plus the jsDelivr entry the loader needs.
`control` grants no `wasm-unsafe-eval` and **must fail**:
a run where the negative control also passes has measured nothing.
That is not hypothetical,
an earlier spike reported a false failure
because no policy granted `'unsafe-inline'`,
so the page's own inline script never ran and that read as "Pyodide failed".

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
