"""CLI configuration management using platformdirs."""

import contextlib
import json
import os
import uuid
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, Field


class CLIConfig(BaseModel):
    """CLI configuration stored in user config directory."""

    # Port allocation: HEDit prod=38427, HEDit dev=38428, OSA prod=38528, OSA dev=38529
    api_url: str = Field(default="http://localhost:38528", description="OSA API URL")
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


# User ID for cache optimization
USER_ID_FILE = "user_id"


def get_user_id() -> str:
    """Get or generate a stable user ID for cache optimization.

    This ID is used by OpenRouter for sticky cache routing to reduce costs.
    It is NOT used for telemetry and is only transmitted to the LLM provider
    for cache routing purposes.

    The ID is generated once and persists in the config directory.

    Returns:
        16-character hexadecimal user ID
    """
    config_dir = get_config_dir()
    user_id_path = config_dir / USER_ID_FILE

    if user_id_path.exists():
        try:
            user_id = user_id_path.read_text().strip()
            # Validate format (16 hex chars)
            if len(user_id) == 16 and all(c in "0123456789abcdef" for c in user_id):
                return user_id
        except (OSError, UnicodeDecodeError):
            pass  # File corrupted, regenerate

    # Generate new user ID
    user_id = uuid.uuid4().hex[:16]

    # Save to file
    with contextlib.suppress(OSError):
        config_dir.mkdir(parents=True, exist_ok=True)
        user_id_path.write_text(user_id)
        # Readable by user only (Unix)
        with contextlib.suppress(OSError, AttributeError):
            os.chmod(user_id_path, 0o600)

    return user_id


def get_user_id_path() -> Path:
    """Get the path to the user ID file."""
    return get_config_dir() / USER_ID_FILE
