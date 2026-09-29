# 0015. A community sets reasoning effort once, on one scale, for every provider

Date: 2026-09-29

## Status

Accepted

## Context

GPT-6 Luna at its maximum reasoning effort was extremely slow: a tool-using turn took 15 to 50 seconds before its first word.
#544 lowered it to `high`, but as a constant in the Bedrock model table, so a community could neither ask for more (a research assistant that wants depth) nor for less (a chat that wants speed), and the setting existed for one model on one platform.

Each platform names the control differently, and one of the shapes is a silent no-op when sent to the wrong model.
Measured against Amazon Bedrock on 2026-09-29:

- GPT-6 Luna takes a nested `reasoning.effort` and accepts `none`, `low`, `medium`, `high`, `xhigh` and `max`; `minimal` is a 400. The flat `reasoning_effort` field is a 400 for it.
- gpt-oss-120b takes a flat `reasoning_effort` and accepts `low`, `medium` and `high`; `none`, `xhigh` and `max` are 400s. The nested field is a 200 that does nothing.
- Qwen3 Next has no reasoning control: a flat `high` hangs until the read timeout, so it is sent nothing.

Through the real NWB graph on a documentation question (median of three runs each), Luna's time to first text was 2.5 s at `none`, 2.8 s at `low`, 5.2 s at `medium` and `high`, 10.6 s at `xhigh` and 48 s at `max`.
At `xhigh` and `max` it also skipped the documentation search in the median run, so the answer had no citations.

The Claude Platform on AWS and OpenRouter have their own fields (`output_config.effort` beside adaptive thinking, and a unified `reasoning.effort` body field), documented by the providers and covered here by the request each client builds, not by a live call: there is no Anthropic or OpenRouter key on the machines that run the live tests.

## Decision

A community sets `reasoning_effort` in its `config.yaml`, one key that every provider path turns into that platform's own request field (issue #545).

- **One scale**: `none`, `low`, `medium`, `high`, `xhigh`, `max`. It is the union of what the platforms name, and a value off it is refused when the config loads.
- **Each model keeps its own predetermined levels** (`REASONING_LEVELS` in `src/core/services/anthropic_models.py`). A level a model does not accept is never sent: it is lowered to the highest level the model accepts at or below the ask, or raised to its lowest when the ask is below all of them (gpt-oss cannot go below `low`).
- **Claude Sonnet is never run above `high`**, on any platform, whatever a community asks. The API accepts more; this is a policy, and it lives in the levels table so it holds on Bedrock, the Claude Platform and OpenRouter alike.
- **Models with no level to set are sent nothing**: Claude Haiku 4.5 thinks with a token budget (`ANTHROPIC_THINKING_BUDGET_TOKENS`), and Qwen3 Next has no control. Every offered model has to be classified on one side or the other, which a test enforces.
- **A model's own default applies when a community sets none**, and on every provider, so a model behaves the same whichever key paid for it: GPT-6 Luna and gpt-oss run at `high`. Sonnet has no default, so nothing is sent and the Claude Platform's own (`high`) applies, as before. NWB and NEMAR, which default to Luna, set `high` explicitly so the key is discoverable there.
- **Per platform**:
  - Bedrock: Luna gets the nested field, gpt-oss the flat one, Qwen nothing (`BedrockModel.reasoning_field`).
  - Claude Platform on AWS: Sonnet gets `output_config.effort` (`low`, `medium` or `high`) beside its default adaptive thinking. No level is lower than `low`, so `none` is no up-front thinking (`between_tools`, which the API accepts only at effort `high` or below) at effort `low`. A `between_tools` request above `high` cannot arise while the table caps Sonnet at `high`; the guard refuses it at construction, not as a 400 from the endpoint, if the table is ever widened.
  - OpenRouter: the unified `reasoning: {effort}` body field, passed through LiteLLM's `model_kwargs` because LiteLLM's own `reasoning_effort` has no `max`. OpenRouter does not let a request turn off the reasoning of the models it marks mandatory (Sonnet, gpt-oss), so `none` is raised to their lowest level there. Only the slugs in `OPENROUTER_MODEL_IDS` count as an offered model: any other slug, including the older `anthropic/claude-*` slugs that `normalize_model` aliases to the offered Sonnet, is sent nothing (OpenRouter runs those as the older model, whose reasoning the offered model's levels say nothing about), and a debug line says so.
- **The default is OSA's, not each provider's.** OpenRouter's own default effort for `openai/gpt-6-luna` and `openai/gpt-oss-120b` is `medium`; with no community key OSA now sends `high` on OpenRouter too, so a caller who brings an OpenRouter key gets more reasoning, and a longer wait, on those two models than before.
- **A level a community's own `default_model` cannot honor is a load-time warning**, not an error: the key applies to every model a request can run, and each clamps it.

## Consequences

- **The Anthropic and OpenRouter shapes are not live-verified.** Tests read the payload `ChatAnthropic` builds and the body LiteLLM posts to a local fake of OpenRouter's endpoint, and the field names and levels were checked against the providers' current documentation and OpenRouter's public models listing during review (2026-09-29), not by a request. The first request through a real Anthropic or OpenRouter key is the check that is left, and `tests/test_integration/test_anthropic_platform.py` (`pytest -m llm`) is where it belongs.
- **`xhigh` and `max` are available on Luna and are the community's call.** They cost seconds, and in the median of three runs on one documentation question they skipped retrieval, which costs the answer its citations. The defaults do not reach them, and the config comment says why.
- **Sonnet `none` is effort `low` on both platforms**, with no up-front thinking on the Claude Platform (the short progress notes Sonnet writes between tool calls still arrive as `thinking` blocks) and, on OpenRouter, where its reasoning is mandatory, with whatever reasoning `low` gives.
- **Claude Haiku 4.5 ignores the key**, and it is the default model of most communities: it thinks with a token budget, not a level, so there is nothing to map. The config load warns when a community sets a level its default model ignores.
- **Changing a community's level changes the request's effort, which invalidates the provider's prompt cache once.** A fixed level per community does not.
- **The warning checks the community's default model only.** A request that names another model runs the same level, clamped to that model.
- **A new offered model has to be classified** (levels, or none) before the test suite passes, so a model cannot be added that silently ignores the key.
