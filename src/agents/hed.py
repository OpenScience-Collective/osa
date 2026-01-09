"""HED Assistant - Specialized agent for Hierarchical Event Descriptors.

This agent provides expertise on HED annotation, schemas, validation,
and tool usage. It has access to 28 HED documents (2 preloaded, 26 on-demand).

Preloaded docs (~13k tokens) include HED annotation semantics and terminology.
Other docs are fetched on-demand to minimize context usage.
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from markdownify import markdownify

from src.agents.base import ToolAgent
from src.tools.hed import (
    HED_DOCS,
    get_preloaded_hed_content,
    retrieve_hed_doc,
)
from src.tools.hed_validation import (
    get_hed_schema_versions,
    suggest_hed_tags,
    validate_hed_string,
)

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
3. If valid → Show to user
4. If invalid → Fix based on errors OR find example in docs → Validate → Show
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


def is_safe_url(url: str) -> tuple[bool, str, str | None]:
    """Validate URL is safe to fetch (prevents SSRF attacks).

    This function resolves DNS and returns the resolved IP to prevent
    TOCTOU (Time-Of-Check-Time-Of-Use) attacks where DNS could return
    different IPs between validation and fetch.

    Args:
        url: The URL to validate.

    Returns:
        Tuple of (is_safe, error_message, resolved_ip).
        - error_message is empty if safe
        - resolved_ip is the IP address to use for fetching (prevents DNS rebinding)
    """
    parsed = urlparse(url)

    # Only allow http/https
    if parsed.scheme not in ("http", "https"):
        logger.warning("SSRF blocked: invalid scheme '%s' in URL: %s", parsed.scheme, url)
        return False, "Only HTTP/HTTPS protocols are allowed", None

    hostname = parsed.hostname
    if not hostname:
        logger.warning("SSRF blocked: empty hostname in URL: %s", url)
        return False, "Invalid hostname", None

    # Resolve hostname to IP to check for private ranges
    try:
        resolved_ip = socket.gethostbyname(hostname)
    except socket.gaierror as e:
        # DNS resolution failed - treat as security error
        logger.warning("SSRF blocked: DNS resolution failed for %s: %s", hostname, e)
        return False, f"DNS resolution failed for {hostname}: {e}", None
    except socket.herror as e:
        logger.warning("SSRF blocked: host error for %s: %s", hostname, e)
        return False, f"Host error for {hostname}: {e}", None
    except TimeoutError as e:
        logger.warning("SSRF blocked: DNS timeout for %s: %s", hostname, e)
        return False, f"DNS resolution timed out for {hostname}", None

    try:
        ip_obj = ipaddress.ip_address(resolved_ip)
    except ValueError as e:
        logger.warning("SSRF blocked: invalid IP address '%s': %s", resolved_ip, e)
        return False, f"Invalid IP address: {resolved_ip}", None

    # Block private/internal IPs to prevent SSRF
    if ip_obj.is_private:
        logger.warning("SSRF blocked: private IP %s for host %s", resolved_ip, hostname)
        return False, f"Access to private IP ranges is not allowed: {resolved_ip}", None
    if ip_obj.is_loopback:
        logger.warning("SSRF blocked: loopback IP %s for host %s", resolved_ip, hostname)
        return False, f"Access to loopback addresses is not allowed: {resolved_ip}", None
    if ip_obj.is_link_local:
        logger.warning("SSRF blocked: link-local IP %s for host %s", resolved_ip, hostname)
        return False, f"Access to link-local addresses is not allowed: {resolved_ip}", None
    if ip_obj.is_reserved:
        logger.warning("SSRF blocked: reserved IP %s for host %s", resolved_ip, hostname)
        return False, f"Access to reserved IP ranges is not allowed: {resolved_ip}", None

    return True, "", resolved_ip


def _fetch_page_content_impl(url: str) -> str:
    """Internal implementation to fetch page content.

    This is not a tool - it's called by the dynamically created tool.

    Args:
        url: The URL of the page to fetch content from.

    Returns:
        The page content in markdown format, or an error message.
    """
    # Validate URL for SSRF protection
    if not url or not url.startswith(("http://", "https://")):
        logger.warning("Page fetch blocked: invalid URL format: %s", url)
        return f"Error: Invalid URL '{url}'. URL must start with http:// or https://"

    is_safe, error_msg, resolved_ip = is_safe_url(url)
    if not is_safe:
        return f"Error: {error_msg}"

    logger.info("Fetching page content from %s (resolved to %s)", url, resolved_ip)

    try:
        # Fetch the page (disable redirects to prevent redirect-based SSRF)
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.get(url)

            # Handle redirects manually with validation
            redirect_count = 0
            max_redirects = 3
            while response.is_redirect and redirect_count < max_redirects:
                redirect_url = response.headers.get("location")
                if not redirect_url:
                    logger.warning("Redirect response missing Location header from %s", url)
                    break

                # Handle relative redirects
                if redirect_url.startswith("/"):
                    parsed = urlparse(url)
                    redirect_url = f"{parsed.scheme}://{parsed.netloc}{redirect_url}"

                is_safe, error_msg, _ = is_safe_url(redirect_url)
                if not is_safe:
                    logger.warning(
                        "SSRF blocked: redirect from %s to unsafe URL %s: %s",
                        url,
                        redirect_url,
                        error_msg,
                    )
                    return f"Error: Redirect to unsafe URL blocked: {error_msg}"

                logger.info("Following redirect to %s", redirect_url)
                response = client.get(redirect_url)
                redirect_count += 1

            if response.is_redirect:
                logger.warning("Too many redirects (>%d) from %s", max_redirects, url)
                return f"Error: Too many redirects (exceeded {max_redirects})"

            response.raise_for_status()

        # Validate content type
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type.lower():
            logger.warning("Non-HTML content type from %s: %s", url, content_type)
            return f"Error: URL returned non-HTML content: {content_type}"

        # Convert HTML to markdown
        content = markdownify(response.text, heading_style="ATX", strip=["script", "style"])

        # Clean up excessive whitespace
        lines = [line.strip() for line in content.split("\n")]
        content = "\n".join(line for line in lines if line)

        # Truncate if too long
        if len(content) > MAX_PAGE_CONTENT_LENGTH:
            logger.info(
                "Content from %s truncated from %d to %d chars",
                url,
                len(content),
                MAX_PAGE_CONTENT_LENGTH,
            )
            content = content[:MAX_PAGE_CONTENT_LENGTH] + "\n\n... [content truncated]"

        return f"# Content from {url}\n\n{content}"

    except httpx.HTTPStatusError as e:
        logger.warning("HTTP error fetching %s: %d", url, e.response.status_code)
        return f"Error fetching {url}: HTTP {e.response.status_code}"
    except httpx.TimeoutException:
        logger.warning("Timeout fetching %s", url)
        return f"Error: Request timed out fetching {url}"
    except httpx.RequestError as e:
        logger.warning("Request error fetching %s: %s", url, e)
        return f"Error fetching {url}: {e}"


@tool
def retrieve_hed_docs(url: str) -> str:
    """Retrieve HED documentation by URL.

    Use this tool to fetch HED documentation when you need detailed
    information about HED annotation, schemas, or tools.

    Args:
        url: The HTML URL of the HED documentation page to retrieve.
             Must be one of the URLs listed in the on-demand documents section.

    Returns:
        The document content in markdown format, or an error message.
    """
    result = retrieve_hed_doc(url)
    if result.success:
        return f"# {result.title}\n\nSource: {result.url}\n\n{result.content}"
    return f"Error retrieving {result.url}: {result.error}"


class HEDAssistant(ToolAgent):
    """Specialized assistant for HED (Hierarchical Event Descriptors).

    This agent has expertise in HED annotation, schemas, validation, and tools.
    It preloads 2 core documents (~13k tokens) and can fetch 26 more on-demand.

    Example:
        ```python
        from src.agents.hed import HEDAssistant
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
        model: BaseChatModel,
        preload_docs: bool = True,
        page_context: "PageContext | None" = None,
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
        ]

        # Add fetch_current_page tool if page context is provided
        # This creates a bound tool that only fetches the specific page URL,
        # preventing the LLM from requesting arbitrary URLs (SSRF protection)
        if page_context and page_context.url:
            page_url = page_context.url  # Capture in closure

            @tool
            def fetch_current_page() -> str:
                """Fetch content from the page where the user is currently asking their question.

                Use this tool when the user's question seems related to the content of the page
                they are viewing. This will retrieve the page content and provide context for
                answering questions about "this page" or "this documentation".

                Returns:
                    The page content in markdown format, or an error message.
                """
                return _fetch_page_content_impl(page_url)

            tools.append(fetch_current_page)

        # Initialize with HED tools: documentation retrieval, validation, and tag suggestions
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


def create_hed_assistant(
    model_name: str | None = None,
    api_key: str | None = None,
    preload_docs: bool = True,
) -> HEDAssistant:
    """Convenience function to create a HED assistant.

    Args:
        model_name: Name of the model to use. If None, uses settings.default_model
                   (default: qwen/qwen3-235b-a22b-2507 via Cerebras).
        api_key: Optional API key override (for BYOK).
        preload_docs: Whether to preload core docs.

    Returns:
        Configured HEDAssistant instance.
    """
    from src.core.services.llm import get_llm_service

    llm_service = get_llm_service()
    model = llm_service.get_model(model_name, api_key=api_key)
    return HEDAssistant(model, preload_docs=preload_docs)
