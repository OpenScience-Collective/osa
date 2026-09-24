# Adding browser-executed Python to a community

A community can let its model write Python and run it in the reader's own browser,
through a Pyodide (WebAssembly Python) runtime the widget boots on demand.
This page describes the configuration surface as the code enforces it today,
citing the file that owns each claim.
For the design rationale (why a lock overlay, why a prelude, why the result format is deterministic),
see `.context/browser-execution-tool-design.md`.

## The config keys

Two top-level YAML sections work together:
`extensions.client_tools` declares the tool the model calls,
and `runtime.python` describes what actually runs it.
Both are validated by `src/core/config/community.py`,
and a `client_tools` entry whose `runtime` names a section that is not configured fails at load
(`CommunityConfig.validate_client_tools_have_runtime`), not at the first call.

### `extensions.client_tools`

```yaml
extensions:
  client_tools:
    - name: execute_code
      runtime: python
      requires_permission: true
      description: Run Python in the reader's browser, against the data this community's tools point at.
```

`ClientToolConfig` (`src/core/config/community.py`) defines the shape:

- `name`: the tool name the model calls, as a plain string.
  It must not collide with a name the server binds itself;
  today that means `get_full_output`, the one reserved name (`RESERVED_CLIENT_TOOL_NAMES`, same file).
- `runtime`: which execution environment answers a call to this tool.
  Only `"python"` exists (`ClientToolRuntime = Literal["python"]`),
  naming the `runtime.python` section below.
- `requires_permission`: whether the browser shows a permission gate
  (the code, syntax-highlighted, with Run and Deny buttons) before running a call.
  Defaults to `True`.
- `description`: what the model reads to decide when to call the tool,
  and the ONLY place to say what it can and cannot do.
  Name what the runtime can reach (see `fetch_allow` below) and what it cannot import,
  since a model that tries an unavailable package gets a `denied_import` result, not a helpful error.

There is a repository-wide kill switch, checked at bind time and again in `/config`.
Setting `OSA_CLIENT_TOOLS_DISABLED` (`CLIENT_TOOL_KILL_SWITCH_ENV`, `src/tools/client_tools.py`)
turns every community's client tools off without a deploy,
and `_client_tool_config` (`src/api/routers/community.py`)
makes sure a switched-off feature does not still cost every visitor a runtime download.

### `runtime.python`

```yaml
runtime:
  python:
    pyodide_version: "0.29.5"
    lockfile: runtime/my-tool-pyodide-lock.json
    preload: [numpy, matplotlib]
    allow_install: []
    preload_on: first_run
    fetch_allow:
      - https://api.example.org/
    index_urls: []
    prelude: |
      import my_transport
    limits:
      exec_seconds: 60
```

`PythonRuntimeConfig` (`src/core/config/community.py`) defines every field:

- `pyodide_version`: the Pyodide distribution to load, required.
  See "The Pyodide pin" below: this is not a free choice.
- `lockfile`: a lock overlay, relative to the community's own folder,
  naming the pure-Python wheels this runtime adds to the Pyodide distribution.
  Omit it when the distribution alone is enough.
  See "The lock overlay" below.
- `preload`: packages loaded when the runtime starts,
  resolved from the Pyodide distribution's own lock or from the overlay,
  with whatever their lock entries depend on.
  This is the whole install list: nothing installs itself when an execution imports it.
- `allow_install`: requirements micropip installs at startup, each with `deps=False`,
  from `index_urls` when the community gives any, otherwise from PyPI.
  Nothing installs after startup,
  and a wheel that needs its own sha256 pin belongs in the lock overlay instead.
- `prelude`: Python run once, after the runtime is sealed.
  See "The prelude" below.
- `preload_on`: `"first_run"` (the default), `"widget_open"`, or `"first_message"`:
  whether the runtime boots lazily, on the first execution;
  eagerly, as soon as the chat widget opens;
  or as soon as the reader sends their first message,
  which overlaps the download with the model's own first turn
  without charging a reader who only opens the chat.
- `fetch_allow`: Uniform Resource Locator (URL) prefixes executed code may fetch from, through the runtime's own `osa.fetch` client.
  See "`fetch_allow` is the egress control" below.
- `index_urls`: package index URLs `allow_install` may install from.
- `limits`: resource caps (`RuntimeLimits`, same file), and a community with no reason to deviate can omit it.
  Four of them are also enforced by the server when a result comes back (`src/api/tool_results.py`),
  so none can be raised past the server's own cap (`src/core/limits.py`).
  `stdout_chars`, `stderr_chars` and `images` default to that cap
  (16,384 characters, 8,192 characters and 3 images; 0 turns images off),
  and `image_px`, the longest edge of a returned image, defaults to 1024 under the server's 8192.
  One cap is not a community's to set: a single reply may ask the browser to run code at most 20 times
  (`MAX_BROWSER_RUNS_PER_REPLY` in `src/core/limits.py`, which the widget mirrors and a test compares).
  A community cannot see those constants,
  so `RuntimeLimits`'s own field bounds enforce them at load,
  rather than letting every browser result fail with a 422 the config never warned about.
  The other two are the browser's alone, and the server neither sees nor enforces them:
  `memory_mb` (default 1536; wasm32 tops out between 2 and 4 GB whatever is written here)
  and `exec_seconds` (default 120, measured by the browser's own clock).

### `fetch_allow` is the egress control

Once the runtime is sealed, executed code has exactly one way to reach the network:
`osa.fetch`, `osa.fetch_bytes` and `osa.fetch_text`, and every one of them checks the URL against `fetch_allow`.
This is an EGRESS allowlist, not an inbound one:
the data host on the other end still needs its own Cross-Origin Resource Sharing (CORS) configuration
for the browser to read the response at all
(`.context/browser-execution-tool-design.md`, "Data-plane CORS").
Treat each entry as a host a model-written program can be steered into sending conversation content to, in a URL,
and assume it logs whatever arrives;
do not allowlist a host that logs request paths somewhere sensitive.

### Values per deployment

`fetch_allow` and `prelude` each take one value, or one per deployment,
for data that has a staging copy on a host production's does not serve
([ADR 0013](adr/0013-the-chat-follows-its-deployment.md)):

```yaml
runtime:
  python:
    fetch_allow:
      production:
        - https://zarr.nemar.org/
      develop:
        - https://zarr-test.nemar.org/
```

A map names both deployments, `production` and `develop`, and nothing else; every deployment's prelude is compiled when the config loads.
The public config response carries the values for the deployment serving it, one list and one prelude,
so the runtime never sees a map.
`extensions.mcp_servers[].url` takes the same shape.
The backend is the develop deployment when `OSA_DEPLOYMENT=develop`,
or, with that unset, when it is mounted at `/osa-dev`, which is how `deploy/auto-update-dev.sh` runs it;
anything else is production.

## The lock overlay

A wheel the Pyodide distribution does not ship
(a community's own pure-Python package, or one with a version pin micropip cannot satisfy)
is added through a lock overlay:
a JSON file, in Pyodide's own lock-entry shape, naming each wheel's `file_name` and `sha256`,
with the wheel itself committed in a `wheels/` folder beside it (`src/core/config/runtime_lock.py`).
The server verifies every wheel against its recorded sha256 when it loads the overlay,
serves the overlay's entries in `/config` as `runtime_lock`,
and serves the wheels themselves at `GET /{community}/runtime/{file_name}`.
The host that sent the hashes is the host the bytes come from,
so the two can never come from different releases.
The widget merges the overlay into the Pyodide distribution's own lock before handing it to `loadPyodide`;
an overlay entry may only ADD a package, never replace one Pyodide's own distribution ships
(`osa-worker-core.js`, `mergeLock`).

**A wheel's file name is its identity.**
Every wheel route is served `Cache-Control: public, max-age=31536000, immutable`
(`src/api/routers/community.py`, `RUNTIME_WHEEL_CACHE_CONTROL`;
the worker in front of it matches, `workers/osa-worker/index.js`, `IMMUTABLE`),
so a changed wheel needs a new file name, never new bytes under the old one.
Committing new bytes under a name that is already in the overlay with a different sha256 is refused at build time
(see below), not silently served stale.

Pyodide accepts pure-Python wheels only here:
`_WHEEL_FILE_NAME` (`src/core/config/runtime_lock.py`) requires a bare `*-py3-none-any.whl` name.
A compiled dependency has to come from the Pyodide distribution itself,
or from upstream packaging that produces a pure-Python or Pyodide-built wheel;
micropip cannot build one in the browser.

`scripts/build_runtime_lock.py` regenerates an overlay from the wheels committed beside it
and a `depends.toml` naming what each one depends on
(a wheel's own metadata names PyPI requirements, not Pyodide lock entries):

```bash
uv run python scripts/build_runtime_lock.py src/assistants/<community>/runtime/<community>-pyodide-lock.json
uv run python scripts/build_runtime_lock.py --check src/assistants/<community>/runtime/<community>-pyodide-lock.json
```

`--check` exits non-zero when the committed overlay is not what the wheels and `depends.toml` currently produce,
and `tests/test_assistants/test_shipped_runtimes.py` runs it for every community that names one.
The overlay is reviewed in a pull request like any lockfile.
For the refresh procedure on a specific community's wheels,
see that community's own `runtime/README.md` (for example `src/assistants/nemar/runtime/README.md`)
rather than this page:
it is community-owned and changes with that community's own dependencies.

## The prelude

`runtime.python.prelude` is Python the community writes,
compiled at config load so a syntax error fails validation rather than a reader's boot
(`PythonRuntimeConfig._prelude_compiles`).
It runs exactly once per boot, **after** the runtime's egress seal,
with precisely the privileges an execution has and no more (`osa-worker-core.js`, `boot()`).
The seal narrows the JavaScript-level egress allowlist
and removes `micropip`, `js`, `pyodide_js` and similar from the namespace first,
and only then does the prelude run.
Top-level `await` is allowed.
A prelude that raises fails the whole boot, with `kind: "prelude"` naming it as such rather than a generic runtime error,
because every later execution would otherwise fail for a reason belonging to the prelude
and not to whatever the model wrote.

Use it for setup every execution needs and that the community itself wrote and reviewed,
such as registering a library's default network transport over `osa.fetch`.
It is not for anything the person should be asked to approve:
it runs before the permission gate exists to ask,
which is exactly why it must be the community's own code, never anything derived from a model or a reader.

## The Pyodide pin

`pyodide_version` names an exact Pyodide distribution,
loaded from jsDelivr's content delivery network (CDN) at boot (`osa-runtime.js`, `buildWorkerConfig`).
It is not a free choice:
continuous integration (CI) can only vouch for the Pyodide version the `pyodide` npm package pins,
because that is the one `frontend/test-worker-core.js` and `frontend/test-data-lane.js` actually boot under Bun.
Every shipped community's `pyodide_version` must equal that npm package's version,
checked in `frontend/test-data-lane.js`
("every community runtime resolves against the Pyodide these tests run").
A community pinned to a different Pyodide would pass every Bun test and fail only in a reader's browser,
which is exactly what this check exists to prevent.

## The embedding page's Content-Security-Policy

The Pyodide worker inherits the Content-Security-Policy (CSP) of the page that embeds the widget,
since it runs from a `blob:` URL rather than a separate origin
(`osa-runtime.js`, "WHY THE WORKER IS A BLOB").
The embedding page needs:

- `script-src` including `'wasm-unsafe-eval'` (for `loadPyodide` itself)
  and the CDN the interpreter loads from (`https://cdn.jsdelivr.net`).
- `worker-src` including `blob:`, or the worker cannot be constructed at all:
  a policy with no `worker-src` falls back to `script-src`, and `'self'` there is not `blob:`.
- `connect-src` covering the application programming interface (API) host (for `/config` and `/chat`),
  the CDN (for the interpreter and any stock wheels `preload` names),
  and every host `fetch_allow` names,
  since the CSP is the outer boundary `fetch_allow` narrows further, not a replacement for it.

`frontend/browser-harness/serve.js`'s `nemarlike` policy is nemar.org's actual production policy,
kept there so it can be re-measured against a real embedding site rather than assumed.

**A refused runtime hangs; it does not error.**
Under a policy missing `'wasm-unsafe-eval'`, `loadPyodide` neither resolves nor rejects and logs nothing
(`osa-runtime.js`, "WHY THE BOOT DEADLINE EXISTS", measured against a real CSP in Chrome).
From the inside, a CSP refusal is indistinguishable from a slow download.
This is why every boot carries a deadline (`DEFAULT_BOOT_TIMEOUT_MS`, 120 seconds)
with a FAILED state and a message that suggests checking `script-src` and `worker-src`,
rather than a spinner with no way out:
a spinner with no deadline renders a permanently blocked runtime as a merely loading one,
and nobody ever learns the policy is wrong.
`frontend/browser-harness/chrome.js`'s `control` page is exactly this case, kept as a gate:
it must time out,
because a control that boots successfully would mean the policy variant under test was never actually applied.

## First-load cost, and what a warm reader pays

Measured on Pyodide 0.29.5, in uncompressed bytes, the interpreter itself is about 5.3 MB.
NEMAR's `preload` list is four names (`numpy`, `matplotlib`, `zarr`, `eegprep-lean`),
which resolve to 23 packages once each one's own dependencies are included:
about 13.6 MB, and 18.9 MB together with the interpreter.
Adding `scipy` to that list would bring the total to about 35.2 MB (scipy alone is about 16.3 MB),
which is the cost dropping it from `preload` avoids
(`.context/browser-execution-tool-design.md`, "Client changes").

Measured on test.nemar.org in a cold Chrome profile:
the model's first turn takes about 8.1 seconds until the Run gate appears,
and under `preload_on: first_run` the download (18.9 MB for NEMAR) only starts once the reader clicks Run,
adding its own time on top before the code actually runs.
`preload_on: first_message` starts that download the moment the reader sends their first message,
well before the model has answered,
so it overlaps the model's turn instead of following it;
NEMAR uses this value.
A reader who only opens the chat and never sends anything is never charged for it,
unlike `widget_open`, which downloads for every visitor regardless of whether they ask anything.

That cost is paid once per browser, not once per session.
Every wheel route is served immutable (see "The lock overlay" above),
and jsDelivr serves the interpreter and the stock wheels with a year-long `max-age` of its own
(measured as `public, max-age=31536000`, without `immutable`).
`frontend/browser-harness/chrome.js` measures this directly.
After a cold boot, a second boot of the same overlay in the same browser
serves every overlay wheel from the browser's own HTTP cache, with 0 bytes over the network,
cross-checked against the transferred byte count rather than trusting a single cache-hit flag.
A wheel re-served under a new file name is the only one that goes back to the network,
which shows the cache is keyed by the exact URL and not by a wheel's contents.
See `frontend/browser-harness/README.md`, "Wheel caching (warm vs cold)",
for the current numbers, dated, with the Chrome version they were measured against.

Because that download happens once,
the widget's progress bar is keyed to boot STEPS rather than to bytes:
one for the interpreter, one per `preload` name,
one for `micropip` plus one per `allow_install` entry when there is one,
and one for the prelude when there is one (`osa-worker-core.js`, `boot()`).
A byte figure would be accurate on a reader's very first visit and misleading on every one after,
since the browser already has most or all of it cached.

## Workspace

Once a community's runtime executes code,
every run's code and output are kept in the reader's own browser, across a reload,
in a per-community workspace (`frontend/osa-workspace.js`).
This is separate from `get_full_output` (`osa-runtime.js`, `FullOutputStore`):
that store answers the model, is per tab, and is gone on reload;
the workspace answers the reader, persists in the browser's IndexedDB storage,
and nothing written to it is ever sent to the server.
Python cannot reach browser storage at all:
the namespace seal removes `js`, Pyodide's `mountOPFS` (for the Origin Private File System) is unreleased,
and `mountNativeFS` (for the File System Access API) is Chromium-only.
So a run's files travel back to the host in the worker's own result message,
the way its fuller copy of the output already does
(bounded at 262,144 characters per stream, `_FULL_CHARS`, not literally unbounded;
see the note on `results/run-NNN/stdout.txt` below),
and the host writes them to IndexedDB before that result is ever answered to the server.

### Layout

Every file lives at `<community>/<session>/{scripts,results,artifacts}/<path>`,
where `session` is the server session ID the tool request carries.
A session's `manifest.json` is DERIVED from what is stored, whenever it is needed
(`deriveManifest`, `frontend/osa-workspace.js`),
and is never itself stored, so it cannot drift from what is actually there.
It names each run: its ordinal, `call_id`, status, description, the files it wrote,
an ISO 8601 timestamp, and `local`, which is true when the reader ran it with "Edit and run" rather than the assistant.

Every executed run is saved automatically,
whether or not the model asked to keep anything:
`scripts/run-NNN.py` (the code as it ran),
`results/run-NNN/stdout.txt`, `stderr.txt` and `summary.txt`
(the same 262,144-character-bounded copy `get_full_output` keeps, `_FULL_CHARS` in `osa-output.js`:
far more than the model's own turn sees, but not literally untruncated),
and a `results/run-NNN/figure-K.png` for each figure it produced.
A `get_full_output` call is not a run and writes nothing.

Only an explicit save is told to the model.
`osa.save_script(name, code)` writes `scripts/<name>` (adding `.py` when `name` has no extension),
and `osa.save_artifact(path, data)` writes `artifacts/<path>`,
where `data` is `bytes`, `bytearray`, `memoryview`, or `str` (encoded UTF-8).
Both return the workspace-relative path they wrote,
and both add that path to the run's `ClientToolResult.artifacts` (`src/api/tool_results.py`),
sorted and de-duplicated,
so the model's prompt cache prefix stays stable across turns that save the same files.

### Limits

Checked in Python at call time,
so a call over any limit raises a `ValueError` the model reads like any other exception
(`_validate_workspace_path` and `_record_saved_file`, `frontend/osa-output.js`),
and checked again on the browser side before anything is written to IndexedDB
(`validateWorkspacePath` and `WorkspaceStore.putFile`, `frontend/osa-workspace.js`):

- A path is relative; each `/`-separated segment matches `[A-Za-z0-9._-]+`;
  no segment is `.` or `..`; depth is at most 4 segments; length is at most 200 characters.
- A single file is at most 10 MB.
- One run's explicit saves together are at most 25 MB, and at most 32 distinct files.
  The files a run saves automatically (its script, output and figures) do not count toward that budget;
  only the per-file and per-community caps apply to them.
- One community's whole workspace is at most 250 MB,
  checked only on the browser side, since it depends on everything already stored.

A write that fails for any reason
(the file itself, the run's budget, the community's budget,
a storage quota, private browsing, or IndexedDB being unavailable at all)
is reported IN THE RESULT, never silently:
a line is appended to `stderr` naming each file not saved and why,
and `artifacts` lists only the files that were actually saved.
The model is never told a file exists when it does not.
A failure to save the automatic files a run always writes is reported the same way,
but never fails the run itself.

### Export

The Settings panel, shown only for a community that declares client tools
(`osa-chat-widget.js`, the workspace section inside the existing settings modal),
shows the space used and offers "Download workspace (.zip)" and "Delete workspace",
the second confirmed with a second click on the button itself, never a browser dialog.

The download is one zip of the whole community's workspace, built entirely in the browser
(`frontend/osa-zip.js`, a small STORED-entry zip writer with no compression library
and no external tool; the same module `test-support/minimal-wheel.js` uses to build a wheel).
Nothing leaves the browser to build it.
Each session's folder inside the zip carries its derived `manifest.json`
and a generated `notebook.ipynb`
(nbformat 4, minor version 5, with a cell id on every cell):
one markdown cell per run, carrying its description,
and one code cell, carrying its code and its outputs:
stdout and stderr as `stream` outputs, each figure as a `display_data` `image/png` output.

### The one limitation worth knowing

IndexedDB is scoped to the page's origin.
The widget's pop-out window can run on a different origin than the page that embeds it,
and a pop-out on another origin sees neither the chat history (already true before this feature)
nor the workspace.
This is documented, not engineered around:
there is no cross-origin storage bridge here, and none is planned.

## The editable re-run panel

Each recorded run in the chat gets an "Edit and run" control,
once the runtime exists on that page.
It opens an inline editor holding the run's code,
bounded by the same length limit a recorded run's code already has,
with Run and Cancel controls.
Clicking Run is the reader's own consent,
so it skips the permission gate entirely
(`ClientToolController.runLocal`, `frontend/osa-controller.js`),
and it runs in the SAME runtime and namespace as the assistant's own runs:
a variable an earlier run defined is still there,
which is what makes this tinkering rather than a fresh interpreter.
No request is sent for the run itself.
But because the namespace is shared,
what it leaves behind, a variable or a file `osa.save_artifact` wrote,
is visible to a LATER run in that same namespace, the assistant's included,
and that later run's own output does reach the model the ordinary way:
sharing the namespace is the point, and it has this one consequence.
The run's own output stays private a second way,
enforced by the runtime itself, not only by the widget's own display:
`get_full_output` refuses a reader's own call id with EXACTLY the answer it gives an unknown one,
so the model cannot read the run back even by asking for it directly,
and is never told the run existed at all
(`PyodideRuntime.execute`'s `local` option, `FullOutputStore`, `frontend/osa-runtime.js`).

The runtime accepts one execution at a time,
so Run is refused outright, not queued,
while the runtime is already busy with the assistant's own code or still booting.
An assistant tool call that arrives while the reader's own run is in progress waits for it instead,
so it is still answered correctly once the runtime is free, rather than refused.
The same egress seal, output limits and execution deadline apply,
because it is the same runtime.
Stop is targeted: the editor's own Stop (`cancelLocal()`) reaches only the reader's own run,
and the assistant's tool panel Stop (`cancel()`) reaches only the assistant's call,
even when both are outstanding at once,
an assistant call queued behind a reader's run still in progress.

The result becomes its own entry among the reply's other runs,
so it survives a reload,
and is labeled plainly as the reader's own: the assistant never sees it.
It is stored in the workspace the same way any other run is,
marked `local` in the stored run record,
so the derived `manifest.json` and the exported `notebook.ipynb` (see "Workspace" above)
can say so too.

## Known blockers

`pybids` cannot be preloaded today:
it depends on `num2words`, which pulls in `docopt`, and `docopt` ships only as a source distribution.
Pyodide has no compiler and micropip cannot build one,
so nothing that reaches a dependency published only as source can resolve
(`.context/browser-execution-tool-design.md`, "Phasing").
`hedtools`, `mne` and `mne-bids` all resolve as pure-Python or Pyodide-built wheels;
if a community needs `pybids` specifically,
the fix is upstream packaging of that dependency chain, not a workaround here.
