# Changelog

All notable changes to OSA are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows the automated scheme described in `CLAUDE.md`: `develop`
carries `.devN` suffixes, `main` carries stable versions, and a release is
cut by merging `develop` into `main`.

Add entries under `[Unreleased]` as part of the PR that makes the change.
When a release PR merges `develop` into `main`, retitle `[Unreleased]` to
the version being released and start a new `[Unreleased]` section above it.

## [Unreleased]

### Added

- **An assistant can run Python in the reader's browser** (epic #429). A community that
  declares `client_tools` and a `runtime` block offers the model `execute_code`: the
  server ends the run with a `tool_request`, the widget runs the code in a sealed Pyodide
  0.29.5 worker behind a per-call permission gate, and `/{community}/chat/resume` carries
  a bounded result back, figures included, into a second run (#430, #431). There is no
  checkpointer; the session store parks one call and closes it out if it goes
  unanswered. Executed code reaches the network only through `fetch_allow`, cannot
  install packages outside `allow_install`, and loads only wheels pinned by sha256 in the
  community's lock overlay. NEMAR is the first community to use it, reading
  `zarr.nemar.org` through a vendored `eegprep-lean` 0.1.0.dev2 that the API serves
  itself (#432). Every other community's behavior is unchanged.
- **A workspace in the browser** (#433). Every run's script, output and figures are kept
  in IndexedDB per community and session, `osa.save_script` and `osa.save_artifact` keep
  something under its own name (only these are reported to the model as artifacts), and
  Settings offers a zip of the whole workspace with a `manifest.json` and a
  `notebook.ipynb` per session. Nothing in the workspace is sent to the server.
- **"Edit and run"** on any recorded run re-runs the reader's edit in the same warm
  runtime, without the permission gate and without anything reaching the server or the
  model (#456). ADR 0010 records why this, and not marimo or a hosted JupyterLite, is the
  notebook surface for now; a hosted JupyterLite is #453.
- `docs/community-browser-runtime.md`, a guide for a community adopting the runtime.

### Changed

- The widget's own bytes, and so its integrity hash, changed: a page that pins the widget
  by Subresource Integrity (SRI) must re-pin to this release.

## [0.8.12] - 2026-09-21

### Added

- A tool result can carry a figure, and the media types that survive the trip are
  declared rather than discovered (#421). `IMAGE_MEDIA_TYPES` sits next to the model
  tables in `src/core/services/anthropic_models.py`, free of third-party imports so
  community config validation reaches it on a command-line-only install, and a test
  compares it against the Anthropic SDK's own declaration so the copy cannot drift
  silently. Nothing in the client stack checked this before: langchain-anthropic copies
  the media type straight into the request, so a producer emitting an unaccepted type
  learned about it as a 400 from the endpoint, after the work that made the picture was
  already done. SVG is the trap worth naming, because `savefig` defaults to PNG but SVG
  is the usual choice for a figure bound for a web page, and it is not accepted.

### Changed

- **The NEMAR assistant no longer tells the model what `search_datasets`' filters are**
  (#425). It said they are *exactly* `query`, `modality`, `task`, `has_hed`, `has_zarr`
  and `limit`, that there is no participant-count filter, and to pass only those six
  names. nemarOrg/nemar-cli 0.10.5 takes that tool to thirty-three declared parameters,
  so all three statements became false, and the third instructed the model away from
  filters that work: a question about channel counts or participant numbers took a worse
  route or was declined, with nothing appearing broken.

  The prompt now points at the tool's own schema, which the server generates from its
  facet definitions, rather than restating a list that goes stale on the next facet
  added. The warning that does not depend on how many filters exist is kept: an argument
  the server does not declare is accepted and silently ignored, so an invented name
  returns unfiltered results that look filtered. Partial towards #370, which asks for a
  whole-config rewrite and raises open questions about how much belongs in the prompt at
  all.

## [0.8.10] - 2026-09-19

### Added

- Migrated OSA's model routing to the Anthropic Claude Platform on AWS,
  retiring the OpenRouter-based route (#395), delivered across four phases:
  - Phase 1: Anthropic LLM service for the Claude Platform on AWS (#365)
  - Phase 2: Anthropic-only key and model policy, community config
    migration (#368)
  - Phase 3: Widget model menu, BYOK key field, deployment configuration
    (#381)
  - Phase 4: Inline citations in answers (#383)
- FAQ generation now runs on the Claude Platform instead of OpenRouter (#387)
- Accept an Anthropic key at every CLI entry point (#385)

### Fixed

- Citation quality, placement, and streaming presentation, including
  sentence-boundary placement and canonical citation content in the widget
  handoff (#388, #399, #400, #401)
- Conceptual code-document searches now require meaningful terms, reserving
  exact identifier matching for function/symbol lookups and reducing
  unrelated citations (#399)
- A community's own Anthropic key is now validated, not just OpenRouter's
  (#391)
- An unusable key header can no longer bypass server auth (#394)
- Release images build without racing the `:latest` tag (#380)
- `develop` syncs to `main` through a pull request instead of a direct push
  (#373)

### Changed

- Model override documentation now describes Claude Platform terms instead
  of OpenRouter's (#392)
