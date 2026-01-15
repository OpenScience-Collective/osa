"""OSA Assistants package.

Self-contained assistant modules with auto-registration.

Each assistant is a subpackage that registers itself when imported.
The registry provides factory access for creating assistant instances.

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
    """Auto-discover and import all assistant packages.

    Scans the assistants directory for subpackages (directories with __init__.py)
    and imports them, triggering their @registry.register decorators.

    Returns:
        List of discovered assistant module names.

    Note:
        Call this once at application startup (e.g., in api/main.py).
    """
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
            importlib.import_module(module_name)
            discovered.append(subdir.name)
            logger.debug("Discovered assistant: %s", subdir.name)
        except Exception:
            # Use exception() to preserve full traceback for debugging
            logger.exception("Failed to load assistant %s", module_name)

    logger.info(
        "Discovered %d assistants: %s",
        len(discovered),
        ", ".join(discovered) if discovered else "(none)",
    )
    return discovered
