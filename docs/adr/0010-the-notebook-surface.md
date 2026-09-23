# 0010. The notebook surface

Date: 2026-09-23

## Status

Accepted

## Context

Issue #423 (child of phase issue #433, epic #429) records that marimo was evaluated as the browser-execution epic's notebook surface on 2026-09-16,
in the same advisory review that produced the two-run transport decision (`.context/browser-execution-tool-design.md`),
and rejected for four integration reasons, none of them speed:

1. No `postMessage` application programming interface (API) for a widget to hand a notebook its files (marimo issue #8139, open at the time).
2. marimo's own storage, backed by IndexedDB File System (IDBFS), not storage a widget can write into.
3. Content delivery network (CDN)-only Pyodide: marimo.app loaded WebAssembly (WASM) Pyodide 0.27.7 during the check, not OSA's pinned 0.29.5.
4. A one-definition-per-variable reactive model, so a plain script the assistant wrote needs a conversion pass before it is a marimo notebook.

That review also adopted JupyterLite behind a small custom drive, to be built third (after an editable re-run panel in the chat),
citing measured costs of a 69 MB static site, about 4 MB compressed for the shell, a second Pyodide instance, and 15 to 17 seconds to first output.
Nobody measured marimo's time to first output,
and neither surface was opened in a browser on our own machines during that review.
The verdict was never written down;
#423 exists to settle it with data and record it, one way or the other.

Phase 4a's workspace exports, per session: `scripts/run-NNN.py` (plain scripts, one per run, the assistant's or the reader's own re-run),
`results/run-NNN/` (each run's output and figures), `artifacts/`, `manifest.json`,
and `notebook.ipynb` (Jupyter notebook format version 4.5, a markdown cell and a code cell per run, with outputs).
The widget is embedded on third-party pages (for example nemar.org);
any notebook surface opens in a new browser tab, on its own origin, as a second Pyodide instance.
Sharing a live kernel with the chat worker is unsupported and out of scope
(stated in #433 and already recorded as a non-goal in `.context/browser-execution-tool-design.md`, "Persistence and the notebook surface").

This ADR's own measurements, dated 2026-09-23, are in the companion note
[`.context/notebook-surface-measurements.md`](../../.context/notebook-surface-measurements.md):
method, full cold and warm results (time, bytes, request and failed-request counts), reproduction commands, the exact code run,
a re-check of all four original integration gaps plus one found only by running the real workload, and what opening a workspace file takes in each surface.
This document states the decision the measurements settled; it does not repeat them.

## Decision

**Skip marimo as the notebook surface.** The 2026-09-16 verdict is reaffirmed, for a now-five-reason, still entirely integration-based case.
The original four gaps are all still present (re-checked directly, not only re-read):
no `postMessage`/embedding API (still open, a maintainer commented twice the day it was filed but nothing shipped since);
its own storage (unchanged);
CDN-only Pyodide, now Pyodide 314.0.0, a full version generation further from 0.29.5 than the 0.27.7 measured in 2026-09-16,
and a documented self-host flag that does not exist in the shipped release;
the one-definition model, whose conversion pass is real but needs an extra dependency and an offline step.
A fifth gap was found only by running the actual NEMAR read against the live archive, not by reasoning about it:
**marimo's own WASM concurrency sandbox refuses the `multiprocessing.Lock` the `numcodecs` package's blosc codec needs to decompress real Zarr chunks,
so marimo cannot complete that read at all, in any configuration tried.**
The speed question #423 asked about is settled and explicitly does not favor marimo:
under a CDN-matched control, marimo and JupyterLite differ by well under a second for a trivial cell,
and the only workload that actually matters, the real NEMAR read, marimo cannot run to completion regardless of speed.

**Adopt JupyterLite, pinned to Pyodide 0.29.5, as the eventual notebook surface, behind a small custom drive.**
Pinning to exactly 0.29.5 is confirmed possible by building it (unlike marimo, where it is not possible at all today),
and the real NEMAR read completes end to end under that pin.

**User decision, 2026-09-23: this release's phase 4c is the editable re-run panel in the chat widget.
A hosted JupyterLite notebook surface is deferred to a follow-up epic phase, not built in this release.**
That follow-up (#453) has four prerequisites this ADR's measurements surfaced, none of them started:

1. A pruned, pinned JupyterLite build (`jupyter lite build --pyodide=<0.29.5 tarball> --no-unused-shared-packages`);
   size not yet measured (the unpruned pin is 529 to 531 MB, an unrealistic deploy size).
2. A hosting origin for the built site.
3. That origin must be one `zarr.nemar.org` answers.
   Its Cross-Origin Resource Sharing (CORS) allowlist already admits `nemar.org` and its subdomains,
   `osc.earth` and every `*.osc.earth` subdomain, and loopback (confirmed live on 2026-09-23; see the measurements note).
   A site hosted under `osc.earth` needs no change there; any other origin needs one in `nemarOrg/nemar-cli`.
4. A JupyterLite extension or content drive that receives the widget's exported workspace files
   (a build-time import was confirmed working for this ADR's measurements;
   nothing live or cross-origin exists yet).

| Surface | Pyodide loaded from | Trivial cell | Real NEMAR read | Static site |
|---|---|---|---|---|
| marimo 0.24.2 | 314.0.0, CDN only, not pinnable | about 3 to 4 s | fails (WASM concurrency sandbox) | 27 MB |
| JupyterLite, default | 314.0.0, CDN | about 3 s | not tested at this pin | about 64 MB |
| JupyterLite, pinned | 0.29.5, self-hosted | about 2 to 3 s | about 3 to 5 s | 529 to 531 MB, unpruned |

## Consequences

- The five-reason case against marimo is written down, sourced, and dated,
  so it no longer depends on a chat transcript no one else can read (the problem #423 was filed to fix).
- A future re-check of marimo does not need to re-litigate speed;
  it needs to re-check the WASM concurrency gap (has marimo's sandbox grown an allowance for a codec-internal lock?)
  and the Pyodide-pin gap (has a self-host flag shipped in a release?),
  both cheap, dated, falsifiable checks against a specific future release rather than a redo of this whole ADR.
- JupyterLite is confirmed workable end to end against the real, live NEMAR read at the exact package set OSA's own runtime uses,
  including a wheel-and-package-list problem the measurements note records in full, so whoever builds the follow-up does not have to rediscover it.
- **Nothing in this release ships a notebook surface.** The chat widget's editable re-run panel is this release's phase 4c,
  and covers "keep tinkering" on its own, at a fraction of a notebook surface's integration cost, independent of this ADR's verdict.
- The four prerequisites above are new, concrete scope for the JupyterLite follow-up, not implied work;
  in particular, prerequisite 3 is a cross-repository dependency (`nemarOrg/nemar-cli`) that has to land before the follow-up can reach the live archive
  from a hosted origin, and should be sequenced accordingly.
- **Not measured, and why**, kept here because it bears on the follow-up's scope, detailed in the measurements note:
  a pruned, pinned JupyterLite build's size; marimo's undocumented-in-practice self-host flag's actual cost;
  OSA's own wheel route's live CORS headers under this epic's code (production has not deployed phases 1 to 3 yet).

## References

- #423, #433, epic #429.
- `.context/notebook-surface-measurements.md` (this ADR's measurements, method, and sources).
- `.context/browser-execution-tool-design.md`, "Persistence and the notebook surface" and its open questions (updated alongside this ADR).
