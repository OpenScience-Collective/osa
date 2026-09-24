# Browser Execution Tool Design

Status: draft, revised after review 2026-09-16. Transport DECIDED: two-run continuation, no checkpointer.
Owner: Yahya (lead maintainer).
Scope: a community-agnostic, client-executed `execute_code` tool for OSA assistants.
First adopter: the NEMAR assistant, against Zarr recipes from the NEMAR MCP server.
Companion decision on the NEMAR side: nemarOrg/nemar-cli ADR 0049.

## Problem

OSA assistants can explain, cite, and retrieve, but they cannot do.
A researcher who asks "show me the alpha power in this recording" gets a description of how to compute it,
not the plot.
The next step for every community, not only NEMAR, is an assistant that writes code, runs it,
looks at the result, and iterates, and then hands the script and the result to the person to keep tinkering.

Three constraints shape where that code can run:

1. **The OSA host is a thin orchestrator.** It runs FastAPI and LangGraph and forwards LLM calls.
   Per-user numpy on it saturates at a handful of concurrent sessions,
   and any sandbox strong enough to run model-written code safely is an operations surface we do not want.
2. **Data is large and public.** NEMAR serves hundreds of datasets as Zarr over HTTPS with anonymous reads.
   Bytes should move from the data plane to the machine doing the work, never through the OSA server.
3. **Communities differ.** NEMAR wants eegprep or MNE on Zarr; HED wants validation with `hedtools`;
   BIDS wants `pybids` over a layout; EEGLAB wants EEGLAB-parity numerics.
   The runtime must be configured per community, not built per community.

## Decision in one paragraph

Code runs in the user's browser, in a Pyodide interpreter inside a dedicated Web Worker owned by the widget.
The agent loop stays on the server in LangGraph.
`execute_code` is a **client-executed tool**: when the model calls it,
the server's run ends with a `tool_request` event to the widget,
the widget runs the code with no request open and posts the result back,
and a second run continues the conversation with that result in the model's context, images included.
The runtime is lazy: Pyodide boots on first use or on widget open,
loads the community's pinned packages as it boots,
and everything is cached by the browser after the first download.
Scripts and results persist in browser storage;
the reader can download them as a zip with a Jupyter notebook, and re-run any run in the chat with their own edits.
No login is involved anywhere in this design; identity is a per-community concern for later stages.

## Goals and non-goals

Goals:

- Zero server compute for code execution; the server's load is the LLM calls it already makes.
- One runtime for "the assistant runs it" and "the person tinkers with it"; results carry over.
- Generic across communities through configuration: base packages, lockfile, install allowlist, fetch allowlist, limits.
- Permission-gated by default: the person sees the code before it runs and can turn on auto-run for the session.
- Results the model can reason about: bounded text output plus a few images as image content blocks.
- Anonymous end to end.

Non-goals:

- Server-side kernels, JupyterHub, or any per-user process on the OSA host.
- Compiled packages without a WebAssembly build; those are the package's problem, not the runtime's.
- HPC or batch submission; that is a community gateway behind that community's identity, outside OSA.
- Cross-device sync of scripts and results; a later opt-in for communities that have accounts.

## Architecture

```
  Browser (widget origin)                          OSA server (FastAPI + LangGraph)
  +--------------------------------------+         +----------------------------------------+
  | chat UI  <---- SSE ------------------|---------|  _stream_chat_response                  |
  |   |  tool_request / tool_result      |         |    ^                                    |
  |   v                                  |         |    |  interrupt() / Command(resume=...)  |
  | client tool registry                 |         |  StateGraph                             |
  |   |  execute_code                    |         |   agent --> tools (server tools)        |
  |   v                                  |         |         --> client_tools (interrupt)    |
  | runtime worker (Pyodide)             |         |  checkpointer keyed by session_id       |
  |   every package loaded at boot       |         +----------------------------------------+
  |   fetch allowlist, output capture    |
  |   v                                  |          Data plane (community-owned, public)
  | workspace (OPFS / IndexedDB)         |<-------  e.g. zarr.nemar.org, S3, PyPI, CDN
  |   scripts, results, artifacts        |
  | notebook surface (JupyterLite)       |
  +--------------------------------------+
```

**As built (phase 1, #430).** The diagram and the components below are the proposal.
The transport that shipped is the two-run continuation this note's header records:
there is no `interrupt()`, no `Command(resume=...)` and no checkpointer.
The `client_tools` node (`src/agents/base.py`) writes a `pending_client_call` into the run's state and routes to `END`,
so run 1 ends; the session store holds that one parked call (`ChatSession.pending_call`),
and `/chat/resume` claims it exactly once (`claim_pending_call`) before run 2 starts from the stored history.
The workspace is IndexedDB, not the Origin Private File System (OPFS),
and the notebook surface is deferred: ADR 0010 (`docs/adr/0010-the-notebook-surface.md`) rejects marimo,
defers a hosted JupyterLite to #453, and ships an editable re-run panel in the chat instead.
ADR 0011 (`docs/adr/0011-the-notebook-site.md`) later hosts that JupyterLite site, at `notebook.osc.earth/osa`.

Components:

- **Runtime worker.** One Pyodide instance per community per tab, in a Web Worker so the page never blocks.
  Boots lazily. Stays warm across turns. Exposes `run(code, call_id)` and `cancel(call_id)`.
- **Client tool registry.** A small table in the widget mapping tool names to browser-side executors.
  `execute_code` is the first entry; a future `render_html` or `read_local_file` slots in the same way.
- **Client tool node.** A LangGraph node whose only body is `interrupt(payload)`.
  It runs for tool calls whose definition is marked `execution: client`.
  Server tools keep using `ToolNode` exactly as today.
- **Checkpointer.** Required by `interrupt`. In-memory for anonymous sessions with an expiry sweep;
  SQLite behind a setting for deployments that want durable threads.
  The thread id is the existing `session_id`.
- **Resume channel.** Either `POST /{community}/chat/resume` alongside the current SSE stream,
  or one WebSocket per session carrying both directions. Recommendation below.
- **Workspace.** A per-community directory tree in browser storage for scripts, results, and artifacts,
  written from Python through a tiny injected helper module and read by the notebook surface.
- **Configuration.** A `runtime` block and a `client_tools` list in the community YAML,
  validated by Pydantic like every other extension.

## Sequence

```
model emits tool_call(execute_code, {code, description})
  -> graph routes to client_tools node
  -> node calls interrupt({call_id, tool, args, requires_permission})
  -> _stream_chat_response sees __interrupt__ nested in on_chain_stream, emits SSE event tool_request
widget receives tool_request
  -> shows code; waits for Run (or auto-run is on)
  -> worker.run(code): execute (every package was loaded at boot; see Lazy loading)
  -> captures stdout/stderr, images, artifact names; applies caps
  -> POST /{community}/chat/resume {session_id, call_id, result}   (or WebSocket frame)
server resumes graph with Command(resume=result)
  -> result becomes the ToolMessage; images become image content blocks
  -> agent node continues; streaming continues on the same SSE connection
```

One round trip per execution.
LLM latency dominates every round trip, so the transport choice is about connection handling, not speed.

**As built (phase 1, #430).** Run 1 streams until the model calls `execute_code`;
the `client_tools` node parks the call, `_stream_chat_response` sends `tool_request`, and the stream ends.
The widget runs the code with no request open, then posts `{session_id, call_id, result}` to `/{community}/chat/resume`.
The server claims the parked call, appends the result as the tool's message (images as content blocks on the Anthropic path),
and runs the graph again from the stored history, streaming run 2 on the resume request's own response.
A result for a call that is not outstanding, or has expired, is refused with a 409.

## Message protocol

The current SSE vocabulary is `content`, `tool_start`, `tool_end`, `done`, `error`.
Three events are added.
All payloads are JSON.

`tool_request` (server to client):

```json
{
  "event": "tool_request",
  "call_id": "c_01J...",
  "session_id": "s_...",
  "tool": "execute_code",
  "args": { "code": "import numpy as np\n...", "description": "Band power per channel" },
  "requires_permission": true,
  "deadline_s": 120
}
```

`tool_result` (client to server):

```json
{
  "call_id": "c_01J...",
  "status": "ok | error | denied | timeout | cancelled",
  "stdout": "...",
  "stderr": "...",
  "truncated": { "stdout": false, "stderr": false },
  "images": [ { "mime": "image/png", "data_base64": "...", "width": 1024, "height": 640 } ],
  "artifacts": [ { "name": "results/alpha_power.csv", "bytes": 4210, "mime": "text/csv" } ],
  "elapsed_ms": 1830,
  "runtime": { "engine": "pyodide", "python": "3.14", "packages_loaded": ["numpy", "scipy"] }
}
```

`tool_cancel` (either direction): `{ "call_id": "c_01J..." }`.

**As built (phases 1 and 2).** The shapes above are the proposal. What is on the wire is
`ToolRequestEvent` and `ClientToolResult` in `src/api/tool_results.py`, which win over
these examples. `tool_request` also carries the run's `content` and `citations`, and no
`deadline_s`: the deadline is `runtime.python.limits.exec_seconds`, which the widget reads
from `/config`. The result adds `summary`, the structured description the model reasons
over, and `oom` as a status; it has no `truncated` field (a clipped stream says so in its
own text, and the full output stays in the browser, readable with `get_full_output`), no
`runtime` block, and `artifacts` are bare names. There is no `tool_cancel` event: Stop
terminates the worker, and the call is answered `cancelled` through `/chat/resume`.

Caps, enforced on the client and re-checked on the server:

| Field | Cap | Why |
|---|---|---|
| stdout | 16 KB | The model needs a summary, not a dump |
| stderr | 8 KB, tail-preserved | The last lines carry the traceback |
| images | 3 per call, longest side 1024 px, PNG | Bounded context cost, still legible |
| artifacts | names and sizes only | Data stays in the browser |
| execution | `deadline_s` from config, default 120 | A runaway cell must not park the thread |

As built, the server's caps are `src/core/limits.py` and a community narrows them with
`runtime.python.limits` (`RuntimeLimits`): the stream caps are as above, artifacts are
names only, the deadline is `exec_seconds`, and images are capped by count and bytes,
with the browser downscaling to the community's `image_px`.
`frontend/test-output.js` reads the Python source, so the two sides cannot drift.
One cap has no row above: a reply may ask for at most 20 browser runs (`MAX_BROWSER_RUNS_PER_REPLY`),
enforced by the server and mirrored by the widget.

Arrays never travel to the server.
A tool that needs numbers back returns a small table in stdout or writes an artifact and reports its name.

## Server changes

- `ClientTool`: a `BaseTool` subclass whose `_run` raises `interrupt(...)` with the request payload
  and returns the resume value as the tool output.
  It is bound to the model like any other tool, so the model sees one tool surface.
- Graph: add a `client_tools` node next to `tools`; the router sends a call to `client_tools`
  when the tool name is registered as client-executed. The node body contains nothing but the interrupt,
  because LangGraph re-runs the node from its start on resume.
- Checkpointer: `InMemorySaver` by default with an expiry sweep for anonymous sessions;
  `PostgresSaver` when `OSA_CHECKPOINTER=postgres`. `langgraph-checkpoint-postgres` is already a declared
  `server`-extra dependency that nothing imports; `langgraph-checkpoint-sqlite` is NOT in the lockfile, so
  SQLite would have to be added before it could be used.
  This is also what gives OSA durable threads, which it does not have today.
- `_stream_chat_response` in `src/api/routers/community.py`, NOT `stream_response`: the `chat.py` router
  that defines `stream_response` is not mounted, so changing it would do nothing. Under
  `astream_events(version="v2")`, which is the API this path uses, `__interrupt__` is NOT a top-level
  streamed key; it arrives nested as `event["data"]["chunk"]["__interrupt__"]` on an `on_chain_stream`
  event from the root graph. The existing loop branches only on `on_chat_model_stream`,
  `on_chat_model_end`, `on_tool_start` and `on_tool_end`, so a new branch is required. The sibling `/ask`
  path needs the same treatment or must refuse client tools. `stream_config` carries no
  `configurable.thread_id` today, and a checkpointer requires one.
  Resume through `POST /{community}/chat/resume`, subject to the transport constraint below.
- Result handling: text becomes the `ToolMessage` content; images are attached as image content blocks
  on the tool message where the model supports them, otherwise summarized as `[image: WxH]`.
- Budget: images must be EXCLUDED from `count_tokens_approximately` and accounted separately at the
provider's image rate. This is a required Phase 1 change, not a refinement. `count_tokens_approximately`
explicitly does not support image content and falls back to `repr()` character counting, so a single
250 KB PNG measures about 85,000 tokens against a `DEFAULT_MAX_CONVERSATION_TOKENS` of 80,000; the
subsequent `trim_messages(strategy="last")` then discards the conversation. The failure mode is silent
context loss, not an error. Cap the `tool_result` payload in BYTES, not only in pixels.
- Timeouts: an unanswered `tool_request` past `deadline_s` plus a grace period resumes the graph
  with `status: timeout` so the model can respond, and the thread is not left parked.

**As built (phase 1, #430).** The bullets above describe the `interrupt()` design, which was not built.
`ClientTool` (`src/tools/client_tools.py`) is bound like any tool but never executed: its `_run` raises `ClientToolNotExecutableError`.
There is no checkpointer and no `thread_id`; the session store parks the call.
The Timeouts bullet's active resumption does not exist either, deliberately:
an unanswered call is closed out when the session is next used, by a new message or a late result,
with a tool message telling the model it was not answered (`abandon_pending_call`),
and the model is never run in the background, since no stream is open to carry its reply.
The image budget bullet was wrong for the langchain-core this project locks: `count_tokens_approximately` has charged a flat `tokens_per_image`
since 1.2.8, and `src/agents/base.py` passes it Anthropic's rate explicitly.

Transport recommendation: start with SSE plus a resume endpoint, because the widget already speaks SSE
and the change is additive.
Move to one WebSocket per session when the resume round trips are measurable in practice,
or when cancellation from the server side is needed.
The message schema is identical either way.

## Client changes

- Worker lifecycle: boot on `preload_on: widget_open` for communities that opt in,
  on `preload_on: first_message` for communities that want the download to overlap
  the model's first turn instead, or otherwise on the first `tool_request`.
  Show a progress bar keyed to package downloads, by step rather than by byte
  (see `docs/community-browser-runtime.md`, "First-load cost, and what a warm
  reader pays", for why: the browser keeps the download after the first boot,
  so a byte figure would be correct once per browser and misleading every
  time after).
  Measured on Pyodide 0.29.5, uncompressed: the interpreter alone is 5.3 MB;
  NEMAR's 23 preloaded packages (its four `preload` names plus what they pull
  in) add 13.6 MB, 18.9 MB in all; adding scipy would bring that to 35.2 MB
  (scipy alone is 16.3 MB), which is the tradeoff dropping it from `preload`
  avoids. ADR 0049's "50 to 60 MB" was an estimate, and a high one; its
  amendment of 2026-09-22 records these measured figures.
  **As built (#495, 2026-09-24):** NEMAR now preloads scipy and pays it: 35.4 MB
  over the network in a cold Chrome 153 profile, 42.9 MB decoded. The figures
  above were over-the-network sizes too, labeled uncompressed. SciPy also needs
  `runtime.python.import_before_seal`, since it imports `ctypes` as it loads and
  the seal refuses `ctypes`; see "Importing before the seal" in
  `docs/community-browser-runtime.md`.
  Keep the worker warm across turns.
- Execution: `pyodide.loadPackagesFromImports(code)` for packages Pyodide ships,
  then `micropip.install` for imports that resolve to pure-Python wheels and are on the community allowlist,
  then `runPythonAsync`.
  An import outside the allowlist produces a `denied_import` in the result rather than a silent install.
  **As built:** nothing is installed at execution. `preload` (from the Pyodide lock and the
  community's overlay) and `allow_install` (micropip, `deps=False`) both run at boot, before the
  seal removes `micropip`; an execution only runs code, and any import of something not
  installed is a `denied_import`.
- Output capture: redirect `sys.stdout` and `sys.stderr`; set the matplotlib backend to Agg
  and collect figures as PNG; support a small `display()` protocol for images and HTML tables.
- Cancellation: cooperative cancellation through `setInterruptBuffer` needs a `SharedArrayBuffer`,
  which needs cross-origin isolation headers on the embedding page.
  Do not require that. Fall back to terminating the worker and rebooting it,
  and make the cold reboot cheap by keeping the lockfile and wheels in the cache.
- Permission gate: show the code with syntax highlighting, Run and Deny buttons,
  and an "auto-run for this session" toggle. The permission gate is ON by default for every community;
  auto-run is OFF until the person turns it on for that session.
- Network: the enforceable boundary is a `Content-Security-Policy: connect-src` on the worker, which the
  browser applies to the worker's own requests. A `fetch` wrapper alone is NOT a boundary: `js.fetch`,
  `XMLHttpRequest` (which Pyodide's synchronous HTTP shims use), `WebSocket` and `EventSource` are all
  reachable from Python, and `micropip` must be removed from the execution namespace or model-written code
  can install for itself. Shim every entry point, set `credentials: "omit"` on the allowed ones so
  same-origin requests do not silently carry cookies, and treat `fetch_allow` as an EGRESS control: an
  allowlisted origin can be sent conversation content inside a URL and will log it.
  **As built:** `fetch` is shimmed with `redirect: 'error'`, and `XMLHttpRequest` is removed outright,
  like `WebSocket` and `EventSource`: XHR follows a redirect with no way to refuse it,
  so a 302 from an allowed origin delivered a disallowed one's body in Chrome, and nothing in the runtime needs it.
- Content Security Policy: the embedding page needs `wasm-unsafe-eval` in `script-src`, `worker-src` for
  the runtime worker (a policy with no `worker-src` falls back to `script-src 'self'` and refuses a blob
  worker), and `connect-src` entries for the data plane, the Pyodide CDN and the wheel host. Website
  ADR 0009 does NOT provide the precedent this note claimed: on nemar.org `wasm-unsafe-eval` is granted
  GLOBALLY, and what is route-scoped to `/dataset/*` is the strictly stronger `'unsafe-eval'`. That ADR
  exists precisely because `wasm-unsafe-eval` alone was not enough to decode Zarr chunks in a browser.
  Whether Pyodide's loader hits the same wall is genuinely open; settle it with a spike before Phase 2.
- Data-plane CORS: ADR 0049 requires that every executing origin be allowed to READ the data plane. This
  is an INBOUND requirement and is not the same thing as `fetch_allow`, which is outbound. Without it every
  read fails in the browser no matter what the outbound allowlist says. As of 2026-09-16 the `nemar` S3
  bucket admits `nemar.org` and its subdomains, the website Pages previews, `demo.osc.earth` and loopback
  for GET and HEAD, and exposes `Content-Range`, `Content-Length` and `Accept-Ranges`, which a sharded Zarr
  reader needs in order to detect a Range request that was not honored. `zarr.nemar.org` and
  `mcp.nemar.org` stay NEMAR-origin-only by choice, so the browser reader addresses the S3 `data_base`
  directly and MCP calls stay server-side in LangGraph. Execution must therefore be gated on an origin
  allowlist and degrade to explain-only elsewhere, which bounds the later claim that the widget stays
  embeddable on third-party pages: embeddable everywhere, executable only on admitted origins.

## Lazy loading and environments

The mental model is a `uv` project per community, mapped onto what Pyodide already provides:

| uv concept | Pyodide mechanism | Where it lives |
|---|---|---|
| `pyproject` dependencies | `runtime.python.preload` and `allow_install` | community `config.yaml` |
| `uv.lock` | a lock **overlay** merged into the stock lock and passed as `loadPyodide({ lockFileContents })` | `src/assistants/<community>/runtime/<community>-pyodide-lock.json`, committed with its wheels |
| `uv sync` | `loadPackage` of `preload` at boot, resolving overlay and stock entries together | worker, at boot only |
| package index | Pyodide CDN for built packages, PyPI for pure wheels, optional community index | `runtime.python.index_urls` |
| interpreter pin | `runtime.python.pyodide_version` | community `config.yaml` |
| cache | browser Cache API; wheels are immutable | user's browser |

Rules:

- Compiled packages come only from the Pyodide distribution or a community-hosted WebAssembly wheel.
  micropip cannot build. If a community's engine needs a compiled dependency,
  the fix is upstream packaging, which is exactly the eegprep case.
- The lockfile is regenerated by a script, reviewed in a PR, and pinned to a Pyodide version.
  Every user of a community gets the same environment.
- `preload` is the small set worth paying for on widget open; `allow_install` is installed at boot as
  well (as built, the model pulls in nothing); anything else is denied and reported.
- Cache invalidation is by URL: a new lockfile means new URLs, old wheels expire on their own.

**As built (phase 2, step 8, #431).** The lockfile is an overlay, not a full lock: the
entries a community adds to the Pyodide distribution of its pinned version, in Pyodide's
own lock-entry shape (`src/core/config/runtime_lock.py`).
The wheels sit in `wheels/` beside it, the server verifies each against its `sha256`
when it loads the overlay, sends the entries in `/config` as `runtime_lock`, and serves
the wheels itself at `GET /{community}/runtime/{file_name}`, immutable, so the host
that sent the hashes is the host the bytes come from and the two cannot come from
different releases. The worker merges the overlay into the stock lock (an entry may add
a package, never replace one), hands the result to `loadPyodide`, and `preload` then
resolves overlay packages and their distribution dependencies in one pass, with the
browser enforcing each digest through `fetch`'s `integrity`. There is no micropip in
this path; `allow_install` remains for installs from `index_urls` at boot.
`scripts/build_runtime_lock.py` regenerates an overlay from its wheels and a
`depends.toml`, and refuses new bytes under a committed wheel name.

Measured on Pyodide 0.29.5: under Node, `loadPackage` resolves a lock `file_name` with
`path.resolve` against its package cache, so an absolute URL does not load there, and
Node ignores the digest entirely. Both work in a browser, which is where they are
verified: in CI by `frontend/browser-harness/chrome.js`, in headless Chrome, whose
tampered wheel is valid so that only the digest can refuse it, and by hand, with the
widget and the live archive, by `frontend/browser-harness/widget_e2e.py`. The Bun test
names the committed wheels by path (`frontend/test-data-lane.js`).

A community can also run a **prelude**, `runtime.python.prelude`: Python run once after
the seal and before the first execution, with exactly executed code's privileges,
compiled at config load. NEMAR's registers eegprep-lean's transport over `osa.fetch`
(the ranged, non-raising client), because eegprep-lean's default reads through
`pyodide.http`, which the seal removes.

## Configuration

Additions to the community YAML, validated like the existing `extensions` block:

```yaml
extensions:
  mcp_servers:
    - name: nemar
      url: https://mcp.nemar.org/mcp
  client_tools:
    - name: execute_code
      runtime: python
      requires_permission: true
      description: Run Python in the user's browser against data the MCP tools point at.

runtime:
  # ADR 0049 requires the assistant to name the ANALYSIS engine it used (eegprep or MNE).
  # `runtime.python` below names the interpreter, which is a different thing; do not
  # mistake one for the other. Carry `analysis_engine` on the result and surface it.
  analysis_engine: mne        # or eegprep, once its Pyodide packaging lands
  python:
    pyodide_version: "314.0.6"
    lockfile: runtime/nemar-pyodide-lock.json
    preload: [numpy, scipy, matplotlib, zarr, numcodecs]
    allow_install: [mne, mne-bids, eegprep, nemar-zarr]
    preload_on: first_run        # or widget_open, or first_message
    fetch_allow:
      - https://zarr.nemar.org
      - https://nemar.s3.us-east-2.amazonaws.com
      - https://api.nemar.org
    limits:
      # ADR 0049 names MEMORY, not CPU, as the binding constraint: wasm32 tops out between
      # 2 and 4 GB and an out-of-memory condition aborts the instance, which a seconds-based
      # deadline does not catch. Budget it explicitly and define an `oom` result status.
      memory_mb: 1536
      stdout_chars: 16384
      stderr_chars: 8192
      images: 3
      image_px: 1024
      exec_seconds: 120
```

Pydantic shape: `ClientTool { name, runtime: Literal["python"], requires_permission: bool = True, description }`,
`PythonRuntimeConfig { pyodide_version, lockfile, preload, allow_install, preload_on, fetch_allow, index_urls, limits }`,
`RuntimeConfig { python: PythonRuntimeConfig | None }`.
`extra="forbid"` throughout, unique tool names, and a validator that a `client_tools` entry
requires a matching `runtime` section.
`mcp_servers` and its runtime consumer shipped in #352 (`src/tools/mcp_client.py`, loaded by
`CommunityAssistant._load_mcp_tools`), and `src/assistants/nemar/config.yaml` already carries the
`mcp_servers` block shown above. `client_tools` and `runtime` are the only new configuration in this design.

**As built (phase 2).** The YAML above is the proposal and is not valid as written: there is no
`analysis_engine` key, which `extra="forbid"` refuses, and no Pyodide `314.0.6`. The shipped block is
`src/assistants/nemar/config.yaml`, and the model is `PythonRuntimeConfig` in
`src/core/config/community.py`, which also has `prelude`. NEMAR pins Pyodide 0.29.5 (zarr 3.4.0 needs
its `google-crc32c`), names its lock overlay in `lockfile`, preloads numpy, matplotlib, zarr and
eegprep-lean (and, since #495, scipy, imported before the seal), reaches only `https://zarr.nemar.org/`, installs nothing with `allow_install`, keeps the
default limits, and registers eegprep-lean's transport in its prelude. The analysis engine ADR 0049
asks to be named is named in NEMAR's prompt, as eegprep-lean. `frontend/test-data-lane.js` requires every
community runtime to pin the Pyodide CI runs.
Note that `CommunityConfig` sets `extra="forbid"`, so a top-level `runtime:` key is rejected until the
model gains the field, and the cross-field validator must live on `CommunityConfig`, not on
`ExtensionsConfig`, because the latter cannot see a top-level sibling.

## Community adoption

| Community | Engine in the browser | Data path | Status |
|---|---|---|---|
| NEMAR | `eegprep-lean` 0.1.0.dev2, vendored and served from OSA's own origin | the MCP's `how_to.python_browser` recipe, HTTPS range reads of `zarr.nemar.org` | shipped (epic #429) |
| EEGLAB | eegprep (EEGLAB-parity numerics) on user-supplied or NEMAR data | same as NEMAR | after NEMAR |
| HED | `hedtools` validation and search | `events.tsv` and sidecars fetched or pasted | candidate; verify pure-Python install |
| BIDS | `pybids` over a fetched layout | dataset trees over HTTPS | candidate; verify pure-Python install |

Each community brings: a lockfile, a `preload` set, an `allow_install` list, a `fetch_allow` list,
prompt guidance on when to run code versus answer from documentation,
and optionally a small pure-Python helper package (for NEMAR, `eegprep-lean`, which turns a recipe into a numpy array).

The NEMAR recipe's `python_zarr` snippet uses anonymous S3 through boto3,
and `boto3`, `botocore`, `aiobotocore` and `s3fs` are all absent from the Pyodide distribution, so a model that copies it fails.
When this note was written only the recipe's TypeScript snippet was browser-safe.
nemar-cli has since added `how_to.python_browser` (its ADRs 0070 and 0071), written for this runtime and reading over HTTPS;
NEMAR's prompt teaches that snippet and tells the model never to use `python_zarr` here.

## What goes back to the model, and why it must be deterministic

Each execution returns output to the agent loop, and that output then sits in the conversation for the rest
of the session. Two constraints shape what it may contain, and they turn out to be the same constraint.

**Prompt caching is a byte-exact prefix match.** The render order is `tools`, then `system`, then `messages`,
and any byte change anywhere in the prefix invalidates everything after it. So a result carrying a memory
address, a wall-clock timestamp, an elapsed duration, or unsorted dictionary keys does not merely cost tokens
once: it poisons the cache prefix for every following turn of the session. Determinism is therefore a
PRECONDITION for a cacheable conversation, not a tidiness preference. Format results explicitly, with fixed
float precision and sorted keys; never `repr()`.

**The context is not where bulk output belongs.** `count_tokens_approximately` does not understand image
content and falls back to character counting, so a single 250 KB plot measures about 85,000 tokens against
an 80,000 budget. What the model needs in order to choose the next step is a compact description, not the
bytes.

So the runtime returns a deterministic summary, and the raw output stays in the browser workspace:

| Instead of | The conversation carries |
|---|---|
| Raw stdout | A capped tail, plus structured facts: variables created, dtype, shape, min, max, mean, NaN count |
| A PNG | An artifact handle plus deterministic plot metadata: title, axis labels, series count, data ranges |
| A full traceback | Exception type, message, and the offending line |

Attach real image content blocks only for the MOST RECENT execution and replace older ones with text
placeholders, because images in the history are bytes in the prefix. Provide a `get_full_output(call_id)`
tool so the model can pull detail on demand: the common path stays cheap and the rare path stays possible.

**MCP tool results follow the same rule, not a separate one (issue #432).** `nemar_render_overview`'s PNG
reaches the model as a real Anthropic image content block, but only on the Anthropic path: OpenRouter and
LiteLLM have not been shown to accept that block shape, so `CommunityAssistant`'s `allow_mcp_images` (resolved
once from the request's provider choice, the same way `citations` is) gates it at tool-wrap time, and a
non-Anthropic run gets a text placeholder instead of a silently-dropped image. The same gate now covers a
browser execution's figures in run 2: `/chat/resume` resolves the provider before it builds the live
message, and on a non-Anthropic path each figure arrives as "not attached: images are not sent to this
model", which NEMAR's prompt tells the model to relay rather than describe. Within one run the image is
re-sent with every later model call -- there is no per-call "most recent" trimming inside a single run, only
across runs -- and stored history never keeps it: `scrub_stored_images` replaces every image block it finds
(a client-tool result's live message that rode along into a second parked call, and a real MCP `ToolMessage`
LangGraph's `ToolNode` built directly) with a text placeholder before `ChatSession.replace_history` persists
anything. `OSA_MCP_IMAGES_DISABLED` is the incident-control kill switch, checked fresh on every call rather
than baked in at discovery time, so it takes effect without waiting out the tool-discovery cache or a restart.

**The breakpoint should move past the system block.** `CachingLLMWrapper` marks system messages only. A
request may carry up to FOUR cache breakpoints, so the pattern this design wants is one covering tools and
system, and a second moving forward over the stable conversation prefix, leaving only the recent tail
uncached.

**Ship on the default 5-minute time to live, and instrument the gap before considering an hour.** Reads cost
about 0.1x base input; writes cost 1.25x at five minutes and 2x at one hour, so the five-minute TTL breaks
even at two requests and the one-hour needs three or more. The lifetime is measured from the START of the
request that writes or reads the entry, and generation time counts against it, so run 1's own generation eats
the window before the browser begins. The decision therefore turns on one unknown: the start-to-start gap
between run 1 and run 2. Under five minutes, the default is strictly cheaper and every continuation refreshes
it. Between five and sixty minutes is the only window where the doubled write price pays. Beyond an hour,
neither helps. Nobody can know that distribution before the feature exists, and getting it wrong costs money
rather than correctness, so it is a knob to turn on evidence, not a design commitment.

**A server-side keep-alive is the better middle option.** While a call is pending the server knows it is
pending, so it can re-send the previous request with `max_tokens: 0` just under five minutes: that refreshes
the timer and bills a cache read instead of a 2x write. The one-hour premium is 0.75x extra on the write and
each keep-alive is about 0.1x, so keep-alives win up to roughly seven of them, about 35 minutes of idle.
It must be server-side: a client-driven keep-alive would consume the proxy worker's per-IP budget of 10 per
minute and 20 per hour, while a server-side one never reaches it. Cache reads also do not count toward
input-token rate limits on most models.

**The trap.** `_prepare_messages` runs `trim_messages(strategy="last")` at an 80,000-token budget, and
trimming drops messages from the FRONT, which changes the prefix and invalidates the whole cache. Trimming
and prefix caching are in direct conflict. The deterministic summary is what keeps the history small enough
that trimming is rare, and rare trimming is what makes caching pay. Built the other way around, the cache
resets every few turns and the feature looks expensive for no visible reason.

**This work belongs with the Claude Platform on AWS migration (#360), not beside it.** That epic retires the
OpenRouter platform route and serves communities directly, which removes the open question of whether
`cache_control` survives LiteLLM and OpenRouter; its phase 1 already owns "prompt caching that survives tool
binding", which is exactly the `CachingLLMWrapper` and `bind_tools` nesting problem. Building conversation
caching against the path being retired would be building it twice.

Three consequences of that epic for this design:
- Prompt caching at both the 5-minute and 1-hour time to live IS available on Claude Platform on AWS, so
  there is no availability constraint on any of the above.
- The offered models narrow to `claude-haiku-4-5` (default, explicit 2048-token thinking budget) and
  `claude-sonnet-5`. NEITHER supports mid-conversation system messages, which are an Opus 5, Opus 4.8,
  Fable and Mythos feature. Operator instructions therefore stay in the top-level system block, and changing
  one resets the cache. Do not design an operator channel that assumes otherwise.
- Cache diagnostics is a first-party API beta and is NOT on Claude Platform on AWS. Verification runs through
  `usage.cache_read_input_tokens` and the `usage.cache_creation` breakdown, which splits by time to live
  (`ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`) and is the right instrument for the TTL
  decision above. A persistent zero read count means a silent invalidator is at work.

One thing to confirm early: the minimum cacheable prefix is model-dependent, between 512 and 4096 tokens, and
Haiku 4.5 sits at the high end, so measure whether the assembled system prompt clears the floor at all.
Haiku's explicit thinking budget also puts thinking blocks in the history, where they become part of the
prefix, which is a further reason the result format must be deterministic.

## Persistence and the notebook surface

- Workspace layout in browser storage: `/<community>/<session>/scripts/`, `/results/`, `/artifacts/`,
  with a `manifest.json` per session naming what the assistant produced and when.
- Python writes through an injected `osa` helper: `osa.save_script(name, code)`, `osa.save_artifact(path, bytes)`.
  The widget mirrors saves into the `tool_result` artifacts list.
- **DECIDED (ADR 0010, 2026-09-23): the notebook surface is JupyterLite with the Pyodide kernel, pinned to Pyodide 0.29.5 (OSA's own pin), not marimo.**
  marimo was measured directly, not only reasoned about:
  it cannot complete the real NEMAR read in any configuration,
  because its own WebAssembly (WASM) concurrency sandbox rejects the `multiprocessing.Lock` `numcodecs`'s blosc codec needs to decompress
  real Zarr chunks (`UnsupportedWasmConcurrencyError`),
  on top of the four integration gaps the 2026-09-16 review already found
  (no `postMessage`/embedding application programming interface (API), its own storage backed by IndexedDB File System (IDBFS),
  content delivery network (CDN)-only Pyodide now a full version generation past 0.29.5,
  and the one-definition-per-variable model).
  The speed question is also settled and does not favor marimo:
  under a CDN-matched control, the two surfaces differ by well under a second for a trivial cell.
  See ADR 0010 and its companion `.context/notebook-surface-measurements.md` for the full measurement.
  No notebook surface ships yet: until JupyterLite lands, the chat widget's own editable re-run panel
  ("Edit and run" on a recorded run, `ClientToolController.runLocal`, `docs/community-browser-runtime.md`)
  is the one place a reader can keep tinkering with what the assistant already ran.
- JupyterLite's contents layer does **not** mount the widget's own Origin Private File System (OPFS) or IndexedDB storage directly
  (confirmed by building it, not only assumed):
  a build-time `--contents <dir>` import into JupyterLite's own, same-origin IndexedDB-backed Contents store is what was tested and works;
  there is no live cross-origin bridge to the widget's storage,
  since the notebook surface opens on its own origin (never the widget's embedding page's origin)
  and neither browser storage nor Pyodide's kernel is shared across that boundary.
  File sharing (export a zip, import it into JupyterLite's own contents) is therefore not a stepping stone toward kernel sharing:
  it is the mechanism, full stop, for as long as the notebook surface is a separate origin.
  Sharing a live kernel with the chat worker stays a non-goal (see below), not a later refinement of this.
- Pinning JupyterLite to exactly 0.29.5 is possible (`jupyter lite build --pyodide=<tarball>`, confirmed working)
  but costs a real, measured 529-531 MB unpruned static deploy, versus ~64 MB for the unpinned default
  (which drifts to whatever Pyodide version `jupyterlite-pyodide-kernel` ships next, currently 314.0.0, the same generation marimo uses, not 0.29.5).
  `--no-unused-shared-packages` should prune that down for a real deployment;
  the pruned size was not measured and is open work for whoever builds this phase.
- Export: download the workspace as a zip at any time. Nothing leaves the browser unless the person exports it.

## Security

- Model-written code runs in the person's own browser sandbox, in a worker, with no credentials.
  The blast radius of prompt injection through fetched data is bounded by that: it can waste the person's CPU,
  not their identity.
- The permission gate is on by default and the widget never auto-runs code that fetches outside `fetch_allow`.
- Result caps bound what reaches the model and the server.
- Existing OSA rate limits and budgets apply unchanged; execution adds no server-side cost beyond the resumed LLM call.
- No cross-origin isolation is required, so the widget stays embeddable on third-party pages.

## What this assumes about OSA today

Added after review. Each item below is a prerequisite that this design silently assumed and that does not
hold against the deployed code. They are Phase 0: none of Phase 1 works end to end until they are settled.

1. **The 120-second abort is no longer a constraint, because two-run parks nothing.** The proxy worker
   and the widget each pass `AbortSignal.timeout(120000)`, counted from construction, so it is a wall-clock
   ceiling on a whole request. This is the finding that decided the transport. Under two-run it bounds only
   a single model streaming response, which is what it bounds today and already works: the browser executes
   between run 1 and run 2 with NO request open, so neither execution nor the human's approval time is
   inside any HTTP budget. **Do not raise it.** 120 seconds is enough, and leaving it alone avoids a
   `wrangler deploy` to two environments and avoids depending on a widget constant that embedders pin by
   SRI hash and never update.

   Two clocks remain, and they are not the HTTP one. `exec_seconds` is the browser's own execution budget,
   enforced in the worker. A separate, generous approval or idle deadline covers human reading time. The
   note previously used `deadline_s` for both; name them apart.

2. **The resume endpoint 404s at the edge.** The proxy worker routes by an anchored allowlist whose chat
   matcher is two path segments; `/{community}/chat/resume` has three, matches nothing, and falls through to
   a 404. Phase 1 therefore includes a worker route and a deploy to both environments. The worker forwards
   only an allowlisted header set, so any correlation identifier must travel in the JSON body, never a header.
3. **Turnstile makes the resume POST unauthenticatable as designed.** Protected POSTs are verified against a
   single-use Turnstile token that the widget clears after each message. This is latent today because
   Turnstile is disabled, and fatal the day it is switched on. Route resume like `/feedback`, which the
   worker already treats as rate-limit-only, and bind authorization to the pending `call_id` instead.
4. **Rate limits are consumed per resume.** Production is 10 requests per minute and 20 per hour per IP.
   One turn with N executions is 1 + N requests, so two executions cost three of a user's twenty hourly
   budget, shared across a NAT'd lab. The claim elsewhere in this note that existing limits apply unchanged
   is wrong. Exempt the resume path from the hourly counter or state the new effective budget.
5. **There is no checkpointer and the graph is compiled per request.** `build_graph()` is called on the hot
   path and `graph.compile()` takes no checkpointer, so `interrupt` has nowhere to persist and no
   `thread_id`. Separating compilation from per-request assistant construction is a request-path refactor,
   not a component; cost it as such. Each resume currently also rebuilds the assistant, which re-runs MCP
   discovery as a live network round trip.
6. **Conversation state would have two owners.** `ChatSession.messages` is typed to hold only human and
   assistant messages, and the stream writes back the final assistant text only. No `ToolMessage` is ever
   persisted, so code and its output vanish at the turn boundary and the model cannot iterate on a result
   across turns. Name the store of record: either the checkpointer becomes the history and `ChatSession`
   becomes an index, or the design accepts single-turn iteration and says so.
7. **Session eviction can drop a parked call.** Sessions expire on a 24-hour TTL and evict LRU at 1000 per
   community, a miss creates a fresh empty session rather than erroring, and there is no locking, so a late
   resume lands silently in a new conversation. Checkpoint retention is not a separate open question; it is
   a second policy that has to agree with this one.
8. **Deploys kill parked calls.** The container is a single process with no `--workers` and no replicas, so
   the in-process rendezvous works, but by accident rather than by design. Record single-process,
   single-replica as an invariant this design now depends on. The hourly auto-update recreates the container
   with no drain.
9. **Old cached widgets hang rather than fail.** The widget dispatches on a JSON key and warns on unknown
   event types, so an old copy will not crash on `tool_request`; it will simply never show a reply, and
   embedders pin SRI-hashed versions that persist indefinitely. The client must declare which client tools
   it can execute, and the server must not emit `tool_request` for a tool the client did not claim. Relatedly
   the `tool_cancel` payload in this note omits the `event` key its own dispatcher requires.
10. **Security: the note answers only one threat model.** The argument that the blast radius is bounded
    addresses attacker-equals-dataset-author. It is silent on attacker-equals-the-person-at-the-keyboard,
    which the resume endpoint newly exposes: a `tool_result` becomes a `ToolMessage` in the model's context,
    and nothing here requires the server to check that `(session_id, call_id)` names an OUTSTANDING interrupt.
    Name the resume handler as the enforcement point, reject any `call_id` not currently parked for that
    exact session, accept each at most once, and size and type every field before use. Separately, the
    return path is itself an injection channel: fetched bytes become `print()` output become a `ToolMessage`,
    so tool output must enter the context fenced and labeled as data, and the result caps are a containment
    control, not only a context-cost control.

## Phasing

1. **Server: client-executed tools.** `ClientTool`, the `client_tools` node, checkpointer, `tool_request` on the stream,
   the resume endpoint, config models, and the Phase 0 prerequisites above.
   Test it by driving the real ASGI app: a scripted chat model emitting one `execute_code` tool call, a real
   SSE read until `tool_request`, a real POST to the resume endpoint, a real in-memory checkpointer, and
   assertions on the SSE event names. The only stand-in is the chat model, which the testing guidelines
   already permit because the model's behavior is not under test; `tests/test_tools/test_mcp_client.py` is
   the precedent, booting a real server on a real socket. Note that `FakeListChatModel`, the repo's usual
   fake, CANNOT emit tool calls and its `bind_tools` raises; `FakeMessagesListChatModel` does drive the tool
   round trip but emits no `on_chat_model_stream`, so tool routing and content streaming need two tests.
   Phase 1's definition of done also includes that `client_tools` stays unset in every shipped `config.yaml`,
   plus a named kill switch, because `develop` auto-deploys and a tool with no executor parks every stream.
2. **Widget: the runtime.** Pyodide worker, `execute_code`, permission gate, output capture and caps,
   images into the model context. NEMAR pilot with MNE and the recipe reader.
3. **Environments.** Lockfile REGENERATION tooling, cache behavior, progress UI. eegprep as the NEMAR engine
   once its packaging lands. Note that the lockfile itself, `allow_install` and `fetch_allow` moved into
   Phase 2, because Phase 2 cannot boot a pinned runtime or load MNE without them and its stated safety
   property depends on the allowlist. `pybids` is BLOCKED under Pyodide by `num2words -> docopt`, which
   ships an sdist only; `hedtools`, `mne` and `mne-bids` all resolve.
4. **Workspace and notebook.** Persistence layout, the `osa` helper, JupyterLite handoff, export.
5. **Second community.** EEGLAB or HED adopts the tool with no change to OSA source, supplying only
   community-owned artifacts. Not "configuration alone": this note also asks each community for a lockfile
   and optionally a helper package, and neither is configuration.

**As built.** Phases 1 to 4 shipped as epic #429 (#430 to #433), all four on one epic branch.
Phase 1's tests drive the real application with no checkpointer, since none was built.
Phase 2's pilot used `eegprep-lean` rather than MNE, and pulled most of phase 3's lockfile work forward.
Phase 4 kept the workspace, the `osa` helper and export, and replaced the JupyterLite handoff with the editable re-run panel (#456); a hosted JupyterLite is deferred to #453 (ADR 0010), and ADR 0011 has since built and hosted it at `notebook.osc.earth/osa`.
Phase 5 has not started.

Phases 1 to 3 land on an epic branch and merge to `develop` together, per this repo's own epic-branch
workflow, which exists for exactly this shape: Phase 2 depends on Phase 3, and Phase 1 alone ships a tool
surface with no executor. Phases 4 and 5 can be separate pull requests. Phase numbers here are local to this
note and do not correspond to the global phases in `.context/plan.md`.

## Open questions

- ~~The transport.~~ **DECIDED 2026-09-16: two-run continuation, option (c) below.**
  The run ends with the assistant message carrying the client tool call; the browser executes with no
  request open; a second request continues the graph with the tool message appended. This design therefore
  does NOT use LangGraph's `interrupt()`, `Command(resume=)`, or a checkpointer at all.
  Rejected: (a) parking the SSE stream and raising the timeouts, and (b) one WebSocket per session. Both
  spend real engineering on holding a connection open across human latency, and both still need the
  checkpointer, which in this codebase is a refactor of the per-request path rather than a component.
  Two measured LangGraph behaviors also bite (a) and (b) and not (c): a resumed node re-runs from its start,
  so any pre-interrupt side effect runs twice, and every interrupt raised in one `ToolNode` task shares an
  `Interrupt.id`, so two `execute_code` calls in one message cannot be told apart. Under (c) these are not
  problems, because nothing is resumed; a continuation is just a new run over a longer message list.
  What (c) costs instead is idempotency work, listed as a Phase 1 requirement above.

- Cooperative cancellation without `SharedArrayBuffer`; the reboot fallback is acceptable if the cache makes it cheap.
- Parallel tool calls. The router returns one destination for a whole assistant message, so a batch mixing a
  server tool and a client tool cannot be split today. Constrain the model to one client-tool call per turn,
  or route with `Send`.
- ~~Whether the provider layer accepts image content blocks on a TOOL message.~~
  **ANSWERED 2026-09-19: it does, and the producer is the only thing that validates the media type.**
  Measured against the real payload `CachingChatAnthropic` builds, in
  `tests/test_core/test_tool_result_image_transport.py`: an image block on a `ToolMessage` arrives nested in
  the `tool_result`, and all four plausible spellings (Anthropic native, LangChain's v1 and v0 standard
  blocks, and an OpenAI-style data URL) normalize to the same Anthropic image block, so a widget cannot pick
  the wrong one and silently lose the figure. `tests/test_integration/test_anthropic_platform.py::TestToolResultImages`
  settles the other half against the live endpoint, for BOTH offered models: the model reports which bar of a
  chart is tallest and which is shortest, an answer carried in no text block anywhere, and a control round trip
  without the picture cannot produce it. The answer comes back through a forced tool call rather than as prose,
  because the first version asked for a formatted reply and `claude-haiku-4-5` answered with a numbered list
  that read the figure correctly and parsed to the wrong bar.
  Two consequences for Phase 2. First, the client stack validates nothing: `image/svg+xml` passes every
  layer here and comes back as a 400 from the endpoint, so a recipe calling `savefig(format="svg")` fails
  after the analysis has already run. The accepted set is declared as `IMAGE_MEDIA_TYPES` in
  `src/core/services/anthropic_models.py` and the widget has to gate on it. Second, an image can also be sent
  as a URL rather than inline bytes, which would have the model's provider fetch from the data plane; inline
  base64 is what keeps this design's "bytes never move through a third party" property, and it is what the
  tests pin.
- ~~JupyterLite storage bridge versus a lighter notebook UI of our own (i.e., marimo).~~
  **DECIDED (ADR 0010, 2026-09-23): JupyterLite, with a build-time import, not a live storage bridge, and not marimo.**
  marimo was built and measured directly against the real NEMAR read and failed on all of them
  (its WASM sandbox refuses a lock a real dependency needs, on top of the four integration gaps the 2026-09-16 review already found),
  so "a lighter notebook UI of our own" resolved to "not marimo" rather than to marimo.
  On the storage half: Pyodide's `mountOPFS` is still not in a released version as of 2026-09-23 (re-checked),
  so there is still no live bridge to mount;
  what was built and confirmed working is a **build-time `--contents` import** into JupyterLite's own, same-origin IndexedDB-backed Contents store,
  an offline step, not a bridge, and that is now the answer rather than an open question.
  Sharing a live JupyterLite kernel with the chat worker remains unsupported and belongs under non-goals, unchanged by this decision.
  `mountNativeFS` (the File System Access API, Chromium-only) remains unexplored.
- ~~Where community lockfiles live.~~ **DECIDED 2026-09-22 (#431, step 8): in the community's own folder
  in this repo, served by this API.** An eegprep-lean bump is then a server deploy, with no widget release
  and no nemar.org re-pin. Hosting them beside the widget was rejected because the server's config and a
  pinned widget's wheels would then drift; a community-owned repository by URL stays open for phase 5.
- Image cost in the model context; whether to downscale further by default.
- The SSE event vocabulary listed earlier in this note is incomplete: the live stream also emits `session`
  and `warning`, and the widget handles both. Anyone adding an event needs the true list.
- Retention of anonymous checkpoints; the sweep interval and what "expired" means for a parked tool request.
  Moot if the two-run option above is chosen.
- Whether MCP tool calls should also become client-executed later, so a widget can run entirely against
  public endpoints. Note this is close to the client-side agent loop that ADR 0049 rejected and kept only as
  a fallback; that fallback is live if interrupt-and-resume proves unworkable, and belongs in this list.

## References

- `.context/tool-system-guide.md`: the QP tool system, whose `execute_python_code` is client-executed against
  a Jupyter kernel. What is shared is the tool contract and the permission model. What is NOT shared is where
  the loop runs: QP's agent loop is client-side, so it needed no interrupt, no checkpointer and no resume.
  OSA's loop is server-side, which is the entire hard part of this design. Cite QP for the contract, not as
  de-risking.
- `.context/qp-worker-architecture.md`: the streaming proxy pattern the widget already uses.
- Pyodide: `loadPackagesFromImports`, `micropip.install`, `micropip.freeze`, `loadPyodide({ lockFileURL })`, `setInterruptBuffer`.
  As built: `loadPyodide({ lockFileContents, packageBaseUrl })` and `loadPackage(name, { checkIntegrity })`;
  neither `micropip.freeze` nor `setInterruptBuffer` is used.
- LangGraph: `interrupt`, `Command(resume=...)`, checkpointers.
- nemarOrg/nemar-cli ADR 0049 (compute in the browser, only HPC submission gated) and issue #1065 (the MCP server whose recipes this runtime consumes).
- sccn/eegprep: the extras split that makes eegprep installable under Pyodide.
