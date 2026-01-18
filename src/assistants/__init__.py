"""OSA Assistants package.

Self-contained assistant modules with auto-registration.

Assistants can be registered in two ways:
1. **YAML config**: Define community in registries/communities.yaml
2. **Decorator**: Use @registry.register decorator in Python code

The discover_assistants() function loads both YAML and Python registrations.

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

import importlib
import logging
import sys
from pathlib import Path

from src.assistants.registry import AssistantInfo, AssistantRegistry, registry

logger = logging.getLogger(__name__)

__all__ = [
    "registry",
    "AssistantRegistry",
    "AssistantInfo",
    "discover_assistants",
    "get_communities_yaml_path",
]

# Default path to communities.yaml (relative to project root)
DEFAULT_COMMUNITIES_YAML = "registries/communities.yaml"


def get_communities_yaml_path() -> Path:
    """Get the path to communities.yaml.

    Searches for the file in the following order:
    1. Relative to project root (registries/communities.yaml)
    2. Relative to current working directory

    Returns:
        Path to communities.yaml file.
    """
    # Try relative to project root (3 levels up from this file)
    project_root = Path(__file__).parent.parent.parent
    yaml_path = project_root / DEFAULT_COMMUNITIES_YAML
    if yaml_path.exists():
        return yaml_path

    # Fallback to current working directory
    return Path.cwd() / DEFAULT_COMMUNITIES_YAML


def discover_assistants(yaml_path: Path | str | None = None) -> list[str]:
    """Auto-discover and register all assistants.

    This function:
    1. Loads community configurations from YAML
    2. Imports Python packages to trigger @registry.register decorators

    The order ensures YAML configs are available before Python factories
    are registered, allowing proper merging.

    Args:
        yaml_path: Optional custom path to communities.yaml.
                   If None, uses get_communities_yaml_path().

    Returns:
        List of discovered assistant module names.

    Note:
        Call this once at application startup (e.g., in api/main.py).
    """
    # Step 1: Load YAML configurations
    if yaml_path is None:
        yaml_path = get_communities_yaml_path()

    yaml_loaded = registry.load_from_yaml(yaml_path)
    logger.debug("Loaded %d communities from YAML", len(yaml_loaded))

    # Step 2: Discover and import Python packages
    discovered: list[str] = []
    assistants_dir = Path(__file__).parent

    for subdir in sorted(assistants_dir.iterdir()):
        # Skip non-directories and private/special directories
        if not subdir.is_dir():
            continue
        if subdir.name.startswith("_"):
            continue
        if not (subdir / "__init__.py").exists():
            continue

        module_name = f"src.assistants.{subdir.name}"
        try:
            # Import or reload the module to ensure decorators run
            # This is needed when the registry was cleared but modules are cached
            if module_name in sys.modules:
                # Module already imported - reload to re-run decorators
                importlib.reload(sys.modules[module_name])
                logger.debug("Reloaded assistant module: %s", subdir.name)
            else:
                importlib.import_module(module_name)
                logger.debug("Imported assistant module: %s", subdir.name)
            discovered.append(subdir.name)
        except Exception:
            # Use exception() to preserve full traceback for debugging
            logger.exception("Failed to load assistant %s", module_name)

    logger.info(
        "Discovered %d assistants: %s",
        len(discovered),
        ", ".join(discovered) if discovered else "(none)",
    )
    return discovered
