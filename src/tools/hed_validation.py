"""HED validation tools - backward compatibility module.

This module re-exports from the new location for backward compatibility.
Import from src.assistants.hed.tools instead.

Deprecated: Use `from src.assistants.hed.tools import validate_hed_string` instead.
"""

# Re-export everything from the new location
from src.assistants.hed.tools import (
    get_hed_schema_versions,
    retrieve_hed_docs,
    suggest_hed_tags,
    validate_hed_string,
)

__all__ = [
    "get_hed_schema_versions",
    "retrieve_hed_docs",
    "suggest_hed_tags",
    "validate_hed_string",
]
