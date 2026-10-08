# 0016. A model class names a tier; one table says which model it is today

Date: 2026-10-08

## Status

Accepted

## Context

A new model generation means editing the same model id in many places.
Moving the platform from Claude Haiku 4.5 to Claude Haiku 5.5 (released October 7, 2026) touched the registry, the pricing table, the default in `Settings`, the community `config.yaml` files, the FAQ summarizer, the widget's hand-kept copy of the tables, `.env.example`, the documentation and a large part of the test suite, each naming `claude-haiku-4-5`.
The ids also say less than the intent: HED, MNE and FieldTrip want "the small fast Claude", not that generation of it.

The Haiku move was not only a rename.
Claude Haiku 5.5 thinks adaptively, steered by an effort level, and rejects a thinking budget, `temperature` and a prefill (the 4.5 generation took a budget and a temperature).
It turns thinking off with `{"type": "disabled"}` where Sonnet 5.5 takes `{"type": "between_tools"}`.
It is priced by prompt length: $0.10 / $0.50 per million tokens up to 100,000 prompt tokens and $0.50 / $2.50 above, against $1 / $5 for 4.5.
Its OpenRouter slug is `anthropic/claude-haiku-5.5`, and OpenRouter does not make its reasoning mandatory.
Those facts differ per generation and belong with the model, not with the places that name it.

On Amazon Bedrock the OpenAI classes are on different generations: `gpt-6-luna`, `gpt-6-astra`, `gpt-6.1-sol` and `gpt-5.6-terra`.

## Decision

`MODEL_CLASSES` in `src/core/services/anthropic_models.py` is the one place a generation is chosen:
`haiku`, `sonnet`, `opus` and `fable` for Anthropic, and `luna`, `terra`, `sol` and `astra` for OpenAI.

- **A class name is accepted wherever a model is named**: a community's `default_model`, an agent's `model`, a request, the CLI, `DEFAULT_MODEL` in the environment.
  `normalize_model` resolves it first, so it means the model the class is today.
  The community config endpoint reports the id it resolved to, so the widget compares ids as before.
- **The facts about a model are keyed by the class's id, never by a literal**:
  `HAIKU`, `SONNET` and `LUNA` in the registry, the reasoning levels, the sampling and thinking tables, the Bedrock serving table, the OpenRouter slugs, the prices and the FAQ summarizer's models.
  The widget label (`Claude Haiku 5.5`) and the OpenRouter slug of a Claude model are derived from its id.
- **Moving a class to a new generation is three edits in that file**:
  its entry in `MODEL_CLASSES`, the id it replaces in `PREVIOUS_GENERATIONS` (so a saved widget setting or an old `config.yaml` still resolves), and the new model's own entries where it differs (price in `src/metrics/cost.py`, reasoning levels, `THINKING_OFF`, `SAMPLING_MODELS`).
  Nothing else names the generation, and the tests read the ids from the same constants.
- **A class that is not offered is refused** as an unknown model until its facts are filled in and it is added to `OFFERED_MODELS`.
  Opus, Fable, Terra, Sol and Astra are in the table now, with the id each is today, so that offering one is that step and nothing more.
- **The previous generations stay priced** in `MODEL_PRICING`, because request logs written before the move name them.

The first move under this decision is `haiku`: Claude Haiku 4.5 to Claude Haiku 5.5, with the same `high` reasoning level for every community and no thinking budget.

## Consequences

- The next generation is a change in one file plus a price, not a hunt through the repository and its tests.
  A community that names a class moves with the platform; a community that must stay on a generation names its id, and gets the model that id resolves to, which for a retired generation is the class's current model.
  That is the existing behavior for the older Sonnet ids, now applied to Haiku.
- A saved setting that names Claude Haiku 4.5 now runs Claude Haiku 5.5.
  The widget's copy of the alias table (`RETIRED_MODEL_IDS`) is kept equal to the backend's by `tests/test_frontend/test_widget_drift.py`.
- Neither offered Claude model takes a `temperature` any more.
  EEGLAB's FAQ evaluation agent, which pinned 0.0 for deterministic scoring on Haiku 4.5, scores at the model's default temperature, and the community's `temperature` lines are gone rather than set and dropped.
- Haiku 5.5 counts about 30% more tokens for the same text than 4.5 does, and a prompt above 100,000 tokens is billed at five times the rate.
  The conversation budget (`DEFAULT_MAX_CONVERSATION_TOKENS`, 80,000 estimated tokens plus the system prompt) may reach that line on a long conversation, since its estimate was made for the older tokenizer; it was not changed here.
- Haiku is now priced about as Luna is, so a Bedrock model is no longer cheaper than the Claude default.
  The test that held every Bedrock model to the Claude default's price holds it to the old cheap tier ($1 / $5) instead.
- `THINKING_BUDGET_TOKENS` and the budget-style request shape are gone: no offered model thinks with a budget.
- Not decided here: which of Opus, Fable, Terra, Sol and Astra to offer, and at what price ceiling.
