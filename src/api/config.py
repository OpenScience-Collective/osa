"""Configuration management for the OSA API."""

import logging
import os
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.services.anthropic_models import DEFAULT_MODEL
from src.version import __version__

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # A rejected key must not be echoed back in the validation error, which ends up
        # in startup logs and tracebacks.
        hide_input_in_errors=True,
    )

    # API Settings
    app_name: str = Field(default="Open Science Assistant", description="Application name")
    app_version: str = Field(default=__version__, description="Application version")
    git_commit_sha: str | None = Field(
        default=None,
        description="Git commit SHA (set via GIT_COMMIT_SHA env var during deployment)",
    )
    debug: bool = Field(default=False, description="Enable debug mode")

    # Server Settings
    # Port allocation: HEDit prod=38427, HEDit dev=38428, OSA prod=38528, OSA dev=38529
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=38528, description="Server port")
    root_path: str = Field(
        default="",
        description="Root path for mounting behind reverse proxy (e.g., '/osa')",
    )

    # CORS Settings
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:3000",
            "http://localhost:8080",
            "http://localhost:8888",
            "https://osc.earth",
            "https://www.osc.earth",
            "https://docs.osc.earth",
            "https://openscience-collective.github.io",
        ],
        description="Allowed CORS origins",
    )

    # API Key Settings (for server-provided resources)
    api_keys: str | None = Field(
        default=None, description="Server API keys for authentication (comma-separated)"
    )
    require_api_auth: bool = Field(default=True, description="Require API key authentication")

    # Per-community admin keys for scoped dashboard access
    # Format: "community_id:key1,community_id:key2" (e.g., "hed:abc123,eeglab:xyz789")
    community_admin_keys: str | None = Field(
        default=None,
        description="Per-community admin API keys (format: community_id:key,...)",
    )

    # LLM Provider Settings (server defaults, can be overridden by BYOK)
    openrouter_api_key: str | None = Field(default=None, description="OpenRouter API key")
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    anthropic_api_key: str | None = Field(
        default=None,
        description="ANTHROPIC_API_KEY: server-mode key for the Claude Platform on AWS",
    )
    anthropic_base_url: str | None = Field(
        default=None,
        description="ANTHROPIC_BASE_URL: Claude Platform on AWS endpoint (an "
        "Anthropic-operated Messages API, not Amazon Bedrock)",
    )
    anthropic_workspace_id: str | None = Field(
        default=None,
        description="ANTHROPIC_WORKSPACE_ID: Claude Platform on AWS workspace id "
        "(format 'wrkspc_...') sent as the anthropic-workspace-id header on "
        "server-mode requests. AWS Marketplace is only the billing channel; "
        "the workspace itself is an Anthropic-operated resource.",
    )
    bedrock_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("bedrock_api_key", "aws_bearer_token_bedrock"),
        description="AWS_BEARER_TOKEN_BEDROCK: Amazon Bedrock API key that pays for the "
        "non-Anthropic models (GPT-6 Luna, Qwen3 Next, gpt-oss-120b) on the platform",
    )

    bedrock_region: str = Field(
        default="us-east-2",
        description="BEDROCK_REGION: AWS region the Bedrock models are called in (default "
        "us-east-2, Ohio). A model that names a region of its own is called there instead "
        "(Qwen3 Next is pinned to us-east-1)",
    )
    bedrock_max_output_tokens: int = Field(
        default=16000,
        description="Default max_tokens for Bedrock model requests. Reasoning counts toward "
        "it, and GPT-6 Luna at high effort can spend thousands of tokens thinking",
    )
    anthropic_max_output_tokens: int = Field(
        default=8000,
        description="Default max_tokens for Anthropic Claude Platform requests",
    )
    anthropic_cache_ttl: str = Field(
        default="5m",
        description="Default prompt-cache lifetime for Anthropic requests ('5m' or '1h')",
    )

    # Model Configuration
    # Phase 2 (issue #362) routes platform/community requests to the Claude
    # Platform on AWS by default: default_model/test_model are first-party
    # Anthropic ids (src.core.services.anthropic_models.OFFERED_MODELS), not
    # OpenRouter's creator/model-name format. default_model_provider and
    # test_model_provider are OpenRouter-only routing hints (see
    # src.core.services.litellm_llm.create_openrouter_llm's `provider` arg):
    # they are ignored on the Anthropic path (src.api.routers.community's
    # _select_model) and only apply when a BYOK or community-funded
    # OpenRouter key selects that provider. See .context/research.md for
    # benchmark details behind the OpenRouter defaults.
    default_model: str = Field(
        default=DEFAULT_MODEL,
        description="Default model for the Claude Platform on AWS path",
    )
    default_model_provider: str | None = Field(
        default="DeepInfra/FP8",
        description="OpenRouter-BYOK-only: provider for routing (e.g., DeepInfra/FP8)",
    )
    test_model: str = Field(
        default=DEFAULT_MODEL,
        description="Default model for testing",
    )
    test_model_provider: str | None = Field(
        default="DeepInfra/FP8",
        description="OpenRouter-BYOK-only: provider for test model routing",
    )
    llm_temperature: float = Field(
        default=0.1,
        description="Default temperature for LLM responses (0.0 - 1.0)",
    )

    # Observability
    langfuse_public_key: str | None = Field(default=None, description="LangFuse public key")
    langfuse_secret_key: str | None = Field(default=None, description="LangFuse secret key")
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com", description="LangFuse host URL"
    )

    # Database
    database_url: str | None = Field(
        default=None, description="PostgreSQL connection URL for state persistence"
    )

    # Knowledge Database Settings
    data_dir: str | None = Field(
        default=None,
        description="Data directory for knowledge database (default: platform-specific user data dir)",
    )

    # Knowledge Sync API Keys (all optional, for higher rate limits)
    github_token: str | None = Field(
        default=None,
        description="GitHub token for REST API (optional, higher rate limits for sync)",
    )
    openalex_api_key: str | None = Field(
        default=None,
        description="OpenAlex API key (optional, for polite pool / higher rate limits)",
    )
    openalex_email: str | None = Field(
        default=None, description="Email for OpenAlex polite pool (optional, used if no API key)"
    )
    semantic_scholar_api_key: str | None = Field(
        default=None, description="Semantic Scholar API key (optional, for higher rate limits)"
    )
    pubmed_api_key: str | None = Field(
        default=None, description="PubMed/NCBI API key (optional, for higher rate limits)"
    )

    # Knowledge Sync Scheduling
    # Master switch only; per-community schedules are defined in each community's config.yaml
    # Empty databases are automatically seeded on startup when sync is enabled
    sync_enabled: bool = Field(default=True, description="Enable automated knowledge sync")

    @field_validator("bedrock_api_key", mode="before")
    @classmethod
    def _clean_bedrock_api_key(cls, value: object) -> object:
        """Trim the key, treat a blank one as unset, and refuse one with whitespace inside.

        A trailing newline or carriage return (an env file with CRLF line endings, a
        secret pasted with its line break) makes the HTTP client refuse the
        Authorization header, and the client's error names the header value, key
        included. A key with whitespace inside is not a key. The error says which
        variable is wrong and never shows the value.
        """
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if not cleaned:
            return None
        if any(char.isspace() or ord(char) < 32 for char in cleaned):
            raise ValueError("AWS_BEARER_TOKEN_BEDROCK contains whitespace or control characters")
        return cleaned

    @model_validator(mode="after")
    def validate_workspace_id_with_base_url(self) -> "Settings":
        """Fail fast if ANTHROPIC_BASE_URL is set without ANTHROPIC_WORKSPACE_ID.

        ``create_anthropic_llm`` (src/core/services/anthropic_llm.py) already
        enforces this at request time on the server-key path, but only there
        -- a misconfigured env var otherwise passes ``uv sync``, startup, and
        ``/health``, and only surfaces as an opaque error on the first real
        request that falls through to the platform key, which is the
        majority of traffic for most communities. Checking it here moves the
        failure to process startup instead.
        """
        if self.anthropic_base_url and not self.anthropic_workspace_id:
            raise ValueError(
                "ANTHROPIC_BASE_URL is set but ANTHROPIC_WORKSPACE_ID is not; the "
                "Claude Platform on AWS endpoint rejects requests without the "
                "anthropic-workspace-id header"
            )
        return self

    def parse_admin_keys(self) -> set[str]:
        """Parse API_KEYS into a set of valid admin keys.

        Returns:
            Set of valid API key strings.
        """
        if not self.api_keys:
            return set()
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    def parse_community_admin_keys(self) -> dict[str, set[str]]:
        """Parse COMMUNITY_ADMIN_KEYS into {community_id: {keys}} mapping.

        Format: "community_id:key1,community_id:key2"
        Multiple keys per community are supported.

        Returns:
            Dict mapping community_id to set of valid API keys.
        """
        if not self.community_admin_keys:
            return {}
        result: dict[str, set[str]] = {}
        for entry in self.community_admin_keys.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                logger.error("Skipping malformed community_admin_keys entry (no ':'): %r", entry)
                continue
            community_id, key = entry.split(":", 1)
            community_id = community_id.strip()
            key = key.strip()
            if community_id and key:
                result.setdefault(community_id, set()).add(key)
        return result


# Environment variables that used to set something and are now ignored (Settings ignores
# unknown ones), with what replaced each. A server that still exports one would otherwise
# change behavior without a word: an operator who lowered the old Haiku thinking budget to
# save cost now gets Haiku 5.5's adaptive thinking at the default level.
RETIRED_ENV_VARS: dict[str, str] = {
    "ANTHROPIC_THINKING_BUDGET_TOKENS": (
        "Claude Haiku no longer thinks with a token budget (Haiku 5.5 thinks adaptively); "
        "reasoning_effort in the community's config.yaml sets how much (none, low, "
        "medium or high; high when unset)"
    ),
}


def _warn_retired_env_vars() -> None:
    """Log a warning for each retired environment variable that is still set.

    Reads the process environment only. ``Settings`` also reads a ``.env`` file, and ignores
    names it does not know, so a retired variable that appears only in ``.env`` is neither
    used nor reported here; the deployment's environment file is where to look for it.
    """
    present = {name.upper() for name in os.environ}
    for name, replacement in RETIRED_ENV_VARS.items():
        if name in present:
            logger.warning("%s is set but no longer used: %s", name, replacement)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    _warn_retired_env_vars()
    return Settings()
