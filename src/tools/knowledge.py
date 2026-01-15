"""HED knowledge discovery tools - backward compatibility module.

This module re-exports from the new location for backward compatibility.
Import from src.assistants.hed.knowledge instead.

Deprecated: Use `from src.assistants.hed.knowledge import search_hed_discussions` instead.
"""

# Re-export everything from the new location
from src.assistants.hed.knowledge import (
    search_hed_discussions,
    search_hed_papers,
)

__all__ = [
    "search_hed_discussions",
    "search_hed_papers",
]
