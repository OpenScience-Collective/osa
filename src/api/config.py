"""Configuration management for the OSA API."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.version import __version__


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
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
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key")

    # Model Configuration
    # OpenRouter model format: creator/model-name (e.g., openai/gpt-oss-120b, qwen/qwen3-235b-a22b-2507)
    # Provider is separate - specifies where the model runs (e.g., DeepInfra/FP8 for Qwen)
    # See .context/research.md for benchmark details
    default_model: str = Field(
        default="qwen/qwen3-235b-a22b-2507",
        description="Default model (OpenRouter format: creator/model-name)",
    )
    default_model_provider: str | None = Field(
        default="DeepInfra/FP8",
        description="Provider for routing (e.g., DeepInfra/FP8 for optimized inference)",
    )
    test_model: str = Field(
        default="qwen/qwen3-235b-a22b-2507",
        description="Model for testing (OpenRouter format: creator/model-name)",
    )
    test_model_provider: str | None = Field(
        default="DeepInfra/FP8",
        description="Provider for test model routing",
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
    semantic_scholar_api_key: str | None = Field(
        default=None, description="Semantic Scholar API key (optional, for higher rate limits)"
    )
    pubmed_api_key: str | None = Field(
        default=None, description="PubMed/NCBI API key (optional, for higher rate limits)"
    )

    # Knowledge Sync Scheduling
    sync_enabled: bool = Field(default=True, description="Enable automated knowledge sync")
    sync_github_cron: str = Field(
        default="0 2 * * *",
        description="Cron schedule for GitHub sync (default: daily at 2am UTC)",
    )
    sync_papers_cron: str = Field(
        default="0 3 * * 0",
        description="Cron schedule for papers sync (default: weekly Sunday at 3am UTC)",
    )

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
            if ":" not in entry:
                continue
            community_id, key = entry.split(":", 1)
            community_id = community_id.strip()
            key = key.strip()
            if community_id and key:
                result.setdefault(community_id, set()).add(key)
        return result


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
