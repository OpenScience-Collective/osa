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
        default=["http://localhost:3000", "http://localhost:8080", "http://localhost:8888"],
        description="Allowed CORS origins",
    )

    # API Key Settings (for server-provided resources)
    api_keys: str | None = Field(
        default=None, description="Server API keys for authentication (comma-separated)"
    )
    require_api_auth: bool = Field(default=True, description="Require API key authentication")

    # LLM Provider Settings (server defaults, can be overridden by BYOK)
    openrouter_api_key: str | None = Field(default=None, description="OpenRouter API key")
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key")

    # Model Configuration
    # OpenRouter model format: creator/model-name (e.g., openai/gpt-oss-120b, qwen/qwen3-235b-a22b-2507)
    # Provider is separate - specifies where the model runs (e.g., Cerebras for fast inference)
    # See .context/research.md for benchmark details
    default_model: str = Field(
        default="openai/gpt-oss-120b",
        description="Default model (OpenRouter format: creator/model-name)",
    )
    default_model_provider: str | None = Field(
        default="Cerebras",
        description="Provider for routing (e.g., Cerebras for fast inference)",
    )
    test_model: str = Field(
        default="openai/gpt-oss-120b",
        description="Model for testing (OpenRouter format: creator/model-name)",
    )
    test_model_provider: str | None = Field(
        default="Cerebras",
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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
