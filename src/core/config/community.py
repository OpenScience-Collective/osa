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

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


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
    """List of repos to sync (format: 'org/repo')."""

    @field_validator("repos")
    @classmethod
    def validate_repos(cls, v: list[str]) -> list[str]:
        """Validate all repos match 'org/repo' format and are unique."""
        repo_pattern = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")

        validated = []
        seen = set()

        for repo in v:
            repo = repo.strip()
            if not repo:
                raise ValueError("Repository name cannot be empty")
            if not repo_pattern.match(repo):
                raise ValueError(f"Repository must be in 'org/repo' format, got: {repo}")

            # Deduplicate
            if repo not in seen:
                seen.add(repo)
                validated.append(repo)

        return validated


class CitationConfig(BaseModel):
    """Citation and paper search configuration."""

    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(default_factory=list)
    """Search queries for finding related papers."""

    dois: list[str] = Field(default_factory=list)
    """Core paper DOIs to track citations for (format: '10.xxxx/yyyy')."""

    @field_validator("queries")
    @classmethod
    def validate_queries(cls, v: list[str]) -> list[str]:
        """Ensure queries are non-empty and deduplicated."""
        cleaned = [q.strip() for q in v if q.strip()]
        return list(dict.fromkeys(cleaned))  # Deduplicate preserving order

    @field_validator("dois")
    @classmethod
    def validate_dois(cls, v: list[str]) -> list[str]:
        """Validate DOI format and normalize."""
        doi_pattern = re.compile(r"^10\.\d{4,}/[^\s]+$")
        normalized = []

        for doi in v:
            # Strip common prefixes
            clean_doi = doi.strip()
            clean_doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", clean_doi)

            if not clean_doi:
                continue

            if not doi_pattern.match(clean_doi):
                raise ValueError(f"Invalid DOI format (expected '10.xxxx/yyyy'): {doi}")

            normalized.append(clean_doi)

        # Deduplicate
        return list(dict.fromkeys(normalized))


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

    url: HttpUrl | None = None
    """URL for remote MCP server."""

    @model_validator(mode="after")
    def validate_command_or_url(self) -> "McpServer":
        """Ensure exactly one of command or url is provided."""
        has_command = self.command is not None
        has_url = self.url is not None

        if not has_command and not has_url:
            raise ValueError("McpServer must have either 'command' (local) or 'url' (remote)")

        if has_command and has_url:
            raise ValueError("McpServer cannot have both 'command' and 'url'; choose one")

        return self

    @field_validator("command")
    @classmethod
    def validate_command(cls, v: list[str] | None) -> list[str] | None:
        """Validate command is non-empty list of non-empty strings."""
        if v is None:
            return None

        if not v:
            raise ValueError("Command list cannot be empty")

        for part in v:
            if not part.strip():
                raise ValueError("Command parts cannot be empty strings")

        return v


class ExtensionsConfig(BaseModel):
    """Extension points for specialized tools."""

    model_config = ConfigDict(extra="forbid")

    python_plugins: list[PythonPlugin] = Field(default_factory=list)
    """Python modules providing additional tools."""

    mcp_servers: list[McpServer] = Field(default_factory=list)
    """MCP servers providing additional tools (Phase 2)."""

    @model_validator(mode="after")
    def validate_unique_extensions(self) -> "ExtensionsConfig":
        """Ensure plugin modules and server names are unique."""
        # Check plugin module uniqueness
        plugin_modules = [p.module for p in self.python_plugins]
        seen_modules: set[str] = set()
        duplicate_modules: list[str] = []

        for module in plugin_modules:
            if module in seen_modules:
                duplicate_modules.append(module)
            seen_modules.add(module)

        if duplicate_modules:
            raise ValueError(f"Duplicate plugin modules: {', '.join(duplicate_modules)}")

        # Check MCP server name uniqueness
        server_names = [s.name for s in self.mcp_servers]
        seen_names: set[str] = set()
        duplicate_names: list[str] = []

        for name in server_names:
            if name in seen_names:
                duplicate_names.append(name)
            seen_names.add(name)

        if duplicate_names:
            raise ValueError(f"Duplicate MCP server names: {', '.join(duplicate_names)}")

        return self


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

    system_prompt: str | None = None
    """Custom system prompt template.

    If provided, replaces the default CommunityAssistant prompt.
    Supports placeholders that are substituted at runtime:
    - {name}: Community display name
    - {description}: Community description
    - {repo_list}: Formatted list of GitHub repos (if configured)
    - {paper_dois}: Formatted list of paper DOIs (if configured)
    - {additional_instructions}: Extra instructions passed at creation time

    Example:
        system_prompt: |
          You are an expert assistant for {name}.

          {description}

          Available repositories:
          {repo_list}
    """

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Validate ID is kebab-case (lowercase, hyphens, alphanumeric)."""
        v = v.strip()
        if not v:
            raise ValueError("Community ID cannot be empty")

        # Kebab-case: lowercase letters, numbers, hyphens (no leading/trailing hyphens)
        id_pattern = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
        if not id_pattern.match(v):
            raise ValueError(f"Community ID must be kebab-case (lowercase, hyphens): {v}")

        return v

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

    def get_sync_config(self) -> dict[str, Any]:
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

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "CommunitiesConfig":
        """Ensure all community IDs are unique."""
        seen_ids: set[str] = set()
        duplicates: list[str] = []

        for community in self.communities:
            if community.id in seen_ids:
                duplicates.append(community.id)
            seen_ids.add(community.id)

        if duplicates:
            raise ValueError(f"Duplicate community IDs found: {', '.join(duplicates)}")

        return self

    @classmethod
    def from_yaml(cls, path: Path) -> "CommunitiesConfig":
        """Load communities configuration from YAML file.

        Args:
            path: Path to communities.yaml file.

        Returns:
            Parsed and validated CommunitiesConfig.

        Raises:
            FileNotFoundError: If file doesn't exist.
            yaml.YAMLError: If YAML syntax is invalid.
            ValidationError: If YAML structure is invalid.
        """
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Failed to parse YAML file {path}: {e}") from e

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
