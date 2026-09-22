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

Recorded on #431, measured 2026-09-22 against Pyodide 0.28.3 in Chrome:

- the generated worker boots under the production policy, in well under a second
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
- **an infinite loop is stopped on the deadline**, measured at 3009 ms against a
  3-second limit, and the runtime then reboots and runs the next execution.
  Pyodide cannot interrupt its own Python without a `SharedArrayBuffer`, which
  would require cross-origin isolation on every embedding page, so terminating
  the worker from the host is the only way to stop a runaway loop and this is
  the only place it can be checked
