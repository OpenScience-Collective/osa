"""CLI configuration management using platformdirs."""

import json
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, Field


class CLIConfig(BaseModel):
    """CLI configuration stored in user config directory."""

    api_url: str = Field(default="http://localhost:38428", description="OSA API URL")
    api_key: str | None = Field(default=None, description="API key for authentication")

    # BYOK settings - users can provide their own LLM API keys
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key")
    openrouter_api_key: str | None = Field(default=None, description="OpenRouter API key")

    # Output preferences
    output_format: str = Field(default="rich", description="Output format: rich, json, plain")
    verbose: bool = Field(default=False, description="Enable verbose output")


def get_config_dir() -> Path:
    """Get the OSA configuration directory."""
    return Path(user_config_dir("osa", ensure_exists=True))


def get_data_dir() -> Path:
    """Get the OSA data directory for storing sessions, history, etc."""
    return Path(user_data_dir("osa", ensure_exists=True))


def get_config_path() -> Path:
    """Get the path to the CLI configuration file."""
    return get_config_dir() / "config.json"


def load_config() -> CLIConfig:
    """Load CLI configuration from file.

    Returns default config if file doesn't exist.
    """
    config_path = get_config_path()

    if not config_path.exists():
        return CLIConfig()

    try:
        with config_path.open() as f:
            data = json.load(f)
        return CLIConfig(**data)
    except (json.JSONDecodeError, OSError):
        # Return defaults on any error
        return CLIConfig()


def save_config(config: CLIConfig) -> None:
    """Save CLI configuration to file."""
    config_path = get_config_path()

    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with config_path.open("w") as f:
        json.dump(config.model_dump(), f, indent=2)


def update_config(**kwargs: str | bool | None) -> CLIConfig:
    """Update CLI configuration with new values.

    Only updates fields that are explicitly provided (not None).
    Returns the updated configuration.
    """
    config = load_config()

    for key, value in kwargs.items():
        if value is not None and hasattr(config, key):
            setattr(config, key, value)

    save_config(config)
    return config
