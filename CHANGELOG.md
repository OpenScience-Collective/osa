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

## [0.8.14] - 2026-09-24

### Added

- **The notebook opens as a tab of the widget's panel** (issue #470, #471, #474):
  a capsule community's notebook icon opens a Notebook tab next to the chat, framing the notebook site,
  where it used to open the site in a new browser tab.
  The frame is made the first time the tab opens and kept while the reader is on chat, so its Python keeps running;
  a new dataset on screen replaces it.
  The notebook site now allows being framed by the platform's widget hosts and each notebook community's `cors_origins`,
  and runs a starter's cells tagged `osa-autorun` when it opens.
  See `docs/adr/0012-the-notebook-as-a-widget-tab.md` and "Launcher" in `docs/community-widget.md`.
- **An opt-in dark appearance** (issue #469): `widget.color_scheme: auto` follows the reader's device,
  including a switch while the page is open; `light`, the default, is unchanged. NEMAR turns it on.
- **The widget's pop-out carries the notebook** (issue #470):
  a capsule community's pop-out window has the panel's Chat and Notebook tabs, as a strip under its header,
  opens on the tab the reader was on, and makes a notebook frame of its own, a fresh notebook session.
  The pop-out button now shows on the notebook tab too.
  A bubble community's pop-out is unchanged.
  See "The pop-out window" in `docs/community-widget.md`.
- **NEMAR's power spectrum and event-related potential (ERP) image, with ERP CORE as the worked example**:
  NEMAR's prompt gains a "Spectra, events and ERP images" section, which teaches Welch's method, a windowed-sinc low-pass and epoching in numpy (the chat runtime has no SciPy),
  and finding a dataset's conditions from `nemar_get_events`' `columns_summary` before asking for only those rows with `where` and `columns`.
  Its dataset-page questions now offer the power spectrum and an ERP image first.
  The notebook starter ends with a power spectrum of the first two minutes of any recording, with SciPy, and links to the fuller worked examples in NEMAR's documentation.
- **The notebook check reads the notebook, not only the page**: `notebook/e2e-check.js` takes its read line, its figures and any error from the notebook model,
  since JupyterLab renders only the cells in view, and it names the error a cell raised.
- **Config values per deployment** (issue #480):
  `extensions.mcp_servers[].url`, `runtime.python.fetch_allow` and `runtime.python.prelude`
  each take one value or a `{production, develop}` map, the shape `notebook.zarr_base` already has.
  The backend resolves a map for the deployment it is:
  `OSA_DEPLOYMENT` when set, otherwise `develop` for the container mounted at `/osa-dev`,
  so the public config response still carries one `fetch_allow` list and one prelude.
  NEMAR's develop chat, which test.nemar.org embeds, now reads `mcp-test.nemar.org` and `zarr-test.nemar.org`,
  where staging's datasets are; it used to read production's hosts, which have none of them.
  Every launch script in `deploy/` names the deployment, production included.
  See `docs/adr/0013-the-chat-follows-its-deployment.md`.
- **Questions about the dataset on screen** (issue #477):
  `widget.dataset_suggested_questions` holds question templates with `{dataset_id}`, `{subject}` and `{task}` blanks,
  and `needs_zarr` on the ones that run code against a recording.
  On a page that names a dataset with `setDataset`, which now also takes `subject` and `task` labels,
  the opening screen shows up to three that the page's facts fill, in place of the general list;
  mid-conversation, a dataset the conversation has not been on yet gets a compact row of two.
  A community that sets none is unchanged.
  NEMAR is the first, and its example dataset is now nm000132 (ERP CORE).
  See "Questions about the dataset on screen" in `docs/community-widget.md`.
- **A three-icon capsule launcher** (issue #436): `launcher: capsule` replaces the
  single chat bubble with a vertical stack of three circular icons -- chat (unchanged),
  notebook, and a high-performance computing (HPC) placeholder -- that expand upward
  above the chat button once it opens; the chat button itself never moves, and a
  community that never sets `launcher` (the `bubble` default) renders exactly as it did
  before this feature existed. A new
  `OSAChatWidget.setDataset({ id, zarr })` call (or `setDataset(null)`) tells the widget
  which dataset, if any, is on screen, driving the notebook icon through four states
  (no dataset, Zarr unknown, no Zarr copy, active); the active state opens the panel's
  Notebook tab, which frames `${notebookUrl}open.html?community=...&dataset=...`, where
  `notebookUrl` is set via `setConfig` (default `https://notebook.osc.earth/osa/`). `launcher_label`
  replaces the hardcoded collapsed-launcher tooltip with a short community-chosen label,
  keeping the greeting and suggested questions behind the click. NEMAR is the first
  community on the capsule, with the label "Explore NEMAR". See the
  "Launcher" section of `docs/community-widget.md`.
- **Three more widget colors, and NEMAR's home page teal in the widget.**
  `theme_text_color`, `accent_color` and `user_bubble_text_color` join `theme_color` and
  `user_bubble_color`: a community can now separate the color painted as a surface (the
  header, the launcher, Run, Send) from the text drawn on it, and from that same color
  used as a FOREGROUND on the widget's white panel (links, borders, focus rings,
  `accent-color`), each optional and defaulting to today's behavior when unset. NEMAR's
  widget now matches nemar.org's home page search button exactly: `theme_color` and
  `user_bubble_color` are the button's own `#5bbad5` with the button's own dark text
  (`#04121f`) rather than the platform's white, and `accent_color` keeps the earlier
  darkened teal (`#257a92`) as the foreground color on the white panel. The logo's brain
  and electrodes move from the old dark-header white-and-gold to nemar.org's own
  light-header treatment (navy and a deeper gold).
- **`preload_on: first_message`**, a third value alongside `first_run` and `widget_open`:
  boots the browser Python runtime as soon as the reader sends their first message,
  rather than waiting for a Run gate, so the download overlaps the model's own first turn
  instead of following it. Unlike `widget_open`, a reader who only opens the chat and
  never sends anything is never charged for the download. NEMAR now uses this value.
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
- **A hosted JupyterLite notebook site**, at `notebook.osc.earth/osa` (issue #453, ADR 0011,
  amending ADR 0010's deferral). A community adds a `notebook:` block to its
  `config.yaml` (a starter `.ipynb` and a dataset-id pattern); the widget's Notebook tab
  frames `notebook.osc.earth/osa/open.html?community=<id>&dataset=<id>`, which drops that community's starter, with the dataset id filled in, into
  JupyterLite's own browser storage and redirects into it. Pyodide 0.29.5 loads from
  jsDelivr rather than being self-hosted (66 MB versus 530 MB for the same build);
  community wheels are bundled through one lock merged across every notebook-enabled
  community, following the same "an overlay may only add, never replace" rule the
  chat-widget runtime already enforces. NEMAR ships the first starter
  (`src/assistants/nemar/notebook/starter.ipynb`). `docs/community-notebook.md` is the
  adopter guide; `scripts/build_notebook_site.py` and
  `.github/workflows/deploy-notebook.yml` build and deploy it. The widget's own
  IndexedDB workspace does not hand off to the notebook yet (ADR 0010's fourth
  prerequisite; still open).

### Changed

- The widget's own bytes, and so its integrity hash, changed: a page that pins the widget
  by Subresource Integrity (SRI) must re-pin to this release.

### Fixed

- **The widget draws in its community's look from the first frame** (issue #475).
  It drew the built-in blue bubble and changed to the community's look when the config arrived, about 450 ms later, on every load.
  It now remembers the last config's `widget` block in the page's `localStorage` and applies it before drawing; the fresh config still wins.
- **The widget keeps its own field colors on a dark page** (issue #469), for every community:
  a host page declaring `color-scheme: dark` turned the chat input dark inside the light panel.
- **The pop-out no longer opens blank on a page without `script-src 'unsafe-inline'`** (issue #470).
  It wrote the widget's source into itself as inline script, which the host page's Content Security Policy, inherited by the pop-out, refused.
  It now loads the widget by its address, with the widget tag's `integrity` and `crossorigin`, so a policy that loads the widget allows its pop-out too,
  and a pop-out whose script the browser refuses says so in its window.
- **NEMAR's ERP images are low-passed.** The prompt described the 30 Hz windowed-sinc filter in prose,
  and a model on staging wrote it as `np.sinc(n)`, a single spike that filters nothing.
  The prompt now carries the filter as code: the cutoff inside `np.sinc`, clamped to a quarter of the sampling rate,
  and the convolution done by FFT, so it takes every channel at once and stays fast at 5000 Hz.
  `frontend/test-data-lane.js` runs that code in Pyodide at 60 to 5000 Hz and checks it keeps 5 Hz, removes the high tone, and equals `np.convolve(..., "same")`.
  The ERP image's time axis is in milliseconds, and the model names a component only when the dataset or its paper says the task evokes it.

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
