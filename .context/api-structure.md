# API Structure and Community Routing

This document describes how the OSA API is structured around communities, how routes are dynamically created, and implementation details for maintaining the API.

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
  "model": "qwen/qwen3-235b-a22b-2507"  // Optional, requires BYOK if custom
}
```

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
