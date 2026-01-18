"""Pydantic models for community configuration.

Defines the schema for communities.yaml, enabling declarative
configuration of research community assistants.

Example YAML:
    communities:
      - id: hed
        name: HED (Hierarchical Event Descriptors)
        description: Event annotation standard for neuroimaging
        documentation:
          - url: https://www.hedtags.org/hed-resources/
            type: sphinx
        github:
          repos:
            - hed-standard/hed-specification
        citations:
          queries:
            - "Hierarchical Event Descriptors"
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class DocSource(BaseModel):
    """Documentation source configuration.

    Defines a documentation website to index and make available
    for retrieval by the assistant.
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    """Base URL of the documentation site."""

    type: Literal["sphinx", "mkdocs", "html"] = "html"
    """Documentation generator type for proper indexing."""

    source_repo: str | None = None
    """GitHub repo for raw markdown sources (e.g., 'org/repo')."""

    description: str | None = None
    """Optional description of what this documentation covers."""


class GitHubConfig(BaseModel):
    """GitHub repository configuration for issue/PR sync."""

    model_config = ConfigDict(extra="forbid")

    repos: list[str] = Field(default_factory=list)
    """List of repos to sync (e.g., ['hed-standard/hed-python'])."""


class CitationConfig(BaseModel):
    """Citation and paper search configuration."""

    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(default_factory=list)
    """Search queries for finding related papers."""

    dois: list[str] = Field(default_factory=list)
    """Core paper DOIs to track citations for."""


class DiscourseConfig(BaseModel):
    """Discourse/forum search configuration."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    """Base URL of the Discourse instance."""

    tags: list[str] = Field(default_factory=list)
    """Tags to filter forum topics by."""


class PythonPlugin(BaseModel):
    """Python plugin extension configuration."""

    model_config = ConfigDict(extra="forbid")

    module: str
    """Python module path (e.g., 'src.assistants.hed.tools')."""

    tools: list[str] | None = None
    """Specific tool names to import, or None for all."""


class McpServer(BaseModel):
    """MCP server extension configuration (Phase 2)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    """Server name identifier."""

    command: list[str] | None = None
    """Command to start local MCP server."""

    url: str | None = None
    """URL for remote MCP server."""


class ExtensionsConfig(BaseModel):
    """Extension points for specialized tools."""

    model_config = ConfigDict(extra="forbid")

    python_plugins: list[PythonPlugin] = Field(default_factory=list)
    """Python modules providing additional tools."""

    mcp_servers: list[McpServer] = Field(default_factory=list)
    """MCP servers providing additional tools (Phase 2)."""


class CommunityConfig(BaseModel):
    """Configuration for a single research community assistant.

    This is the main configuration model that defines everything
    needed to create a functional assistant for a community.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    """Unique identifier (e.g., 'hed', 'bids', 'eeglab')."""

    name: str
    """Display name (e.g., 'HED (Hierarchical Event Descriptors)')."""

    description: str
    """Short description of the community/tool."""

    status: Literal["available", "beta", "coming_soon"] = "available"
    """Availability status of the assistant."""

    documentation: list[DocSource] = Field(default_factory=list)
    """Documentation sources to index."""

    github: GitHubConfig | None = None
    """GitHub configuration for issue/PR sync."""

    citations: CitationConfig | None = None
    """Paper/citation search configuration."""

    discourse: list[DiscourseConfig] = Field(default_factory=list)
    """Discourse forum configurations (Phase 2)."""

    extensions: ExtensionsConfig | None = None
    """Extension points for specialized tools."""

    def get_sync_config(self) -> dict:
        """Generate sync_config dict for registry compatibility.

        Returns format expected by AssistantInfo.sync_config.
        """
        config = {}
        if self.github:
            config["github_repos"] = self.github.repos
        if self.citations:
            config["paper_queries"] = self.citations.queries
            config["paper_dois"] = self.citations.dois
        return config


class CommunitiesConfig(BaseModel):
    """Root configuration containing all communities.

    This is the top-level model parsed from communities.yaml.
    """

    model_config = ConfigDict(extra="forbid")

    communities: list[CommunityConfig] = Field(default_factory=list)
    """List of community configurations."""

    @classmethod
    def from_yaml(cls, path: Path) -> "CommunitiesConfig":
        """Load communities configuration from YAML file.

        Args:
            path: Path to communities.yaml file.

        Returns:
            Parsed and validated CommunitiesConfig.

        Raises:
            FileNotFoundError: If file doesn't exist.
            ValidationError: If YAML structure is invalid.
        """
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data or {})

    def get_community(self, community_id: str) -> CommunityConfig | None:
        """Get a community by ID.

        Args:
            community_id: The community identifier.

        Returns:
            CommunityConfig if found, None otherwise.
        """
        for community in self.communities:
            if community.id == community_id:
                return community
        return None
