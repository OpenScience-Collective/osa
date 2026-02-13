"""Pydantic models for community configuration.

Defines the schema for community config.yaml files, enabling declarative
configuration of research community assistants.

Each community has its own config.yaml file (e.g., src/assistants/hed/config.yaml)
that is parsed directly as a CommunityConfig.

Example config.yaml:
    id: hed
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

import ipaddress
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

if TYPE_CHECKING:
    from src.tools.base import DocRegistry


class SSRFViolationError(ValueError):
    """Raised when URL violates SSRF protection rules."""

    pass


# Shared regex for OpenRouter model identifiers (creator/model-name)
_MODEL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+$")
_MODEL_ID_MAX_LENGTH = 100


def _validate_model_id(v: str | None, field_label: str = "Model identifier") -> str | None:
    """Validate an OpenRouter model identifier (creator/model-name).

    Args:
        v: The model string to validate, or None.
        field_label: Label used in error messages.

    Returns:
        The stripped model string, or None.

    Raises:
        ValueError: If the format is invalid or too long.
    """
    if v is None:
        return None

    v = v.strip()
    if not v:
        return None

    if not _MODEL_ID_PATTERN.match(v):
        raise ValueError(
            f"Invalid {field_label.lower()}: '{v}'. "
            "Must match pattern: provider/model-name "
            "(e.g., 'anthropic/claude-3.5-sonnet')"
        )

    if len(v) > _MODEL_ID_MAX_LENGTH:
        raise ValueError(f"{field_label} too long (max {_MODEL_ID_MAX_LENGTH} chars): {v[:50]}...")

    return v


class DocSource(BaseModel):
    """Documentation source configuration.

    Defines a documentation page to index and make available
    for retrieval by the assistant.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    """Human-readable document title."""

    url: HttpUrl
    """HTML page URL for user reference (included in responses)."""

    source_url: str | None = None
    """Raw markdown/content URL for fetching. Required if preload=True."""

    preload: bool = False
    """If True, content is preloaded and embedded in system prompt."""

    category: str = "general"
    """Category for organizing documents (e.g., 'core', 'specification', 'tools')."""

    type: Literal["sphinx", "mkdocs", "html", "markdown", "json"] = "html"
    """Documentation format type."""

    source_repo: str | None = None
    """GitHub repo for raw markdown sources (e.g., 'org/repo')."""

    description: str | None = None
    """Short description of what this documentation covers."""

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, v: str | None) -> str | None:
        """Validate source_url to prevent SSRF attacks.

        Blocks access to private IPs, localhost, and AWS metadata service
        to prevent attackers from probing internal infrastructure.

        Note: This validator only checks URL format and IP literals, not DNS
        resolution. Hostnames that resolve to private IPs (DNS rebinding attacks)
        are not prevented by this check and should be validated at fetch time.
        """
        if v is None:
            return None

        v = v.strip()
        if not v:
            return None

        # urlparse is highly reliable and doesn't raise for string inputs
        parsed = urlparse(v)

        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Invalid URL scheme '{parsed.scheme}'. Only http:// and https:// are allowed."
            )

        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"URL must have a valid hostname: {v}")

        if hostname in ("localhost", "127.0.0.1", "::1"):
            raise SSRFViolationError(
                f"Cannot fetch from localhost: {v}. "
                "Documentation must be hosted on a public server."
            )

        # Try to parse as IP address to check if it's private
        try:
            ip = ipaddress.ip_address(hostname)

            # Check link-local FIRST (before is_private) since link-local addresses
            # are also private, and we want the more specific error message
            # Link-local: 169.254.0.0/16 for IPv4 (AWS metadata), fe80::/10 for IPv6
            if ip.is_link_local:
                raise SSRFViolationError(
                    f"Cannot fetch from link-local address: {hostname}. "
                    "This prevents access to cloud metadata services like AWS at 169.254.169.254."
                )

            # Block private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
            if ip.is_private:
                raise SSRFViolationError(
                    f"Cannot fetch from private IP address: {hostname}. "
                    "Documentation must be hosted on a public server."
                )

            # Block loopback
            if ip.is_loopback:
                raise SSRFViolationError(f"Cannot fetch from loopback address: {hostname}")

        except SSRFViolationError:
            # Re-raise our SSRF validation errors
            raise
        except ValueError:
            # Not an IP address - it's a hostname, which is acceptable
            # (Note: Hostnames that resolve to private IPs are not checked here)
            pass

        return v

    @model_validator(mode="after")
    def validate_preload_has_source_url(self) -> "DocSource":
        """Ensure preloaded docs have a source_url for fetching."""
        if self.preload and not self.source_url:
            raise ValueError(
                f"DocSource '{self.title}' has preload=True but no source_url. "
                "Preloaded documents require a source_url to fetch content."
            )
        return self


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


class MailmanConfig(BaseModel):
    """Mailing list configuration for FAQ generation."""

    model_config = ConfigDict(extra="forbid")

    list_name: str
    """Mailing list identifier (e.g., 'eeglablist')."""

    base_url: HttpUrl
    """Base URL to pipermail archive."""

    display_name: str | None = None
    """Human-readable name."""

    start_year: int | None = None
    """Earliest year to sync (default: all available)."""


class DocstringsRepoConfig(BaseModel):
    """Configuration for extracting docstrings from a repository."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    """Repository in 'org/name' format (e.g., 'sccn/eeglab')."""

    branch: str = "main"
    """Default branch to extract from (e.g., 'main', 'develop', 'master')."""

    languages: list[Literal["matlab", "python"]] = Field(
        default_factory=lambda: ["matlab", "python"]
    )
    """Languages to extract docstrings from."""

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, v: str) -> str:
        """Validate repo matches 'org/repo' format."""
        repo_pattern = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
        v = v.strip()
        if not v:
            raise ValueError("Repository name cannot be empty")
        if not repo_pattern.match(v):
            raise ValueError(f"Repository must be in 'org/repo' format, got: {v}")
        return v

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, v: str) -> str:
        """Validate branch name is non-empty."""
        v = v.strip()
        if not v:
            raise ValueError("Branch name cannot be empty")
        return v


class DocstringsConfig(BaseModel):
    """Configuration for docstring extraction."""

    model_config = ConfigDict(extra="forbid")

    repos: list[DocstringsRepoConfig] = Field(default_factory=list)
    """Repositories to extract docstrings from."""

    @model_validator(mode="after")
    def validate_unique_repos(self) -> "DocstringsConfig":
        """Ensure all repo names are unique."""
        seen_repos: set[str] = set()
        duplicates: list[str] = []

        for repo_config in self.repos:
            if repo_config.repo in seen_repos:
                duplicates.append(repo_config.repo)
            seen_repos.add(repo_config.repo)

        if duplicates:
            raise ValueError(f"Duplicate docstring repos: {', '.join(duplicates)}")

        return self


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


class AgentConfig(BaseModel):
    """LLM agent configuration for FAQ generation tasks."""

    model_config = ConfigDict(extra="forbid")

    model: str
    """Model identifier in OpenRouter format (creator/model-name)."""

    provider: str | None = None
    """Provider routing preference (e.g., 'Anthropic', 'DeepInfra/FP8').

    Provider format examples:
    - 'Anthropic' - Direct Anthropic API for best performance
    - 'DeepInfra/FP8' - DeepInfra with FP8 (8-bit) quantization for cost reduction
    - 'Cerebras' - Cerebras for ultra-fast inference
    """

    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    """Sampling temperature for model responses."""

    enable_caching: bool = True
    """Enable prompt caching to reduce costs."""

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        """Validate model identifier format (provider/model-name)."""
        result = _validate_model_id(v, field_label="Model identifier")
        if not result:
            raise ValueError("Model identifier cannot be empty")
        return result

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str | None) -> str | None:
        """Validate provider identifier if specified."""
        if v is None:
            return None

        v = v.strip()
        if not v:
            return None

        # Reasonable length check for provider names
        if len(v) > 50:
            raise ValueError(f"Provider name too long (max 50 chars): {v[:30]}...")

        return v


class FAQSourceConfig(BaseModel):
    """Configuration for a specific FAQ source type."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    """Whether this source is enabled for FAQ generation."""

    min_messages: int = Field(default=2, ge=1)
    """Minimum messages in a thread to consider for FAQ.

    Default of 2 ensures at least a question and answer.
    """

    min_participants: int = Field(default=2, ge=1)
    """Minimum unique participants in a thread to consider for FAQ.

    Default of 2 ensures dialogue rather than monologue.
    """

    @model_validator(mode="after")
    def validate_minimums(self) -> "FAQSourceConfig":
        """Ensure minimum values make sense together.

        Multi-participant threads should have enough messages for conversation.
        """
        # A single-message thread from multiple participants is unusual
        # If we require multiple participants, we should require enough messages
        # for them to have a conversation
        if self.min_participants >= 2 and self.min_messages < 2:
            raise ValueError(
                f"min_messages ({self.min_messages}) should be at least 2 "
                f"when min_participants is {self.min_participants}"
            )
        return self


class FAQGenerationConfig(BaseModel):
    """FAQ generation configuration for threaded discussions.

    Supports multiple source types (mailman, discourse, forums) with
    configurable evaluation and summarization agents.
    """

    model_config = ConfigDict(extra="forbid")

    # Known source types that have sync implementations
    VALID_SOURCE_TYPES: ClassVar[set[str]] = {"mailman", "discourse", "github_discussions"}

    evaluation_agent: AgentConfig
    """Agent for scoring thread quality (many calls, needs speed/cost efficiency)."""

    summary_agent: AgentConfig
    """Agent for creating FAQ entries (fewer calls, needs quality)."""

    quality_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    """Minimum quality score (0.0-1.0) required for FAQ generation.

    Recommended ranges:
    - 0.5-0.6: Permissive, captures more FAQs but may include marginal content
    - 0.7-0.8: Balanced, good quality with reasonable coverage (recommended)
    - 0.9+: Restrictive, only highest quality content
    """

    sources: dict[str, FAQSourceConfig] = Field(default_factory=dict)
    """Source-specific settings for different discussion platforms.

    Valid source types: mailman, discourse, github_discussions
    """

    @field_validator("sources")
    @classmethod
    def validate_source_types(cls, v: dict[str, FAQSourceConfig]) -> dict[str, FAQSourceConfig]:
        """Validate source type keys are recognized."""
        if not v:
            # Empty sources dict is allowed during initial config
            return v

        invalid_types = set(v.keys()) - cls.VALID_SOURCE_TYPES
        if invalid_types:
            raise ValueError(
                f"Unknown source types: {', '.join(sorted(invalid_types))}. "
                f"Valid types: {', '.join(sorted(cls.VALID_SOURCE_TYPES))}"
            )

        return v

    @model_validator(mode="after")
    def validate_agent_roles(self) -> "FAQGenerationConfig":
        """Warn if agent configurations don't match their intended roles."""
        import warnings

        # Check if the same model is used for both (which defeats the purpose)
        if self.evaluation_agent.model == self.summary_agent.model:
            # This might be intentional for small communities, so warn rather than error
            warnings.warn(
                f"Both agents use the same model ({self.evaluation_agent.model}). "
                "Consider using a faster/cheaper model for evaluation_agent to reduce costs.",
                UserWarning,
                stacklevel=2,
            )

        return self


class BudgetConfig(BaseModel):
    """Budget limits and alert thresholds for a community.

    When configured, the scheduler periodically checks spend against
    these limits and creates GitHub issues when thresholds are exceeded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    daily_limit_usd: float = Field(..., gt=0, description="Maximum daily spend in USD")
    monthly_limit_usd: float = Field(..., gt=0, description="Maximum monthly spend in USD")
    alert_threshold_pct: float = Field(
        default=80.0,
        ge=0,
        le=100,
        description="Percentage of limit at which to trigger alert (default: 80%)",
    )

    @model_validator(mode="after")
    def validate_limits(self) -> "BudgetConfig":
        """Ensure daily limit does not exceed monthly limit."""
        if self.daily_limit_usd > self.monthly_limit_usd:
            raise ValueError(
                f"daily_limit_usd ({self.daily_limit_usd}) cannot exceed "
                f"monthly_limit_usd ({self.monthly_limit_usd})"
            )
        return self


class WidgetConfig(BaseModel):
    """Widget display configuration for frontend embedding.

    Controls how the chat widget appears and behaves when embedded on websites.
    All fields are optional; the frontend applies sensible defaults
    (title defaults to community name, placeholder to "Ask a question...").
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = Field(default=None, max_length=100)
    """Widget header title. Defaults to community name if not specified."""

    initial_message: str | None = Field(default=None, max_length=1000)
    """First greeting message shown when the widget opens."""

    placeholder: str | None = Field(default=None, max_length=200)
    """Input field placeholder text. Defaults to "Ask a question..." if not specified."""

    suggested_questions: list[str] = Field(default_factory=list)
    """Clickable suggestion buttons shown below the initial message."""

    @field_validator("title", "initial_message", "placeholder", mode="before")
    @classmethod
    def normalize_empty_strings(cls, v: str | None) -> str | None:
        """Normalize empty/whitespace-only strings to None."""
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    @field_validator("suggested_questions")
    @classmethod
    def validate_suggested_questions(cls, v: list[str]) -> list[str]:
        """Filter empty entries and enforce a reasonable maximum."""
        cleaned = [q.strip() for q in v if isinstance(q, str) and q.strip()]
        if len(cleaned) > 10:
            msg = f"Too many suggested questions ({len(cleaned)}). Maximum is 10."
            raise ValueError(msg)
        return cleaned

    def resolve(self, community_name: str) -> dict[str, Any]:
        """Return widget config with defaults applied."""
        return {
            "title": self.title or community_name or "Assistant",
            "initial_message": self.initial_message,
            "placeholder": self.placeholder or "Ask a question...",
            "suggested_questions": self.suggested_questions,
        }


class LinksConfig(BaseModel):
    """External links for a community (homepage, docs, repo, demo).

    All fields are optional; only populated links are exposed via the API.
    """

    model_config = ConfigDict(extra="forbid")

    homepage: HttpUrl | None = None
    """Primary community website URL."""

    documentation: HttpUrl | None = None
    """Documentation or tutorials URL."""

    repository: HttpUrl | None = None
    """Source code repository (GitHub org or repo URL)."""

    demo: HttpUrl | None = None
    """Live demo page URL for the community assistant."""

    def resolve(self) -> dict[str, str] | None:
        """Return only populated links as strings, or None if empty."""
        links = {k: str(v) for k, v in self.model_dump().items() if v is not None}
        return links or None


class SyncTypeSchedule(BaseModel):
    """Schedule configuration for a single sync type.

    Defines the cron expression for when this sync type should run.
    """

    model_config = ConfigDict(extra="forbid")

    cron: str
    """Cron expression (5-field) for scheduling (e.g., '0 2 * * *' for daily at 2am UTC)."""

    @field_validator("cron")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        """Validate cron expression format and field values."""
        from apscheduler.triggers.cron import CronTrigger

        v = v.strip()
        try:
            CronTrigger.from_crontab(v)
        except ValueError as e:
            raise ValueError(f"Invalid cron expression '{v}': {e}") from e
        return v


class SyncConfig(BaseModel):
    """Per-community sync schedule configuration.

    Each field corresponds to a sync type. Only types with both a schedule
    here AND the corresponding data config (e.g., github.repos, citations,
    mailman, docstrings, faq_generation) will be scheduled.
    """

    model_config = ConfigDict(extra="forbid")

    github: SyncTypeSchedule | None = None
    """Schedule for GitHub issues/PRs sync."""

    papers: SyncTypeSchedule | None = None
    """Schedule for academic papers sync."""

    docstrings: SyncTypeSchedule | None = None
    """Schedule for code docstring extraction sync."""

    mailman: SyncTypeSchedule | None = None
    """Schedule for mailing list archive sync."""

    faq: SyncTypeSchedule | None = None
    """Schedule for FAQ generation from discussions (uses LLM, costs money)."""

    beps: SyncTypeSchedule | None = None
    """Schedule for BIDS Extension Proposals sync (BIDS-specific)."""


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

    mailman: list[MailmanConfig] = Field(default_factory=list)
    """Mailing list configurations for FAQ generation."""

    docstrings: DocstringsConfig | None = None
    """Docstring extraction configuration for function documentation."""

    faq_generation: FAQGenerationConfig | None = None
    """FAQ generation configuration from threaded discussions (mailman, discourse, etc.)."""

    sync: SyncConfig | None = None
    """Per-community sync schedule configuration.

    Controls when each sync type runs for this community.
    Only sync types that also have their corresponding data config
    (e.g., github.repos for github sync) will actually be scheduled.

    Example:
        sync:
          github:
            cron: "0 2 * * *"       # daily at 2am UTC
          papers:
            cron: "0 3 * * 0"       # weekly Sunday at 3am UTC
    """

    extensions: ExtensionsConfig | None = None
    """Extension points for specialized tools."""

    enable_page_context: bool = True
    """Enable page context tool for widget embedding (default: True).

    When True, the assistant includes a fetch_current_page tool that
    retrieves content from the page where the widget is embedded.
    Set to False if the assistant won't be used in a widget context.
    """

    cors_origins: list[str] = Field(default_factory=list)
    """Allowed CORS origins for this community's widget embedding.

    Supports exact origins (e.g., 'https://hedtags.org') and wildcard
    subdomains (e.g., 'https://*.pages.dev'). These are aggregated with
    platform-level origins at API startup.
    """

    openrouter_api_key_env_var: str | None = None
    """Environment variable name for community's OpenRouter API key.

    If specified, the assistant will use the key from this environment variable
    instead of the platform-level default. This allows per-community API key
    control for cost attribution and management.

    Example:
        openrouter_api_key_env_var: "OPENROUTER_API_KEY_HED"

    The backend must have this environment variable set for the assistant to work.
    """

    default_model: str | None = None
    """Default LLM model for this community (OpenRouter format: creator/model-name).

    If specified, overrides the platform-level default_model for this community.
    Allows communities to use models better suited to their domain.

    Example:
        default_model: "anthropic/claude-3.5-sonnet"

    If not specified, uses the platform-level default from Settings.
    """

    default_model_provider: str | None = None
    """Provider routing preference for the default model (e.g., "Cerebras", "Together").

    Specifies where the model should run for optimal performance.
    Only applies if default_model is also specified.

    Example:
        default_model_provider: "Cerebras"

    If not specified, uses default routing for the model.
    """

    maintainers: list[str] = Field(default_factory=list)
    """GitHub usernames of community maintainers.

    Used for:
    - @mentioning in automated alert issues (budget alerts, etc.)
    - Documenting who is responsible for the community

    Example:
        maintainers:
          - octocat
          - janedoe
    """

    budget: BudgetConfig | None = None
    """Budget limits and alert thresholds for cost management.

    When configured, the scheduler checks spend against these limits
    and creates GitHub issues when thresholds are exceeded.

    Example:
        budget:
          daily_limit_usd: 5.0
          monthly_limit_usd: 50.0
          alert_threshold_pct: 80
    """

    widget: WidgetConfig | None = None
    """Widget configuration for frontend embedding.

    Controls display properties like title, placeholder text, initial message,
    and suggested questions. If not specified, the frontend uses defaults
    derived from the community name.

    Example:
        widget:
          title: HED Assistant
          placeholder: Ask about HED...
          initial_message: "Hi! I'm the HED Assistant..."
          suggested_questions:
            - What is HED and how is it used?
            - How do I annotate an event with HED tags?
    """

    links: LinksConfig | None = None
    """External links for the community (homepage, docs, repo, demo).

    Used by the dashboard to show quick-access links for each community.

    Example:
        links:
          homepage: https://www.hedtags.org
          documentation: https://www.hedtags.org/hed-resources
          repository: https://github.com/hed-standard
          demo: https://demo.osc.earth/?community=hed
    """

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, v: list[str]) -> list[str]:
        """Validate CORS origins are well-formed URL patterns."""
        origin_pattern = re.compile(
            r"^https?://"  # scheme
            r"(\*\.)?[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?"  # optional wildcard + first label
            r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*"  # additional labels
            r"(:\d{1,5})?$"  # optional port
        )
        validated = []
        for origin in v:
            origin = origin.strip()
            if not origin:
                continue
            if len(origin) > 255:
                raise ValueError(f"CORS origin too long (max 255 chars): {origin[:50]}...")
            if not origin_pattern.match(origin):
                raise ValueError(
                    f"Invalid CORS origin '{origin}'. Must be a valid origin "
                    f"(e.g., 'https://example.org' or 'https://*.pages.dev')"
                )
            if origin not in validated:
                validated.append(origin)
        return validated

    @field_validator("maintainers")
    @classmethod
    def validate_maintainers(cls, v: list[str]) -> list[str]:
        """Validate GitHub usernames in maintainers list.

        GitHub usernames: 1-39 chars, alphanumeric or hyphens,
        cannot start or end with hyphen.
        """
        gh_username_pattern = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$")
        validated = []
        for username in v:
            username = username.strip()
            if not username:
                continue
            if not gh_username_pattern.match(username):
                raise ValueError(
                    f"Invalid GitHub username: '{username}'. "
                    "Must be 1-39 alphanumeric characters or hyphens, "
                    "cannot start/end with hyphen."
                )
            if username not in validated:
                validated.append(username)
        return validated

    @field_validator("openrouter_api_key_env_var")
    @classmethod
    def validate_openrouter_api_key_env_var(cls, v: str | None) -> str | None:
        """Validate environment variable name to prevent accessing arbitrary secrets.

        Only allows variables matching OPENROUTER_API_KEY_* pattern to prevent
        communities from referencing other secrets like AWS credentials.
        """
        if v is None:
            return None

        v = v.strip()
        if not v:
            return None

        # Only allow OPENROUTER_API_KEY_* pattern (uppercase, underscores, alphanumeric)
        env_var_pattern = re.compile(r"^OPENROUTER_API_KEY_[A-Z0-9_]+$")
        if not env_var_pattern.match(v):
            raise ValueError(
                f"Invalid environment variable name: '{v}'. "
                "Must match pattern: OPENROUTER_API_KEY_[A-Z0-9_]+ "
                "(e.g., 'OPENROUTER_API_KEY_HED')"
            )

        return v

    @field_validator("default_model")
    @classmethod
    def validate_default_model(cls, v: str | None) -> str | None:
        """Validate model name format (provider/model-name)."""
        return _validate_model_id(v, field_label="Model name")

    @model_validator(mode="after")
    def validate_expensive_model_without_byok(self) -> "CommunityConfig":
        """Warn about expensive models without BYOK to prevent surprise billing.

        Communities using expensive models should provide their own API key
        to avoid unexpected platform costs.
        """
        if not self.default_model or self.openrouter_api_key_env_var:
            # No model specified or BYOK configured - OK
            return self

        # Hardcoded list of known expensive models (>$15/1M output tokens)
        # This prevents communities from setting ultra-expensive models on platform key
        # Pricing source: https://openrouter.ai/models (check regularly for updates)
        # Last updated: 2025-01-28
        # Maintainer: Update when new expensive models are released or pricing changes
        ultra_expensive_models = {
            "openai/o1",
            "openai/o1-preview",
            "anthropic/claude-opus-4",
            "anthropic/claude-3-opus",
        }

        # Extract base model name (remove date suffix like -2024-12-17 if present)
        # This allows dated versions of expensive models while not blocking cheaper variants
        base_model = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", self.default_model)

        # Check if base model is ultra-expensive (exact match only)
        if base_model in ultra_expensive_models:
            raise ValueError(
                f"Model '{self.default_model}' requires BYOK (Bring Your Own Key). "
                f"Add 'openrouter_api_key_env_var: OPENROUTER_API_KEY_<YOUR_COMMUNITY>' to your config.yaml, "
                f"then set that environment variable to your OpenRouter API key. "
                f"Ultra-expensive models (>$15/1M tokens) cannot use the platform API key."
            )

        return self

    def get_sync_config(self) -> dict[str, Any]:
        """Generate sync_config dict for registry compatibility.

        Returns format expected by AssistantInfo.sync_config.
        Includes both data sources and schedule configuration.
        """
        config: dict[str, Any] = {}
        if self.github:
            config["github_repos"] = self.github.repos
        if self.citations:
            config["paper_queries"] = self.citations.queries
            config["paper_dois"] = self.citations.dois
        if self.sync:
            schedules = {}
            for sync_type in ("github", "papers", "docstrings", "mailman", "faq", "beps"):
                schedule = getattr(self.sync, sync_type, None)
                if schedule:
                    schedules[sync_type] = schedule.cron
            if schedules:
                config["schedules"] = schedules
        return config

    def get_doc_registry(self) -> "DocRegistry":
        """Create a DocRegistry from this community's documentation config.

        Returns:
            DocRegistry with all configured documentation pages.
        """
        from src.tools.base import DocPage, DocRegistry

        doc_pages = [
            DocPage(
                title=doc.title,
                url=str(doc.url),
                source_url=doc.source_url or str(doc.url),
                preload=doc.preload,
                category=doc.category,
                description=doc.description or "",
            )
            for doc in self.documentation
        ]

        return DocRegistry(name=self.id, docs=doc_pages)

    @classmethod
    def from_yaml(cls, path: Path) -> "CommunityConfig":
        """Load a single community configuration from YAML file.

        Unlike CommunitiesConfig.from_yaml which loads a list of communities,
        this loads a single community's config.yaml file directly.

        Args:
            path: Path to the community's config.yaml file.

        Returns:
            Parsed and validated CommunityConfig.

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
