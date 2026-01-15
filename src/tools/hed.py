"""HED documentation tools - backward compatibility module.

This module re-exports from the new location for backward compatibility.
Import from src.assistants.hed.docs instead.

Deprecated: Use `from src.assistants.hed.docs import HED_DOCS` instead.
"""

# Re-export documentation registry and helpers from docs.py
from src.assistants.hed.docs import (
    HED_DOCS,
    format_hed_doc_list,
    get_hed_registry,
    get_preloaded_hed_content,
    retrieve_hed_doc,
    retrieve_hed_docs,
    retrieve_hed_docs_by_category,
)

# Also re-export base classes for convenience
from src.tools.base import DocPage, DocRegistry, RetrievedDoc

__all__ = [
    "HED_DOCS",
    "DocPage",
    "DocRegistry",
    "RetrievedDoc",
    "format_hed_doc_list",
    "get_hed_registry",
    "get_preloaded_hed_content",
    "retrieve_hed_doc",
    "retrieve_hed_docs",
    "retrieve_hed_docs_by_category",
]
