# Browser Execution Tool Design

Status: draft for review, 2026-09-08.
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
the graph interrupts, the server streams a `tool_request` event to the widget,
the widget runs the code and streams a `tool_result` back, and the graph resumes with that result
in the model's context, images included.
The runtime is lazy: Pyodide boots on first use or on widget open,
packages load from the code's imports against a per-community pinned lockfile,
and everything is cached by the browser after the first download.
Scripts and results persist in browser storage, and a notebook surface can open them later.
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
  | chat UI  <---- SSE / WebSocket ------|---------|  stream_response                        |
  |   |  tool_request / tool_result      |         |    ^                                    |
  |   v                                  |         |    |  interrupt() / Command(resume=...)  |
  | client tool registry                 |         |  StateGraph                             |
  |   |  execute_code                    |         |   agent --> tools (server tools)        |
  |   v                                  |         |         --> client_tools (interrupt)    |
  | runtime worker (Pyodide)             |         |  checkpointer keyed by session_id       |
  |   loadPackagesFromImports / micropip |         +----------------------------------------+
  |   fetch allowlist, output capture    |
  |   v                                  |          Data plane (community-owned, public)
  | workspace (OPFS / IndexedDB)         |<-------  e.g. zarr.nemar.org, S3, PyPI, CDN
  |   scripts, results, artifacts        |
  | notebook surface (JupyterLite)       |
  +--------------------------------------+
```

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
  -> stream_response sees __interrupt__ and emits SSE event tool_request
widget receives tool_request
  -> shows code; waits for Run (or auto-run is on)
  -> worker.run(code): loadPackagesFromImports, micropip for allowlisted imports, execute
  -> captures stdout/stderr, images, artifact names; applies caps
  -> POST /{community}/chat/resume {session_id, call_id, result}   (or WebSocket frame)
server resumes graph with Command(resume=result)
  -> result becomes the ToolMessage; images become image content blocks
  -> agent node continues; streaming continues on the same SSE connection
```

One round trip per execution.
LLM latency dominates every round trip, so the transport choice is about connection handling, not speed.

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

Caps, enforced on the client and re-checked on the server:

| Field | Cap | Why |
|---|---|---|
| stdout | 16 KB | The model needs a summary, not a dump |
| stderr | 8 KB, tail-preserved | The last lines carry the traceback |
| images | 3 per call, longest side 1024 px, PNG | Bounded context cost, still legible |
| artifacts | names and sizes only | Data stays in the browser |
| execution | `deadline_s` from config, default 120 | A runaway cell must not park the thread |

Arrays never travel to the server.
A tool that needs numbers back returns a small table in stdout or writes an artifact and reports its name.

## Server changes

- `ClientTool`: a `BaseTool` subclass whose `_run` raises `interrupt(...)` with the request payload
  and returns the resume value as the tool output.
  It is bound to the model like any other tool, so the model sees one tool surface.
- Graph: add a `client_tools` node next to `tools`; the router sends a call to `client_tools`
  when the tool name is registered as client-executed. The node body contains nothing but the interrupt,
  because LangGraph re-runs the node from its start on resume.
- Checkpointer: `MemorySaver` by default with an expiry sweep for anonymous sessions;
  `SqliteSaver` when `OSA_CHECKPOINTER=sqlite`.
  This is also what gives OSA durable threads, which it does not have today.
- `stream_response`: detect `__interrupt__` in the stream, emit `tool_request`, and leave the SSE connection open.
  Resume through `POST /{community}/chat/resume` or the WebSocket; the resumed run streams on the same connection.
- Result handling: text becomes the `ToolMessage` content; images are attached as image content blocks
  on the tool message where the model supports them, otherwise summarized as `[image: WxH]`.
- Budget: tool results count toward the existing conversation token budget; images at the provider's image rate.
- Timeouts: an unanswered `tool_request` past `deadline_s` plus a grace period resumes the graph
  with `status: timeout` so the model can respond, and the thread is not left parked.

Transport recommendation: start with SSE plus a resume endpoint, because the widget already speaks SSE
and the change is additive.
Move to one WebSocket per session when the resume round trips are measurable in practice,
or when cancellation from the server side is needed.
The message schema is identical either way.

## Client changes

- Worker lifecycle: boot on `preload_on: widget_open` for communities that opt in,
  otherwise on the first `tool_request`.
  Show a progress bar keyed to package downloads; the first load of a scientific stack is 50 to 60 MB.
  Keep the worker warm across turns.
- Execution: `pyodide.loadPackagesFromImports(code)` for packages Pyodide ships,
  then `micropip.install` for imports that resolve to pure-Python wheels and are on the community allowlist,
  then `runPythonAsync`.
  An import outside the allowlist produces a `denied_import` in the result rather than a silent install.
- Output capture: redirect `sys.stdout` and `sys.stderr`; set the matplotlib backend to Agg
  and collect figures as PNG; support a small `display()` protocol for images and HTML tables.
- Cancellation: cooperative cancellation through `setInterruptBuffer` needs a `SharedArrayBuffer`,
  which needs cross-origin isolation headers on the embedding page.
  Do not require that. Fall back to terminating the worker and rebooting it,
  and make the cold reboot cheap by keeping the lockfile and wheels in the cache.
- Permission gate: show the code with syntax highlighting, Run and Deny buttons,
  and an "auto-run for this session" toggle. Default on for every community.
- Network: wrap `fetch` inside the worker so Python's HTTP calls can only reach the community's `fetch_allow` origins
  plus the package sources. No cookies or credentials are ever passed into the worker.
- Content Security Policy: the embedding page needs `wasm-unsafe-eval` for the worker.
  nemar.org already scopes that to the viewer route (website ADR 0009), so there is precedent.

## Lazy loading and environments

The mental model is a `uv` project per community, mapped onto what Pyodide already provides:

| uv concept | Pyodide mechanism | Where it lives |
|---|---|---|
| `pyproject` dependencies | `runtime.python.preload` and `allow_install` | community `config.yaml` |
| `uv.lock` | `micropip.freeze()` output, loaded through `loadPyodide({ lockFileURL })` | `runtime/<community>-pyodide-lock.json`, committed |
| `uv sync` | `loadPackagesFromImports` plus `micropip.install` on demand | worker, at run time |
| package index | Pyodide CDN for built packages, PyPI for pure wheels, optional community index | `runtime.python.index_urls` |
| interpreter pin | `runtime.python.pyodide_version` | community `config.yaml` |
| cache | browser Cache API; wheels are immutable | user's browser |

Rules:

- Compiled packages come only from the Pyodide distribution or a community-hosted WebAssembly wheel.
  micropip cannot build. If a community's engine needs a compiled dependency,
  the fix is upstream packaging, which is exactly the eegprep case.
- The lockfile is regenerated by a script, reviewed in a PR, and pinned to a Pyodide version.
  Every user of a community gets the same environment.
- `preload` is the small set worth paying for on widget open; `allow_install` is what the model may pull in;
  anything else is denied and reported.
- Cache invalidation is by URL: a new lockfile means new URLs, old wheels expire on their own.

## Configuration

Proposed additions to the community YAML, validated like the existing `extensions` block:

```yaml
extensions:
  python_plugins:
    - module: src.assistants.nemar.tools
  mcp_servers:
    - name: nemar
      url: https://mcp.nemar.org/mcp
  client_tools:
    - name: execute_code
      runtime: python
      requires_permission: true
      description: Run Python in the user's browser against data the MCP tools point at.

runtime:
  python:
    pyodide_version: "314.0.6"
    lockfile: runtime/nemar-pyodide-lock.json
    preload: [numpy, scipy, matplotlib, zarr, numcodecs]
    allow_install: [mne, mne-bids, eegprep, nemar-zarr]
    preload_on: first_run        # or widget_open
    fetch_allow:
      - https://zarr.nemar.org
      - https://nemar.s3.us-east-2.amazonaws.com
      - https://api.nemar.org
    limits:
      stdout_bytes: 16384
      stderr_bytes: 8192
      images: 3
      image_px: 1024
      exec_seconds: 120
```

Pydantic shape: `ClientTool { name, runtime: Literal["python"], requires_permission: bool = True, description }`,
`PythonRuntimeConfig { pyodide_version, lockfile, preload, allow_install, preload_on, fetch_allow, index_urls, limits }`,
`RuntimeConfig { python: PythonRuntimeConfig | None }`.
`extra="forbid"` throughout, unique tool names, and a validator that a `client_tools` entry
requires a matching `runtime` section.
The `mcp_servers` model already exists; wiring a runtime consumer for it is a separate, smaller change
that the NEMAR assistant also needs.

## Community adoption

| Community | Engine in the browser | Data path | Status |
|---|---|---|---|
| NEMAR | eegprep once its extras split ships; MNE until then | Zarr recipes from the MCP, HTTPS reads | first adopter |
| EEGLAB | eegprep (EEGLAB-parity numerics) on user-supplied or NEMAR data | same as NEMAR | after NEMAR |
| HED | `hedtools` validation and search | `events.tsv` and sidecars fetched or pasted | candidate; verify pure-Python install |
| BIDS | `pybids` over a fetched layout | dataset trees over HTTPS | candidate; verify pure-Python install |

Each community brings: a lockfile, a `preload` set, an `allow_install` list, a `fetch_allow` list,
prompt guidance on when to run code versus answer from documentation,
and optionally a small pure-Python helper package (for NEMAR, a reader that turns a recipe into a numpy array).

## Persistence and the notebook surface

- Workspace layout in browser storage: `/<community>/<session>/scripts/`, `/results/`, `/artifacts/`,
  with a `manifest.json` per session naming what the assistant produced and when.
- Python writes through an injected `osa` helper: `osa.save_script(name, code)`, `osa.save_artifact(path, bytes)`.
  The widget mirrors saves into the `tool_result` artifacts list.
- The notebook surface is JupyterLite with the Pyodide kernel, opened from the widget with the session's workspace.
  Phase 4 evaluates whether JupyterLite's contents layer can mount the same storage directly
  or needs an import step, and whether sharing the live kernel with the chat worker is worth its complexity.
  File sharing ships first; kernel sharing is a refinement.
- Export: download the workspace as a zip at any time. Nothing leaves the browser unless the person exports it.

## Security

- Model-written code runs in the person's own browser sandbox, in a worker, with no credentials.
  The blast radius of prompt injection through fetched data is bounded by that: it can waste the person's CPU,
  not their identity.
- The permission gate is on by default and the widget never auto-runs code that fetches outside `fetch_allow`.
- Result caps bound what reaches the model and the server.
- Existing OSA rate limits and budgets apply unchanged; execution adds no server-side cost beyond the resumed LLM call.
- No cross-origin isolation is required, so the widget stays embeddable on third-party pages.

## Phasing

1. **Server: client-executed tools.** `ClientTool`, the `client_tools` node, checkpointer, `tool_request` on the stream,
   the resume endpoint, config models, tests with a fake client.
2. **Widget: the runtime.** Pyodide worker, `execute_code`, permission gate, output capture and caps,
   images into the model context. NEMAR pilot with MNE and the recipe reader.
3. **Environments.** Lockfile generation script, `preload` and `allow_install`, fetch allowlist, cache behavior,
   progress UI. eegprep as the NEMAR engine once its packaging lands.
4. **Workspace and notebook.** Persistence layout, the `osa` helper, JupyterLite handoff, export.
5. **Second community.** EEGLAB or HED adopts the tool through configuration alone, which is the proof of genericity.

Each phase is one issue and one PR into `develop`.

## Open questions

- SSE plus resume endpoint versus one WebSocket per session; start with the former, measure.
- Cooperative cancellation without `SharedArrayBuffer`; the reboot fallback is acceptable if the cache makes it cheap.
- JupyterLite storage bridge versus a lighter notebook UI of our own.
- Where community lockfiles live: in this repo next to the config, or in a community-owned repo referenced by URL.
- Image cost in the model context; whether to downscale further by default.
- Retention of anonymous checkpoints; the sweep interval and what "expired" means for a parked tool request.
- Whether MCP tool calls should also become client-executed later, so a widget can run entirely against public endpoints.

## References

- `.context/tool-system-guide.md`: the QP tool system, whose `execute_python_code` is client-executed against a Jupyter kernel;
  this design keeps that shape and swaps the kernel for Pyodide.
- `.context/qp-worker-architecture.md`: the streaming proxy pattern the widget already uses.
- Pyodide: `loadPackagesFromImports`, `micropip.install`, `micropip.freeze`, `loadPyodide({ lockFileURL })`, `setInterruptBuffer`.
- LangGraph: `interrupt`, `Command(resume=...)`, checkpointers.
- nemarOrg/nemar-cli ADR 0049 (compute in the browser, only HPC submission gated) and issue #1065 (the MCP server whose recipes this runtime consumes).
- sccn/eegprep: the extras split that makes eegprep installable under Pyodide.
