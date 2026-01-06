"""Base tool infrastructure for OSA document retrieval."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocPage:
    """A documentation page that can be retrieved.

    Follows the QP pattern of dual URLs:
    - url: The HTML page URL for user reference (displayed in responses)
    - source_url: The raw markdown URL for fetching content
    """

    title: str
    """Human-readable document title."""

    url: str
    """HTML page URL for user reference (included in responses)."""

    source_url: str
    """Raw markdown/content URL for fetching."""

    preload: bool = False
    """If True, content is preloaded and embedded in system prompt."""

    category: str = "general"
    """Category for organizing documents."""

    description: str = ""
    """Short (1-3 sentence) description of document content for search/discovery."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "title": self.title,
            "url": self.url,
            "source_url": self.source_url,
            "preload": self.preload,
            "category": self.category,
            "description": self.description,
        }


@dataclass
class DocRegistry:
    """Registry of available documentation for an assistant.

    Manages preloaded vs on-demand documents and provides
    categorized listings for tool descriptions.
    """

    name: str
    """Name of the registry (e.g., 'hed', 'bids', 'eeglab')."""

    docs: list[DocPage] = field(default_factory=list)
    """List of all available documents."""

    def get_preloaded(self) -> list[DocPage]:
        """Get documents that should be preloaded."""
        return [d for d in self.docs if d.preload]

    def get_on_demand(self) -> list[DocPage]:
        """Get documents available on-demand."""
        return [d for d in self.docs if not d.preload]

    def get_by_category(self, category: str) -> list[DocPage]:
        """Get documents in a specific category."""
        return [d for d in self.docs if d.category == category]

    def get_categories(self) -> list[str]:
        """Get unique categories in order of first appearance."""
        seen: set[str] = set()
        categories: list[str] = []
        for doc in self.docs:
            if doc.category not in seen:
                seen.add(doc.category)
                categories.append(doc.category)
        return categories

    def find_by_url(self, url: str) -> DocPage | None:
        """Find a document by its URL."""
        for doc in self.docs:
            if doc.url == url:
                return doc
        return None

    def add(self, doc: DocPage) -> None:
        """Add a document to the registry."""
        self.docs.append(doc)

    def format_doc_list(
        self, include_preloaded: bool = True, include_descriptions: bool = True
    ) -> str:
        """Format a readable list of available documents.

        Used in tool descriptions to show what docs are available.

        Args:
            include_preloaded: Whether to include preloaded docs
            include_descriptions: Whether to include document descriptions
        """
        lines: list[str] = []

        # Preloaded docs
        preloaded = self.get_preloaded()
        if preloaded and include_preloaded:
            lines.append("### Preloaded Documents (already available):")
            for doc in preloaded:
                lines.append(f"- {doc.title}: {doc.url}")
                if include_descriptions and doc.description:
                    lines.append(f"  {doc.description}")
            lines.append("")

        # On-demand docs by category
        for category in self.get_categories():
            on_demand = [d for d in self.get_by_category(category) if not d.preload]
            if on_demand:
                # Format category name nicely
                category_name = category.replace("-", " ").replace("_", " ").title()
                lines.append(f"### {category_name}:")
                for doc in on_demand:
                    lines.append(f"- {doc.title}: {doc.url}")
                    if include_descriptions and doc.description:
                        lines.append(f"  {doc.description}")
                lines.append("")

        return "\n".join(lines)


@dataclass
class RetrievedDoc:
    """A document that has been retrieved."""

    title: str
    """Document title."""

    url: str
    """HTML URL for reference."""

    content: str
    """The retrieved markdown content."""

    error: str | None = None
    """Error message if retrieval failed."""

    @property
    def success(self) -> bool:
        """Whether the document was retrieved successfully."""
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "title": self.title,
            "url": self.url,
            "content": self.content,
        }
        if self.error:
            result["error"] = self.error
        return result
