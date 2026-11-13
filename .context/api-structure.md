# API Structure and Community Routing

This document describes how the OSA API is structured around communities, how routes are dynamically created, and implementation details for maintaining the API.

> **Provider routing and models:** the examples below are from the OpenRouter era (OpenRouter model ids, `X-OpenRouter-Key`).
> What a request goes through today, on the Claude Platform on AWS and Amazon Bedrock, is in [Provider Routing and the Bedrock Models](#provider-routing-and-the-bedrock-models) at the end of this file,
> and the reasons are in `docs/adr/0004-anthropic-claude-platform-migration.md` and `docs/adr/0014-bedrock-models-alongside-claude.md`.
> The response examples also predate the `warnings` list and the 502 an unanswered reply gets (see the 0.8.16 entries in `CHANGELOG.md`).

## Core Principle: Community-Based Routing

**Each community gets its own namespace at the root level:**

```
/{community_id}/           # Community config endpoint
/{community_id}/ask        # Single question
/{community_id}/chat       # Multi-turn conversation
/{community_id}/sessions   # Session management
```

**Example:**
```
/hed/           → GET community config (id, name, description, default_model)
/hed/ask        → POST ask a HED question
/hed/chat       → POST chat with HED assistant
/hed/sessions   → GET list HED sessions
```

**NOT like this:**
```
❌ /communities/hed/ask     # Wrong - breaks the pattern
❌ /api/hed/ask             # Wrong - adds unnecessary prefix
❌ /assistant/hed/ask       # Wrong - community ID should be root
```

## Dynamic Router Creation

Routes are created dynamically at startup for each registered community:

```python
# src/api/main.py
def register_routes(app: FastAPI):
    for info in registry.list_available():
        router = create_community_router(info.id)  # Creates /{community_id} routes
        app.include_router(router)
```

### How create_community_router Works

```python
# src/api/routers/community.py
def create_community_router(community_id: str) -> APIRouter:
    """Creates a complete router for one community."""
    info = registry.get(community_id)
    router = APIRouter(prefix=f"/{community_id}", tags=[f"{info.name} Assistant"])

    # Define endpoints inside the function so they close over community_id
    @router.get("/", response_model=CommunityConfigResponse)
    async def get_community_config():
        # Returns config for THIS community
        ...

    @router.post("/ask", response_model=AskResponse)
    async def ask(body: AskRequest, ...):
        # Handles ask for THIS community
        ...

    return router
```

**Key insight:** Each community gets its own router with its own endpoint handlers. The `community_id` is captured in the closure, so each router "knows" which community it serves.

## Standard Endpoints for Every Community

### 1. GET /{community_id}/ - Community Configuration

**Purpose:** Returns community metadata and model configuration for the frontend widget.

**Authentication:** None required (public configuration)

**Response:**
```json
{
  "id": "hed",
  "name": "HED (Hierarchical Event Descriptors)",
  "description": "Event annotation standard for neuroimaging research",
  "default_model": "qwen/qwen3-235b-a22b-2507",
  "default_model_provider": "DeepInfra/FP8"
}
```

**Used by:** Frontend widget to display model settings and community info.

**Implementation:**
```python
@router.get("/", response_model=CommunityConfigResponse)
async def get_community_config():
    settings = get_settings()
    default_model = settings.default_model
    default_provider = settings.default_model_provider

    # Community can override platform defaults
    if info.community_config and info.community_config.default_model:
        default_model = info.community_config.default_model
        default_provider = info.community_config.default_model_provider

    return CommunityConfigResponse(
        id=info.id,
        name=info.name,
        description=info.description,
        default_model=default_model,
        default_model_provider=default_provider,
    )
```

### 2. POST /{community_id}/ask - Single Question

**Purpose:** Ask a single question without conversation history.

**Authentication:** Requires API key OR BYOK (X-OpenRouter-Key)

**Request:**
```json
{
  "question": "What is HED?",
  "stream": false,
  "page_context": {
    "url": "https://hedtags.org/docs",
    "title": "HED Documentation"
  },
  "model": "qwen/qwen3-235b-a22b-2507"  // Optional, requires BYOK if custom
}
```

**Response:**
```json
{
  "answer": "HED (Hierarchical Event Descriptors) is...",
  "tool_calls": [
    {"name": "retrieve_hed_docs", "args": {...}}
  ]
}
```

### 3. POST /{community_id}/chat - Multi-turn Conversation

**Purpose:** Chat with conversation history and session management.

**Authentication:** Requires API key OR BYOK

**Request:**
```json
{
  "message": "Can you validate this HED string?",
  "session_id": "abc123",  // Optional, creates new if omitted
  "stream": true,
  "model": "qwen/qwen3-235b-a22b-2507",  // Optional, requires BYOK if custom
  "prior_turns": [                       // Optional: the chat so far, to reseed a session the server lost
    {"role": "user", "text": "find datasets with face stimuli"},
    {"role": "assistant", "text": "The closest match is on002718."}
  ],
  "mode": "quick",                       // Optional: "quick" or "analysis", used with prior_turns
  "expect_session": false                // Optional: true asks for a 409 instead of an empty session
}
```

`expect_session` (default false) is for a client that holds the chat but has stopped sending
`prior_turns` because the server confirmed the session. It needs `session_id` (a 422 without).
If it is true, the request has no `prior_turns`, and the server holds no live session for
`session_id` (unknown, evicted or expired), the server answers `409`
`{"detail": "<plain sentence>", "code": "session_unknown"}` before it creates a session, starts a
stream or calls a model; it writes no usage row, so it is not counted as an error.
With `prior_turns` present it reseeds as above;
without the field nothing changes. `/chat/resume` does not take it (it already answers 404 for an unknown session).

`prior_turns` (at most 24 turns of at most 4,000 characters; anything else is a 422) and `mode`
are used only when the request creates the session; a session that already has messages ignores
both. The server keeps the newest whole turns that fit 12,000 characters, marked
`[earlier turns omitted]` when it dropped any. `mode` applies only to a request routed between a
quick and an analysis model. See `docs/community-widget.md` ("A conversation the server forgot").

**Response:**
```json
{
  "session_id": "abc123",
  "message": {
    "role": "assistant",
    "content": "Let me validate that for you..."
  },
  "tool_calls": [...]
}
```

### 4. GET /{community_id}/sessions - List Sessions

**Purpose:** List all active sessions for this community.

**Authentication:** Requires API key

**Response:**
```json
[
  {
    "session_id": "abc123",
    "community_id": "hed",
    "message_count": 5,
    "created_at": "2026-01-26T10:00:00Z",
    "last_active": "2026-01-26T10:05:00Z"
  }
]
```

### 5. POST /{community_id}/overview - Search Overview

**Purpose:** A short, cited reading of the top results of a dataset search, for the Discover panel (board D2, plan E6.3). The caller already holds the results; it sends them, and the community's quick model answers as a colleague would (#748): a short lead-in on what the search seems to focus on, up to 10 picks with how each relates to it, and a suggestion of where to start. Code: `src/api/overview.py` (models, normalization, cache, prompt, parser, citation filter; no FastAPI import) and the route in `create_community_router`, beside `/ask`. Page for embedders: `docs/search-overview.md`.

**Authentication:** An authorized origin (the community's `cors_origins`) or BYOK, as `/ask`. No other credential; the worker gives it rate limiting and no Turnstile (readers of Discover hold no token).

**Request:**
```json
{
  "query": "face recognition in EEG",
  "filters": {"modality": ["eeg"], "has_hed": true},
  "results": [
    {"id": "on002718", "title": "Face processing EEG", "description": "...", "modalities": "eeg"}
  ],
  "total_count": 240
}
```
- `query`: 1 to 300 characters, not blank, and at least `MIN_QUERY_WORDS` (3) words as `query_word_count` counts them (whitespace-separated tokens holding a letter or digit); the maintainer's rule (#728).
- `filters`: at most 20 keys; a value is a string, bool, int or list of strings (names up to 100 characters, strings up to 200, lists up to 20 items).
- `results`: 1 to 20 rows (`MAX_RESULTS`) in the search's ranking order. `id` is up to 64 characters of letters, digits, `.`, `_`, `-` and unique in the request; `title` up to 200 characters; `description` up to 500; `modalities` is an optional string.
- `total_count`: optional, how many results matched in all.

**Response 200:**
```json
{
  "summary": [
    {"text": "Your search seems to focus on how the brain responds to faces. I had a look at the top 20 matches, and this one looks especially promising."}
  ],
  "picks": [{"id": "on002718", "reason": "18 participants viewed famous and scrambled faces."}],
  "suggestion": [
    {"text": "I'd start with "},
    {"dataset_id": "on002718"},
    {"text": ", since it is built around famous faces."}
  ],
  "cited_ids": ["on002718"],
  "shown": 20,
  "cached": false,
  "model": "claude-haiku-5-5",
  "generated_at": "2026-11-11T08:30:00.123456Z"
}
```
- `summary` (the lead-in) and `suggestion` (where to start, shown after the picks) are text and result-id segments, never markdown, so the website links an id without parsing HTML; `summary` always has text and may name no result, and `suggestion` may be empty. `picks` has at most 10 (`MAX_PICKS`). `cited_ids` lists the ids the summary, the picks and then the suggestion name, in order of first mention. `shown` is the number of results the overview was written from.
- `cached` is true when this request did not call the model: it was served from the cache, or it shared the call of an identical request already in flight.

**Errors:**

| Status | When |
|---|---|
| 403 | No authorized origin and no BYOK key; or the model costs more than the platform allows |
| 404 | The community has no `quick_model` (no config key was added for this endpoint) |
| 422 | A field is out of bounds: more than 20 results, an over-long field, an empty query, a query under 3 words, duplicate result ids |
| 500 | A failure that is not the provider's (a bug, a missing API key for the model factory): logged with its traceback, not cached. The body is `{"detail": ...}` with an error id |
| 502 | The model wrote nothing that survives the citation filter, a summary with no words, a reply that names no result, could not be parsed, or the provider failed (after one retry). Not cached. The body is `{"detail": ...}` |

**Citations.** The allowed ids are the ids of the request's `results`, and nothing the model or a result's text says widens them. The model is told to write `[[id]]` markers and a `picks` list as one JSON object; the server parses it. A sentence holding a `[[marker]]` or a bare id-shaped token (`\b[a-z]{2}\d{6}\b`, case-insensitive) that is not an allowed id is dropped, and so is a pick of such an id or whose reason names one. A bare token that is an allowed id becomes a link too. The text of the results reaches the model as JSON inside `<data>` tags with `<` and `>` escaped, and the prompt calls it untrusted. A sentence holding a link (`http://`, `https://`, `www.`) is dropped, and stray single brackets are removed from text. The bare-token check is hard-wired to the NEMAR id shape; another community's ids that do not match it are checked only inside `[[...]]` markers. The suggestion is filtered sentence by sentence like the summary. When no sentence of the summary survives, the summary has no words, or the summary, picks and suggestion together name no result (the prompt asks for picks by id, so a refusal, an off-format reply or planted text has none), the answer is a 502 and nothing is cached. The summary and the suggestion are each held to 60 words (`SUMMARY_MAX_WORDS`, `SUGGESTION_MAX_WORDS`): sentences after the first that would pass it are dropped, and a first sentence that is over the cap on its own is kept.

**Cache.** In memory per community and per process, 24 hours, at most 500 entries (the oldest evicted first), keyed on the normalized query (NFKC, case folded, spaces collapsed), the canonical filters (key order, list order and string case do not matter; `true` is not `1`) and a SHA-256 of the results payload (ids, titles, descriptions, modalities, in order, and `total_count`). The results are in the key because the caller supplies them: keyed on the query alone, one caller could plant an overview that every reader of that query is then shown. Concurrent identical requests share one in-flight model call, and a failure is delivered to all of them and cached for none. Authorization runs before the cache is read, so a hit is never served to a caller who could not have made the call. The cache is lost on restart and not shared between workers (`src/api/overview.py`, `OverviewCache`; the clock is injectable).

**Model and budget.** `_route_request(..., tier="quick")` picks the community's `quick_model`, then `_check_model_cost`, then `_build_chat_model` (the helper `create_community_assistant` also uses) builds it with `reasoning_effort="low"` and `max_tokens=1500`; a failure the provider says can clear is retried once (`invoke_with_retry`, as the handover summary does). The metrics row is set as `/ask`'s is (`request.state.metrics_agent_data`), with `tier` `quick`, the model, the key source, the tokens and `estimated_cost`, and `overview` is in the middleware's `_COMMUNITY_SUFFIXES`, so the row carries the `community_id` that `src.metrics.budget.check_budget` sums by. A call that produced no answer is still paid for and still has its row. A cache hit, or a request that shared another's call, logs a row with zero tokens and zero cost. The call outlives a cancelled request (others may wait on it, and its result is cached), so when the request that started it is cancelled the call books its own cost, on a 499 row with the community, tier and tokens, once (`_OverviewSpend`). An error that is not the provider's is a 500 logged with its traceback; a classified provider failure is a 502.

**Worker.** `workers/osa-worker/index.js` routes `POST /:communityId/overview` (`ROUTE_PATTERNS.communityOverview`) with rate limiting only: the per-minute limiter it shares with `/ask` and `/chat`, and its own per-IP hourly budget (`OVERVIEW_LIMIT_PER_HOUR`, 100 in production and 500 in development, key `rl:overview:hour:`), not the 20 an hour `/chat` counter, so Discover searches do not use up a reader's chat questions. There is no Turnstile and an `Origin` header is enforced by browsers only, so these per-IP limits are the only enforced control on the quick model's spend (the community's budget is monitored by the scheduler, which opens an alert issue when it is crossed, and does not refuse a request). `.github/workflows/sync-worker-cors.yml` deploys the production worker on a push to `main` that touches `workers/osa-worker/`, so the route is live on `widget.osc.earth/osa` and `edge.osc.earth/osa` once a release with it merges; the backend serves it only after the server pulls that release's image, so call it after `/health` reports the release. nemar.org calls `widget.osc.earth` (its allowlist and CSP name that host).

## Model Selection Logic

The model selection follows a clear priority:

```python
def _select_model(community_info, requested_model, has_byok):
    """
    Priority:
    1. User requests custom model → requires BYOK, use requested_model
    2. Community has default_model → use community default
    3. Else → use platform default_model
    """
    settings = get_settings()

    # Determine community/platform default
    default_model = settings.default_model
    default_provider = settings.default_model_provider
    if community_info.community_config and community_info.community_config.default_model:
        default_model = community_info.community_config.default_model
        default_provider = community_info.community_config.default_model_provider

    # Custom model requires BYOK
    if requested_model and requested_model != default_model:
        if not has_byok:
            raise HTTPException(403, detail="Custom model requires BYOK")
        return (requested_model, None)  # Custom uses default routing

    return (default_model, default_provider)
```

**Why this matters:**
- Platform can set a default model for all communities
- Communities can override with their preferred model
- Users can bring their own key to use any model they want

## Configuration Flow

```
Platform Config (src/api/config.py)
    ↓
    default_model: "qwen/qwen3-235b-a22b-2507"
    default_model_provider: "DeepInfra/FP8"

Community Config (src/assistants/{community}/config.yaml)
    ↓
    default_model: "anthropic/claude-3.5-sonnet"  # Optional override
    default_model_provider: null

Request Body
    ↓
    model: "openai/gpt-4o"  # Optional, requires BYOK
```

**Final model selection:**
1. Check request body `model` field → if present and different from default, require BYOK
2. Check community config `default_model` → if present, use it
3. Else use platform `default_model`

## Common Implementation Mistakes

### ❌ Mistake 1: Using /communities/ prefix

**Wrong:**
```python
router = APIRouter(prefix="/communities/{community_id}")  # ❌
```

**Right:**
```python
router = APIRouter(prefix=f"/{community_id}")  # ✅
```

**Why:** Each community gets its own root namespace. The `/communities/` prefix was never implemented and breaks the pattern.

### ❌ Mistake 2: Missing trailing slash on config endpoint

The config endpoint must handle both `/hed` and `/hed/` due to FastAPI redirects.

**Wrong:**
```python
@router.get("")  # ❌ Only matches /hed (redirects /hed/ → /hed)
```

**Right:**
```python
@router.get("/")  # ✅ Matches both /hed and /hed/ correctly
```

### ❌ Mistake 3: Hardcoding model fallbacks

**Wrong:**
```javascript
// Widget code
if (!data.default_model) {
  communityDefaultModel = 'openai/gpt-oss-120b';  // ❌ Hardcoded
}
```

**Right:**
```javascript
if (!data.default_model) {
  console.error('Default model not configured');
  showError('Community configuration incomplete');  // ✅ Show error
  return;
}
```

**Why:** If the config endpoint fails or doesn't return a model, that's a real error that needs to be surfaced, not silently worked around with a hardcoded fallback.

### ❌ Mistake 4: Wrong provider routing format

**Wrong:**
```python
model_kwargs["provider"] = {"only": [provider]}  # ❌ OpenRouter rejects this
```

**Right:**
```python
model_kwargs["provider"] = {"order": [provider]}  # ✅ OpenRouter accepts this
```

**Why:** OpenRouter's API specifically requires `{"order": [...]}` not `{"only": [...]}`. Using the wrong field causes "No allowed providers are available" errors.

## Testing the API Structure

```bash
# Note: Examples use dev port 38529. Default prod port is 38528.
# Adjust port based on your deployment.

# 1. Test config endpoint
curl http://localhost:38529/hed/ | jq .
# Should return: id, name, description, default_model, default_model_provider

# 2. Test ask endpoint
curl -X POST http://localhost:38529/hed/ask \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is HED?"}'

# 3. Test with BYOK
curl -X POST http://localhost:38529/hed/ask \
  -H "X-OpenRouter-Key: sk-or-v1-..." \
  -H "Content-Type: application/json" \
  -d '{"question": "What is HED?"}'

# 4. Test custom model (requires BYOK)
curl -X POST http://localhost:38529/hed/ask \
  -H "X-OpenRouter-Key: sk-or-v1-..." \
  -H "Content-Type: application/json" \
  -d '{"question": "What is HED?", "model": "anthropic/claude-opus-4"}'
```

## Adding a New Community

To add a new community, you only need to update the YAML config:

```yaml
# src/assistants/my-community/config.yaml
id: my-community
name: My Community
description: Short description

# Optional: override platform defaults
default_model: "anthropic/claude-3.5-sonnet"
default_model_provider: null

# Optional: dedicated API key
openrouter_api_key_env_var: "OPENROUTER_API_KEY_MY_COMMUNITY"

# Optional: widget origins
cors_origins:
  - https://my-community.org
  - https://*.my-community.dev
```

The API routes are automatically created at startup. No code changes needed.

## Key Files

| File | Purpose |
|------|---------|
| `src/api/main.py` | Registers all community routers at startup |
| `src/api/routers/community.py` | Factory function to create router for one community |
| `src/api/config.py` | Platform-level defaults (model, provider) |
| `src/assistants/{community}/config.yaml` | Per-community configuration |
| `src/core/services/litellm_llm.py` | LLM creation with provider routing |

## Summary

**The OSA API follows a simple pattern:**
1. Each community gets routes at `/{community_id}/...`
2. Routes are created dynamically from registry at startup
3. The `GET /{community_id}/` endpoint returns public configuration
4. Model selection: user request > community default > platform default
5. Provider routing uses `{"order": [provider]}` format for OpenRouter

**When adding features, maintain this structure:**
- Don't add new route prefixes (`/communities/`, `/api/`, etc.)
- Don't hardcode model fallbacks in frontend or backend
- Always test with the actual OpenRouter API to verify provider formats
- Keep configuration in YAML, not code

## Provider Routing and the Bedrock Models

The section above predates the move to the Claude Platform on AWS (ADR 0004) and the Bedrock models
(ADR 0014); the code is the reference. What a request goes through today, in
`src/api/routers/community.py` (`_route_request`):

1. `_resolve_provider` picks the provider by whose key pays: a caller's own key (BYOK) for the
   provider its header names, else (on an authorized origin) the community's key, else the platform's
   Anthropic key.
2. `_select_model` picks the model. On the Anthropic path the requested or default id must be an
   offered model (`OFFERED_MODELS` in `src/core/services/anthropic_models.py`), else 400.
3. If that model is one of the Bedrock-served ones (`BEDROCK_MODELS`), `_bedrock_choice` moves the
   request onto the Bedrock provider on the **platform's** Bedrock key: a BYOK Anthropic caller is
   refused (403), and a deployment with no Bedrock key answers 400 and does not list the models in
   `offered_models` (which also needs the platform's Anthropic key, since routing starts from the
   Anthropic provider). A community's Anthropic key (`anthropic_api_key_env_var`) does not pay for
   Bedrock either: the platform does. When the model came from a community's `default_model`
   rather than the request, and the caller cannot have it, the request runs the deployment's Claude
   default instead (`_claude_fallback`: `DEFAULT_MODEL`, the Haiku class unless the deployment changes it)
   and logs an error naming the community (`_log_bedrock_fallback`), or a warning when the only cause
   is the caller's own Anthropic key (the CLI); naming the model gets the 403 or 400. With no
   `ANTHROPIC_API_KEY` a platform-funded request is not on the Anthropic provider at all: it goes to
   OpenRouter's slug for the model, or fails with HTTP 500 when there is no OpenRouter key either.
4. On Bedrock, `create_bedrock_llm` (`src/core/services/bedrock_llm.py`) builds the chat model. Tools return
   `search_result` blocks as on the Anthropic path; the model layer turns them into `[src:N]` tags
   and the model's tags back into citations (`src/core/services/tagged_citations.py`).
5. On OpenRouter (a caller's or a community's own key), `create_openrouter_llm`
   (`src/core/services/litellm_llm.py`) builds a `TaggedCitationChatLiteLLM` (`litellm_chat.py`):
   the same tagged citations as Bedrock, cache breakpoints on the system prompt and the last
   message (Anthropic slugs only), and the provider's own cache and reasoning counts on
   `usage_metadata`. Each call carries its own key and writes none to LiteLLM's module, which every
   request in the process shares.

Per-model prompt notes come from `BedrockModel.prompt_addendum` and a community's
`model_instructions:` config section.
