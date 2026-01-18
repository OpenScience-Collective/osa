"""Generic CommunityAssistant for YAML-configured communities.

This module provides a generic assistant that can be created from YAML
configuration alone, without requiring custom Python code.

Features:
- Documentation retrieval (preloaded + on-demand)
- Page context tool (fetch current page for widget embedding)
- GitHub discussion search (if repos configured)
- Recent GitHub activity listing (if repos configured)
- Paper search (if citations configured)
- Python plugin tools (if extensions configured)
"""

import importlib
import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from langchain_core.tools import BaseTool, StructuredTool, tool
from markdownify import markdownify

from src.agents.base import ToolAgent
from src.core.config.community import CommunityConfig
from src.tools.base import DocRegistry
from src.tools.fetcher import get_fetcher
from src.tools.knowledge import create_knowledge_tools

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


# Default system prompt template for generic communities
COMMUNITY_SYSTEM_PROMPT_TEMPLATE = """You are an expert assistant for {name}.

{description}

## Your Role

You help users understand and work with {name} by:
1. Answering questions about concepts, best practices, and usage
2. Providing guidance based on official documentation
3. Linking to relevant GitHub discussions and academic papers for further reading

## Important Guidelines

**Use Documentation**: Use the retrieve_docs tool to fetch relevant documentation
before answering questions. Include links to documentation in your responses.

**Discovery, Not Authority**: When referencing GitHub discussions or papers:
- Present them as "related resources" or "further reading"
- Say: "There's a related discussion, see: [link]"
- Do NOT use discussion content to formulate authoritative answers

**Be Precise**: If you're unsure about something, say so. It's better to acknowledge
uncertainty than to provide incorrect information.

**Cite Sources**: When referencing documentation, include links so users can verify
and explore further.

{preloaded_docs_section}

{available_docs_section}

{page_context_section}

{additional_instructions}
"""


# ---------------------------------------------------------------------------
# SSRF Protection Utilities (for page context tool)
# ---------------------------------------------------------------------------


def is_safe_url(url: str) -> tuple[bool, str, str | None]:
    """Validate URL is safe to fetch (prevents SSRF attacks).

    Args:
        url: The URL to validate.

    Returns:
        Tuple of (is_safe, error_message, resolved_ip).
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        logger.warning("SSRF blocked: invalid scheme '%s' in URL: %s", parsed.scheme, url)
        return False, "Only HTTP/HTTPS protocols are allowed", None

    hostname = parsed.hostname
    if not hostname:
        logger.warning("SSRF blocked: empty hostname in URL: %s", url)
        return False, "Invalid hostname", None

    try:
        resolved_ip = socket.gethostbyname(hostname)
    except socket.gaierror as e:
        logger.warning("SSRF blocked: DNS resolution failed for %s: %s", hostname, e)
        return False, f"DNS resolution failed for {hostname}: {e}", None
    except socket.herror as e:
        logger.warning("SSRF blocked: host error for %s: %s", hostname, e)
        return False, f"Host error for {hostname}: {e}", None
    except TimeoutError:
        logger.warning("SSRF blocked: DNS timeout for %s", hostname)
        return False, f"DNS resolution timed out for {hostname}", None

    try:
        ip_obj = ipaddress.ip_address(resolved_ip)
    except ValueError as e:
        logger.warning("SSRF blocked: invalid IP address '%s': %s", resolved_ip, e)
        return False, f"Invalid IP address: {resolved_ip}", None

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
    """Internal implementation to fetch page content with SSRF protection."""
    if not url or not url.startswith(("http://", "https://")):
        logger.warning("Page fetch blocked: invalid URL format: %s", url)
        return f"Error: Invalid URL '{url}'. URL must start with http:// or https://"

    is_safe_result, error_msg, resolved_ip = is_safe_url(url)
    if not is_safe_result:
        return f"Error: {error_msg}"

    logger.info("Fetching page content from %s (resolved to %s)", url, resolved_ip)

    try:
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

                if redirect_url.startswith("/"):
                    parsed = urlparse(url)
                    redirect_url = f"{parsed.scheme}://{parsed.netloc}{redirect_url}"

                redirect_safe, redirect_error, _ = is_safe_url(redirect_url)
                if not redirect_safe:
                    logger.warning(
                        "SSRF blocked: redirect from %s to unsafe URL %s: %s",
                        url,
                        redirect_url,
                        redirect_error,
                    )
                    return f"Error: Redirect to unsafe URL blocked: {redirect_error}"

                logger.info("Following redirect to %s", redirect_url)
                response = client.get(redirect_url)
                redirect_count += 1

            if response.is_redirect:
                logger.warning("Too many redirects (>%d) from %s", max_redirects, url)
                return f"Error: Too many redirects (exceeded {max_redirects})"

            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type.lower():
            logger.warning("Non-HTML content type from %s: %s", url, content_type)
            return f"Error: URL returned non-HTML content: {content_type}"

        content = markdownify(response.text, heading_style="ATX", strip=["script", "style"])
        lines = [line.strip() for line in content.split("\n")]
        content = "\n".join(line for line in lines if line)

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


def _create_fetch_current_page_tool(page_url: str) -> BaseTool:
    """Create a bound tool that fetches a specific page URL."""

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

    return fetch_current_page


def _create_retrieve_docs_tool(
    community_id: str, community_name: str, doc_registry: DocRegistry
) -> BaseTool:
    """Create a retrieve docs tool for a community."""
    fetcher = get_fetcher()

    def retrieve_docs_impl(url: str) -> str:
        """Retrieve documentation by URL."""
        doc = doc_registry.find_by_url(url)
        if doc is None:
            return f"Document not found in {community_name} registry: {url}"

        result = fetcher.fetch(doc)
        if result.success:
            return f"# {result.title}\n\nSource: {result.url}\n\n{result.content}"
        return f"Error retrieving {result.url}: {result.error}"

    doc_list = doc_registry.format_doc_list(include_preloaded=False)

    description = (
        f"Retrieve {community_name} documentation by URL. "
        f"Use this to fetch detailed documentation when answering questions.\n\n"
        f"Available documents:\n{doc_list}"
    )

    return StructuredTool.from_function(
        func=retrieve_docs_impl,
        name=f"retrieve_{community_id}_docs",
        description=description,
    )


class CommunityAssistant(ToolAgent):
    """Generic assistant for any YAML-configured community.

    This assistant provides standard functionality for any community:
    - Documentation retrieval (preloaded + on-demand)
    - Page context tool (if page_context provided)
    - GitHub discussion search (if repos configured)
    - Recent GitHub activity listing (if repos configured)
    - Paper search (if citations configured)
    - Python plugin tools (if extensions configured)

    Args:
        model: The language model to use.
        config: Community configuration from YAML.
        preload_docs: Whether to preload docs marked with preload=True.
        page_context: Optional context about the page where widget is embedded.
        additional_tools: Extra tools to include beyond auto-generated ones.
        additional_instructions: Extra text to add to the system prompt.
    """

    def __init__(
        self,
        model: "BaseChatModel",
        config: CommunityConfig,
        preload_docs: bool = True,
        page_context: PageContext | None = None,
        additional_tools: list[BaseTool] | None = None,
        additional_instructions: str = "",
    ) -> None:
        """Initialize the community assistant."""
        self.config = config
        self.additional_instructions = additional_instructions
        self._preload_docs = preload_docs
        self._page_context = page_context
        self._preloaded_content: dict[str, str] = {}

        # Build doc registry from config
        self._doc_registry = config.get_doc_registry()

        # Preload documents if requested and there are docs to preload
        if preload_docs and self._doc_registry.get_preloaded():
            self._preloaded_content = self._fetch_preloaded_docs()

        # Build tools from config
        tools = self._build_tools(config)

        # Add documentation tool if docs are configured
        if config.documentation:
            doc_tool = _create_retrieve_docs_tool(config.id, config.name, self._doc_registry)
            tools.append(doc_tool)

        # Add page context tool if enabled in config and page context is provided
        if config.enable_page_context and page_context and page_context.url:
            fetch_tool = _create_fetch_current_page_tool(page_context.url)
            tools.append(fetch_tool)

        # Add any additional tools
        if additional_tools:
            tools.extend(additional_tools)

        # Load plugin tools from extensions
        plugin_tools = self._load_plugin_tools(config)
        tools.extend(plugin_tools)

        # Generate system prompt
        system_prompt = self._build_system_prompt(config, additional_instructions)

        super().__init__(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
        )

    def _fetch_preloaded_docs(self) -> dict[str, str]:
        """Fetch content for docs marked as preload=True."""
        fetcher = get_fetcher()
        return fetcher.preload(self._doc_registry.docs)

    def _build_tools(self, config: CommunityConfig) -> list[BaseTool]:
        """Build standard tools based on community configuration."""
        tools: list[BaseTool] = []

        repos = config.github.repos if config.github else None
        has_github = config.github and config.github.repos
        has_citations = config.citations and (config.citations.queries or config.citations.dois)

        knowledge_tools = create_knowledge_tools(
            community_id=config.id,
            community_name=config.name,
            repos=repos,
            include_discussions=bool(has_github),
            include_recent=bool(has_github),
            include_papers=bool(has_citations),
        )
        tools.extend(knowledge_tools)

        return tools

    def _load_plugin_tools(self, config: CommunityConfig) -> list[BaseTool]:
        """Load tools from Python plugin extensions."""
        all_tools: list[BaseTool] = []

        if not config.extensions or not config.extensions.python_plugins:
            return all_tools

        for plugin in config.extensions.python_plugins:
            plugin_tools: list[BaseTool] = []
            try:
                module = importlib.import_module(plugin.module)

                if plugin.tools:
                    for tool_name in plugin.tools:
                        if hasattr(module, tool_name):
                            tool_obj = getattr(module, tool_name)
                            if isinstance(tool_obj, BaseTool) or callable(tool_obj):
                                plugin_tools.append(tool_obj)
                            else:
                                logger.warning(
                                    "Plugin %s.%s is not a valid tool",
                                    plugin.module,
                                    tool_name,
                                )
                        else:
                            logger.warning(
                                "Tool %s not found in plugin %s",
                                tool_name,
                                plugin.module,
                            )
                else:
                    tool_names = getattr(module, "__all__", [])
                    for name in tool_names:
                        obj = getattr(module, name, None)
                        if isinstance(obj, BaseTool) or (callable(obj) and hasattr(obj, "name")):
                            plugin_tools.append(obj)

                logger.info(
                    "Loaded %d tools from plugin %s",
                    len(plugin_tools),
                    plugin.module,
                )
                all_tools.extend(plugin_tools)

            except ImportError as e:
                logger.error("Failed to import plugin %s: %s", plugin.module, e)
            except Exception as e:
                logger.error("Error loading plugin %s: %s", plugin.module, e)

        return all_tools

    def _format_preloaded_section(self) -> str:
        """Format preloaded documents for the system prompt."""
        if not self._preloaded_content:
            return ""

        sections = []
        for doc in self._doc_registry.get_preloaded():
            content = self._preloaded_content.get(doc.url, "")
            if content:
                # Truncate very long content
                if len(content) > 50000:
                    content = content[:50000] + "\n\n... [truncated for length]"
                sections.append(f"### {doc.title}\nSource: {doc.url}\n\n{content}")

        if sections:
            return (
                "## Preloaded Documents\n\nThe following documents are already available:\n\n---\n\n"
                + ("\n\n---\n\n".join(sections))
            )
        return ""

    def _format_available_docs_section(self) -> str:
        """Format list of available on-demand documents."""
        on_demand = self._doc_registry.get_on_demand()
        if not on_demand:
            return ""

        lines = ["## Available Documents", "", "Use retrieve_docs to fetch these when needed:", ""]
        for doc in on_demand:
            lines.append(f"- **{doc.title}**: `{doc.url}`")
            if doc.description:
                lines.append(f"  {doc.description}")

        return "\n".join(lines)

    def _format_page_context_section(self) -> str:
        """Format page context section for system prompt."""
        if not self.config.enable_page_context:
            return ""
        if not self._page_context or not self._page_context.url:
            return ""

        return f"""## Page Context

The user is asking this question from the following page:
- **Page URL**: {self._page_context.url}
- **Page Title**: {self._page_context.title or "(No title)"}

If the user's question seems related to the content of this page, you can use the fetch_current_page tool
to retrieve the page content and provide more contextually relevant answers. This is especially useful when:
- The user references "this page" or "this documentation"
- The question seems to be about specific content that might be on the page

Only fetch the page content if it seems relevant to the question."""

    def _build_system_prompt(
        self,
        config: CommunityConfig,
        additional_instructions: str,
    ) -> str:
        """Build the system prompt from configuration.

        Uses config.system_prompt if provided, otherwise uses the default template.
        Supports placeholders: {name}, {description}, {repo_list}, {paper_dois},
        {preloaded_docs_section}, {available_docs_section}, {page_context_section},
        {additional_instructions}.
        """
        # Use custom prompt if provided, otherwise use default template
        template = config.system_prompt or COMMUNITY_SYSTEM_PROMPT_TEMPLATE

        # Build placeholder values
        repo_list = ""
        if config.github and config.github.repos:
            repo_list = "\n".join(f"- `{repo}`" for repo in config.github.repos)

        paper_dois = ""
        if config.citations and config.citations.dois:
            paper_dois = "\n".join(f"- `{doi}`" for doi in config.citations.dois)

        preloaded_section = self._format_preloaded_section()
        available_docs_section = self._format_available_docs_section()
        page_context_section = self._format_page_context_section()

        # Substitute placeholders
        # Use a safe approach that ignores missing placeholders
        prompt = template
        substitutions = {
            "name": config.name,
            "description": config.description,
            "repo_list": repo_list,
            "paper_dois": paper_dois,
            "preloaded_docs_section": preloaded_section,
            "available_docs_section": available_docs_section,
            "page_context_section": page_context_section,
            "additional_instructions": additional_instructions,
        }

        for key, value in substitutions.items():
            prompt = prompt.replace("{" + key + "}", value)

        return prompt

    def get_system_prompt(self) -> str:
        """Return the system prompt for this assistant."""
        return self._build_system_prompt(self.config, self.additional_instructions)

    @property
    def preloaded_doc_count(self) -> int:
        """Number of documents successfully preloaded."""
        return len(self._preloaded_content)

    @property
    def available_doc_count(self) -> int:
        """Total number of documents available (preloaded + on-demand)."""
        return len(self._doc_registry.docs)


def create_community_assistant(
    model: "BaseChatModel",
    config: CommunityConfig,
    **kwargs,
) -> CommunityAssistant:
    """Factory function to create a generic community assistant.

    Args:
        model: The language model to use.
        config: Community configuration from YAML.
        **kwargs: Additional arguments passed to CommunityAssistant.
            - preload_docs: Whether to preload docs (default: True)
            - page_context: PageContext for widget embedding
            - additional_tools: Extra tools to include
            - additional_instructions: Extra text for system prompt

    Returns:
        Configured CommunityAssistant instance.
    """
    return CommunityAssistant(model=model, config=config, **kwargs)
