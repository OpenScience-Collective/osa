"""Security and authentication for the OSA API."""

from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from src.api.config import Settings, get_settings

# API key header for server authentication
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Header extractors for BYOK (defined before verify_api_key which uses them)
openai_key_header = APIKeyHeader(name="X-OpenAI-API-Key", auto_error=False)
anthropic_key_header = APIKeyHeader(name="X-Anthropic-API-Key", auto_error=False)
openrouter_key_header = APIKeyHeader(name="X-OpenRouter-API-Key", auto_error=False)


async def verify_api_key(
    api_key: Annotated[str | None, Security(api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
    openai_key: Annotated[str | None, Security(openai_key_header)] = None,
    anthropic_key: Annotated[str | None, Security(anthropic_key_header)] = None,
    openrouter_key: Annotated[str | None, Security(openrouter_key_header)] = None,
) -> str | None:
    """Verify the API key if server authentication is enabled.

    Returns the API key if valid, None if auth is disabled or BYOK is used.
    Raises HTTPException if auth is enabled but key is invalid (and no BYOK).

    BYOK Policy: If user provides their own LLM API key (BYOK), they don't
    need a server API key. This allows researchers to use their own keys
    without requiring server authentication.
    """
    # If auth is not required, skip verification
    if not settings.require_api_auth:
        return None

    # If no server API keys configured, authentication is disabled
    if not settings.api_keys:
        return None

    # BYOK bypass: If user provides their own LLM key, skip server auth
    # This allows researchers to use the service with their own API keys
    if openai_key or anthropic_key or openrouter_key:
        return None

    # Parse comma-separated API keys
    valid_keys = {k.strip() for k in settings.api_keys.split(",") if k.strip()}

    # If auth is enabled, require valid API key
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required (or provide your own LLM key via BYOK headers)",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return api_key


# Dependency for routes that require authentication
RequireAuth = Annotated[str | None, Depends(verify_api_key)]


class BYOKHeaders:
    """BYOK (Bring Your Own Key) headers for LLM providers.

    Users can provide their own API keys for LLM providers.
    These override server-provided keys when present.
    """

    def __init__(
        self,
        openai_key: str | None = None,
        anthropic_key: str | None = None,
        openrouter_key: str | None = None,
    ) -> None:
        self.openai_key = openai_key
        self.anthropic_key = anthropic_key
        self.openrouter_key = openrouter_key


async def get_byok_headers(
    openai_key: Annotated[str | None, Security(openai_key_header)],
    anthropic_key: Annotated[str | None, Security(anthropic_key_header)],
    openrouter_key: Annotated[str | None, Security(openrouter_key_header)],
) -> BYOKHeaders:
    """Extract BYOK headers from request.

    These allow users to provide their own LLM API keys,
    which override server-configured keys.
    """
    return BYOKHeaders(
        openai_key=openai_key,
        anthropic_key=anthropic_key,
        openrouter_key=openrouter_key,
    )


# Dependency for extracting BYOK headers
GetBYOK = Annotated[BYOKHeaders, Depends(get_byok_headers)]


def get_llm_api_key(
    provider: str,
    byok: BYOKHeaders,
    settings: Settings,
) -> str | None:
    """Get the API key for an LLM provider.

    Priority: BYOK header > server config > None
    """
    key_mapping = {
        "openai": (byok.openai_key, settings.openai_api_key),
        "anthropic": (byok.anthropic_key, settings.anthropic_api_key),
        "openrouter": (byok.openrouter_key, settings.openrouter_api_key),
    }
    if provider not in key_mapping:
        return None
    byok_key, server_key = key_mapping[provider]
    return byok_key or server_key
