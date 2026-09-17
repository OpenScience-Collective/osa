"""CLI configuration management.

Config is split into two files for security:
- config.yaml: Non-sensitive settings (API URL, output format, etc.)
- credentials.yaml: API keys (stored with restricted permissions)
"""

import contextlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Literal

import yaml
from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

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

    format: Literal["rich", "json", "plain"] = Field(default="rich", description="Output format")
    verbose: bool = Field(default=False, description="Verbose output")
    streaming: bool = Field(default=True, description="Stream responses")


class CLIConfig(BaseModel):
    """Complete CLI configuration (stored in config.yaml)."""

    api: APIConfig = Field(default_factory=APIConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


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
    except (yaml.YAMLError, OSError, TypeError, ValidationError) as e:
        logger.warning("Failed to load config from %s, using defaults: %s", CONFIG_FILE, e)
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
    except (yaml.YAMLError, OSError, TypeError, ValidationError) as e:
        logger.warning(
            "Failed to load credentials from %s, no API keys available: %s",
            CREDENTIALS_FILE,
            e,
        )
        return CredentialsConfig()


def save_credentials(creds: CredentialsConfig) -> None:
    """Save credentials to credentials.yaml with restricted permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in creds.model_dump().items() if v is not None}
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)

    # Write with restricted permissions from the start (avoid TOCTOU race)
    try:
        fd = os.open(CREDENTIALS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, content.encode())
        finally:
            os.close(fd)
    except OSError as e:
        # Fallback for platforms that don't support os.open mode (e.g., Windows)
        logger.warning(
            "Secure file write failed (%s), falling back to standard write for %s",
            e,
            CREDENTIALS_FILE,
        )
        CREDENTIALS_FILE.write_text(content)
        try:
            os.chmod(CREDENTIALS_FILE, 0o600)
        except OSError as chmod_err:
            logger.warning(
                "Could not restrict permissions on %s: %s. "
                "Credentials file may be readable by other users.",
                CREDENTIALS_FILE,
                chmod_err,
            )


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


def classify_api_key(key: str) -> Literal["anthropic", "openrouter"]:
    """Infer which provider a BYOK key belongs to from the key's own prefix.

    Same motivation as the widget's ``inferKeyProvider``: the user holds one
    key and should not have to declare what kind it is, because the key
    already says so. Anthropic keys start with ``sk-ant-``; OpenRouter's start
    with ``sk-or-``.

    The rule here is looser than the widget's on purpose. The widget validates
    the full key shape (``/^sk-ant-[a-zA-Z0-9_-]{80,}$/i``) and returns null
    for anything it does not recognize, because it is gating a text field a
    person just typed. The CLI instead routes anything unrecognized to
    OpenRouter: every key saved before the Claude Platform migration was an
    OpenRouter key stored without a prefix check, so a stricter rule would
    strand a working config. Rejecting malformed keys is the server's job
    either way, and a wrong guess costs one clear 401 rather than a silently
    dropped credential.

    Args:
        key: A non-empty API key.

    Returns:
        "anthropic" or "openrouter".
    """
    return "anthropic" if key.startswith("sk-ant-") else "openrouter"


def get_effective_byok_keys(api_key: str | None = None) -> tuple[str | None, str | None]:
    """Resolve the OpenRouter and Anthropic keys to use for one invocation.

    An explicit ``-k/--api-key`` wins outright and selects its own provider by
    prefix, so the other slot is left empty. That matters because
    ``OSAClient`` prefers Anthropic whenever both are present: without this,
    an exported ANTHROPIC_API_KEY would silently override the key the user
    just typed on the command line.

    With no flag, each provider resolves independently: its env var first,
    then credentials.yaml.

    Args:
        api_key: Value of the ``-k/--api-key`` flag, if given. May be either
            provider's key.

    Returns:
        Tuple of (openrouter_key, anthropic_key), either or both None.
    """
    if api_key:
        if classify_api_key(api_key) == "anthropic":
            return None, api_key
        return api_key, None

    creds = load_credentials()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or creds.openrouter_api_key
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or creds.anthropic_api_key
    return openrouter_key, anthropic_key


# --- Legacy migration ---


def _migrate_legacy_config() -> CLIConfig:
    """Migrate from legacy config.json to new YAML format."""
    try:
        with LEGACY_CONFIG_FILE.open() as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to migrate legacy config from %s: %s", LEGACY_CONFIG_FILE, e)
        return CLIConfig()

    # Build new config from legacy fields
    config = CLIConfig()
    old_default_url = "http://localhost:38528"
    if "api_url" in data and data["api_url"] and data["api_url"] != old_default_url:
        config.api.url = data["api_url"]
    if "output_format" in data:
        config.output.format = data["output_format"]
    if "verbose" in data:
        config.output.verbose = data["verbose"]

    # Migrate credentials (field names match between legacy and new config)
    cred_fields = ("openrouter_api_key", "openai_api_key", "anthropic_api_key")
    cred_data = {k: data[k] for k in cred_fields if data.get(k)}
    creds = CredentialsConfig(**cred_data)

    # Save in new format
    save_config(config)
    if cred_data:
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
