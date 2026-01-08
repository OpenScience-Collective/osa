"""Protocol definitions for OSA components.

These protocols define the contracts that components must implement,
enabling parameterized testing across different implementations
(HED, BIDS, EEGLAB, etc.).

Usage:
    from src.interfaces import DocRegistryProtocol, AgentProtocol

    def test_registry(registry: DocRegistryProtocol):
        assert len(registry.docs) > 0
        assert len(registry.get_preloaded()) + len(registry.get_on_demand()) == len(registry.docs)
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DocPageProtocol(Protocol):
    """Protocol for document page entries.

    Defines the required attributes and methods for a document
    that can be registered and retrieved by the system.
    """

    title: str
    """Human-readable document title."""

    url: str
    """HTML page URL for user reference (included in responses)."""

    source_url: str
    """Raw markdown/content URL for fetching."""

    preload: bool
    """If True, content is preloaded and embedded in system prompt."""

    category: str
    """Category for organizing documents."""

    description: str
    """Short description of document content for search/discovery."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        ...


@runtime_checkable
class RetrievedDocProtocol(Protocol):
    """Protocol for retrieved documents.

    Defines the structure of a document after it has been fetched.
    """

    title: str
    """Document title."""

    url: str
    """HTML URL for reference."""

    content: str
    """The retrieved markdown content."""

    error: str | None
    """Error message if retrieval failed."""

    @property
    def success(self) -> bool:
        """Whether the document was retrieved successfully."""
        ...

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        ...


@runtime_checkable
class DocRegistryProtocol(Protocol):
    """Protocol for document registries.

    Defines the contract that all document registries (HED, BIDS, EEGLAB, etc.)
    must implement. This enables parameterized testing across registries.
    """

    name: str
    """Name of the registry (e.g., 'hed', 'bids', 'eeglab')."""

    docs: list[DocPageProtocol]
    """List of all available documents."""

    def get_preloaded(self) -> list[DocPageProtocol]:
        """Get documents that should be preloaded."""
        ...

    def get_on_demand(self) -> list[DocPageProtocol]:
        """Get documents available on-demand."""
        ...

    def get_by_category(self, category: str) -> list[DocPageProtocol]:
        """Get documents in a specific category."""
        ...

    def get_categories(self) -> list[str]:
        """Get unique categories in order of first appearance."""
        ...

    def find_by_url(self, url: str) -> DocPageProtocol | None:
        """Find a document by its URL."""
        ...

    def add(self, doc: DocPageProtocol) -> None:
        """Add a document to the registry."""
        ...

    def format_doc_list(
        self, include_preloaded: bool = True, include_descriptions: bool = True
    ) -> str:
        """Format a readable list of available documents."""
        ...


@runtime_checkable
class ToolProtocol(Protocol):
    """Protocol for LangChain-compatible tools.

    Defines the minimum interface for tools that can be used by agents.
    """

    name: str
    """Unique name of the tool."""

    description: str
    """Description of what the tool does (used by LLM for tool selection)."""

    def invoke(self, input: dict[str, Any]) -> Any:
        """Invoke the tool with the given input."""
        ...


@runtime_checkable
class AgentProtocol(Protocol):
    """Protocol for OSA agents.

    Defines the contract that all agents (HED, BIDS, EEGLAB, etc.)
    must implement. Enables parameterized testing across agents.
    """

    model: Any
    """The language model used by the agent."""

    tools: list[ToolProtocol]
    """List of tools available to the agent."""

    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent."""
        ...

    def invoke(
        self,
        messages: list[Any] | str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke the agent synchronously."""
        ...

    async def ainvoke(
        self,
        messages: list[Any] | str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke the agent asynchronously."""
        ...

    def build_graph(self) -> Any:
        """Build and compile the LangGraph workflow."""
        ...
