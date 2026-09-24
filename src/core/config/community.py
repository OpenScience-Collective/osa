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

import ast
import ipaddress
import logging
import re
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from urllib.parse import urlparse

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
    model_validator,
)

from src.core.config.notebook_lock import (
    NOTEBOOK_ENVIRONMENTS,
    NOTEBOOK_SITE_PYODIDE_VERSION,
    environment_base_url_problem,
    starter_path_problem,
)
from src.core.config.runtime_lock import lockfile_path_problem
from src.core.limits import (
    MAX_IMAGE_EDGE_PX,
    MAX_IMAGES,
    MAX_STDERR_CHARS,
    MAX_STDOUT_CHARS,
)

# Dependency-free by design, so importing it here keeps this module usable on a
# CLI-only install (see src/core/services/anthropic_models.py). Importing
# anthropic_llm instead would break `osa validate` for anyone without the
# server extra.
from src.core.services.anthropic_models import SAMPLING_MODELS, normalize_model

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.tools.base import DocRegistry


class SSRFViolationError(ValueError):
    """Raised when URL violates SSRF protection rules."""

    pass


# Shared regex for model identifiers. Accepts both the OpenRouter
# creator/model-name form (e.g. "anthropic/claude-3.5-sonnet") and a bare
# first-party id with no provider prefix (e.g. "claude-haiku-4-5", one of
# src.core.services.anthropic_llm.OFFERED_MODELS) -- the Claude Platform on
# AWS path has no separate "creator" segment.
_MODEL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+(/[a-zA-Z0-9._-]+)?$")
_MODEL_ID_MAX_LENGTH = 100


def _validate_model_id(v: str | None, field_label: str = "Model identifier") -> str | None:
    """Validate a model identifier: creator/model-name, or a bare first-party id.

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
            "Must match pattern: provider/model-name (e.g., 'anthropic/claude-3.5-sonnet') "
            "or a bare first-party id (e.g., 'claude-haiku-4-5')"
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

    live_search: bool = Field(default=False)
    """Expose an on-demand live paper search tool (opencite) for recent literature.

    Off by default: the tool adds external-API latency to a turn and queries
    OpenAlex anonymously. Communities opt in explicitly, and their prompt should
    tell the agent to ask the user before running it."""

    paper_labels: dict[str, str] = Field(default_factory=dict)
    """Optional human-readable labels for canonical DOIs (DOI -> short label).

    Used to label the stacked series in the public citations dashboard
    (e.g. '10.1038/s41597-019-0104-8' -> 'EEG-BIDS (Pernet 2019)'). Keys are
    normalized like ``dois`` so they match the stored ``cites_doi`` values.
    DOIs without a label fall back to the bare DOI in consumers."""

    @field_validator("paper_labels")
    @classmethod
    def validate_paper_labels(cls, v: dict[str, str]) -> dict[str, str]:
        """Normalize and validate DOI keys so labels line up with stored DOIs.

        Applies the same prefix-stripping and format check as ``dois`` so a
        mistyped key fails loudly at config load instead of silently producing
        a label that never matches a citation bucket. If two keys normalize to
        the same DOI, the last one wins (mirrors ``dois`` dedup behavior).
        """
        doi_pattern = re.compile(r"^10\.\d{4,}/[^\s]+$")
        normalized: dict[str, str] = {}
        for doi, label in v.items():
            clean_doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi.strip())
            if not clean_doi:
                continue
            if not doi_pattern.match(clean_doi):
                raise ValueError(
                    f"Invalid DOI key in paper_labels (expected '10.xxxx/yyyy'): {doi}"
                )
            normalized[clean_doi] = label
        return normalized

    aliases: dict[str, list[str]] = Field(default_factory=dict)
    """Version DOIs to merge into a canonical paper's citation count.

    Maps a primary DOI (from ``dois``) to other DOIs for the *same paper*
    (typically a preprint and the published version). OpenAlex splits citations
    across version records, so the citation sync queries them together and
    deduplicates, attributing the merged per-year counts to the primary DOI.
    Example: '10.1162/IMAG.a.136' -> ['10.1101/2024.02.13.580071']. Keys and
    values are normalized like ``dois``."""

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        """Normalize and validate primary + alias DOIs (same rules as ``dois``)."""
        doi_pattern = re.compile(r"^10\.\d{4,}/[^\s]+$")

        def _clean(doi: str) -> str:
            cleaned = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi.strip())
            if cleaned and not doi_pattern.match(cleaned):
                raise ValueError(f"Invalid DOI in aliases (expected '10.xxxx/yyyy'): {doi}")
            return cleaned

        normalized: dict[str, list[str]] = {}
        for primary, versions in v.items():
            clean_primary = _clean(primary)
            if not clean_primary:
                continue
            clean_versions: list[str] = []
            for d in versions:
                clean = _clean(d)
                if not clean:
                    # An empty version entry (e.g. `- ""`) is an authoring slip
                    # that would silently drop a version from the merge.
                    raise ValueError(f"Empty alias version DOI for primary '{primary}'")
                if clean not in clean_versions:
                    clean_versions.append(clean)
            normalized[clean_primary] = clean_versions
        return normalized

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

    @model_validator(mode="after")
    def validate_alias_primaries_in_dois(self) -> "CitationConfig":
        """Every alias primary DOI must be a tracked DOI, else the merge is a no-op.

        Runs after field validators, so both ``dois`` and ``aliases`` keys are
        already normalized and directly comparable.
        """
        unknown = set(self.aliases) - set(self.dois)
        if unknown:
            raise ValueError(f"aliases primary DOIs not present in dois: {sorted(unknown)}")
        return self


class DiscourseCategoryConfig(BaseModel):
    """A Discourse category to sync."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    """Category slug (e.g., 'support')."""

    id: int = Field(ge=1)
    """Category numeric ID."""


class DiscourseConfig(BaseModel):
    """Discourse/forum search configuration."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    """Base URL of the Discourse instance."""

    tags: list[str] = Field(default_factory=list)
    """Tags to filter forum topics by."""

    categories: list[DiscourseCategoryConfig] = Field(default_factory=list)
    """Optional categories to limit sync to. Empty means sync all."""


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


FULL_OUTPUT_TOOL_NAME = "get_full_output"
"""The client tool that reads back output a browser run kept locally.

Derived rather than configured: ``src.tools.client_tools.build_client_tools``
binds it whenever a python-runtime tool is bound and the caller declares it,
so a community never lists it and cannot misconfigure it. Declared here rather
than in ``src.tools.client_tools`` because this module must import without the
``server`` extra, and the name has to be reserved at config load.
"""

RESERVED_CLIENT_TOOL_NAMES = frozenset({FULL_OUTPUT_TOOL_NAME})
"""Names a community may not configure, because the server binds them itself."""

MAX_DECLARED_CLIENT_TOOLS = 8
"""How many client tool names one chat or resume request may declare."""

MAX_CONFIGURED_CLIENT_TOOLS = MAX_DECLARED_CLIENT_TOOLS - len(RESERVED_CLIENT_TOOL_NAMES)
"""How many client tools a community may configure.

Derived, not chosen: the widget declares every configured tool plus the
reserved ones, and a request declaring more than ``MAX_DECLARED_CLIENT_TOOLS``
is refused whole. A community allowed one more tool than this would get a 422
on every message, from a config that loaded without complaint.
"""


ClientToolRuntime = Literal["python"]
"""The runtimes a client tool may run in. One definition, read by the config and by
the public config response, so the widget is told exactly the set it switches on."""


class ClientToolConfig(BaseModel):
    """A tool the server binds to the model but never executes itself.

    Named ``ClientToolConfig`` rather than ``ClientTool`` to avoid colliding
    with ``src.tools.client_tools.ClientTool`` (the ``BaseTool`` subclass
    built from this config entry): both would otherwise need to be imported
    into the same module under the same name.

    See ``RuntimeConfig`` / ``PythonRuntimeConfig`` for the execution
    environment a ``runtime`` value refers to, and the model validator on
    ``CommunityConfig`` that requires a matching ``runtime`` section to
    exist whenever any ``client_tools`` entry is configured.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    """Tool name, as the model will see and call it (e.g. 'execute_code')."""

    runtime: ClientToolRuntime
    """Which configured runtime environment executes this tool's calls.

    Selects the argument schema the tool is bound with; see
    ``src.tools.client_tools.build_client_tools``. Only 'python' exists in
    phase 1 (browser execution, see .context/browser-execution-tool-design.md).
    """

    requires_permission: bool = True
    """Whether the browser must show a permission gate before running a call
    to this tool. Carried through to the `client_tools` graph node's
    `pending_client_call` so the enforcement point (phase 2's resume handler)
    does not need a second lookup back into this config."""

    description: str
    """Tool description shown to the model: what it does and when to call it."""

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        """Refuse a name the server binds itself.

        A configured tool under a reserved name would be bound beside the derived
        one, and the model would see two tools with one name and different
        argument shapes. Refusing it at load is the only point where that is a
        clear error rather than a confusing tool call.
        """
        if value in RESERVED_CLIENT_TOOL_NAMES:
            raise ValueError(
                f"'{value}' is reserved: the server binds it itself whenever a python "
                "client tool is bound, so it must not be configured."
            )
        return value


class RuntimeLimits(BaseModel):
    """Resource caps a community declares for a client tool's execution environment.

    These are what the community TELLS the browser it may produce. What the server
    will actually accept is fixed in `src.api.tool_results`, and the two must not be
    able to disagree, because a community cannot see the server's constants.

    So the fields the server also enforces are bounded BY those constants rather than
    written out again. Without the upper bounds, a community could validly declare
    `stdout_chars: 65536` or `images: 5`, the browser would honor its own config, and
    every result it sent would be rejected whole with a 422 by a cap it was never told
    about. The failure would look like the browser misbehaving; it would be the config
    lying. The defaults are the server's caps, so the common case needs no thought.

    Phase 1 defines these and enforces the server's own copy on the way in; it does not
    implement the browser-side client that produces them (phase 2, #431).
    """

    model_config = ConfigDict(extra="forbid")

    memory_mb: int = Field(default=1536, ge=64)
    """Maximum memory, in megabytes, the runtime may use.

    Not bounded against a server constant: the server never sees memory, and wasm32
    tops out between 2 and 4 GB regardless of what is written here."""

    stdout_chars: int = Field(default=MAX_STDOUT_CHARS, ge=256, le=MAX_STDOUT_CHARS)
    """Maximum captured stdout, in characters, per execution.

    Characters, not bytes, because that is what everything downstream counts:
    `ClientToolResult` bounds the field with `max_length`, and both the browser
    and the Python harness clip by string length. These fields were first named
    `*_bytes`, which told a community author that multibyte output costs more of
    the budget than it does."""

    stderr_chars: int = Field(default=MAX_STDERR_CHARS, ge=256, le=MAX_STDERR_CHARS)
    """Maximum captured stderr, in characters, per execution."""

    images: int = Field(default=MAX_IMAGES, ge=0, le=MAX_IMAGES)
    """Maximum number of images an execution may return. 0 disables images."""

    image_px: int = Field(default=1024, ge=16, le=MAX_IMAGE_EDGE_PX)
    """Maximum width or height, in pixels, of a returned image."""

    exec_seconds: int = Field(default=120, ge=1)
    """Maximum wall-clock time, in seconds, a single execution may run.

    Not bounded against a server constant: this is the browser's own clock, and the
    server neither measures nor enforces it."""


#: A prelude is a few lines of setup, not a program; this bounds what every reader's
#: browser runs before anything they asked for.
MAX_PRELUDE_CHARS = 4000


class PythonRuntimeConfig(BaseModel):
    """Configuration for the browser-side Python (Pyodide) runtime.

    Describes the environment a ``runtime: python`` client tool executes
    in: which Pyodide build to load, the wheels it adds to that build, what
    is loaded at startup, a prelude, and the resource caps in ``limits``.
    """

    model_config = ConfigDict(extra="forbid")

    pyodide_version: str
    """Pyodide distribution version to load in the browser."""

    lockfile: str | None = None
    """A Pyodide lock overlay, relative to the community's folder: the pure-Python
    wheels this runtime adds to the Pyodide distribution, each with its sha256, in
    ``wheels/`` beside it. Omit it when the distribution alone is enough.

    The server verifies every wheel against its entry when it loads the overlay,
    sends the entries in ``/config``, and serves the wheels itself, so a package named
    in ``preload`` resolves from here or from Pyodide's own lock. See
    ``src/core/config/runtime_lock.py``."""

    preload: list[str] = Field(default_factory=list)
    """Packages loaded when the runtime starts, from the Pyodide distribution or the
    lock overlay, with the dependencies their lock entries name."""

    allow_install: list[str] = Field(default_factory=list)
    """Requirements micropip installs when the runtime starts, each with ``deps=False``:
    from ``index_urls`` when the community gives any, and otherwise from micropip's own
    default index, PyPI. Nothing is installed after startup. A wheel pinned by sha256
    belongs in the lock overlay instead."""

    prelude: str | None = Field(default=None, max_length=MAX_PRELUDE_CHARS)
    """Python run once in the reader's browser after the runtime is sealed and before
    the first execution, with exactly the privileges executed code has.

    For setup every execution needs, such as registering a library's transport over
    ``osa.fetch``. It runs without the permission gate, which exists for code a model
    wrote; this is code the community wrote and reviewed. Top-level ``await`` is
    allowed. If it raises, the runtime fails to start, because every later execution
    would otherwise fail in a way that names the wrong cause."""

    preload_on: Literal["first_run", "widget_open", "first_message"] = "first_run"
    """When to trigger preloading: at the first execution, as soon as the widget opens, or
    as soon as the reader sends their first message.

    `first_message` overlaps the Python download with the model's first turn, without
    charging a reader who only opens the chat: the boot starts the moment the reader sends
    something, before the model has answered and well before any code execution asks for a
    Run gate.
    """

    fetch_allow: list[str] = Field(default_factory=list)
    """URL prefixes the runtime is allowed to fetch from."""

    index_urls: list[str] = Field(default_factory=list)
    """Package index URLs the runtime may install from."""

    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    """Resource caps for this runtime. Defaults to every ``RuntimeLimits``
    field's own default, so a community that has no reason to deviate from
    them can omit this key entirely rather than spelling out ``limits: {}``."""

    @field_validator("lockfile")
    @classmethod
    def _lockfile_stays_in_the_community_folder(cls, value: str | None) -> str | None:
        if value is None:
            return None
        problem = lockfile_path_problem(value)
        if problem is not None:
            raise ValueError(f"lockfile {value!r}: {problem}")
        return value

    @field_validator("prelude")
    @classmethod
    def _prelude_compiles(cls, value: str | None) -> str | None:
        """Compiled here so a syntax error fails a config check, not a reader's boot."""
        if value is None:
            return None
        try:
            compile(value, "<prelude>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        except SyntaxError as err:
            raise ValueError(f"prelude does not compile: {err}") from err
        return value


class RuntimeConfig(BaseModel):
    """Top-level execution environments available to this community's client tools."""

    model_config = ConfigDict(extra="forbid")

    python: PythonRuntimeConfig | None = None
    """The browser-side Python (Pyodide) runtime, if configured."""


#: A dataset id reaches the starter's Python source unescaped (``{{dataset_id}}``,
#: substituted client-side by ``notebook/open.js``), so a community's
#: ``dataset_pattern`` must never accept any of these, regardless of how loose
#: or careless the pattern's author was: a quote (either kind), a backslash, a
#: newline, and a space. Checked by ``NotebookConfig``'s own field validator
#: below, independent of ``open.js``'s generic client-side shape guard.
HOSTILE_DATASET_PROBES = ('"', "'", "\\", "\n", " ")


class NotebookConfig(BaseModel):
    """A community's starter notebook for the separate notebook.osc.earth/osa site.

    Optional, and unrelated to ``extensions.client_tools``/``runtime.python``
    above: those run model-written code in the chat widget, in the reader's
    browser, on the embedding page's own origin. This is a link a widget's own
    button (built separately, not by this config) can open into a NEW tab, on
    the notebook site's own origin, that drops a starter notebook -- this
    community's own template, with ``{{dataset_id}}`` filled in -- into
    JupyterLite's storage and opens it (issue #453,
    docs/adr/0011-the-notebook-site.md; ADR 0010 is what deferred building it).

    A community that sets this MUST pin ``runtime.python.pyodide_version`` to
    the notebook site's own Pyodide (see ``validate_notebook_needs_matching_
    pyodide`` on ``CommunityConfig``): one site loads one Pyodide, so a starter
    that ran under a different pin would be untested by anything that runs it.

    Also declares ``zarr_base`` and ``dataset_page_base``: the data host and
    website a starter reads, one per environment (production, develop), filled
    in at build time. Why they are build inputs: docs/adr/0011-the-notebook-site.md.
    """

    model_config = ConfigDict(extra="forbid")

    starter: str
    """Path to an ``.ipynb`` template, relative to the community's own folder.

    Must contain the literal token ``{{dataset_id}}`` in at least one cell's
    source (checked by ``src.core.config.notebook_lock.validate_notebook_
    starter``, not here: that check reads the file, and this model's own
    validator only checks the path's shape, the same split ``lockfile`` above
    draws against ``runtime_lock.py``)."""

    dataset_pattern: str
    """A regular expression a dataset id must match for this starter to open.

    Must be anchored (``^...$``): an unanchored pattern would match a dataset
    id that merely contains a valid-looking substring, and ``notebook/open.js``
    uses this pattern as the whole gate between an arbitrary query string and
    writing into the reader's own browser storage."""

    zarr_base: dict[str, str]
    """This community's Zarr host for each environment, keyed by
    ``NOTEBOOK_ENVIRONMENTS`` (``"production"``, ``"develop"``).

    ``scripts/build_notebook_site.py --environment`` fills one entry into the
    starter's ``{{zarr_base}}`` token
    (``src.core.config.notebook_lock.fill_build_time_tokens``), and refuses an
    environment this map does not declare.

    Example::

        zarr_base:
          production: https://zarr.nemar.org
          develop: https://zarr-test.nemar.org
    """

    dataset_page_base: dict[str, str]
    """This community's website for each environment, for the starter's link to
    a dataset's page; filled into ``{{dataset_page_base}}`` the same way as
    ``zarr_base``."""

    @field_validator("zarr_base", "dataset_page_base")
    @classmethod
    def _environment_url_maps_are_well_formed(
        cls, value: dict[str, str], info: ValidationInfo
    ) -> dict[str, str]:
        for environment, url in value.items():
            if environment not in NOTEBOOK_ENVIRONMENTS:
                raise ValueError(
                    f"{info.field_name} names an unrecognized environment {environment!r}; "
                    f"must be one of {NOTEBOOK_ENVIRONMENTS}"
                )
            problem = environment_base_url_problem(url)
            if problem is not None:
                raise ValueError(f"{info.field_name}[{environment!r}] {problem}")
        return value

    @field_validator("starter")
    @classmethod
    def _starter_stays_in_the_community_folder(cls, value: str) -> str:
        problem = starter_path_problem(value)
        if problem is not None:
            raise ValueError(f"starter {value!r}: {problem}")
        return value

    @field_validator("dataset_pattern")
    @classmethod
    def _dataset_pattern_is_anchored_and_compiles(cls, value: str) -> str:
        if not (value.startswith("^") and value.endswith("$")):
            raise ValueError(f"dataset_pattern must be anchored with ^...$: {value!r}")
        try:
            re.compile(value)
        except re.error as err:
            raise ValueError(f"dataset_pattern does not compile: {err}") from err
        return value

    @field_validator("dataset_pattern")
    @classmethod
    def _dataset_pattern_refuses_hostile_probes(cls, value: str) -> str:
        """Defense in depth: a starter substitutes the dataset id unescaped into
        Python source (``{{dataset_id}}``, filled in client-side by
        ``notebook/open.js``), so a pattern that would accept a quote, a
        backslash, a newline or a space is never safe to ship, independent of
        ``open.js``'s own generic shape guard (``^[A-Za-z0-9._-]{1,64}$``,
        checked before any community's own pattern). This check exists so a
        community's pattern can never rely on that client-side guard alone.
        """
        pattern = re.compile(value)
        hostile = [probe for probe in HOSTILE_DATASET_PROBES if pattern.match(probe)]
        if hostile:
            raise ValueError(
                f"dataset_pattern {value!r} would accept a hostile probe "
                f"{hostile!r}; a dataset id is substituted unescaped into the "
                "starter's Python source, so the pattern must never match a "
                "quote, a backslash, a newline, or a space"
            )
        return value


class ExtensionsConfig(BaseModel):
    """Extension points for specialized tools."""

    model_config = ConfigDict(extra="forbid")

    python_plugins: list[PythonPlugin] = Field(default_factory=list)
    """Python modules providing additional tools."""

    mcp_servers: list[McpServer] = Field(default_factory=list)
    """MCP servers providing additional tools (Phase 2)."""

    client_tools: list[ClientToolConfig] = Field(
        default_factory=list, max_length=MAX_CONFIGURED_CLIENT_TOOLS
    )
    """Tools the server binds so the model can call them, but never executes
    itself; the browser executes them instead (phase 1 plumbing, phase 2
    execution: #431). Uniqueness of names, and the requirement that a
    matching ``runtime`` section exists, are enforced on ``CommunityConfig``
    (see its model validator), not here: this model cannot see the
    top-level ``runtime`` sibling field."""

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
    """Model identifier: one of the offered Claude models.

    FAQ generation runs on the Claude Platform on AWS, so this must resolve
    through ``MODEL_ALIASES`` to an entry in ``OFFERED_MODELS``
    (``claude-haiku-4-5`` or ``claude-sonnet-5``). Legacy OpenRouter-style ids
    such as "anthropic/claude-haiku-4.5" still resolve; anything else raises
    at run time when the agent is built.
    """

    provider: str | None = None
    """Deprecated OpenRouter routing hint; ignored.

    This selected among OpenRouter's upstream hosts ('Anthropic',
    'DeepInfra/FP8', 'Cerebras'). The Claude Platform on AWS has no routing
    layer, so the field has no effect. It is still accepted so existing
    config.yaml files keep loading, and
    ``faq_summarizer._warn_if_provider_ignored`` logs a warning when one is
    set, rather than dropping it silently.
    """

    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    """Sampling temperature for model responses.

    Only honored on models that still accept sampling parameters
    (``claude-haiku-4-5``). ``claude-sonnet-5`` rejects ``temperature``, so it
    is not forwarded there; see ``SAMPLING_MODELS`` in
    src/core/services/anthropic_models.py. Setting one anyway is a warning at
    config load, not an error, so a community can switch models without its
    config failing to parse.
    """

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
        """Warn if agent configurations don't match their intended roles.

        Every check runs against the model id ``normalize_model`` resolves, not
        the literal string, so a config still carrying a legacy OpenRouter-style
        id ("anthropic/claude-sonnet-4.5") is judged as the model it will
        actually bill (``claude-sonnet-5``).

        Three things are worth saying at config load, all as warnings rather
        than errors so that a config keeps parsing (this schema backs the whole
        community, not just FAQ generation):

        - An unresolvable model, which would otherwise fail at the first
          FAQ run rather than at ``osa validate`` time.
        - The expensive model on the evaluation agent. The two-agent split
          exists so the thousands of scoring calls run on something cheap and
          only the few hundred surviving threads pay for quality. With two
          models offered, the wasteful shape is specifically "score everything
          with the expensive one": ``claude-haiku-4-5`` for both is the
          cheapest valid configuration, so warning about any repeated model
          would fire on the recommended setup.
        - A ``temperature`` on a model that ignores it, which is otherwise
          dropped silently at request time.
        """
        expensive = "claude-sonnet-5"

        for role, agent in (
            ("evaluation_agent", self.evaluation_agent),
            ("summary_agent", self.summary_agent),
        ):
            try:
                resolved = normalize_model(agent.model)
            except ValueError as e:
                warnings.warn(
                    f"{role}.model is not usable: {e} FAQ generation for this "
                    "community will fail until it is changed.",
                    UserWarning,
                    stacklevel=2,
                )
                continue

            # Name both ids when they differ, so a maintainer who wrote an
            # alias recognizes the config line the warning is about.
            as_written = agent.model if agent.model == resolved else f"{agent.model} ({resolved})"

            if role == "evaluation_agent" and resolved == expensive:
                warnings.warn(
                    f"evaluation_agent uses {as_written}, which scores every thread at "
                    "the higher rate and defeats the two-agent cost split. Use "
                    "claude-haiku-4-5 for evaluation and reserve the more capable model "
                    "for summary_agent.",
                    UserWarning,
                    stacklevel=2,
                )

            # Only an explicitly configured temperature is worth a warning; the
            # field's own default is not something the community chose.
            if "temperature" in agent.model_fields_set and resolved not in SAMPLING_MODELS:
                warnings.warn(
                    f"{role}.temperature={agent.temperature} is ignored: {as_written} "
                    "accepts only its default temperature, so the value is dropped "
                    "rather than sent. Remove the field, or use claude-haiku-4-5 for "
                    "this agent if the temperature matters.",
                    UserWarning,
                    stacklevel=2,
                )

        return self


class PublicFeedsConfig(BaseModel):
    """Opt-in flags for exposing community data as public, read-only JSON feeds.

    Both feeds are off by default. Enabling a feed publishes already-synced
    data (FAQ entries, citation counts) at unauthenticated endpoints so
    communities can build their own frontends on top of it.
    """

    model_config = ConfigDict(extra="forbid")

    faq: bool = False
    """Expose generated FAQ entries at GET /{community_id}/faq."""

    citations: bool = False
    """Expose canonical-paper citation counts at GET /{community_id}/citations."""


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

    theme_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    """Primary theme color as a hex code (e.g., '#008a79').

    Paints the launcher button and header surfaces (and every other surface that
    otherwise reads the platform blue). Defaults to the platform blue (#2563eb) if
    not specified. Pairs with `theme_text_color` (defaults to white, the text and
    icon color drawn on this surface) and `accent_color` (defaults to `theme_color`
    itself, this same color used as a foreground on the widget's white panel rather
    than as a surface); set `theme_text_color` when `theme_color` is too light for
    white text, and `accent_color` when `theme_color` is too light to read on white.
    """

    user_bubble_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    """Background of the reader's own message bubbles, as a hex code.

    Separate from `theme_color` so that setting one never changes the other: a community
    that sets only `theme_color` keeps the platform blue (#2563eb) bubbles it has always had.
    Pairs with `user_bubble_text_color` (default white); set both when the surface is light.
    """

    theme_text_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    """Text and icon color drawn ON surfaces painted with `theme_color`: the header, the
    launcher button, the send button, the primary buttons (such as Run), and any other
    element whose background is `theme_color`.

    Separate from `theme_color` so a community that sets only the theme keeps the white
    text every community has always had. Set this when `theme_color` is light enough that
    white text would fail contrast (below 4.5:1); the widget does not check this for you.
    """

    accent_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    """`theme_color` used as a FOREGROUND on the widget's white panel: link colors, icon
    colors, borders, focus rings, and `accent-color` on native checkboxes. Every one of
    these normally just reads `theme_color` directly, which is fine for a color dark
    enough to read on white; this field lets a community whose `theme_color` is a light
    surface color (and so unreadable as text on white) name a separate, darker foreground
    for the same brand hue. Defaults to `theme_color` itself when unset, so a community
    that only sets `theme_color` sees no change here.
    """

    user_bubble_text_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    """Text color in the reader's own message bubbles, painted on `user_bubble_color`.

    Separate from `user_bubble_color` for the same reason `theme_text_color` is separate
    from `theme_color`: a community that sets only `user_bubble_color` keeps white bubble
    text. Set this when `user_bubble_color` is too light for white text to read.
    """

    logo_url: str | None = Field(default=None, max_length=500)
    """URL to a custom logo/icon image for the widget header avatar.

    Must be an HTTP(S) URL or a path starting with ``/``.  When not set,
    the API auto-detects a ``logo.*`` file (SVG, PNG, JPG, JPEG, WEBP)
    in the community's folder.  Falls back to a default brain icon in
    the widget if no logo is found.
    """

    launcher: Literal["bubble", "capsule"] = "bubble"
    """The floating launcher's shape (#436).

    "bubble" (default) is today's single chat button. "capsule" adds two more circular
    icons, a notebook and a high-performance computing (HPC) placeholder, that expand
    out of the chat button once it is clicked (upward, or into a row on a narrow
    window); the chat button itself never moves. The
    notebook icon opens the community's starter notebook as a tab of the widget's panel
    (#470), so "capsule" requires a top-level ``notebook`` section
    (``CommunityConfig.validate_capsule_needs_notebook``).
    A community that never sets this renders exactly as it did before this field existed.
    """

    color_scheme: Literal["light", "auto"] = "light"
    """Whether the widget has a dark appearance (#469).

    "light" (default) was the widget's only appearance before this field. "auto"
    follows the reader's device setting, and a host page can also set light or dark
    explicitly with ``OSAChatWidget.setColorScheme`` (for a site with its own theme
    switch). In dark mode the panel, text and borders use the widget's dark palette,
    and ``accent_color``, chosen to read on the white panel, gives way to
    ``theme_color`` itself, or to a lighter shade of it when ``theme_color`` is too
    dark to read on the dark panel (``darkAccentFor`` in the widget).
    A community that never sets this renders exactly as it did before this field existed.
    """

    launcher_label: str | None = Field(default=None, max_length=40)
    """Tooltip text shown beside the collapsed launcher.

    Replaces the hardcoded "Ask me about <title>". Kept deliberately short: the greeting,
    suggested questions and the rest of the panel stay behind the click (#436). Unset
    keeps the existing hardcoded text.
    """

    @field_validator("launcher_label", mode="before")
    @classmethod
    def validate_launcher_label(cls, v: str | None) -> str | None:
        """Strip whitespace, normalize empty to None, and refuse markup."""
        if not isinstance(v, str):
            return v
        v = v.strip()
        if not v:
            return None
        if "<" in v or ">" in v:
            msg = "launcher_label must be plain text (no '<' or '>')"
            raise ValueError(msg)
        return v

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, v: str | None) -> str | None:
        """Ensure logo_url uses a safe scheme (http, https, or relative path)."""
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if not (v.startswith("http://") or v.startswith("https://") or v.startswith("/")):
            msg = "logo_url must use http://, https://, or be a path starting with '/'"
            raise ValueError(msg)
        return v

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

    def resolve(self, community_name: str, logo_url: str | None = None) -> dict[str, Any]:
        """Return widget config with defaults applied.

        Args:
            community_name: Display name used as fallback for title.
            logo_url: Fallback logo URL (e.g. from convention-based detection).
                      Only used when ``self.logo_url`` is not set.
        """
        result = {
            "title": self.title or community_name or "Assistant",
            "initial_message": self.initial_message,
            "placeholder": self.placeholder or "Ask a question...",
            "suggested_questions": self.suggested_questions,
            "logo_url": self.logo_url or logo_url,
        }
        if self.theme_color:
            result["theme_color"] = self.theme_color
        if self.user_bubble_color:
            result["user_bubble_color"] = self.user_bubble_color
        if self.theme_text_color:
            result["theme_text_color"] = self.theme_text_color
        if self.accent_color:
            result["accent_color"] = self.accent_color
        if self.user_bubble_text_color:
            result["user_bubble_text_color"] = self.user_bubble_text_color
        if self.launcher == "capsule":
            result["launcher"] = self.launcher
        if self.color_scheme != "light":
            result["color_scheme"] = self.color_scheme
        if self.launcher_label:
            result["launcher_label"] = self.launcher_label
        return result


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

    discourse: SyncTypeSchedule | None = None
    """Schedule for Discourse forum topic sync."""


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

    public_feeds: PublicFeedsConfig | None = None
    """Opt-in flags for exposing FAQ/citation data as public JSON feeds."""

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

    runtime: RuntimeConfig | None = None
    """Execution environments available to this community's client tools.

    Required whenever ``extensions.client_tools`` is non-empty (see
    ``validate_client_tools_have_runtime`` below): a client tool names a
    ``runtime`` value, and this is where that value's environment is
    actually configured. ``CommunityConfig`` is ``extra="forbid"``, so this
    key is rejected by any config written before this field existed;
    it and ``ClientToolConfig`` land together for that reason.

    Example:
        runtime:
          python:
            pyodide_version: "0.29.5"
            lockfile: "runtime/pyodide-lock.json"
            limits:
              memory_mb: 1536
    """

    notebook: NotebookConfig | None = None
    """A starter notebook for the separate notebook.osc.earth/osa site (issue #453).

    Requires ``runtime.python.pyodide_version`` to equal the notebook site's own
    pin (``validate_notebook_needs_matching_pyodide`` below), the same way
    ``extensions.client_tools`` requires a matching ``runtime`` section. Required in
    turn by ``widget.launcher: capsule``, whose notebook icon opens this starter
    (``validate_capsule_needs_notebook`` below).

    Example:
        notebook:
          starter: notebook/starter.ipynb
          dataset_pattern: "^(nm|ds|on|xx)[0-9]{6}$"
          zarr_base:
            production: https://zarr.nemar.org
            develop: https://zarr-test.nemar.org
          dataset_page_base:
            production: https://nemar.org
            develop: https://test.nemar.org
    """

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

    anthropic_api_key_env_var: str | None = None
    """Environment variable name for community's own Anthropic API key.

    If specified, the assistant will use the key from this environment variable
    instead of the platform key when routing to the Claude Platform on AWS.
    Checked before ``openrouter_api_key_env_var`` (see ``_resolve_provider`` in
    src/api/routers/community.py). This allows per-community API key control
    for cost attribution and management.

    Example:
        anthropic_api_key_env_var: "ANTHROPIC_API_KEY_HED"

    The backend must have this environment variable set for the assistant to work.
    """

    openrouter_api_key_env_var: str | None = None
    """Environment variable name for community's OpenRouter API key.

    If specified, the assistant will use the key from this environment variable
    instead of the platform-level default. This allows per-community API key
    control for cost attribution and management. Only reached when
    ``anthropic_api_key_env_var`` is not set: a community can still fund
    itself through OpenRouter instead of the Claude Platform on AWS.

    Example:
        openrouter_api_key_env_var: "OPENROUTER_API_KEY_HED"

    The backend must have this environment variable set for the assistant to work.
    """

    default_model: str | None = None
    """Default LLM model for this community: one of the offered Claude models.

    If specified, overrides the platform-level default_model for this community.
    Must resolve through ``MODEL_ALIASES`` to an entry in ``OFFERED_MODELS``
    (``claude-haiku-4-5`` or ``claude-sonnet-5``); legacy OpenRouter-style ids
    such as "anthropic/claude-haiku-4.5" still resolve.

    Example:
        default_model: "claude-haiku-4-5"

    If not specified, uses the platform-level default from Settings.
    """

    default_model_provider: str | None = None
    """OpenRouter-only routing hint (e.g., "Cerebras", "DeepInfra/FP8").

    Selects among OpenRouter's upstream hosts, and so only has an effect on a
    request that goes through OpenRouter: a caller's own OpenRouter key, or
    ``openrouter_api_key_env_var`` on this community. The Claude Platform on
    AWS has no routing layer and ignores it.

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

    @field_validator("anthropic_api_key_env_var")
    @classmethod
    def validate_anthropic_api_key_env_var(cls, v: str | None) -> str | None:
        """Validate environment variable name to prevent accessing arbitrary secrets.

        Only allows variables matching ANTHROPIC_API_KEY_* pattern to prevent
        communities from referencing other secrets like AWS credentials.
        """
        if v is None:
            return None

        stripped = v.strip()
        if not stripped:
            # A whitespace-only value coerces to None with no signal
            # otherwise, which is harder to notice than a wrong env var name
            # (that logs an error at request time in _resolve_provider): a
            # broken template substitution (e.g. an unrendered "{{ var }}")
            # would silently look identical to "not configured".
            logger.warning(
                "anthropic_api_key_env_var was set but blank/whitespace-only "
                "(%r); treating it as not configured. Check for a broken "
                "template substitution in config.yaml.",
                v,
            )
            return None
        v = stripped

        # Only allow ANTHROPIC_API_KEY_* pattern (uppercase, underscores, alphanumeric)
        env_var_pattern = re.compile(r"^ANTHROPIC_API_KEY_[A-Z0-9_]+$")
        if not env_var_pattern.match(v):
            raise ValueError(
                f"Invalid environment variable name: '{v}'. "
                "Must match pattern: ANTHROPIC_API_KEY_[A-Z0-9_]+ "
                "(e.g., 'ANTHROPIC_API_KEY_HED')"
            )

        return v

    @field_validator("openrouter_api_key_env_var")
    @classmethod
    def validate_openrouter_api_key_env_var(cls, v: str | None) -> str | None:
        """Validate environment variable name to prevent accessing arbitrary secrets.

        Only allows variables matching OPENROUTER_API_KEY_* pattern to prevent
        communities from referencing other secrets like AWS credentials.
        """
        if v is None:
            return None

        stripped = v.strip()
        if not stripped:
            # See the matching comment in validate_anthropic_api_key_env_var:
            # a whitespace-only value coercing to None silently is harder to
            # notice than a wrong env var name, which does log at request time.
            logger.warning(
                "openrouter_api_key_env_var was set but blank/whitespace-only "
                "(%r); treating it as not configured. Check for a broken "
                "template substitution in config.yaml.",
                v,
            )
            return None
        v = stripped

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
    def validate_default_model_resolvable(self) -> "CommunityConfig":
        """Warn when a bare default_model id won't resolve on either path.

        Format is already checked by ``validate_default_model`` above; this
        checks resolvability. A bare id (no "/") that ``normalize_model``
        rejects is neither an offered Anthropic model nor a recognized
        legacy alias. The Anthropic path fails safe (``_select_model``
        raises an HTTPException 400), but an OpenRouter-funded request for
        the same community silently falls back to a hardcoded default model
        (logged as an error in ``_select_model``, not surfaced at
        config-load time).

        A creator/model-name id (containing "/") is assumed to be a genuine
        OpenRouter slug and is not checked here: ``normalize_model`` only
        resolves first-party Anthropic ids and their legacy aliases, so it
        is not the right tool to validate an OpenRouter slug.
        """
        if not self.default_model or "/" in self.default_model:
            return self
        try:
            normalize_model(self.default_model)
        except ValueError:
            warnings.warn(
                f"default_model={self.default_model!r} is not an offered Anthropic "
                "model or a recognized alias. A request funded by an Anthropic key "
                "will get a clear 400; a request funded by an OpenRouter key will "
                "silently fall back to a hardcoded default model instead of the one "
                "configured here. Use one of "
                "src.core.services.anthropic_llm.OFFERED_MODELS, or an OpenRouter "
                "creator/model-name id if you intend to route there.",
                UserWarning,
                stacklevel=2,
            )
        return self

    @model_validator(mode="after")
    def validate_default_model_provider_has_effect(self) -> "CommunityConfig":
        """Warn when default_model_provider is set but will be ignored.

        ``_select_model`` (src/api/routers/community.py) only forwards
        ``default_model_provider`` when ``default_model`` is itself an
        OpenRouter creator/model-name id (contains "/") on a request that
        goes through OpenRouter. A bare id is ignored on both paths: the
        Anthropic path has no routing layer at all, and the OpenRouter path
        maps a bare id to its own OpenRouter slug and always drops the
        provider hint in that branch (see the "Phase 2" comment there).
        This mirrors ``faq_summarizer._warn_if_provider_ignored``'s warning
        for the analogous ``AgentConfig.provider`` field.
        """
        if self.default_model_provider and (
            not self.default_model or "/" not in self.default_model
        ):
            warnings.warn(
                f"default_model_provider={self.default_model_provider!r} is ignored: "
                "it only has an effect when default_model is itself an OpenRouter "
                "creator/model-name id (e.g. 'deepinfra/some-model'), not a bare id "
                f"like {self.default_model!r}. Remove the field, or set default_model "
                "to an OpenRouter-format id if routing control is needed.",
                UserWarning,
                stacklevel=2,
            )
        return self

    @model_validator(mode="after")
    def validate_expensive_model_without_byok(self) -> "CommunityConfig":
        """Warn about expensive models without BYOK to prevent surprise billing.

        Communities using expensive models should provide their own API key
        to avoid unexpected platform costs. This guard only concerns
        OpenRouter-format ids: the Anthropic offering
        (src.core.services.anthropic_llm.OFFERED_MODELS) is deliberately
        limited to two cost-capped models, so there is no ultra-expensive
        Anthropic id a community's default_model could resolve to.
        """
        if (
            not self.default_model
            or self.openrouter_api_key_env_var
            or self.anthropic_api_key_env_var
        ):
            # No model specified, or the community funds itself (either
            # provider) - OK
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
                f"Add 'openrouter_api_key_env_var: OPENROUTER_API_KEY_<YOUR_COMMUNITY>' to your "
                f"config.yaml and set that environment variable to your OpenRouter API key -- "
                f"or, to use the Anthropic offering instead, set 'default_model' to one of the "
                f"models in src.core.services.anthropic_llm.OFFERED_MODELS (e.g. "
                f"'claude-haiku-4-5'), which are cost-capped and never require BYOK. "
                f"Ultra-expensive models (>$15/1M tokens) cannot use the platform API key."
            )

        return self

    @model_validator(mode="after")
    def validate_client_tools_have_runtime(self) -> "CommunityConfig":
        """A configured client tool must name a runtime this community configures.

        Runs on ``CommunityConfig`` rather than ``ExtensionsConfig`` because
        ``runtime`` is this model's own field, not a sibling
        ``ExtensionsConfig`` can see. Also enforces unique ``client_tools``
        names here, for the same reason ``ExtensionsConfig.
        validate_unique_extensions`` enforces uniqueness of plugin modules
        and MCP server names on itself: one entry silently shadowing
        another under the same name is exactly the kind of config mistake
        that should fail at load time, not at the first tool call that
        picks the wrong one.
        """
        client_tools = self.extensions.client_tools if self.extensions else []
        if not client_tools:
            return self

        names = [entry.name for entry in client_tools]
        seen: set[str] = set()
        duplicates: list[str] = []
        for name in names:
            if name in seen:
                duplicates.append(name)
            seen.add(name)
        if duplicates:
            raise ValueError(f"Duplicate client_tools names: {', '.join(duplicates)}")

        if self.runtime is None:
            raise ValueError(
                "extensions.client_tools is set but no top-level 'runtime' "
                "section is configured. Add a 'runtime:' section describing "
                "the execution environment(s) the declared client tools run in."
            )

        # Which `runtime` section each declared runtime requires. A mapping rather
        # than a chain of `elif`s, because a chain has no else: adding a runtime to
        # ClientToolConfig's Literal and forgetting a branch here would let a community
        # declare a client tool whose execution environment was never configured. The
        # tool would bind, the model would call it, the call would park, and nothing
        # would ever answer it. A missing entry here is a KeyError at config load
        # instead, which is loud and immediate.
        required_section = {"python": "python"}

        for entry in client_tools:
            section = required_section[entry.runtime]
            if getattr(self.runtime, section, None) is None:
                raise ValueError(
                    f"client_tools entry '{entry.name}' declares runtime: "
                    f"{entry.runtime}, but runtime.{section} is not configured."
                )

        return self

    @model_validator(mode="after")
    def validate_notebook_needs_matching_pyodide(self) -> "CommunityConfig":
        """A community with a notebook starter must be pinned to the notebook
        site's own Pyodide, because one site loads one Pyodide (docs/adr/
        0011-the-notebook-site.md). A community pinned to a different version
        would parse, and its own widget runtime would work fine, while its
        notebook starter ran under an interpreter nothing tested it against --
        exactly the failure mode that stays invisible until a reader opens it.
        """
        if self.notebook is None:
            return self
        python = self.runtime.python if self.runtime else None
        pinned = python.pyodide_version if python else None
        if pinned != NOTEBOOK_SITE_PYODIDE_VERSION:
            raise ValueError(
                "notebook is configured but runtime.python.pyodide_version is "
                f"{pinned!r}, not {NOTEBOOK_SITE_PYODIDE_VERSION!r}, the notebook "
                "site's own pin. One notebook site loads one Pyodide."
            )
        return self

    @model_validator(mode="after")
    def validate_capsule_needs_notebook(self) -> "CommunityConfig":
        """The capsule launcher's notebook icon opens the community's starter on the
        notebook site as a tab of the widget's panel (#470). Without a ``notebook``
        section the site has no starter for the community, so the icon would open
        a tab that can only report an error. Refused here rather than hidden in the
        widget, so a community asking for the capsule learns what it also needs.
        """
        widget = self.widget
        if widget is not None and widget.launcher == "capsule" and self.notebook is None:
            raise ValueError(
                "widget.launcher is 'capsule' but no notebook section is configured. "
                "The capsule's notebook icon opens the community's starter notebook "
                "(docs/community-notebook.md)."
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
