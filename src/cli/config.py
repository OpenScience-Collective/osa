"""CLI configuration management.

Config is split into two files for security:
- config.yaml: Non-sensitive settings (API URL, output format, etc.)
- credentials.yaml: API keys (stored with restricted permissions)
"""

import contextlib
import json
import os
import uuid
from pathlib import Path

import yaml
from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, Field

# Paths
CONFIG_DIR = Path(user_config_dir("osa", appauthor=False, ensure_exists=True))
CONFIG_FILE = CONFIG_DIR / "config.yaml"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.yaml"
USER_ID_FILE = CONFIG_DIR / "user_id"
FIRST_RUN_FILE = CONFIG_DIR / ".first_run"

# Legacy path (for migration)
LEGACY_CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_API_URL = "https://api.osc.earth/osa"


# --- Config models ---


class APIConfig(BaseModel):
    """API endpoint configuration."""

    url: str = Field(default=DEFAULT_API_URL, description="OSA API URL")


class OutputConfig(BaseModel):
    """Output formatting preferences."""

    format: str = Field(default="rich", description="Output format: rich, json, plain")
    verbose: bool = Field(default=False, description="Verbose output")
    streaming: bool = Field(default=True, description="Stream responses")


class ExecutionConfig(BaseModel):
    """Execution mode configuration."""

    mode: str = Field(default="api", description="Execution mode: api or standalone")


class CLIConfig(BaseModel):
    """Complete CLI configuration (stored in config.yaml)."""

    api: APIConfig = Field(default_factory=APIConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)


class CredentialsConfig(BaseModel):
    """Credentials stored separately with restricted permissions."""

    openrouter_api_key: str | None = Field(default=None, description="OpenRouter API key")
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key")


# --- Config I/O ---


def load_config() -> CLIConfig:
    """Load CLI configuration from config.yaml.

    Migrates from legacy config.json if needed.
    """
    # Migrate from legacy JSON if new YAML doesn't exist yet
    if not CONFIG_FILE.exists() and LEGACY_CONFIG_FILE.exists():
        return _migrate_legacy_config()

    if not CONFIG_FILE.exists():
        return CLIConfig()

    try:
        data = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        return CLIConfig(**data)
    except (yaml.YAMLError, OSError, TypeError):
        return CLIConfig()


def save_config(config: CLIConfig) -> None:
    """Save CLI configuration to config.yaml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = config.model_dump()
    CONFIG_FILE.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def load_credentials() -> CredentialsConfig:
    """Load credentials from credentials.yaml."""
    if not CREDENTIALS_FILE.exists():
        return CredentialsConfig()

    try:
        data = yaml.safe_load(CREDENTIALS_FILE.read_text()) or {}
        return CredentialsConfig(**data)
    except (yaml.YAMLError, OSError, TypeError):
        return CredentialsConfig()


def save_credentials(creds: CredentialsConfig) -> None:
    """Save credentials to credentials.yaml with restricted permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in creds.model_dump().items() if v is not None}
    CREDENTIALS_FILE.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    with contextlib.suppress(OSError, AttributeError):
        os.chmod(CREDENTIALS_FILE, 0o600)


def get_effective_config(
    api_key: str | None = None,
    api_url: str | None = None,
) -> tuple[CLIConfig, str | None]:
    """Merge saved config with per-invocation overrides.

    API key priority: CLI flag > OPENROUTER_API_KEY env > credentials.yaml

    Returns:
        Tuple of (config, effective_api_key)
    """
    config = load_config()
    creds = load_credentials()

    # Override API URL if provided
    if api_url:
        config.api.url = api_url

    # Resolve API key with priority chain
    effective_key = api_key or os.environ.get("OPENROUTER_API_KEY") or creds.openrouter_api_key

    return config, effective_key


# --- Legacy migration ---


def _migrate_legacy_config() -> CLIConfig:
    """Migrate from legacy config.json to new YAML format."""
    try:
        with LEGACY_CONFIG_FILE.open() as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return CLIConfig()

    # Build new config from legacy fields
    config = CLIConfig()
    if "api_url" in data and data["api_url"]:
        config.api.url = data["api_url"]
    if "output_format" in data:
        config.output.format = data["output_format"]
    if "verbose" in data:
        config.output.verbose = data["verbose"]

    # Migrate credentials
    creds = CredentialsConfig()
    if data.get("openrouter_api_key"):
        creds.openrouter_api_key = data["openrouter_api_key"]
    if data.get("openai_api_key"):
        creds.openai_api_key = data["openai_api_key"]
    if data.get("anthropic_api_key"):
        creds.anthropic_api_key = data["anthropic_api_key"]

    # Save in new format
    save_config(config)
    if creds.openrouter_api_key or creds.openai_api_key or creds.anthropic_api_key:
        save_credentials(creds)

    return config


# --- Data directory ---


def get_data_dir() -> Path:
    """Get the OSA data directory for storing sessions, history, knowledge database, etc.

    Respects DATA_DIR environment variable for Docker deployments.
    """
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        path = Path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(user_data_dir("osa", ensure_exists=True))


# --- User ID ---


def get_user_id() -> str:
    """Get or generate a stable user ID for cache optimization.

    Used by OpenRouter for sticky cache routing to reduce costs.
    NOT used for telemetry. Generated once and persisted.

    Returns:
        16-character hexadecimal user ID
    """
    if USER_ID_FILE.exists():
        try:
            user_id = USER_ID_FILE.read_text().strip()
            if len(user_id) == 16 and all(c in "0123456789abcdef" for c in user_id):
                return user_id
        except (OSError, UnicodeDecodeError):
            pass

    user_id = uuid.uuid4().hex[:16]

    with contextlib.suppress(OSError):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        USER_ID_FILE.write_text(user_id)
        with contextlib.suppress(OSError, AttributeError):
            os.chmod(USER_ID_FILE, 0o600)

    return user_id


# --- First run detection ---


def is_first_run() -> bool:
    """Check if this is the first time the CLI is being run."""
    return not FIRST_RUN_FILE.exists()


def mark_first_run_complete() -> None:
    """Mark that the first run setup has been completed."""
    with contextlib.suppress(OSError):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        FIRST_RUN_FILE.touch()
