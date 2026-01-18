"""HED Assistant - Hierarchical Event Descriptors.

Self-contained assistant module for HED annotation, validation, and documentation.

This module provides specialized Python tools for HED that cannot be
auto-generated from YAML:
- validate_hed_string: Validate HED annotations via hedtools.org API
- suggest_hed_tags: Suggest tags using hed-lsp semantic search
- get_hed_schema_versions: List available HED schema versions

All other configuration (docs, system prompt, repos, citations) is in config.yaml.

Usage:
    ```python
    from src.assistants import registry, discover_assistants

    # Discover all assistants (loads config.yaml)
    discover_assistants()

    # Create HED assistant
    assistant = registry.create_assistant("hed", model=llm)
    ```
"""

from dataclasses import dataclass

# Re-export specialized tools for plugin loading
from .tools import get_hed_schema_versions, suggest_hed_tags, validate_hed_string


@dataclass
class PageContext:
    """Context about the page where the assistant widget is embedded."""

    url: str | None = None
    title: str | None = None


__all__ = [
    "PageContext",
    "validate_hed_string",
    "suggest_hed_tags",
    "get_hed_schema_versions",
]
