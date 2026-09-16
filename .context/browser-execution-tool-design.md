# Browser Execution Tool Design

Status: draft, revised after review 2026-09-16. The transport question in Open Questions is UNRESOLVED and blocks Phase 1.
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
  | chat UI  <---- SSE ------------------|---------|  _stream_chat_response                  |
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
  -> _stream_chat_response sees __interrupt__ nested in on_chain_stream, emits SSE event tool_request
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

Transport recommendation: start with SSE plus a resume endpoint, because the widget already speaks SSE
and the change is additive.
Move to one WebSocket per session when the resume round trips are measurable in practice,
or when cancellation from the server side is needed.
The message schema is identical either way.

## Client changes

- Worker lifecycle: boot on `preload_on: widget_open` for communities that opt in,
  otherwise on the first `tool_request`.
  Show a progress bar keyed to package downloads;
  the first load of a scientific stack is about 33 MB, dominated by scipy at about 14 MB.
  ADR 0049's "50 to 60 MB" figure is too high and should be corrected there in the same pass.
  Dropping scipy and matplotlib from `preload` gets under 10 MB, which changes the tradeoff.
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
  and an "auto-run for this session" toggle. The permission gate is ON by default for every community;
  auto-run is OFF until the person turns it on for that session.
- Network: the enforceable boundary is a `Content-Security-Policy: connect-src` on the worker, which the
  browser applies to the worker's own requests. A `fetch` wrapper alone is NOT a boundary: `js.fetch`,
  `XMLHttpRequest` (which Pyodide's synchronous HTTP shims use), `WebSocket` and `EventSource` are all
  reachable from Python, and `micropip` must be removed from the execution namespace or model-written code
  can install for itself. Shim every entry point, set `credentials: "omit"` on the allowed ones so
  same-origin requests do not silently carry cookies, and treat `fetch_allow` as an EGRESS control: an
  allowlisted origin can be sent conversation content inside a URL and will log it.
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
    preload_on: first_run        # or widget_open
    fetch_allow:
      - https://zarr.nemar.org
      - https://nemar.s3.us-east-2.amazonaws.com
      - https://api.nemar.org
    limits:
      # ADR 0049 names MEMORY, not CPU, as the binding constraint: wasm32 tops out between
      # 2 and 4 GB and an out-of-memory condition aborts the instance, which a seconds-based
      # deadline does not catch. Budget it explicitly and define an `oom` result status.
      memory_mb: 1536
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
`mcp_servers` and its runtime consumer shipped in #352 (`src/tools/mcp_client.py`, loaded by
`CommunityAssistant._load_mcp_tools`), and `src/assistants/nemar/config.yaml` already carries the block
shown above. `client_tools` and `runtime` are the only new configuration in this design.
Note that `CommunityConfig` sets `extra="forbid"`, so a top-level `runtime:` key is rejected until the
model gains the field, and the cross-field validator must live on `CommunityConfig`, not on
`ExtensionsConfig`, because the latter cannot see a top-level sibling.

## Community adoption

| Community | Engine in the browser | Data path | Status |
|---|---|---|---|
| NEMAR | eegprep once its extras split ships; MNE until then | Zarr recipes from the MCP, HTTPS reads | first adopter |

The NEMAR recipe carries two `how_to` snippets and only the TypeScript one is marked browser-safe: its
`python_zarr` snippet uses anonymous S3 through boto3, and `boto3`, `botocore`, `aiobotocore` and `s3fs` are
all absent from the Pyodide distribution. A model that copies it will fail. The reader must consume
`data_base` and `array_path` over HTTPS, and the community prompt must tell the model to ignore
`how_to.python_zarr` in the browser lane. `aiohttp` and `fsspec` are present, so the HTTPS path is viable.
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

## What this assumes about OSA today

Added after review. Each item below is a prerequisite that this design silently assumed and that does not
hold against the deployed code. They are Phase 0: none of Phase 1 works end to end until they are settled.

1. **The turn is hard-aborted at 120 seconds, in two independent places.** The proxy worker passes
   `AbortSignal.timeout(120000)` to the backend fetch whose body is the SSE stream, and the widget sets its
   own `AbortSignal.timeout(120000)` on the chat POST. `AbortSignal.timeout` counts from construction, so
   this is a wall-clock ceiling on the WHOLE turn, not an idle timeout. A parked stream has to fit the first
   model response, the Pyodide boot and first-load download, a human reading the code and clicking Run, the
   execution itself, and the resumed model call inside that budget. This design's own `exec_seconds` default
   is 120, which consumes all of it. **This is the finding that decides the transport.** See the open
   question below.
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

Phases 1 to 3 land on an epic branch and merge to `develop` together, per this repo's own epic-branch
workflow, which exists for exactly this shape: Phase 2 depends on Phase 3, and Phase 1 alone ships a tool
surface with no executor. Phases 4 and 5 can be separate pull requests. Phase numbers here are local to this
note and do not correspond to the global phases in `.context/plan.md`.

## Open questions

- **The transport, and it is now the decision this design turns on. UNRESOLVED; do not start Phase 1
  until it is settled.** This note recommended SSE plus a resume endpoint because the change is additive.
  Review found that unbuildable as specified: the 120-second wall-clock abort exists independently in the
  proxy worker and in the widget, and it covers the entire turn, so a stream cannot be parked across a
  human deciding whether to click Run. Three options.
  (a) Keep parked SSE and raise both timeouts, add keepalive comments and an idle-based deadline. Smallest
  conceptual change, but it keeps a connection open across human latency and still needs the checkpointer,
  the rendezvous registry and interrupt semantics.
  (b) Move to one WebSocket per session. Removes the abort problem cleanly, larger client and worker change.
  (c) **Two-run continuation, and no checkpointer at all.** End the run with the assistant message carrying
  the client tool call; the browser executes; a second request continues the graph with the tool message
  appended. This is what CopilotKit and the Vercel AI SDK actually ship for client-executed tools. It
  sidesteps the 120-second ceiling entirely because nothing is parked, and it dissolves the checkpointer,
  the expiry sweep and the anonymous-retention question below, since retention becomes the session TTL that
  already exists. It costs idempotency work instead: validate pending `tool_call_id`s against the session's
  last assistant message, accept each once, and synthesize abandoned tool results if the next user message
  arrives with a call still pending, because providers reject an assistant message whose tool calls have no
  results. Measured caveats that favor it: on the installed LangGraph, a resumed node re-runs from its start
  so any pre-interrupt side effect runs twice, and every interrupt raised in one `ToolNode` task shares an
  `Interrupt.id`, so parallel client tool calls cannot be disambiguated.
- Cooperative cancellation without `SharedArrayBuffer`; the reboot fallback is acceptable if the cache makes it cheap.
- Parallel tool calls. The router returns one destination for a whole assistant message, so a batch mixing a
  server tool and a client tool cannot be split today. Constrain the model to one client-tool call per turn,
  or route with `Send`.
- Whether the provider layer accepts image content blocks on a TOOL message. This decides whether plots reach
  the model at all, and it is stated as settled earlier in this note without being verified.
- JupyterLite storage bridge versus a lighter notebook UI of our own. Note that sharing a live JupyterLite
  kernel with the chat worker is not an open tradeoff; it is unsupported, and belongs under non-goals.
  Pyodide's `mountOPFS` is also not in a released version yet, so Phase 4 should plan on IndexedDB plus an
  import step; the stable `mountNativeFS` is the File System Access API and is Chromium-only.
- Where community lockfiles live: in this repo next to the config, or in a community-owned repo referenced by URL.
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
- LangGraph: `interrupt`, `Command(resume=...)`, checkpointers.
- nemarOrg/nemar-cli ADR 0049 (compute in the browser, only HPC submission gated) and issue #1065 (the MCP server whose recipes this runtime consumes).
- sccn/eegprep: the extras split that makes eegprep installable under Pyodide.
