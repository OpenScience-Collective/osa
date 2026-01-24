# API Key Authorization Design

## Problem Statement

The platform needs to control who can use platform/community API keys to prevent abuse:

1. **CLI users** should ALWAYS provide their own key (BYOK)
2. **Widget users** from authorized origins should be able to use community/platform keys
3. **Custom model requests** should ALWAYS require BYOK (regardless of origin)
4. **Communities** should be able to specify their preferred default model

## Solution Design

### 1. CORS-Based Authorization

Use the `Origin` header to determine if platform/community key fallback is allowed:

```
IF request has BYOK (X-OpenRouter-Key header):
    ✓ Use BYOK (always allowed)

ELSE IF request has Origin header AND Origin matches community's CORS origins:
    ✓ Allow fallback to community key → platform key
    ✓ This is a legitimate widget embed on an authorized page

ELSE:
    ✗ Reject with 403 "BYOK required"
    ✗ This catches CLI requests without BYOK
    ✗ This catches unauthorized web requests
```

### 2. Model Selection

Add per-community model configuration with custom model restrictions:

```
IF user specifies custom model (different from community/platform default):
    IF no BYOK:
        ✗ Reject with 403 "BYOK required for custom models"
    ELSE:
        ✓ Use custom model with BYOK

ELSE IF community has default_model configured:
    ✓ Use community's default_model

ELSE:
    ✓ Use platform's default_model (from Settings)
```

## Implementation

### 1. Add to CommunityConfig (src/core/config/community.py)

```yaml
# Example: src/assistants/hed/config.yaml
default_model: "anthropic/claude-3.5-sonnet"  # Override platform default
default_model_provider: null                   # Use default routing
```

### 2. Add to Request Bodies

```python
class AskRequest(BaseModel):
    question: str
    stream: bool = False
    page_context: PageContext | None = None
    model: str | None = None  # NEW: Optional model override

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    stream: bool = True
    model: str | None = None  # NEW: Optional model override
```

### 3. Origin Validation Helper

```python
def _is_authorized_origin(origin: str | None, community_id: str) -> bool:
    """Check if Origin header matches community's CORS origins.

    Returns:
        True if origin is in community's allowed CORS origins, False otherwise.
        Returns False if origin is None.
    """
```

### 4. API Key Selection Logic

```python
def _select_api_key(
    community_id: str,
    byok: str | None,
    origin: str | None,
) -> tuple[str, str]:  # (api_key, source)
    """Select API key based on BYOK and origin authorization.

    Raises:
        HTTPException(403): If BYOK required but not provided

    Returns:
        Tuple of (api_key, source) where source is "byok", "community", or "platform"
    """
```

### 5. Model Selection Logic

```python
def _select_model(
    community_info: AssistantInfo,
    requested_model: str | None,
    has_byok: bool,
) -> tuple[str, str | None]:  # (model, provider)
    """Select model based on community config and user request.

    Raises:
        HTTPException(403): If custom model requested without BYOK

    Returns:
        Tuple of (model, provider)
    """
```

## Security Considerations

### Why Origin-based?

1. **Origin header cannot be spoofed** by web browsers (browser-enforced)
2. **CORS middleware already validates** these origins for preflight requests
3. **Reuses existing CORS configuration** - no duplicate security config

### Attack Vectors

1. **CLI users without BYOK**: Blocked ✓ (no Origin header)
2. **Curl/API clients without BYOK**: Blocked ✓ (no Origin header or invalid origin)
3. **Widget on unauthorized site**: Blocked ✓ (origin not in CORS list)
4. **Custom model abuse**: Blocked ✓ (requires BYOK)
5. **Widget on authorized site**: Allowed ✓ (intended use case)

### Edge Cases

1. **Local development** (`http://localhost:*`): Add to platform CORS origins
2. **Browser extensions**: No Origin header → requires BYOK
3. **Mobile apps**: No Origin header → requires BYOK

## Testing

### Test Cases

1. ✓ BYOK provided → always succeeds
2. ✓ Widget from authorized origin → uses community/platform key
3. ✗ CLI without BYOK → 403 error
4. ✗ Widget from unauthorized origin → 403 error
5. ✗ Custom model without BYOK → 403 error
6. ✓ Custom model with BYOK → succeeds
7. ✓ Community default model → uses community model
8. ✓ Platform default model → uses platform model

## Documentation Updates

1. **README.md**: Document `default_model` in YAML config
2. **API docs**: Document `model` parameter in request bodies
3. **Error messages**: Clear guidance when BYOK required
4. **Community onboarding**: Explain model configuration

## Migration

### Backward Compatibility

- All existing behavior preserved (no breaking changes)
- New fields are optional
- Default behavior: use platform model with platform key
- Communities can opt-in to custom models

### Rollout

1. Deploy API changes
2. Update community configs (optional)
3. Update widget code to use new `model` parameter (optional)
4. Update CLI docs to emphasize BYOK requirement
