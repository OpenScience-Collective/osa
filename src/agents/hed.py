"""HED Assistant - backward compatibility module.

This module re-exports from the new location for backward compatibility.
Import from src.assistants.hed instead.

Deprecated: Use `from src.assistants.hed import HEDAssistant` instead.
"""

# Re-export everything from the new location
from src.assistants.hed import (
    HED_SYSTEM_PROMPT_TEMPLATE,
    MAX_PAGE_CONTENT_LENGTH,
    PAGE_CONTEXT_SECTION_TEMPLATE,
    HEDAssistant,
    PageContext,
    _fetch_page_content_impl,
    _format_ondemand_section,
    _format_preloaded_section,
    create_hed_assistant,
    is_safe_url,
)

# Re-export tools
from src.assistants.hed.tools import retrieve_hed_docs

__all__ = [
    "HED_SYSTEM_PROMPT_TEMPLATE",
    "HEDAssistant",
    "MAX_PAGE_CONTENT_LENGTH",
    "PAGE_CONTEXT_SECTION_TEMPLATE",
    "PageContext",
    "_fetch_page_content_impl",
    "_format_ondemand_section",
    "_format_preloaded_section",
    "create_hed_assistant",
    "is_safe_url",
    "retrieve_hed_docs",
]
