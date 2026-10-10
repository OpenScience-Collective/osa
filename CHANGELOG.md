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

- **A failure names the model to try, and the widget offers it** (issue #593, from #514): where a failed request said "Please choose another model.", it now says "Try Claude Haiku 5.5, or choose another model." (Claude Sonnet 5.5 when Haiku is the model that failed; it never names the model that failed).
  The `error` event carries the same model as `suggested_model`, an object with its `id` and `label`, only when the message names one: not for a rate-limited key of the caller's own, a refused request or key, or a failure that is not a model's.
  The widget shows a button under the message, "Try Claude Haiku 5.5", for a request that showed no reply.
  It sends what is in the box (the question, put back there, which the reader may have narrowed) again on that model once, using the server's model when the widget can send it and its own pick otherwise, and leaves the saved model setting alone; the model also runs the later runs of that reply.
  A stream the widget gave up on after it had begun and gone quiet gets the same button, with the model picked from the community's offered models; a request that timed out before the server answered gets none.
- **A run that used all its steps says so** (issue #593): a model that keeps calling tools until langgraph's step limit (GPT-OSS did on a HED annotation question) was reported with the generic unrecognized-error text (on `/chat`, "An error occurred while processing your request.").
  It now says "The current model (X) used all its steps without finishing. Try Claude Haiku 5.5, or ask for a smaller part of the task." and carries `suggested_model`.
  The log line is a WARNING with no traceback, since it is the model's behavior and not an outage, and `/ask` and `/chat` with `stream: false` answer the same text in the HTTP 500's `detail`, at WARNING too, where they paged the operator with a traceback and sent the caller to support.

### Changed

- **Models are named by class, and Claude Haiku 5.5 replaces Claude Haiku 4.5** (ADR 0016): `haiku`, `sonnet`, `opus` and `fable` for Anthropic and `luna`, `terra`, `sol` and `astra` for OpenAI are accepted wherever a model is named (a community's `default_model`, an agent's `model`, a request, the CLI, `DEFAULT_MODEL`), and `MODEL_CLASSES` in `src/core/services/anthropic_models.py` says which model each is today.
  Moving a class to a new generation is an edit there, the id it replaces in `PREVIOUS_GENERATIONS`, and the new model's own price and thinking facts.
  The communities now name `haiku`, `sonnet` and `luna`; the config endpoint reports the id they resolve to.
  A saved widget setting, a `config.yaml` or a `DEFAULT_MODEL` that names Claude Haiku 4.5 runs Claude Haiku 5.5.
  Haiku 5.5 thinks adaptively at an effort level (`high` unless a community sets another; `none` sends effort `low` and `thinking: {"type": "disabled"}`), where 4.5 thought with a token budget, so `THINKING_BUDGET_TOKENS` is gone.
  It rejects `temperature`, so no offered Claude model takes one: EEGLAB's FAQ agents lose the 0.0 and 0.1 they set, and their `temperature` lines are removed.
  It costs $0.10 / $0.50 per million tokens up to a 100,000-token prompt and $0.50 / $2.50 above (4.5 cost $1 / $5), and counts about 30% more tokens for the same text.
  The higher rate is judged on the largest single model call of a reply, not on the reply's total: a tool loop sends the conversation again with each call, so its calls add up past 100,000 tokens while no prompt is near that line, and only a call whose own prompt is over it puts the reply at the higher rate.
  The usage line under a reply, the request log and the FAQ cost estimate (per thread) all price it this way.
  The Bedrock cost-ceiling test now holds the Bedrock models to $1 / $5, since Haiku is no longer the more expensive option.
  Not measured: the HED and NEMAR answers on Haiku 5.5.

- **HED runs Claude Haiku** (issue #591): HED's `default_model` is `haiku` (Claude Haiku 5.5, see the model classes entry below), where it was GPT-6 Luna.
  HED's annotation questions are a tag, validate and correct loop.
  On dev, Luna answered 1 of 3 of them (the others ended in a dropped stream and in a stall past the read timeout, each after tool calls), GPT-OSS answered 1 of 2 (the other hit the recursion limit), and Claude Haiku 4.5, which Haiku was then, answered all 3 in 12 to 24 s; Haiku 5.5 has not been measured on them.
  NWB, EEGLAB and BIDS run Haiku too (see below), and a reader can still choose Luna for HED from the model menu.
  Haiku 4.5 cost about nine times Luna's price per token; Haiku 5.5 costs about what Luna does, so HED's budget (`$5` a day, `$50` a month), which only alerts, is unchanged and no longer alerts at a ninth of the traffic.
  The reasoning level stays `high`, which Haiku 5.5 is sent explicitly (its own default is `medium`).
- **NWB, EEGLAB, BIDS and NEMAR run Claude Haiku** (ADR 0016): their `default_model` is `haiku` (Claude Haiku 5.5), where NWB, EEGLAB and BIDS were GPT-6 Luna on Amazon Bedrock and NEMAR was Claude Sonnet 5.5 (issue #522).
  NEMAR's figures from `nemar_render_overview` and browser-run code are image blocks, which Luna cannot take, so NEMAR cannot move to Luna; Haiku 5.5 has not been measured on those figures.
- **A streamed request in the widget is bounded by its silence, not its length** (issue #593): it was aborted 120 s after it was sent, however much the run had done since, which cut off a long tool loop that was making progress.
  It is now given up 60 s after the last data from the server (text, thinking, a tool call starting, running or finishing), for each run of a reply that runs code.
  While a server tool is running, which sends nothing until it ends and has a limit of its own (a minute for an MCP tool), the widget allows 2 minutes.
  A reply that keeps sending data is not cut off by a time limit; the step limit and the cap on browser runs still bound its length.
  A request sent with streaming off, and a JSON reply to a request that asked for a stream, have 2 minutes.
  The CLI already bounds silence (its 120 s read timeout is between chunks) and is unchanged, and so is the Cloudflare worker, which hands the stream through after the backend has started to answer.

### Fixed

- **The widget's 60 s stall timer could not fire** (issue #564): it was checked only between reads, so a read that never returned was never timed.
  The idle timer above replaces it and aborts the read.
  The server sends no keepalive on the chat stream, so more than 60 s with no data now ends a request.
  A model that streams its reasoning sends `thinking` events while it works, but one that is quiet that long before its first word (Qwen3 Next has no reasoning to stream) is given up on.
- **An error response whose body never finished could not be abandoned** in the widget: with the old fixed limit gone, it is now bounded by the idle timer, as the request is.
- **Documentation sources that are not markdown keep their angle brackets** (issue #514, section 4; PR #602): `DocumentFetcher.fetch` stripped the HTML tags from every document, and the stripper removes whatever sits between a `<` and the next `>`.
  In reStructuredText that is the target of each `` `text <https://...>`_ `` link and of each cross-reference; in a Python example it is a comparison or a generic (`if a<b and c>d` became `if ad`); in a converted web page it is a repr such as `<Raw | sample_audvis_raw.fif>`.
  Tags are now stripped only from a source whose URL path ends in `.md`, `.markdown` or `.mdx`.
  Of the 325 documentation entries of the shipped communities, 91 that are not markdown change (NWB 63, MNE 19, OpenNeuroPET 7, HED 1, EEGLAB 1).
- **Markdown keeps what is not HTML markup** (issue #514, section 4; PR #602): the stripper treated any angle-bracket text in a markdown source as a tag, so the BIDS specification's `sub-<label>` reached the model as `sub-` (242 times), and `<https://...>` autolinks and the placeholders in HED's schema examples were dropped too.
  A tag is now removed only when its name is an HTML element, and a placeholder that shares a name with an element (`<label>`) stays unless the text closes it or gives it attributes.
  Fenced code blocks and inline code are kept as written, except the body of a MyST directive such as ```` ```{admonition} ````, which is markdown.
  Of the 153 markdown documents of the shipped communities, 45 change (BIDS 24, FieldTrip 8, HED 5, OpenNeuroPET 5, NWB 3); no HTML element is left in the output outside the `<label>` and `<source-entities>` placeholders.
- **An HTML page is recognized by more than a leading doctype, and its scripts and styles are dropped** (PR #602): a page that began with a byte order mark, an `<?xml?>` prolog, a comment, `<head>` or `<body>`, or an HTML fragment served as `text/html`, was not converted to markdown, so with the change above its tags would have reached the model.
  A source whose URL ends in `.md` is never converted because of its `Content-Type`, and a bare `<div>` or `<p>` at the start does not make a markdown file a page.
  The text of `<script>` and `<style>` elements was kept as page content (up to 18 KB of JavaScript in one MNE tutorial); it is now dropped with the element.
  Of the 48 HTML pages among the shipped sources, 20 change (18 MNE tutorial pages, the BIDS specification page and PetSurfer), by script and style text only in the pages sampled.

## [0.8.17] - 2026-10-06

### Added

- **What a reply used and cost, shown with it** (issue #582): each reply now tells the reader its tokens, how many were cached, and an estimated cost, for example "1,240 in (980 cached), 310 out, about $0.0021".
  The `done` event of `/ask` and `/chat`, the `tool_request` event that ends a browser-execution run, and the non-streaming `AskResponse` and `ChatResponse` carry a `usage` object: `input_tokens` (cached ones included), `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `estimated_cost` in US dollars (null for a model with no price) and `partial`.
  On a stream the object covers one run, so a client that drives `/chat/resume` itself adds up the runs of a reply; the widget does.
  The widget shows one line under each finished reply and keeps it in the saved history, with "at least" in front when a model run reported no tokens, since the figures then leave that run out.
  `osa ask` and `osa chat` print the same wording after "Usage:" on stderr, so a pipe still carries the answer alone (with `-o json`, `usage` is a field of the JSON).
  `usage` is null when the provider reported no tokens (which is not the same as a free reply), when the request was served through OpenRouter (not covered yet), and when OSA could not build it (it is logged).
  A reply that ends in an error shows none, and the tokens of a model call that failed and was tried again are not counted.
  The cost is an estimate from OSA's price table, with cache writes priced at the five-minute rate, not an invoice.

### Changed

- **NWB: documentation pull request previews can use the widget** (pull request #579, issue #514): six Read the Docs preview origins (`pynwb--2266`, `neuroconv--2064`, `nwbinspector--768`, `hdmf--1587`, `nwb-overview--197` and `matnwb--891`, each `.org.readthedocs.build`) are on the NWB community's `cors_origins`, so reviewers can try the assistant on a preview before the documentation pull request merges.
  They are exact origins in the community's `cors_origins`, not a wildcard, so no other Read the Docs project can use the community's platform key.
  They are temporary: the config comment names the pull requests, and the entries go once those merge.
  The Cloudflare worker's list carries them too, though its suffix rules already admit any `*.readthedocs.build` origin.
- **NEMAR: a clearer notebook welcome** (pull request #577): the starter notebook now opens as a "Python playground".
  Its short welcome says where the code runs, what is installed, that the latest MNE does not install in this runtime, and that the Zarr copy is lossy.
- **NEMAR: a curated first plot** (pull request #577): the chat's guidance for power spectra and event-related potential (ERP) images now sets the plot range before drawing.
  It orders ERP image rows (by response time, else by each epoch's amplitude in a window), and describes what a plot shows, calling a recording noisy only when the dropped-epoch count supports it.
- **A failed model is called unavailable** (issue #578): when a model call fails in a way that is not the request's fault, the `error` event of a streamed `/ask` or `/chat` now says "The current model (its model id) is not available right now. Please choose another model."
  It used to say "An error occurred while processing your request." on `/chat` and "An error occurred while generating the response. Please try again." on `/ask`, so a client that matches either string needs updating.
  There is no automatic switch to another model, which would change what a community chose and what a request costs.
  A caller whose own API key is rate limited is told so instead, since another model on the same key may be limited the same way.
  A failure that is not a model's (a tool of ours failing) keeps its wording, and a refused request or key keeps its own.
  The wording and the retry below apply to streamed replies; a request with `stream: false` still gets the generic HTTP 500 for a provider failure, as before.

### Fixed

- **A stream cut short after the response began is tried once more, and a stall is classified** (issue #578): GPT-6 Luna streams on Bedrock sometimes ended almost at once with no `messageStop` event, and one request on develop stalled until the 120 s read timeout.
  A stall came out of botocore as urllib3's own error, which the classifier did not know, so it was logged as an unexpected error with no `retryable` field; it is now classified as a timeout, which is not retried and reaches an API client that waits longer than 120 s with the unavailable message.
  The CLI's own 120 s read timeout ends at about the same moment, so it may show its own connection error instead.
  A widget reader's own 120 s request timeout fires first (see the widget fix below), and the widget's stall timer, issue #564, still never fires.
  A stream that fails within ten seconds, after the response began, in a way that can clear by itself (the service ending the stream early, a dropped connection, a service error inside the stream), before the reader has been shown any text, reasoning, tool call or tool result and before any model call has finished, is run once more after about a second.
  A failure before the response began is not retried here, because the provider's client already retried it with backoff (botocore, the Anthropic SDK): a second try would only multiply the calls a degraded provider gets.
  A throttle is not retried either (botocore and the Anthropic SDK retry one with backoff; OpenRouter's client does not), nor a timeout (it has waited out its limit), a refused request or key, an error of our own, or a non-streamed request.
  Only Bedrock and Anthropic failures are recognized as coming after the response began, so a model served through OpenRouter gets no retry here, and a 5xx before its response gets none from its client either.
  The log says when a request was retried and whether the second try worked.
  A provider outage that reaches the reader (the service unavailable, the connection lost, a read that timed out) is logged at ERROR whether or not a retry was possible.
  The traceback is kept when a retry would have covered it and none was made (the retry failed, output had already been shown, or the failure took too long to arrive).
  So an alert on ERROR sees the outage and not only some of its requests; before this release such a failure was logged as a warning, without a traceback.
- **The widget names its own request timeout** (issue #578): `AbortSignal.timeout` aborts with a `TimeoutError`, not the `AbortError` the widget checked for.
  A stalled request therefore showed the browser's own text ("signal timed out") in the error banner, and "Stream interrupted" in a reply that already had text on screen.
  Both names now read as the request timeout they are: the banner says "Request timed out. Please try again.", and a reply that already had text or code ends with "Connection timeout".
- **The widget describes a failed network request**: Safari's "Load failed" and "The network connection was lost.", and Chrome's "network error" for a body cut short, reached the banner as the browsers worded them.
  They now read "Network error. Please check your connection.", as "Failed to fetch" already did.
- **A bad error message cannot break the CLI**: `osa` escapes the error and warning messages it prints, including one that is not text.
  When the server sends a null message it says "Unknown error" or "Unknown warning", where it printed "Error: None".
- **A cost too large to be real is shown as no cost**: the widget and the CLI leave out a cost of one billion dollars or more.
- **The classifier cannot raise**: an unexpected shape of provider error is "unknown", and logged once, not a second failure inside the handler that reports the first.
  A link of a cause chain it cannot read no longer hides a readable cause behind it, and an error whose text cannot be printed still gets its failure line.
- **A stream the reader closes or cancels is not retried**: a failure that surfaces as the reader closes the stream, or in place of the cancellation of its task, no longer starts a second model call that nobody will read.
  It is logged with the request's context.

## [0.8.16] - 2026-09-30

### Added

- **A new community, NWB** (issue #514, pull requests #517, #520 and #521): an assistant for Neurodata Without Borders (NWB), the data standard for neurophysiology, and the software around it (PyNWB, the Hierarchical Data Modeling Framework (HDMF), NeuroConv and NWB Inspector), to replace the separately maintained assistant embedded in the PyNWB documentation.
  It answers from 119 documentation pages: 105 from the index that assistant uses (magland/nwb-doc-index), and 14 more that the index lacks (NWB Inspector from the command line and as a library, MatNWB, the Distributed Archives for Neurophysiology Data Integration (DANDI) workflow for creating, validating and uploading a Dandiset, an overview of NWB and of writing and publishing extensions, and the schema's release notes).
  The PyNWB "NWB File Basics" and NeuroConv "Data Interfaces" pages are preloaded and the rest are fetched when a question needs them.
  The community sets its widget title, greeting and suggested questions, page context, and a budget of $5 a day and $50 a month.
  The cross-origin resource sharing (CORS) origins are `pynwb.readthedocs.io`, `nwb.org` and `www.nwb.org`, and the documentation sites of HDMF, NeuroConv, NWB Inspector, the NWB overview and MatNWB on `readthedocs.io`, so the widget can be embedded on each.
  The Cloudflare worker's allowed origins, which a workflow keeps in step with the community configs, carry the same list.
  Read the Docs preview builds (`*.readthedocs.build`) are left out on purpose, since a wildcard would let any project hosted there use the community's platform key.
  GitHub, docstring, Discourse and citation sync are not enabled yet.
  Its default model is GPT-6 Luna (see below).
- **BIDS launcher, 20% larger and clear of the page's own buttons** (issue #553, bids-standard/bids-specification#2541): the BIDS community's launcher is 67px instead of 56px and sits 104px up, above the Read the Docs version bar and the footer's social icons that it covered on bids-specification.readthedocs.io.
  It reaches the specification site with this release, since the stable release is what serves `demo.osc.earth`; the site itself needs no change, because it takes the launcher's size and place from this config.
  The values were applied by hand to the live page, since the settings that carry them ship in this release, and measured there at 1280px and 1920px wide and on a phone:
  the launcher clears the version bar (by 15px, and 11px at 1920px), the footer icons and the copyright line, and an opened version menu keeps every link and its search box uncovered.
- **Launcher position, size and offsets** (issue #553, from the BIDS site's feedback that the launcher is too small and covers the page's own buttons):
  `widget.launcher_position` (`bottom-right` or `bottom-left`), `launcher_size` and `launcher_open_size` (44 to 96px), `launcher_offset_x` and `launcher_offset_y` (0 to 200px from the side and bottom edges), and `launcher_mobile_offset_x` and `launcher_mobile_offset_y` for 600px wide and narrower.
  The launcher, its label and the panel follow the position and offsets, including the capsule in its column and its row, whose circles are reordered so Tab follows what is seen; the panel's resize handle moves to the far top corner, and the panel's minimum width and height give way to the room the offsets leave, so it stays inside a small window.
  The launcher shrinks from its closed size to its open size with the capsule's animation and its corner held still, for the bubble as well as the capsule; with only `launcher_size` set, the open size is about 80% of it (rounded, never below 44px).
  The floor is 44px, the enhanced target size of the Web Content Accessibility Guidelines (WCAG) 2.2.
  The server refuses a value outside a range, a float, a quoted number or a boolean (a bare `yes` or `off` in YAML), and an open size above the closed one, when it loads a community.
  A community that sets none of these renders exactly as before; NEMAR now spells out its own position, sizes and offsets (bottom-right, 58 and 46, 20 and 20), which are what they already were.
  A page's `setConfig` takes the same settings in camelCase and outranks the community; a wrong value from a page is refused with a warning, so the community's own value still applies.
  Tested by `frontend/test-widget-launcher-geometry.js` and, in Chrome on every animation frame, `frontend/browser-harness/launcher-geometry-check.mjs`.
- **GPT-6 Luna, Qwen3 Next 80B A3B and gpt-oss-120b** (issue #523):
  three non-Anthropic models served from Amazon Bedrock, each priced at or below Claude Haiku 4.5
  ($0.11 / $0.55, $0.14 / $1.20 and $0.15 / $0.60 per 1M input / output tokens; Haiku is $1 / $5).
  They appear in the widget's model menu and can be a community's `default_model`.
  GPT-6 Luna's default reasoning effort is high, not maximum (issue #543), and a community may set another level with `reasoning_effort` (below): at maximum, a tool-using turn took 15 to 50 seconds before its first word, all of it the model's own silent reasoning.
  On real community questions, NWB took 7 to 9 seconds to answer at high against about 27 at maximum, with one or two searches, cited sources and no tag leaks; the 48 seconds at maximum under `reasoning_effort` below is a different measurement, the median time to first text on one documentation question.
  The accepted levels, measured on Bedrock, are none, low, medium, high, xhigh and max (`minimal` is rejected).
  A deployment turns them on with `AWS_BEARER_TOKEN_BEDROCK`, and needs the platform's `ANTHROPIC_API_KEY` as well, since a request reaches Bedrock only from the platform's Claude path; without the Bedrock key they are not listed and a request naming one gets a 400.
  Requests run in Ohio (`us-east-2`, `BEDROCK_REGION`), except Qwen3 Next, whose Ohio endpoint does not answer, so it runs in N. Virginia.
  A caller's own Anthropic key cannot select them, because the platform pays for Bedrock; an OpenRouter key runs the same model there.
  When a community's `default_model` is one of them and a request that names no model cannot have it, what runs depends on the keys.
  With the Bedrock key missing but `ANTHROPIC_API_KEY` set, the request runs the deployment's Claude default (`DEFAULT_MODEL`, Claude Haiku 4.5 unless the deployment changes it) and an error naming the community is logged.
  A caller's own Anthropic key (every CLI request) runs the same default, and logs a warning instead when the cause is only that key.
  With no `ANTHROPIC_API_KEY`, the request goes to OpenRouter's slug for the model, or fails with HTTP 500 when there is no OpenRouter key either.
  The server also logs one record per such community at startup, an error when requests run Claude or fail and a warning when they go to OpenRouter, naming the keys that would serve the model from Bedrock.
  `osa validate` warns about such a default.
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
  The one live check recorded is the maintainer's of 2026-09-29, in which GPT-6 Luna and Qwen3 Next were each answered through OpenRouter (ADR 0015); whether the numbered markers and the cached-request cost figures came out right against the service is not recorded.
  `tests/test_integration/test_openrouter_citations.py` is written for it and should be run before relying on the cost figures.
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
  NWB, HED, EEGLAB, BIDS and NEMAR set `high`.
  Through the real NWB graph, Luna's median time to first text was 2.5 s at none, 5.2 s at medium and high, 10.6 s at xhigh and 48 s at max, and at xhigh and max it skipped the documentation search in the median of three runs on one question, so the answer had no citations.
  Bedrock's request fields were measured against the live service.
  The maintainer then ran live requests on 2026-09-29 (ADR 0015): one through the Claude Platform (the model was not recorded, and the effort field applies to Sonnet only) and, through OpenRouter, GPT-6 Luna, which was sent `reasoning.effort`, and Qwen3 Next, which is sent no reasoning field and so confirms routing only.
  All were answered; whether the level changed anything (reasoning tokens, latency) was not measured, and a 200 can be a no-op, as gpt-oss-120b on Bedrock showed.
  Claude Haiku, gpt-oss-120b and Claude Sonnet through OpenRouter have not been run against the service; those shapes are tested against the requests the clients build.
- **LangFuse trace metadata and feedback scores** (issue #515): a `/chat` or streaming chat turn is tagged with its community, carries the `X-User-ID` header as the trace's user and the conversation's session id, so a community's traces can be filtered and a conversation's turns grouped.
  `POST /feedback` now attaches the rating to the rated request's trace as a `user_feedback` score (`up` or `down`, with the comment).
  Like the feedback row it writes, it is best-effort: it is skipped when LangFuse is not configured or the request was not traced, and a LangFuse error is logged without failing the request.

### Changed

- **A reply with no answer, or one that stopped short, is reported instead of returned as a success** (pull requests #566, #568 and #572):
  the model can end a reply without raising anything: it spends its output budget on reasoning (GPT-6 Luna and gpt-oss-120b can), fills its context window, declines, or writes nothing.
  0.8.15 answered such a request with HTTP 200 and an empty answer, or with a `done` event with empty content, and logged nothing.
  Streamed, a reply with no text and no code run is now an `error` event with no `done` after it, and one that has text but stopped short is a `warning` event before `done`.
  An `error` carries `message`, `error_id` (the id its log line carries) and `request_id` (the key of the request's row in the metrics).
  A `warning` carries `message` and `code` (`cut_off` or `long_conversation`); a reply gets at most one, and when it was cut off in a long conversation the one event has `code` `cut_off` and `codes` listing both.
  Not streamed, `/ask` and `/chat` answer HTTP 502 with `{"detail", "error_id", "request_id"}` where 0.8.15 returned 200, and a cut-off reply that has text stays a 200 with the same text in a new `warnings` list on `AskResponse` and `ChatResponse`.
  A client that retries on a 5xx status will now retry these requests, and each attempt is billed, since the model ran each time and a retry often ends the same way; `error_id` tells the attempts apart.
  Each case logs one warning naming the community, model, request id and the provider's stop reason, and an error's row in the metrics is a 502 with an `error_message`.
- **A stream that fails says what failed** (pull requests #566, #568 and #572): the exception that ended it is classified as throttled, timeout, unavailable, connection, rejected (the provider refused the request, which fails the same way every time) or unauthorized, whether it came from Amazon Bedrock, the Claude Platform or OpenRouter; an error of OSA's own, such as a tool failing, is not claimed to be the model's.
  The log line names the kind, the provider's code or status and whether a retry can succeed; it is a warning when the failure can clear by itself or is a caller's own key being refused, and an error with the traceback otherwise.
  The `error` event adds `retryable` (`true` or `false`; absent when not known), and the reader is told to try again only when that is honest: a refused request says trying again will not help, a refused caller's key says to check it, and a refused platform or community key says to contact support.
- **A message that got no reply stays in the session** (pull requests #568 and #572): a chat turn that ends in an error or with no answer leaves the reader's message in the session with nothing after it, and the next message lands behind it, so the model still sees what was asked.
  A non-streamed `/chat` no longer stores an empty assistant message in the reply's place, as the streamed path never did.
- **Missing token usage is logged once per request, and an estimated cost is flagged** (pull requests #566 and #568): a model run that reports no tokens adds nothing to the request's cost row, and one whose usage is LiteLLM's own estimate (OpenRouter, when the provider sent none) is priced without cache or reasoning counts.
  Neither raises, so nothing showed it; a request now logs one warning when either happened, naming the community, model and request id and saying whether the recorded cost is missing (NULL), too low or approximate.
  A non-streamed chat request's cost row also sums only its own turn's model runs, not those the session's history carries.
- **The command-line interface (CLI) prints a reply's warnings and an error's reference** (pull requests #566 and #568): `osa ask` and `osa chat` print each warning, from a `warning` event or a response's `warnings`, to stderr under the answer, and a streamed error is followed by the hint "When reporting this, quote request ID ... and error ID ...", so a report finds the request's row and its log line.
- **The widget keeps a failed request's error and marks a cut-off reply** (pull requests #567 and #570): the error banner stays until the reader dismisses it or sends again, where it went after five seconds, and shows the error's reference (`error_id`) with a button that copies it.
  Warnings stack one line each, every line up for ten seconds, where the latest replaced the earlier before it could be read.
  A reply the server says was cut off carries a `cutOff` mark in the saved conversation, so the note that it may be incomplete (or the server's own explanation, when it has no text) is still there after the conversation is reloaded, and a reply with no text is kept for it.
- **Every model runs at high reasoning effort by default, Claude Haiku included** (issue #548): a community that sets no `reasoning_effort` gets `high` on every provider for Claude Sonnet 5.5, Claude Haiku 4.5, GPT-6 Luna and gpt-oss-120b (Qwen3 Next has no control).
  Claude Haiku has no effort field, so its level is a thinking budget: low 1024, medium 2048, high 4096 tokens, none no thinking, and its default goes from 2048 to 4096 tokens, so it can think up to twice as long before answering (more output tokens billed and a longer wait before the first word).
  A community that wants the old behavior sets `reasoning_effort: medium`.
  A budget that would not fit under the request's `max_tokens` is lowered so it does instead of refusing the request (a `max_tokens` of 1024 or less is still refused).
  On a caller's own OpenRouter key Haiku now reasons too, with the same budget sent as `reasoning.max_tokens` (and no temperature while it thinks); OpenRouter would otherwise turn an effort into a share of an unset `max_tokens`.
  Claude Sonnet is now sent `high` explicitly, which is the Claude Platform's own default.
  `ANTHROPIC_THINKING_BUDGET_TOKENS` is removed and the level sets the budget now; a server that still exports it logs a warning at startup naming `reasoning_effort` as the replacement.
- **A caller's own OpenRouter key now runs GPT-6 Luna and gpt-oss-120b at high reasoning effort** (issue #545): OpenRouter's own default for both is medium, and OSA sends its default (high) on every provider so a model behaves the same whichever key paid for it.
  More reasoning means a longer wait before the first word; a community can set `reasoning_effort` to change it.
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
- **A new `tool_call` event in the server-sent events (SSE) stream** of `/chat`, `/chat/resume` and `/ask` (issue #538): `{"event": "tool_call", "name": "..."}`, sent once per call when the model starts writing it, which for a long code call is many seconds before `tool_start` (or `tool_request`, for a browser call).
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
- **Figures count as about 4,800 tokens each when a conversation is measured** (issue #522, `src/agents/base.py`): they were 1,600.
  4,800 is about what Sonnet bills for a full-size image, so a long, figure-heavy chat is trimmed sooner.
- **NEMAR answers with Sonnet by default** (issue #522): `default_model` is now `claude-sonnet-5-5`.
  Sonnet costs twice what Haiku 4.5 does, per input token ($2 against $1 per 1M) and per output token ($10 against $5), and NEMAR asks it to think at high reasoning effort, which is billed as output, so a request costs about twice as much or more.
  NEMAR's budget ($5 a day, $50 a month) was sized for Haiku and is unchanged.
- **HED, EEGLAB and BIDS answer with GPT-6 Luna by default** (issue #559): `default_model` is now `openai.gpt-6-luna`, as NWB's is, with `reasoning_effort: high`; it was `claude-haiku-4-5`.
  Luna is priced at or below Haiku 4.5 ($0.11 / $0.55 against $1 / $5 per 1M input / output tokens).
  A caller with their own Anthropic key who names no model (the command-line interface, CLI) runs the deployment's Claude default (`DEFAULT_MODEL`, Claude Haiku 4.5 unless the deployment changes it) as before,
  and so does every request on a deployment that has `ANTHROPIC_API_KEY` but no Bedrock key, which also logs an error naming the community;
  with no `ANTHROPIC_API_KEY` a request goes to OpenRouter's Luna slug, or fails with HTTP 500 when there is no OpenRouter key either.
  None of the three has a tool that returns an image, so Luna's lack of image blocks loses nothing.
  NEMAR stays on Sonnet rather than moving to Luna as issue #530 proposed; Luna cannot take the images that `nemar_render_overview` and browser-run figures produce.
- **The logs keep the Bedrock key, and a caller's key, out** (pull requests #527 and #528): the log formatter now redacts Amazon Bedrock keys (`ABSK...` and `bedrock-api-key-...`) and any bearer credential in an `Authorization` header dump, and the botocore and LiteLLM loggers are held at WARNING even when the log level is DEBUG, because each prints its requests with their credentials.
  The formatter covers what this process prints, not a record that reaches another handler, which is why the two loggers are held back as well.
  `AWS_BEARER_TOKEN_BEDROCK` is trimmed, a blank value counts as unset, and one with whitespace inside is refused with an error that names the variable and never shows its value.

### Fixed

- **A stream that failed was not counted as an agent error** (pull requests #566 and #568): it wrote its row in the metrics with status 500 and no `error_message`, which is what `agent_errors` counts, so the figure left out every failed stream.
  A failed stream's row now carries an `error_message`: the exception class and the provider's code or status (none of the provider's own message), or, for a reply with no answer, why there is none.
  After the upgrade `agent_errors` counts the failed streams it used to miss, so it rises with nothing new failing; rows written before the upgrade keep no `error_message`.
- **An Anthropic error that arrives in the middle of a stream was not classified** (final review of this release): a stream that fails part way has already answered 200, so the SDK raises it with status 200 and the kind only in the error body (`overloaded_error`, `rate_limit_error`, ...).
  It was logged as an unexpected error with a traceback and sent with no `retryable`; it is now classified from the body's error type, as a failure with an HTTP status is.
  The `ValueError` that `langchain-aws` raises for a stream event it has no parser for is now the provider's too, where it was answered as a 400 "Invalid request" carrying the event.
- **A reply that ended empty after the model had written a sentence before calling a tool was reported as answered when streamed** (final review): the streamed paths judged the text of every model run, so "Let me look that up" made an empty last run look like an answer and the reader got a `done` holding only that sentence.
  They now judge the last run's text, as the non-streamed paths did, so the reply is an `error` event (or, when the last run was cut off with no text, the "ran out of room" error and not a cut-off warning); the sentence the reader already saw stays on the page.
- **A model's reply over 10,000 characters was refused and thrown away** (final review): the session held a model's text to the 10,000 characters a person may send, so a long reply, and above all one cut off at the output limit, ended a streamed chat in an `error` event with no `done` and no cut-off warning, and a non-streamed one in HTTP 500, after the model was paid for.
  A model's reply may now be up to 100,000 characters (`MAX_ASSISTANT_MESSAGE_LENGTH`); a person's message is still limited to 10,000.
  Luna's 16,000-token output limit makes such replies likelier than they were on Haiku.
- **A non-streamed request that reported no token usage recorded zero tokens and a cost of 0.0** (final review), where a streamed one records NULL, so the warning's "missing (NULL)" was not what the row held and a query for `IS NULL` missed it.
  Both now record NULL.
- **A non-streamed `/chat` answered a provider's `ValueError` as the caller's bad request** (final review): `langchain-aws` raises one for a service exception event, and the endpoint returned it as HTTP 400 with the provider's text.
  It is now logged with its traceback and answered as the generic HTTP 500, like any other model error; a `ValueError` of OSA's own is still a 400.
- **The widget showed no reference for an empty-reply 502** (final review): with `streamingEnabled: false`, or any non-streamed response, the banner showed the message without the `error_id` and its copy button that a streamed error shows.
- **The widget's Settings dialog refused an OpenRouter slug with a variant suffix, such as `openai/gpt-oss-120b:nitro`** (issue #552).
  Its model-name check had no room for `:variant`, while the server's own check for a community's ids has, so a caller with their own OpenRouter key could not save `:nitro`, `:floor` or `:free` slugs, and a saved one was dropped on load.
  The widget now applies the server's pattern and its 100-character limit, both allow any number of variants (OpenRouter lets them be stacked, as in `:nitro:exacto`), and both are held to one shared list of valid and invalid ids.
  A routing variant (`:nitro`, `:floor`, `:exacto` and the deprecated `:online`, which OpenRouter accepts on any model and which change how the request is routed, `:nitro` and `:floor` possibly its price tier) is looked through when a slug is mapped to an offered model, so `openai/gpt-oss-120b:nitro` runs at the same reasoning level as `openai/gpt-oss-120b` instead of at OpenRouter's own default; `:free` and the other catalog variants are models of their own and are left alone.
  An `anthropic/` slug that carries a variant is no longer pinned to the Anthropic provider, so the routing the caller chose is not overridden.
- **A warning for every streamed tool-call chunk filled the production log** (issue #540).
  Anthropic streams a tool call's arguments as `input_json_delta` blocks, and the content classifier only knew `tool_use`, so each chunk of each tool call logged "unrecognized content block type".
  Production had 26,389 of them in four days.
  `input_json_delta` is now a known block that carries no answer text, and a block type that really is unknown still warns once per call.
- **Concurrent OpenRouter requests could go out under each other's API key** (issue #526).
  LiteLLM keeps credentials on a module every request in the process shares, and did not send them with the call, so requests running at the same time under different keys (a caller's own key next to the platform's) used whichever key was written last: 20 of 40 interleaved requests used the other key when measured.
  The key now travels with each call.
  Anyone who ran a public OpenRouter path with more than one key in use at once should treat this as a key-mixing incident for that period.
- **LangFuse split every LLM and tool call into a trace of its own** (issue #515).
  A request's trace id was the community and 12 hex characters, and LangFuse accepts only 32 lowercase hex characters, so it refused each id and the request's calls were recorded one by one, and `request_log.langfuse_trace_id` matched no trace.
  The id is now 32 hex characters, so a request is one trace, and its id is the one in the request log.
- **Inline code showed its backticks in the widget's lists, headings and tables** (pull request #519): only a plain paragraph turned `` `code` `` into code.
  Every place that renders inline text now does, with the code's own text escaped and not read as bold, italics or a `[1]` citation marker.
  An indented list item, as models write a nested list, stays in its list instead of becoming a paragraph that starts with a literal `*`; nesting is flattened.
  Tested by `frontend/test-markdown.js`, which runs the widget's own renderer.

### Security

- **The widget's markdown renderer could put a model's text into the page as HTML** (pull request #571): when a reply rendered to nothing (it was only an opening code fence), its text was returned unescaped for `innerHTML`, so a fence whose info string is HTML, such as an `<img>` with an `onerror` attribute, became a live element in the page; the model, or a document steering it, controls that text.
  The text is now escaped.
  The 0.8.15 widget also passed a `<` in a paragraph through unescaped; the paragraph rendering rewritten for this release already escapes it, and a test now pins both.
  A page that pins the widget by Subresource Integrity (SRI) hash, such as nemar.org, keeps serving the unfixed widget and stays exposed until it is re-pinned to the 0.8.16 widget.

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

## [0.8.13] - 2026-09-22

### Changed

- **The chat widget reaches the worker at `widget.osc.earth/osa`, not at a `workers.dev` hostname** (issue #437, #438):
  the account-scoped `workers.dev` name is pinned in nemar.org's Content-Security-Policy, so moving the worker to another account would have forced a release of two repositories together.
  A path-mounted Cloudflare route, `widget.osc.earth/osa/*` (`develop-widget.osc.earth/osa/*` for develop), now delivers requests, and the worker strips the `/osa` prefix once before it matches a route, so the two `workers.dev` hostnames and `wrangler dev` still match unprefixed paths.
  A `[[routes]]` table turns `workers_dev` off unless it is set, which took the dev hostname down during the rollout, so `workers_dev = true` is now explicit.

### Fixed

- **Hardened the widget release** (issue #437, #443):
  the deploy's `wrangler` is pinned to 4.136.2 and its steps use Bun instead of npm, so a new `wrangler` release cannot change a deploy with no change here.
  `test-mounted-hosts.js` fails when the worker's `MOUNTED_HOSTS` and `wrangler.toml`'s route patterns disagree, which is silent in one direction: a host with no route never reaches the worker at all.
  Workers Logs are on for both environments, so the worker's 404 diagnostic can be read after an outage.
  `widget-route-health.yml` probes both mounted hosts, the legacy `workers.dev` name and the host gate every 15 minutes, files a labeled issue on failure and closes it on recovery; the status dashboard talks to the backend directly, so it stayed green through a route outage.
  `release.yml` now runs when a release is published: it listened only for tag pushes, which `tag-release.yml` makes under `GITHUB_TOKEN` and GitHub does not act on, so it had not run since v0.6.2 and release notes lacked the Widget Embedding section that carries the Subresource Integrity hash for a version-pinned embed.
  The rollback section records that a release's tag and PyPI upload cannot be undone, so the way back is a revert that cuts the next patch, and that `/health` does not show a detached route.
- **How the Workers Routes permission was resolved is recorded** (issue #439, #440, #441):
  the API token already held `Workers Routes`, but scoped to a zone that did not include `osc.earth`, and Cloudflare answers `No access to the specified resource` for a missing permission and a missing zone alike.
  A failed route step leaves the uploaded script live while the job exits 1, and registering a route by hand does not make it pass.

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

## [0.8.11] - 2026-09-19

### Fixed

- **`sync-develop.yml` no longer races itself into duplicate pull requests** (issue #417, #418):
  `tag-release.yml` runs twice per release, and each completion started a sync, so the second run could open a new branch and pull request after the first had merged and closed its own, leaving one in conflict.
  The runs now queue under a `concurrency` group, and a fresh `git merge-base --is-ancestor origin/main origin/develop` check ends a run whose sync already happened.

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

## [0.8.9] - 2026-09-17

### Added

- **NEMAR's dataset tools come from its Model Context Protocol server** (#352):
  `search_nemar_datasets` and `get_nemar_dataset_details` called `nemar.org/api/dataexplorer/...`, which answers 404 since the old site was retired, so neither worked.
  They are replaced by the tools `mcp.nemar.org` serves, anonymously and with no credentials: `search_datasets`, `describe_dataset` and four more about what is inside a dataset.
  Discovery runs in a thread of its own, because the assistant is built inside the running event loop, where `asyncio.run` raises.
- A Code of Conduct and a Contributing guide.
- A design note for a browser-run `execute_code` tool, in `.context/browser-execution-tool-design.md` (#351).

### Fixed

- **The NEMAR assistant no longer invents a filter** (#367):
  its prompt's worked example called `search_datasets` with `modality_filter`, which does not exist, and the server drops an unknown argument without saying so, so a request for MEG datasets returned 92 mostly-EEG results as if filtered (3 with the real `modality`).
  The prompt now names the six real filters, says that unknown arguments are ignored, and sends questions about participant counts to `subject_count` on the results; two of the widget's suggested questions, written for the old tools, are replaced.
- **A stalled tool server can no longer freeze the API** (#367):
  discovery's connect and handshake sat outside its timeout and its result had no bound, on the event loop's thread, so a host that took the connection and stalled froze every community for up to five minutes, failed the health probe and restarted the container.
  Both are bounded now, and discovered tools are cached for five minutes.
- **The Docker image builds again** (issue #356, #359):
  the base image was the end-of-life `python:3.12-slim-bullseye`, whose first `apt-get update` failed, and because the image push needs that build, no image was pushed for `main`, `develop` or a tag.
  It is `python:3.12-slim-bookworm`.
- The documentation URL check sends a named User-Agent and retries with GET when HEAD is refused with 403 or 405, since it had reported a working PetSurfer page as broken (#355).
- NEMAR's dataset-browser documentation entry points at `nemar.org/discover`, where `/dataexplorer` moved (#354).
- The test that HED's routes are mounted reaches them, where it read `app.routes`, which Starlette 1.x fills with objects that have no `path` (#353).
