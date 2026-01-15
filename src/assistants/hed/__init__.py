"""HED Assistant - Hierarchical Event Descriptors.

Self-contained assistant module for HED annotation, validation, and documentation.

This module auto-registers with the OSA assistant registry when imported.
All HED-specific code is contained within this package:
- docs.py: HED documentation registry (28 docs, 2 preloaded)
- tools.py: Validation, tag suggestion, and doc retrieval tools
- knowledge.py: GitHub and paper search tools
- sync.py: Knowledge sync configuration

Usage:
    # Via registry (preferred)
    from src.assistants import registry

    assistant = registry.create_assistant("hed", model=llm)

    # Direct import (also works)
    from src.assistants.hed import HEDAssistant

    assistant = HEDAssistant(model=llm)
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from src.agents.base import ToolAgent
from src.assistants.registry import registry

from .docs import HED_DOCS, get_preloaded_hed_content
from .knowledge import search_hed_discussions, search_hed_papers
from .sync import SYNC_CONFIG
from .tools import (
    get_hed_schema_versions,
    retrieve_hed_docs,
    suggest_hed_tags,
    validate_hed_string,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

# Maximum characters to return from fetched page content
MAX_PAGE_CONTENT_LENGTH = 30000


@dataclass
class PageContext:
    """Context about the page where the assistant widget is embedded."""

    url: str | None = None
    title: str | None = None


# HED System Prompt - adapted from QP's hedAssistantSystemPrompt.ts
HED_SYSTEM_PROMPT_TEMPLATE = """You are a technical assistant specialized in helping users with the Hierarchical Event Descriptors (HED) standard.
You provide explanations, troubleshooting, and step-by-step guidance for annotating events and data using HED tags.
You must stick strictly to the topic of HED and avoid digressions.
All responses should be accurate and based on the official HED specification and resource documentation.

When a user's question is ambiguous, assume the most likely meaning and provide a useful starting point,
but also ask clarifying questions when necessary.
Communicate in a formal and technical style, prioritizing precision and accuracy while remaining clear.
Balance clarity and technical accuracy, starting with accessible explanations and expanding into more detail when needed.
Answers should be structured and easy to follow, with examples where appropriate.

The HED homepage is https://www.hedtags.org/
The HED specification documentation is available at https://www.hedtags.org/hed-specification
Main HED resources and guides are at https://www.hedtags.org/hed-resources
The HED GitHub organization is at https://github.com/hed-standard
HED schemas can be viewed at https://www.hedtags.org/hed-schema-browser

You will respond with markdown formatted text. Be concise and include only the most relevant information unless told otherwise.

## Using Tools Liberally

You have access to tools for validation and documentation retrieval. **Use them proactively and liberally.**

- Tool calls are inexpensive, so don't hesitate to validate strings or retrieve docs
- When in doubt about syntax or tag validity, use the validation tool
- Validation tools strengthen your responses and build user trust
- Retrieve relevant documentation to ensure your answers are accurate and current

Think of tools as enhancing your capabilities at minimal cost. Prefer calling a tool to confirm your understanding over making assumptions.

## Using the retrieve_hed_docs Tool

Before responding, use the retrieve_hed_docs tool to get any documentation you need.
Include links to relevant documents in your response.

**Important guidelines:**
- Do NOT retrieve docs that have already been preloaded (listed below)
- Retrieve multiple relevant documents at once so you have all the information you need
- Get background information documents in addition to specific documents for the question
- If you have already loaded a document in this conversation, don't load it again

## Preloaded Documents

The following documents are already available to you (DO NOT retrieve these):

{preloaded_docs}

## On-Demand Documents

Use retrieve_hed_docs to fetch these when needed:

{ondemand_docs}

## Guidelines for HED Annotations

When providing examples of HED annotations:
- Use code blocks for clarity
- Your annotations MUST be valid
- Only use tags from the HED schema that follow HED rules
- ALWAYS use the SHORT FORM of tags

## Using suggest_hed_tags for Tag Discovery

When users describe events in natural language, use the suggest_hed_tags tool to find valid HED tags:

**Workflow for constructing annotations:**
1. Identify the key concepts in the user's description (e.g., "button press", "visual flash")
2. Call suggest_hed_tags with those concepts to get valid tag suggestions
3. Select the most appropriate tags from the suggestions
4. Construct the HED annotation string using proper syntax
5. Validate the final string with validate_hed_string before showing to user

**Example:**
```
User: "I need to annotate when the participant presses a button after seeing a flash"

Your internal process:
1. Key concepts: "button press", "visual flash", "response"
2. Call: suggest_hed_tags(["button press", "visual flash", "response"])
3. Get suggestions like: "button press" -> ["Press", "Button", ...], etc.
4. Construct: "Sensory-event, Visual-presentation, Flash, (Agent-action, Press, Button)"
5. Validate, then show to user
```

## CRITICAL: Validate Examples Before Showing to Users

**Important Workflow for Providing Examples:**

When you want to give the user an example HED annotation string:

1. **Generate** the example based on documentation and your knowledge
2. **VALIDATE** using the validate_hed_string tool BEFORE showing to user
3. **If valid**: Present the example to the user
4. **If invalid**:
   - Fix the example based on the error messages
   - OR use a known-good example from the documentation instead
   - Validate again until correct
5. **Never show invalid examples to users**

This self-check process ensures you only provide correct examples to researchers,
building trust and preventing users from adopting invalid annotation patterns.

**Example workflow:**
```
User asks: "How do I annotate a visual stimulus?"
Your internal process:
1. Generate: "Sensory-event, Visual-presentation, Red"
2. Call: validate_hed_string("Sensory-event, Visual-presentation, Red")
3. If valid -> Show to user
4. If invalid -> Fix based on errors OR find example in docs -> Validate -> Show
```

## Key References

- **HED standard schema**: JSON vocabulary with all valid tags and properties
- **HED annotation semantics**: How tags should be used in annotations (consult first for annotation advice)
- **HED errors**: List of validation errors and meanings (for explaining validation errors)
- **Test cases**: JSON examples of passing/failing tests (some examples have multiple error codes)

If you are unsure, do not guess or hallucinate. Stick to what you can learn from the documents.
Feel free to read as many documents as you need.

Common topics include:
- Basic HED annotation and tag selection
- HED string syntax and formatting
- Working with HED schemas and vocabularies
- Validation procedures and error resolution
- Tool usage (Python, MATLAB, JavaScript, online)
- Integration with BIDS, NWB, and EEGLAB
- Event categorization and experimental design
- Advanced features like definitions and temporal scope

## Knowledge Discovery Tools

You have access to tools that search synced GitHub discussions and academic papers.
These are for **DISCOVERY**, not for answering questions.

**Correct usage:**
- "There's a related discussion about this: [link]"
- "You might find this paper helpful for more context: [link]"

**Incorrect usage (DO NOT):**
- "Based on issue #123, the answer is..."
- "According to a discussion, you should..."

Use these tools when:
- The user asks about recent developments or ongoing work
- The user's question relates to known issues or feature requests
- The user might benefit from seeing related academic research
- You want to point the user to community discussions

The knowledge database may not be populated. If you get a message about initializing the database,
simply answer the question without the discovery tools.

{page_context_section}"""


PAGE_CONTEXT_SECTION_TEMPLATE = """## Page Context

The user is asking this question from the following page:
- **Page URL**: {page_url}
- **Page Title**: {page_title}

If the user's question seems related to the content of this page, you can use the fetch_current_page tool
to retrieve the page content and provide more contextually relevant answers. This is especially useful when:
- The user references "this page" or "this documentation"
- The question seems to be about specific content that might be on the page
- The page appears to be HED-related documentation

Only fetch the page content if it seems relevant to the question. For general HED questions,
you don't need to fetch the page content."""


def _format_preloaded_section(preloaded_content: dict[str, str]) -> str:
    """Format preloaded documents for the system prompt."""
    sections = []
    for doc in HED_DOCS.get_preloaded():
        content = preloaded_content.get(doc.url, "")
        if content:
            # Truncate very long content (like the schema JSON)
            if len(content) > 50000:
                content = content[:50000] + "\n\n... [truncated for length]"
            sections.append(f"### {doc.title}\nSource: {doc.url}\n\n{content}")
    return "\n\n---\n\n".join(sections)


def _format_ondemand_section() -> str:
    """Format on-demand documents list for the system prompt."""
    lines = []
    for category in HED_DOCS.get_categories():
        on_demand = [d for d in HED_DOCS.get_by_category(category) if not d.preload]
        if on_demand:
            category_name = category.replace("-", " ").replace("_", " ").title()
            lines.append(f"**{category_name}:**")
            for doc in on_demand:
                lines.append(f"- {doc.title}: `{doc.url}`")
            lines.append("")
    return "\n".join(lines)


def _create_fetch_current_page_tool(page_url: str):
    """Create a bound tool that fetches a specific page URL.

    This prevents the LLM from requesting arbitrary URLs (SSRF protection).
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    import httpx
    from markdownify import markdownify

    def is_safe_url(url: str) -> tuple[bool, str]:
        """Validate URL is safe to fetch."""
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False, "Only HTTP/HTTPS protocols are allowed"

        hostname = parsed.hostname
        if not hostname:
            return False, "Invalid hostname"

        try:
            resolved_ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(resolved_ip)

            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
                return False, f"Access to private/local IP not allowed: {resolved_ip}"

        except (socket.gaierror, ValueError) as e:
            return False, f"DNS resolution failed: {e}"

        return True, ""

    @tool
    def fetch_current_page() -> str:
        """Fetch content from the page where the user is currently asking their question.

        Use this tool when the user's question seems related to the content of the page
        they are viewing. This will retrieve the page content and provide context for
        answering questions about "this page" or "this documentation".

        Returns:
            The page content in markdown format, or an error message.
        """
        is_safe, error = is_safe_url(page_url)
        if not is_safe:
            return f"Error: {error}"

        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                response = client.get(page_url)
                response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type.lower():
                return f"Error: URL returned non-HTML content: {content_type}"

            content = markdownify(response.text, heading_style="ATX", strip=["script", "style"])
            lines = [line.strip() for line in content.split("\n")]
            content = "\n".join(line for line in lines if line)

            if len(content) > MAX_PAGE_CONTENT_LENGTH:
                content = content[:MAX_PAGE_CONTENT_LENGTH] + "\n\n... [content truncated]"

            return f"# Content from {page_url}\n\n{content}"

        except httpx.HTTPError as e:
            return f"Error fetching {page_url}: {e}"

    return fetch_current_page


class HEDAssistant(ToolAgent):
    """Specialized assistant for HED (Hierarchical Event Descriptors).

    This agent has expertise in HED annotation, schemas, validation, and tools.
    It preloads 2 core documents (~13k tokens) and can fetch 26 more on-demand.

    Example:
        ```python
        from src.assistants.hed import HEDAssistant
        from src.core.services.llm import get_llm_service

        llm_service = get_llm_service()
        model = llm_service.get_model("claude-3-5-sonnet")

        assistant = HEDAssistant(model)
        result = assistant.invoke("How do I annotate a button press event?")
        print(result["messages"][-1].content)
        ```
    """

    def __init__(
        self,
        model: "BaseChatModel",
        preload_docs: bool = True,
        page_context: PageContext | None = None,
    ) -> None:
        """Initialize the HED Assistant.

        Args:
            model: The language model to use.
            preload_docs: Whether to preload core docs into system prompt.
                         Set to False for testing without network calls.
            page_context: Optional context about the page where the widget is embedded.
        """
        self._preload_docs = preload_docs
        self._preloaded_content: dict[str, str] = {}
        self._page_context = page_context

        # Preload documents if requested
        if preload_docs:
            self._preloaded_content = get_preloaded_hed_content()

        # Build tools list
        tools = [
            retrieve_hed_docs,
            validate_hed_string,
            suggest_hed_tags,
            get_hed_schema_versions,
            # Knowledge discovery tools (for finding related discussions and papers)
            search_hed_discussions,
            search_hed_papers,
        ]

        # Add fetch_current_page tool if page context is provided
        if page_context and page_context.url:
            fetch_tool = _create_fetch_current_page_tool(page_context.url)
            tools.append(fetch_tool)

        # Initialize with HED tools
        super().__init__(
            model=model,
            tools=tools,
            system_prompt=None,  # We override get_system_prompt
        )

    def get_system_prompt(self) -> str:
        """Build the system prompt with preloaded documents and page context."""
        if self._preload_docs and self._preloaded_content:
            preloaded_section = _format_preloaded_section(self._preloaded_content)
        else:
            preloaded_section = "(Preloaded documents not available - use retrieve_hed_docs tool)"

        ondemand_section = _format_ondemand_section()

        # Build page context section if available
        if self._page_context and self._page_context.url:
            page_context_section = PAGE_CONTEXT_SECTION_TEMPLATE.format(
                page_url=self._page_context.url,
                page_title=self._page_context.title or "(No title)",
            )
        else:
            page_context_section = ""

        return HED_SYSTEM_PROMPT_TEMPLATE.format(
            preloaded_docs=preloaded_section,
            ondemand_docs=ondemand_section,
            page_context_section=page_context_section,
        )

    @property
    def preloaded_doc_count(self) -> int:
        """Number of documents successfully preloaded."""
        return len(self._preloaded_content)

    @property
    def available_doc_count(self) -> int:
        """Total number of documents available (preloaded + on-demand)."""
        return len(HED_DOCS.docs)


# Register with the assistant registry
@registry.register(
    id="hed",
    name="HED",
    description="Hierarchical Event Descriptors - annotation standard for neuroimaging",
    status="available",
    sync_config=SYNC_CONFIG,
)
def create_hed_assistant(
    model: "BaseChatModel",
    preload_docs: bool = True,
    page_context: PageContext | None = None,
) -> HEDAssistant:
    """Factory function to create a HED assistant.

    Args:
        model: The language model to use.
        preload_docs: Whether to preload core docs.
        page_context: Optional page context for widget embedding.

    Returns:
        Configured HEDAssistant instance.
    """
    return HEDAssistant(
        model=model,
        preload_docs=preload_docs,
        page_context=page_context,
    )


# Re-export for convenience
__all__ = [
    "HEDAssistant",
    "PageContext",
    "create_hed_assistant",
    "HED_DOCS",
]
