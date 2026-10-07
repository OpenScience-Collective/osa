# 0015. A community sets reasoning effort once, on one scale, for every provider

Date: 2026-09-29

## Status

Accepted

## Context

GPT-6 Luna at its maximum reasoning effort was extremely slow: a tool-using turn took 15 to 50 seconds before its first word.
#544 lowered it to `high`, but as a constant in the Bedrock model table,
so a community could neither ask for more (a research assistant that wants depth) nor for less (a chat that wants speed),
and the setting existed for one model on one platform.

Each platform names the control differently, and one of the shapes is a silent no-op when sent to the wrong model.
Measured against Amazon Bedrock on 2026-09-29:

- GPT-6 Luna takes a nested `reasoning.effort` and accepts `none`, `low`, `medium`, `high`, `xhigh` and `max`; `minimal` is a 400.
  The flat `reasoning_effort` field is a 400 for it.
- gpt-oss-120b takes a flat `reasoning_effort` and accepts `low`, `medium` and `high`; `none`, `xhigh` and `max` are 400s.
  The nested field is a 200 that does nothing.
- Qwen3 Next has no reasoning control: a flat `high` hangs until the read timeout, so it is sent nothing.

Through the agent graph of the Neurodata Without Borders (NWB) community on a documentation question (median of three runs each),
Luna's time to first text was 2.5 s at `none`, 2.8 s at `low`, 5.2 s at `medium` and `high`, 10.6 s at `xhigh` and 48 s at `max`.
At `xhigh` and `max` it also skipped the documentation search in the median run, so the answer had no citations.
The times above are times to the first text of one question, a different measurement from the 15 to 50 seconds above (the range over tool-using turns before a first word at `max`)
and from the 7 to 9 seconds at `high` against about 27 at `max` that #544 recorded as the time for NWB to answer real community questions; the figures do not conflict.

The Claude Platform on AWS and OpenRouter have their own fields (`output_config.effort` beside adaptive thinking, and a unified `reasoning.effort` body field),
documented by the providers and covered here by the request each client builds;
live requests the maintainer has since run are recorded under Consequences.

## Decision

A community sets `reasoning_effort` in its `config.yaml`, one key that every provider path turns into that platform's own request field (issue #545).

- **One scale**: `none`, `low`, `medium`, `high`, `xhigh`, `max`.
  It is the union of what the platforms name, and a value off it is refused when the config loads.
- **Each model keeps its own predetermined levels** (`REASONING_LEVELS` in `src/core/services/anthropic_models.py`).
  A level a model does not accept is never sent: it is lowered to the highest level the model accepts at or below the ask,
  or raised to its lowest when the ask is below all of them (gpt-oss cannot go below `low`).
- **Claude Sonnet is never run above `high`**, on any platform, whatever a community asks.
  The API accepts more; this is a policy, and it lives in the levels table so it holds on Bedrock, the Claude Platform and OpenRouter alike.
- **Claude Haiku 4.5 has no effort field, so its level is a thinking budget** (`THINKING_BUDGET_TOKENS`, issue #548):
  `low` 1024 tokens (the API's floor), `medium` 2048 (what Haiku ran at before levels), `high` 4096, `none` no thinking;
  `xhigh` and `max` give `high`, since a larger budget leaves too little of `max_tokens` for the answer.
  A budget that would not fit under the request's `max_tokens` is lowered so it does (the API refuses one that is not below it):
  it leaves the API's minimum budget's worth of tokens for the answer where `max_tokens` allows (at least 2048),
  is the minimum itself (1024) between 1025 and 2047, and a `max_tokens` of 1024 or less is still refused, since no budget is valid.
  A budget a caller names is never lowered.
- **Models with no level to set are sent nothing**: Qwen3 Next has no control.
  Every offered model has to be classified as having levels or not, which a test enforces.
- **Every model runs at `high` when a community sets none**, on every provider, so a model behaves the same whichever key paid for it (`DEFAULT_REASONING_EFFORT`, issue #548; #545 had per-model defaults for Luna and gpt-oss only).
  Claude Sonnet is then sent `high` explicitly, which is the Claude Platform's own default, so nothing changes there; Haiku's default budget goes from 2048 to 4096.
  The communities that default to Luna (NWB, Hierarchical Event Descriptors (HED), EEGLAB and Brain Imaging Data Structure (BIDS)) set `high` explicitly, and so does NEMAR, so the key is discoverable there.
  (HED has defaulted to Claude Haiku 4.5 since 2026-10-07, issue #591, and keeps `high`, which is a 4096-token thinking budget there.)
- **Per platform**:
  - Bedrock: Luna gets the nested field, gpt-oss the flat one, Qwen nothing (`BedrockModel.reasoning_field`).
  - Claude Platform on AWS: Sonnet gets `output_config.effort` (`low`, `medium` or `high`) beside its default adaptive thinking.
    No level is lower than `low`, so `none` is no up-front thinking (`between_tools`, which the API accepts only at effort `high` or below) at effort `low`.
    A `between_tools` request above `high` cannot arise while the table caps Sonnet at `high`;
    the guard refuses it at construction, not as a 400 from the endpoint, if the table is ever widened.
  - OpenRouter: the unified `reasoning: {effort}` body field, passed through LiteLLM's `model_kwargs` because LiteLLM's own `reasoning_effort` has no `max`.
    OpenRouter does not let a request turn off the reasoning of the models it marks mandatory (Sonnet, gpt-oss), so `none` is raised to their lowest level there.
    Only the slugs in `OPENROUTER_MODEL_IDS`, or one of them with routing variants (`:nitro`), count as an offered model when the client is built;
    any other slug is sent nothing, and a debug line says so.
    The router maps the model to a slug before that (`_to_openrouter_model_via_canonical` in `src/api/routers/community.py`), so what a request runs depends on how it was named.
    A model a caller names becomes the offered model's slug when it is an offered id or a known alias (`claude-sonnet-4.5`, `anthropic/claude-sonnet-4.5`),
    so the request runs the offered Sonnet (`anthropic/claude-sonnet-5.5`) at its levels; a community default written as a bare id is mapped the same way.
    Any other slug reaches the client as written and is sent nothing, because the offered model's levels say nothing about the model it names:
    an alias slug with a variant suffix (`anthropic/claude-sonnet-4.5:nitro`), which OpenRouter runs as the older model, or a community's `default_model` written as a slug outside the table.
- **The default is OSA's, not each provider's.** OpenRouter's own default effort for `openai/gpt-6-luna` and `openai/gpt-oss-120b` is `medium`,
  and Claude models there run without reasoning unless asked (Haiku) or at OpenRouter's own level (Sonnet);
  with no community key OSA sends `high` on OpenRouter too, so a caller who brings an OpenRouter key gets more reasoning, and a longer wait, on those models than before.
- **A level a community's own `default_model` cannot honor is a load-time warning**, not an error: the key applies to every model a request can run, and each clamps it.

## Consequences

- **What was verified by a request.** The maintainer reported on 2026-09-29 that a live request through the Claude Platform worked (the model was not recorded, and `output_config.effort` applies to Sonnet only),
  and that, through OpenRouter, GPT-6 Luna and Qwen3 Next worked.
  Luna's request carried `reasoning: {effort}` and was accepted; whether the level took effect (reasoning tokens, latency) was not measured, and a 200 can be a no-op, as gpt-oss on Bedrock showed.
  Qwen3 Next is sent no reasoning field, so it confirms routing only.
  Not yet run by a request: Claude Haiku through OpenRouter (`reasoning.max_tokens`), gpt-oss-120b through OpenRouter and Claude Sonnet through OpenRouter.
  Tests read the payload `ChatAnthropic` builds and the body LiteLLM posts to a local fake of OpenRouter's endpoint,
  and the field names and levels were checked against the providers' current documentation and OpenRouter's public models listing during review (2026-09-29).
  Haiku's budget levels are OSA's mapping, not a provider's.
- **An OpenRouter routing variant is looked through, a catalog variant is not** (issue #552).
  OpenRouter's "Model variants" page lists `:nitro` (fastest providers), `:floor` (cheapest), `:exacto` (best at tool calls) and the deprecated `:online` as routing variants,
  accepted on any model and stackable in any order (`openai/gpt-5.2:nitro:exacto`), so the same model runs (`:nitro` and `:floor` can also change the price tier) and it gets the plain slug's reasoning level.
  `:free`, `:batch`, `:thinking` and `:extended` are catalog entries of their own, at most one to a slug, and, like any slug OSA does not map,
  are sent no reasoning field: stripping `:free` would resolve to the paid entry.
  A variant of an alias slug (`anthropic/claude-sonnet-4.5:nitro`) is not mapped:
  the router maps the plain alias to the offered Sonnet's slug, but a slug with a suffix is not an id it knows,
  so it is sent as written, runs the older model and gets no reasoning field.
  The slug a caller types is theirs: OSA checks its format, sends it as written and sends no `provider.order` for it,
  and for an `anthropic/` slug that carries a variant it does not pin the Anthropic provider as it does for a plain one,
  since OpenRouter's documentation does not say whether a routing variant and an explicit `provider.order` conflict.
  A community `default_model` that is a variant slug passes config validation but is refused at request time by the pricing check,
  which does not look through the suffix; that is loud, and it is not changed here.
- **`xhigh` and `max` are available on Luna and are the community's call.** They cost seconds, and in the median of three runs on one documentation question they skipped retrieval, which costs the answer its citations.
  The defaults do not reach them, and the config comment says why.
- **Sonnet `none` is effort `low` on both platforms**, with no up-front thinking on the Claude Platform (the short progress notes Sonnet writes between tool calls still arrive as `thinking` blocks) and,
  on OpenRouter, where its reasoning is mandatory, with whatever reasoning `low` gives.
- **Haiku thinks up to twice as much by default** (a 4096-token budget against 2048):
  more output tokens billed (Haiku is $1 / $5 per 1M) and a longer wait before the first word.
  It is the default model of FieldTrip, HED, MNE, MetaBCI and OpenNeuroPET,
  and the model a Luna default falls back to when Luna cannot be served (for EEGLAB, BIDS and NWB: a deployment with no Bedrock key, or a caller's own Anthropic key with no model named).
  A community that wants the old behavior sets `reasoning_effort: medium`.
  The deployment setting `ANTHROPIC_THINKING_BUDGET_TOKENS`, which held the old budget, is removed:
  it defaulted to the same number the example file shipped, so a server that copied it would have kept Haiku off the new default.
  `Settings` ignores unknown variables, so a leftover one is harmless to startup, and `get_settings` logs a warning naming `reasoning_effort` as the replacement,
  so an operator who had lowered the budget on purpose is told it no longer applies.
- **On OpenRouter, Haiku is sent its budget itself**, `reasoning: {"max_tokens": <the level's budget>}`, not an effort:
  OpenRouter's documentation turns an effort into a share of `max_tokens` for Claude models (`max_tokens` is unset on this path),
  which would not be OSA's budget, and says `reasoning.max_tokens` is used as given (1024 at least).
  `none` sends no reasoning field (without one Haiku does not think there), and no temperature is sent while it thinks,
  as on the Claude Platform path (Anthropic does not allow one with thinking).
  This is bring-your-own-key (BYOK) only and is documented, not live-verified:
  OpenRouter's models listing gives Haiku no `supported_efforts`, which is why an effort is not used.
- **The config load warns when a community sets a level its default model ignores** (Qwen3 Next).
- **Changing a community's level changes the request's effort, which invalidates the provider's prompt cache once.**
  A fixed level per community does not.
- **The warning checks the community's default model only.**
  A request that names another model runs the same level, clamped to that model.
- **A new offered model has to be classified** (levels, or none) before the test suite passes, so a model cannot be added that silently ignores the key.
