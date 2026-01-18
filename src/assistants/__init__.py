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

    Note:
        Call this once at application startup (e.g., in api/main.py).
    """
    from src.core.config.community import CommunityConfig

    discovered: list[str] = []
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
        except Exception:
            logger.exception("Failed to load config from %s", config_path)

    logger.info(
        "Discovered %d assistants: %s",
        len(discovered),
        ", ".join(discovered) if discovered else "(none)",
    )
    return discovered
