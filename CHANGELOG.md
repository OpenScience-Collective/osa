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

- **GPT-6 Luna, Qwen3 Next 80B A3B and gpt-oss-120b** (issue #523):
  three non-Anthropic models served from Amazon Bedrock, each priced at or below Claude Haiku 4.5
  ($0.11 / $0.55, $0.14 / $1.20 and $0.15 / $0.60 per 1M input / output tokens; Haiku is $1 / $5).
  They appear in the widget's model menu and can be a community's `default_model`.
  GPT-6 Luna runs at high reasoning effort.
  A deployment turns them on with `AWS_BEARER_TOKEN_BEDROCK`; without it they are not listed and a request naming one gets a 400.
  Requests run in Ohio (`us-east-2`, `BEDROCK_REGION`), except Qwen3 Next, whose Ohio endpoint does not answer, so it runs in N. Virginia.
  A caller's own Anthropic key cannot select them, because the platform pays for Bedrock; an OpenRouter key runs the same model there.
  When a community's `default_model` is one of them and a request cannot have it (the caller's own key with no model named, or no Bedrock key on the deployment), the request runs the deployment's Claude default and an error naming the community is logged; `osa validate` warns about such a default.
  The widget shows them as unavailable next to an Anthropic key.
  FAQ generation stays on Claude.
- **Numbered citations for the Bedrock models**: they reject Anthropic's `search_result` blocks, so retrieved sources are tagged `[src:N]` and the model writes the tag after each claim it draws from one.
  The tags come back as the same `citation` events and `[n]` markers the Anthropic path produces.
  A tag that names no source is dropped, and the quoted passage is the closest one in the source, not a span the provider vouches for.
  Models follow the convention less reliably than Claude's native citations.
  Retrieved text cannot write a tag: `[src:N]` in a document is shown to the model as `(src:N)`, so a forum post cannot pass itself off as another source.
- **Numbered citations on the OpenRouter path** (issue #526): a request funded by an OpenRouter key now gets the same numbered `[n]` markers and source list as Claude, through the same tagged-source layer, instead of the prompt-only "end the sentence with a markdown link" rule.
  It applies to every OpenRouter model, Claude slugs included.
  Cache reads and writes and reasoning tokens in OpenRouter's usage are now recorded, so cached requests are priced at the cache rate; the token counts are the provider's own, where LiteLLM alone reported a local estimate for the usage chunk OpenRouter documents.
  Only Anthropic's models are sent cache markers.
  Not yet verified against the live service (no OpenRouter key on the development machine); `tests/test_integration/test_openrouter_citations.py` is written for it and should be run before relying on the cost figures.
- **Prompt caching for GPT-6 Luna** needs no request changes (the service caches a repeated prompt prefix on its own); its cache reads and writes are recorded and priced like Claude's.
  gpt-oss-120b and Qwen3 Next do no caching.
- **`model_instructions`** in a community's `config.yaml`: extra system-prompt text for particular models, added after the platform's own note for that model.
  GPT-6 Luna and gpt-oss-120b get a note that stops them searching in a loop.
- **`reasoning_effort`, one key in a community's `config.yaml` that sets how hard every model reasons, on every provider** (issue #545, ADR 0015):
  `none`, `low`, `medium`, `high`, `xhigh` or `max`, turned into each platform's own request field: the nested `reasoning.effort` for GPT-6 Luna and the flat `reasoning_effort` for gpt-oss-120b on Amazon Bedrock (Qwen3 Next has no control and is sent nothing), `output_config.effort` beside adaptive thinking for Claude Sonnet 5.5 on the Claude Platform, and the unified `reasoning.effort` body field on OpenRouter.
  Each model keeps its own predetermined levels, and a level it does not accept is clamped, never sent: gpt-oss-120b runs low to high, and Claude Sonnet is never run above high on any platform, whatever a community asks.
  Claude Haiku 4.5 has no effort field, so its level is a thinking budget (see the change below); Qwen3 Next ignores the key.
  Unset, every model with levels runs at high on every provider.
  On the Claude Platform, Sonnet's `none` is no up-front thinking at effort low.
  A level the community's own `default_model` cannot honor is a warning when the config loads.
  NWB and NEMAR set `high`.
  Through the real NWB graph, Luna's median time to first text was 2.5 s at none, 5.2 s at medium and high, 10.6 s at xhigh and 48 s at max, and at xhigh and max it skipped the documentation search in the median of three runs on one question, so the answer had no citations.
  Bedrock's request fields were measured against the live service; the Anthropic and OpenRouter shapes are tested against the requests the clients build, not yet against those services (no keys on the development machine).

### Changed

- **Every model runs at high reasoning effort by default, Claude Haiku included** (issue #548): a community that sets no `reasoning_effort` gets `high` on every provider for Claude Sonnet 5.5, Claude Haiku 4.5, GPT-6 Luna and gpt-oss-120b (Qwen3 Next has no control).
  Claude Haiku has no effort field, so its level is a thinking budget: low 1024, medium 2048, high 4096 tokens, none no thinking, and its default goes from 2048 to 4096 tokens, so it can think up to twice as long before answering (more output tokens billed and a longer wait before the first word).
  A community that wants the old behavior sets `reasoning_effort: medium`.
  A budget that would not fit under the request's `max_tokens` is lowered so it does instead of refusing the request (a `max_tokens` of 1024 or less is still refused).
  On a caller's own OpenRouter key Haiku now reasons too, with the same budget sent as `reasoning.max_tokens` (and no temperature while it thinks); OpenRouter would otherwise turn an effort into a share of an unset `max_tokens`.
  Claude Sonnet is now sent `high` explicitly, which is the Claude Platform's own default.
  `ANTHROPIC_THINKING_BUDGET_TOKENS` is removed and the level sets the budget now; a server that still exports it logs a warning at startup naming `reasoning_effort` as the replacement.
- **A caller's own OpenRouter key now runs GPT-6 Luna and gpt-oss-120b at high reasoning effort** (issue #545): OpenRouter's own default for both is medium, and OSA sends its default (high) on every provider so a model behaves the same whichever key paid for it.
  More reasoning means a longer wait before the first word; a community can set `reasoning_effort` to change it.
- **GPT-6 Luna runs at high reasoning effort, not maximum** (issue #543): at maximum, a tool-using turn took 15 to 50 seconds before its first word, all of it the model's own silent reasoning.
  On real community questions at high, NWB answered in 7 to 9 seconds against about 27 at maximum, with one or two searches, cited sources and no tag leaks.
  The accepted levels, measured on Bedrock, are none, low, medium, high, xhigh and max (`minimal` is rejected).
- **The widget spreads a streamed reply's burst over half a second, at most** (issues #531 and #538): text a reply has delivered is drawn as soon as it arrives when nothing is pending (a first line or so at once, the rest within a tick), and every character is drawn no more than about half a second after it arrived, instead of the moment each chunk arrives.
  GPT-6 Luna reasons silently for most of a turn and then emits its answer in about a second, so it used to appear all at once.
  Each chunk carries its own half-second deadline, so a burst faster than a line per tick is spread over at most half a second, including one that lands late in an earlier burst's reveal, and a reply that finishes waits for the reveal for at most 0.7 seconds before the canonical text replaces it.
  A model that streams slower than that is drawn as it arrives, at most one 80 millisecond tick late.
  The wait before the first word is the model's own: OSA's model layer adds nothing over raw Bedrock (measured in issue #538), and lowering Luna's reasoning effort is what shortens it.
  The redraw rate is about 12 per second at most.
  A fenced code block is not typed out: it is shown whole once the reveal reaches it, and code still arriving inside a block is shown as it arrives.
  A reveal never ends inside a `[n]` citation marker, a source is listed only once its marker has been shown, and a reader who is typing in a message, or has scrolled up, is not interrupted by the redraws.
  A reader who asked their system for reduced motion gets every chunk on arrival, gathered into one redraw per tick; a hidden tab or a page being left shows and saves the rest of the reply at once, and a page that is already hidden is not paced at all.
- **The widget says what a pending reply is doing** (issue #538), where it used to say only "Thinking..." or the community's title.
  The label names the tool from its name alone, never its arguments ("Searching datasets...", "Looking up documentation...", "Listing recordings..."), from the moment the model starts writing the call.
  A call to run code reads "Writing code..." while it is written and "Running code..." once it runs; a tool is called code only if its name says so (code, python or script), so one that runs a query reads "Working...".
  Once a batch of tools has answered, or a browser run has finished, it reads "Analyzing results..." until the model's next text; "Thinking..." stays for a wait with no tool in it.
  A tool's label is written once: parallel calls announced as the model writes them are not relabeled one by one as they start.
  A server without the new `tool_call` event still sends `tool_start` and `tool_end`, and the widget labels from those: a tool is named once it starts rather than while it is written, and "Analyzing results..." follows its end.
  After 5 seconds of waiting, the label is followed by how long the reader has waited ("Searching datasets... 12 s"): the time since the message was sent, since a browser run's result went back, or since the reply's last visible text.
  A new label, whitespace, or a slow response does not start the time again.
  It is read from a monotonic clock (`performance.now`, else `Date.now`) and updated once a second without redrawing the conversation.
  Before any reply text is on screen the status is the loading bubble's label; once the reply has visible text, a later activity is a status line under that text in the same message.
  A status never adds a message of its own and never stands under an empty one: a pending reply is drawn only once it has visible text or a record of code it ran.
  Text that is only whitespace (a reasoning model's `"\n\n"` before a tool call) is not text: the loading bubble and any status stay, and a reply that ends as only whitespace is dropped, as an empty one is.
  A screen reader hears each new label once, from one polite status region made with the widget and kept outside the conversation, so redraws do not repeat it; the visible label is hidden from it, the title placeholder and the seconds are never announced, and the region is emptied when the status ends.
  None of it is saved with the conversation.
- **A new `tool_call` SSE event** on `/chat`, `/chat/resume` and `/ask` (issue #538): `{"event": "tool_call", "name": "..."}`, sent once per call when the model starts writing it, which for a long code call is many seconds before `tool_start` (or `tool_request`, for a browser call).
  It is read from the model's streamed tool-call chunks in the shapes the Anthropic, Bedrock and OpenAI-style adapters yield, and is tested against Claude's own streamed events.
  It carries the tool's name only; no existing event, answer, citation or token count changes, and a widget that does not know it only logs a console warning.
- **The OpenRouter chat model is a `ChatLiteLLM` subclass, not a wrapper around one** (`src/core/services/litellm_chat.py`, replacing `CachingLLMWrapper`): `bind_tools` and streaming are native, and tool results and earlier assistant turns are sent as their text instead of a stringified block list.
- **Claude Sonnet 5.5 replaces Sonnet 5** (issue #522):
  the offered Sonnet is now `claude-sonnet-5-5`, at the same price ($2 / $10 per 1M tokens).
  `claude-sonnet-5` and the older Sonnet ids still resolve, to the new model, so saved widget settings and community configs keep working.
  Sonnet 5.5 rejects `thinking: {"type": "disabled"}`, so requests that turn thinking off (FAQ generation) send `{"type": "between_tools"}` instead.
  A widget setting that still names `claude-sonnet-5` is moved to the new model instead of showing as a custom one.
- **The widget's Settings dialog asks for the model first** (issue #522): the API key and the model name appear only when Custom is chosen, since they exist for a model the community does not offer, paid for by the reader's own Anthropic or OpenRouter key.
  A key that is already saved stays in view so it can be removed, and a custom model can no longer be saved without a key.
  Figures now count as about 4,800 tokens each when the conversation is measured (they were 1,600), which is what Sonnet bills for a full-size image, so long figure-heavy chats are trimmed sooner.
- **NEMAR answers with Sonnet by default** (issue #522): `default_model` is now `claude-sonnet-5-5`.
  Each request costs about twice what Haiku 4.5 does per input token.
- **NEMAR answers with GPT-6 Luna by default** (issue #530): `default_model` is now `openai.gpt-6-luna`, as NWB's is, so it costs less than Haiku 4.5 per token instead of about twice as much.
  A caller with their own Anthropic key who names no model (the CLI), and every request on a deployment with no Bedrock key, run Claude Haiku 4.5, not Sonnet.
  Luna cannot take image blocks, so the model no longer sees `nemar_render_overview` images or browser-run figures; it says so, and the reader still sees the figure.

### Fixed

- **The widget's Settings dialog refused an OpenRouter slug with a variant suffix, such as `openai/gpt-oss-120b:nitro`** (issue #552).
  Its model-name check had no room for `:variant`, while the server's own check for a community's ids has, so a caller with their own OpenRouter key could not save `:nitro`, `:floor` or `:free` slugs, and a saved one was dropped on load.
  The widget now applies the server's pattern and its 100-character limit, and both are held to one shared list of valid and invalid ids.
  A routing variant (`:nitro`, `:floor`, `:exacto`, which OpenRouter accepts on any model and which only change which providers serve the request) is looked through when a slug is mapped to an offered model, so `openai/gpt-oss-120b:nitro` runs at the same reasoning level as `openai/gpt-oss-120b` instead of at OpenRouter's own default; `:free` and the other catalog variants are models of their own and are left alone.
- **A warning for every streamed tool-call chunk filled the production log** (issue #540).
  Anthropic streams a tool call's arguments as `input_json_delta` blocks, and the content classifier only knew `tool_use`, so each chunk of each tool call logged "unrecognized content block type".
  Production had 26,389 of them in four days.
  `input_json_delta` is now a known block that carries no answer text, and a block type that really is unknown still warns once per call.
- **Concurrent OpenRouter requests could go out under each other's API key** (issue #526).
  LiteLLM keeps credentials on a module every request in the process shares, and did not send them with the call, so requests running at the same time under different keys (a caller's own key next to the platform's) used whichever key was written last: 20 of 40 interleaved requests used the other key when measured.
  The key now travels with each call.
  Anyone who ran a public OpenRouter path with more than one key in use at once should treat this as a key-mixing incident for that period.

## [0.8.15] - 2026-09-25

### Added

- **A run's code in place, with Copy and Download** (issue #491):
  each run in the chat now shows its code as a disclosure of its own, `Code · N lines`, under the run's output and figures,
  so it opens without them.
  Its bar copies the exact code (with a fallback that selects it for the reader where the browser will not copy, and never a dialog)
  and downloads it as a `.py` file named for the dataset and the run, such as `nm000132-run-3.py`.
  The permission gate gets the same Copy.
  Only a community whose model runs code sees any of it.
  See "A run in the chat" in `docs/community-widget.md`.
- **A figure's Download** (issue #492): each figure a run shows has a Download button under it,
  which saves the figure's PNG as, for example, `nm000132-run-3-figure-1.png`, named for the dataset, the run and the figure;
  it is keyboard reachable and labeled for screen readers.
- **SciPy in NEMAR's chat runtime** (issue #495): NEMAR preloads SciPy,
  and its prompt uses `scipy.signal.welch` for spectra and `butter` with `sosfiltfilt` for the ERP low-pass,
  in place of the hand-written Welch's method and windowed-sinc filter models got wrong,
  which also retires the note on keeping that filter's slice from the ERP entry under Fixed.
  `welch` makes a view of every window at once, which 32-bit WebAssembly refuses at 2 GiB,
  so the prompt calls it one channel at a time on at most `2**28 // nperseg` samples;
  `execute_code`'s description, which the model reads on every call, now names SciPy and that rule.
  The first load is about 35.4 MB over the network, from 19.1 MB.
  The notebook starter's setup cell installs SciPy too.
- **`runtime.python.import_before_seal`**: modules the browser runtime imports after its installs and before its namespace seal,
  for a package that imports a sealed module as it loads.
  SciPy imports `ctypes`, which the seal refuses, so under the seal it did not import at all;
  NEMAR imports `scipy`, `scipy.stats` and `scipy.io` first, about 1.2 seconds of its boot.
  Empty by default, so every other community boots as before.
  The seal still refuses `import ctypes` to executed code, and SciPy's own modules keep the reference they imported,
  which is consistent with the seal's purpose; see "Importing before the seal" in `docs/community-browser-runtime.md`.

### Changed

- **A larger capsule launcher at rest** (issue #490):
  with the panel closed, a capsule community's chat circle is drawn 25% larger, 58px instead of 46px, so it is easier to see,
  and settles to 46px as the panel opens, where the open layout is exactly as before;
  with reduced motion it changes size at once.
  A bubble community is unchanged.
  See "Launcher" in `docs/community-widget.md`.

### Fixed

- **NEMAR's notebook reads data in Safari and Firefox** (issue #496).
  eegprep-lean's browser transport sent `User-Agent`, which Safari and Firefox send and Chrome drops,
  so every read in those browsers became a CORS preflight that `zarr.nemar.org` refuses.
  eegprep-lean 0.1.0.dev3 (sccn/eegprep#420, #423) sends only `Range`; the wheel is re-vendored at `50c50879`, which the chat runtime and the notebook site both serve.
- **A failed request no longer hangs Python in Safari** (issue #496):
  Safari's fetch rejects with a TypeError that has no `stack`,
  which Pyodide 0.29.5 does not take for an error,
  so the `await` on it never returned.
  In the notebook the cell stayed running and the kernel busy for good;
  in the chat the run was stopped at its 120-second deadline and the runtime's state was lost.
  Both runtimes now raise instead, as they already did in Chrome and Firefox:
  the chat's runtime installs a rejection guard before its seal,
  and the notebook site's bridge sends the same guard to each new kernel.
  The notebook's own reads still fail in Safari and Firefox until eegprep-lean stops sending a `User-Agent` header,
  which makes each read a CORS preflight whose answer from zarr.nemar.org does not allow that header;
  they now fail at once, naming the URL.
- **An OSA address without its trailing slash works** (issue #500). `widget.osc.earth/osa` and `develop-widget.osc.earth/osa` answered 522,
  because the Worker's `/osa/*` routes do not match the bare path; each host now also routes `/osa*`, and the Worker answers `/osa` with a 308 to `/osa/`, keeping the query string, and a path outside the mount such as `/osafoo` with a 404.
  `deploy/apache-api.osc.earth.conf` redirects `api.osc.earth/osa` and `/osa-dev` the same way; the server's copy is applied by hand.
- **NEMAR's ERP images use the events they name.** On nemar.org a model asked for every stimulus of ERP CORE's N170 recording in one call,
  sorted the rows into faces and scrambled faces while copying them into code, and averaged 282 "faces" of a recording that has 80.
  The prompt now asks for one condition per `nemar_get_events` call,
  and has the code assert each list's length against that call's `total_count`.
- **NEMAR's spectra and ERPs leave out every EOG channel.** The prompt names ERP CORE's labels (`HEOG_left`, `HEOG_right`, `VEOG_lower`)
  and says to match `EOG`, `ECG`, `EKG` and `EMG` anywhere in a label, since exact names missed all three.

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
