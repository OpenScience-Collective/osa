"""OSA Assistants package.

Self-contained assistant modules with auto-registration.

Each community has its own directory with:
- config.yaml: All configuration (docs, system prompt, repos, citations)
- tools.py: Specialized Python tools (optional)

The discover_assistants() function scans for config.yaml files and registers
each community automatically.

Example:
    ```python
    from src.assistants import registry

    # List available assistants
    for assistant in registry.list_available():
        print(f"{assistant.id}: {assistant.description}")

    # Create an assistant
    assistant = registry.create_assistant("hed", model=llm)
    ```
"""

import logging
import os
from pathlib import Path

from src.assistants.registry import AssistantInfo, AssistantRegistry, registry

logger = logging.getLogger(__name__)

__all__ = [
    "registry",
    "AssistantRegistry",
    "AssistantInfo",
    "discover_assistants",
]


def discover_assistants() -> list[str]:
    """Auto-discover and register all assistants from per-community config.yaml files.

    Scans src/assistants/*/config.yaml for community configurations.
    Each valid config is loaded and registered with the registry.

    Returns:
        List of discovered community IDs.

    Raises:
        RuntimeError: If any config.yaml files fail to load.

    Note:
        Call this once at application startup (e.g., in api/main.py).
        If any configs fail to load, all failures are collected and reported
        together to help identify all broken configs at once.
    """
    from src.core.config.community import CommunityConfig

    discovered: list[str] = []
    failures: list[tuple[Path, Exception]] = []
    assistants_dir = Path(__file__).parent

    for subdir in sorted(assistants_dir.iterdir()):
        # Skip non-directories and private/special directories
        if not subdir.is_dir():
            continue
        if subdir.name.startswith("_"):
            continue

        # Look for config.yaml in each subdirectory
        config_path = subdir / "config.yaml"
        if not config_path.exists():
            continue

        try:
            config = CommunityConfig.from_yaml(config_path)
            registry.register_from_config(config)
            discovered.append(config.id)
            logger.info("Discovered assistant: %s from %s", config.id, config_path)

            # Warn once at startup; detailed health surfaced via API endpoints.
            # See: https://github.com/OpenScience-Collective/osa/issues/220
            if config.openrouter_api_key_env_var and not os.getenv(
                config.openrouter_api_key_env_var
            ):
                logger.warning(
                    "Community '%s': env var '%s' not set, will use platform key",
                    config.id,
                    config.openrouter_api_key_env_var,
                    extra={
                        "community_id": config.id,
                        "env_var": config.openrouter_api_key_env_var,
                    },
                )
        except KeyboardInterrupt:
            # Never catch user interruption
            raise
        except SystemExit:
            # Never catch intentional exits
            raise
        except Exception as e:
            logger.exception("Failed to load config from %s", config_path)
            failures.append((config_path, e))

    # Report summary
    logger.info(
        "Discovered %d assistants: %s",
        len(discovered),
        ", ".join(discovered) if discovered else "(none)",
    )

    # Fail fast if any configs failed to load
    if failures:
        error_details = "\n".join(
            f"  - {path.relative_to(assistants_dir.parent)}: {type(e).__name__}: {e}"
            for path, e in failures
        )
        raise RuntimeError(
            f"Failed to load {len(failures)} community config(s):\n{error_details}\n\n"
            "All community configs must be valid for the application to start. "
            "Fix the above errors and restart."
        )

    return discovered
