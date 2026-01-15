"""HED documentation tools - backward compatibility module.

This module re-exports from the new location for backward compatibility.
Import from src.assistants.hed.docs instead.

Deprecated: Use `from src.assistants.hed.docs import HED_DOCS` instead.
"""

# Re-export documentation registry and helpers from docs.py
from src.assistants.hed.docs import (
    HED_DOCS,
    get_hed_registry,
    get_preloaded_hed_content,
    retrieve_hed_doc,
    retrieve_hed_docs_by_category,
)

# Also re-export base classes for convenience
from src.tools.base import DocPage, DocRegistry, RetrievedDoc


def format_hed_doc_list() -> str:
    """Format a readable list of available HED documentation.

    Deprecated: Use HED_DOCS.format_doc_list() directly.
    """
    return HED_DOCS.format_doc_list()


def retrieve_hed_docs(url: str) -> str:
    """Retrieve HED documentation by URL.

    Use this tool to fetch HED documentation when you need detailed
    information about HED annotation, schemas, or tools.

    Available documents:
    {doc_list}

    Args:
        url: The HTML URL of the HED documentation page to retrieve.

    Returns:
        The document content in markdown format, or an error message.
    """
    result = retrieve_hed_doc(url)
    if result.success:
        return f"# {result.title}\n\nSource: {result.url}\n\n{result.content}"
    return f"Error retrieving {result.url}: {result.error}"


# Update docstring with available docs
retrieve_hed_docs.__doc__ = retrieve_hed_docs.__doc__.format(doc_list=HED_DOCS.format_doc_list())


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
