# 0014. Serve GPT-6 Luna, Qwen3 Next and gpt-oss-120b from Amazon Bedrock, next to Claude

Date: 2026-09-28

## Status

Accepted.
Amends [0004](0004-anthropic-claude-platform-migration.md): 0004 decided that every offered model is a Claude model served by the Claude Platform on AWS,
"explicitly **not** Amazon Bedrock", and kept OpenRouter for callers who bring their own key.
That still holds for Claude.
This record adds three non-Anthropic models that are served from Bedrock.
0004's decision is unchanged; only its status note and its mention of the offered Sonnet were brought up to date.

## Context

Small deployments asked for something cheaper than Claude Haiku 4.5 ($1 / $5 per 1M input / output tokens).
Issue #514, for a community around Neurodata Without Borders (NWB), measured `openai/gpt-6-luna` ($0.10 / $0.50 on OpenRouter) against its existing assistant on 25 questions and found it competitive,
and asked for non-Anthropic models on a supported path.
Since 0004, OpenRouter is a bring-your-own-key (BYOK) route only, so those models could not be offered to a community that wanted the platform to pay.

The platform has a Bedrock API key (a bearer token).
Amazon Bedrock serves the three models the maintainers asked for, at or below Haiku's price (verified 2026-09-28 against the Bedrock model cards and price list):

| Model | Bedrock id | Region called | Input / output per 1M tokens |
|---|---|---|---|
| OpenAI GPT-6 Luna | `us.openai.gpt-6-luna` (inference profile) | us-east-2 | $0.11 / $0.55 |
| Qwen3 Next 80B A3B | `qwen.qwen3-next-80b-a3b` | us-east-1 | $0.14 / $1.20 |
| OpenAI gpt-oss-120b | `openai.gpt-oss-120b-1:0` | us-east-2 | $0.15 / $0.60 |

Calling them showed what a Claude-shaped request cannot assume:

- **No native citations.** Bedrock answers a `searchResult` block on all three with "This model doesn't support the searchResult field".
  The `[n]` markers and source list the widget shows come from those blocks (ADR 0004, phase 4), so without a substitute these models would answer with no citations,
  the project's first design principle being citation-backed answers.
- **Reasoning cannot be sent back.** GPT-6 Luna answers 400 to `reasoningContent.reasoningText.text` in an assistant turn; gpt-oss-120b and Qwen3 Next reject its `signature`.
  Every model call after a tool call replays the history, so the second call of any tool loop would fail.
- **Caching differs.** All three reject explicit cache points.
  GPT-6 Luna caches a repeated prompt prefix on its own and reports the tokens written and read (10,855 written, then read back, in the test run); the other two do no caching.
- **Sampling.** GPT-6 Luna rejects `temperature` and `topP`.
- **Search loops.** GPT-6 Luna (measured at maximum effort) and gpt-oss-120b, given the same retrieval tools as Claude, kept searching with reworded queries until they ran out of tokens.
  A three-sentence prompt note fixed it for both; Claude and Qwen3 Next did not need it.
- **Region.** Qwen3 Next accepts the request in Ohio and never answers (nothing after 180 s; its siblings answer in under a second), while N. Virginia and Oregon answer normally.
- **The `global.` inference profile is denied** by a service control policy on this account, and would route outside the United States.
  GPT-6 Luna's `us.` profile carries a 10% premium over global pricing, which the table above includes.
- **Credential resolution.** `langchain-aws` accepts the API key, but still builds its clients through botocore's ambient credential chain first.
  On a machine whose default AWS profile is an `aws login` session that fails, asking for `botocore[crt]`, before the key is used.

## Decision

Serve the three models from Bedrock through the Converse API, one transport for all of them, on the platform's Bedrock key (`AWS_BEARER_TOKEN_BEDROCK`), and offer them next to the Claude models.

- **One registry.** `BEDROCK_MODELS` in `src/core/services/anthropic_models.py` holds each model's label, invoke id, region, `reasoning_field`, caching behavior and prompt note.
  `reasoning_field` says how the model is sent its reasoning level: nested for GPT-6 Luna, flat for gpt-oss-120b, none for Qwen3 Next.
  The level itself is not in the registry: it is the community's, and `high` when a community sets none, see [0015](0015-community-reasoning-effort.md).
  GPT-6 Luna started at a fixed `max`, and a tool-using turn then took 15 to 50 seconds before its first word.
  `OFFERED_MODELS` is built from the registry, so the widget menu, the command-line interface (CLI) and community config validation see one list.
- **Platform-funded only.** A caller's own Anthropic key cannot select a Bedrock model: BYOK skips the origin check because it pays for itself, and the platform pays for Bedrock.
  A deployment without a Bedrock key does not list the models and answers a request for one with 400.
  A deployment without the platform's own `ANTHROPIC_API_KEY` does not list them either:
  a request moves to Bedrock only from the Claude Platform path, which a platform-funded request takes only when that key is set (`_serves_bedrock_models` in `src/api/routers/community.py`).
  A caller who brings an OpenRouter key gets the same model's OpenRouter slug.
  A community that funds itself with its own Anthropic key (`anthropic_api_key_env_var`) also runs Bedrock models on the platform's key,
  not its own: the community's key is an Anthropic key, and the models are not Anthropic's.
  That is a policy choice, not an accident; a community that wants Bedrock spend attributed to itself needs a Bedrock key of its own, which this record does not add.
- **A default nobody chose is not a refusal.** When a community's `default_model` is a Bedrock model and the request cannot have it (the caller has an Anthropic key of their own and named no model, which is every CLI request,
  or the deployment has no Bedrock key), the request runs the deployment's Claude default (`DEFAULT_MODEL`, the `haiku` class unless the deployment changes it: Claude Haiku 4.5 when this was written, Claude Haiku 5.5 since [0016](0016-model-classes.md))
  and logs an error naming the community, or a warning when the only cause is the caller's own Anthropic key.
  Refusing would take the whole community down for those callers.
  A caller who names the model still gets the 403 or 400, and `osa validate` warns about such a default.
  With no `ANTHROPIC_API_KEY` on the deployment, a platform-funded request does not take the Claude path at all: it goes to OpenRouter's slug for the model, or fails with HTTP 500 when there is no OpenRouter key either.
- **Tagged citations.** Tool results are rewritten as `[src:N] Title` text on the way to the model, the system prompt asks it to write the tag after each claim it draws from a source,
  and the tags are cut from the reply and returned as the citations the Anthropic path produces (`src/core/services/tagged_citations.py`).
  The layer sits at the model boundary, so `CitationAssembler`, the server-sent events (SSE) and the marker placement are unchanged.
  It is model-agnostic, and the OpenRouter path uses it too (#526): every OpenRouter model, Claude slugs included, gets tagged citations in place of the earlier prompt-only markdown-link rule.
  Because the tags are text in the same channel as the documents, retrieved text is defanged before the model reads it (`[src:N]` becomes `(src:N)`, titles and sources are put on one line),
  so a document cannot forge a source header and be cited under another source's tag;
  native blocks cannot be forged that way because their boundaries are structural.
- **Streams wait on a thread pool of their own.** `langchain-aws` has no async client, so an async stream waits for each chunk on a worker thread,
  and a reasoning model may think for half a minute before its first.
  On the event loop's default executor (cpu count + 4 threads, at most 32) that capped concurrent streams at a handful on a small host and starved everything else that uses the executor;
  `bedrock_llm.py` waits on 64 threads of its own instead.
  Boto3 clients are cached per (service, Region, key, timeouts) rather than built twice per request.
- **Reasoning is dropped from replayed history**, for Bedrock requests;
  and a Bedrock turn is reduced to its text and tool calls before it is sent to Claude, since a chat can switch models between requests.
- **Caching costs no code.** GPT-6 Luna's automatic cache is priced with the same multipliers as Claude's (1.25x to write, 0.1x to read; Luna's $0.1375 and $0.011 against $0.11 input),
  and its cache tokens arrive in `usage_metadata.input_token_details`, where the metrics already read them.
  Bedrock's `inputTokens` excludes the cached tokens (2 of 9,216 on a repeated prompt);
  `langchain-aws` adds the cache read and write counts back, so `input_tokens` is the whole prompt, which is what the cost arithmetic (ordinary = input - read - write) assumes.
  `tests/test_integration/test_bedrock_platform.py` asserts it against the service.
- **Per-model prompt notes**: a built-in note for the two models that loop, and a `model_instructions:` section in a community's `config.yaml` for the community's own, applied only on requests that run that model.
- **A cost ceiling by test**: every Bedrock-served model must be priced at or below Claude Haiku 4.5. (Update, 2026-10-08, [0016](0016-model-classes.md): the `haiku` class is now Claude Haiku 5.5, which costs about what Luna does, so the ceiling is held at the old $1 / $5 per million tokens instead.)
- **Frequently asked questions (FAQ) generation stays on Claude.** It builds its agents with `create_anthropic_llm`, which refuses a Bedrock id, and community config validation warns.
- The boto clients are built in `bedrock_llm.py`, with the key in the client's own token chain and placeholder credentials to stop the ambient walk.
  Nothing is written to `os.environ`.

## Consequences

- **Citations by convention are weaker than native ones.** The quoted passage is the closest one in the source, not a span the provider vouches for.
  A tag naming no source is dropped rather than shown, but a model can still tag the wrong source, or none.
  How often models follow the convention was measured through the full stack on three Hierarchical Event Descriptors (HED) documentation questions,
  counting runs in which the model retrieved a document (2026-09-28): GPT-6 Luna cited in 9 of 9, gpt-oss-120b in 9 of 9, and Qwen3 Next in 12 of 13.
  With only the system-prompt instruction Qwen3 Next cited in 5 of 7, so each tagged source now ends with a one-line reminder to tag claims drawn from it.
  A reply without tags simply has no citations, as a Claude reply that cited nothing has none.
- **A new dependency**: `langchain-aws`, which brings `boto3` and `botocore`.
- **A new secret to deploy**: `AWS_BEARER_TOKEN_BEDROCK` in the deployment's environment file, next to the platform's `ANTHROPIC_API_KEY`: the models are served only when both are set.
  Until then they are not offered, but that is not a safe default for every community.
  HED, EEGLAB, Brain Imaging Data Structure (BIDS) and NWB default to GPT-6 Luna,
  so on a deployment with an Anthropic key and no Bedrock key they run the deployment's Claude default (Claude Haiku 4.5 unless `DEFAULT_MODEL` names another)
  and log an error naming the community on every request that names no model.
  A deployment that serves them should treat the Bedrock key as required, and one that accepts Haiku for those four communities may leave it out.
  (Update, 2026-10-08, [0016](0016-model-classes.md): HED, NWB, EEGLAB and BIDS all default to the `haiku` class now, Claude Haiku 5.5 (HED since 2026-10-07, issue #591, because Luna answered 1 of 3 of its annotation questions, a validate-and-fix loop, on dev).
  No shipped community defaults to a Bedrock model, so the Bedrock key is needed only for a reader who picks one from the model menu, and the Claude default named above is the `haiku` class.)
- **A bearer-token client relies on botocore internals** (`auth_scheme_preference` and a replaced token-provider component).
  `langchain-aws` does the same, and is capped below 2.x for it; an upstream botocore change would show up in `tests/test_core/test_bedrock_llm.py`,
  which asserts the outgoing `Authorization` header and runs in continuous integration (CI).
  The live tests (`pytest -m llm`) are run by hand: `tests.yml`, the workflow that gates merging, ignores `tests/test_integration`,
  and `test.yml` runs `-m "integration or llm"` only on a push to `main` or a manual dispatch, when an Anthropic key is set, and passes no Bedrock key, so the Bedrock tests skip there.
- **The Bedrock key is kept out of logs and errors.** botocore logs each request, `Authorization` header included, at DEBUG;
  the log formatter redacts Bedrock keys and any bearer credential, the botocore logger is held at WARNING,
  and `Settings` trims the key, treats a blank one as unset, and refuses one with whitespace inside without echoing it.
- **Tool-call ids are unguessable**, which `/chat/resume` relies on (the id is the credential for continuing a turn):
  measured on all three models, ids are `call_` plus 32 hex characters (Luna) or `tooluse_` plus 22 characters (gpt-oss-120b, Qwen3 Next), unique across calls.
- **Qwen3 Next runs in another Region than the rest**, because Ohio does not answer for it.
  If Ohio recovers the pin can go.
- **Prices move.** GPT-6 Luna's long-context rate (more than 272K input tokens) is double the one in the table; conversations here stay far below that.
- The Bedrock models take no images: Model Context Protocol (MCP) and browser-tool figures stay on the Anthropic path (`takes_native_blocks`).
