"""Tests for community configuration models.

Tests cover:
- Pydantic model validation
- YAML loading and parsing
- Config serialization
"""

from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from pydantic import ValidationError

from src.core.config.community import (
    BudgetConfig,
    CitationConfig,
    CommunitiesConfig,
    CommunityConfig,
    DocSource,
    ExtensionsConfig,
    GitHubConfig,
    McpServer,
    PythonPlugin,
)


class TestDocSource:
    """Tests for DocSource model."""

    def test_valid_doc_source(self) -> None:
        """Should create DocSource with valid inputs."""
        doc = DocSource(
            title="Example Docs",
            url="https://docs.example.com/",
            type="sphinx",
            source_repo="org/repo",
        )
        assert doc.title == "Example Docs"
        assert str(doc.url) == "https://docs.example.com/"
        assert doc.type == "sphinx"
        assert doc.source_repo == "org/repo"

    def test_doc_source_defaults(self) -> None:
        """Should use default values for optional fields."""
        doc = DocSource(title="Docs", url="https://docs.example.com/")
        assert doc.type == "html"
        assert doc.source_repo is None
        assert doc.description is None
        assert doc.preload is False
        assert doc.category == "general"

    def test_doc_source_preload_requires_source_url(self) -> None:
        """Should require source_url when preload is True."""
        with pytest.raises(ValidationError, match="preload=True but no source_url"):
            DocSource(
                title="Preloaded Docs",
                url="https://docs.example.com/",
                preload=True,
            )

        # Should pass with source_url
        doc = DocSource(
            title="Preloaded Docs",
            url="https://docs.example.com/",
            source_url="https://raw.example.com/content.md",
            preload=True,
        )
        assert doc.preload is True
        assert doc.source_url == "https://raw.example.com/content.md"

    def test_invalid_url_raises_error(self) -> None:
        """Should reject invalid URLs."""
        with pytest.raises(ValidationError):
            DocSource(title="Docs", url="not-a-url")

    def test_invalid_type_raises_error(self) -> None:
        """Should reject invalid documentation types."""
        with pytest.raises(ValidationError):
            DocSource(title="Docs", url="https://docs.example.com/", type="invalid")


class TestGitHubConfig:
    """Tests for GitHubConfig model."""

    def test_valid_github_config(self) -> None:
        """Should create GitHubConfig with valid inputs."""
        config = GitHubConfig(repos=["org/repo1", "org/repo2"])
        assert len(config.repos) == 2
        assert "org/repo1" in config.repos

    def test_empty_repos_default(self) -> None:
        """Should default to empty list."""
        config = GitHubConfig()
        assert config.repos == []

    def test_validates_repo_format(self) -> None:
        """Should validate repo format."""
        with pytest.raises(ValidationError, match="'org/repo' format"):
            GitHubConfig(repos=["invalid-repo-format"])

        with pytest.raises(ValidationError, match="'org/repo' format"):
            GitHubConfig(repos=["org/repo", "bad format"])

    def test_rejects_empty_repo_names(self) -> None:
        """Should reject empty repo names."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            GitHubConfig(repos=[""])

        with pytest.raises(ValidationError, match="cannot be empty"):
            GitHubConfig(repos=["org/repo", "  "])

    def test_deduplicates_repos(self) -> None:
        """Should deduplicate repo names."""
        config = GitHubConfig(repos=["org/repo", "org/repo", "org/other"])
        assert len(config.repos) == 2
        assert "org/repo" in config.repos
        assert "org/other" in config.repos

    def test_accepts_valid_repo_patterns(self) -> None:
        """Should accept various valid repo patterns."""
        config = GitHubConfig(
            repos=[
                "org/repo",
                "my-org/my-repo",
                "org123/repo456",
                "org.name/repo.name",
                "org_name/repo_name",
            ]
        )
        assert len(config.repos) == 5


class TestCitationConfig:
    """Tests for CitationConfig model."""

    def test_valid_citation_config(self) -> None:
        """Should create CitationConfig with valid inputs."""
        config = CitationConfig(
            queries=["query 1", "query 2"],
            dois=["10.1234/example"],
        )
        assert len(config.queries) == 2
        assert len(config.dois) == 1

    def test_empty_defaults(self) -> None:
        """Should default to empty lists."""
        config = CitationConfig()
        assert config.queries == []
        assert config.dois == []

    def test_validates_doi_format(self) -> None:
        """Should validate DOI format."""
        with pytest.raises(ValidationError, match="Invalid DOI format"):
            CitationConfig(dois=["invalid-doi"])

        with pytest.raises(ValidationError, match="Invalid DOI format"):
            CitationConfig(dois=["10.1234/valid", "bad-doi"])

    def test_normalizes_doi_prefixes(self) -> None:
        """Should strip common DOI URL prefixes."""
        config = CitationConfig(
            dois=[
                "10.1234/example",
                "https://doi.org/10.5678/test",
                "http://dx.doi.org/10.9012/paper",
                "doi.org/10.3456/article",
            ]
        )
        assert "10.1234/example" in config.dois
        assert "10.5678/test" in config.dois
        assert "10.9012/paper" in config.dois
        assert "10.3456/article" in config.dois
        # Should not contain prefixes
        for doi in config.dois:
            assert not doi.startswith("http")
            assert not doi.startswith("doi.org")

    def test_deduplicates_dois(self) -> None:
        """Should deduplicate DOIs."""
        config = CitationConfig(dois=["10.1234/example", "10.1234/example", "10.5678/other"])
        assert len(config.dois) == 2
        assert "10.1234/example" in config.dois
        assert "10.5678/other" in config.dois

    def test_deduplicates_queries(self) -> None:
        """Should deduplicate queries."""
        config = CitationConfig(queries=["query 1", "query 1", "query 2"])
        assert len(config.queries) == 2
        assert "query 1" in config.queries
        assert "query 2" in config.queries

    def test_removes_empty_queries(self) -> None:
        """Should remove empty queries."""
        config = CitationConfig(queries=["query 1", "", "  ", "query 2"])
        assert len(config.queries) == 2
        assert "query 1" in config.queries
        assert "query 2" in config.queries

    def test_skips_empty_dois(self) -> None:
        """Should skip empty DOI strings."""
        config = CitationConfig(dois=["10.1234/example", "", "  "])
        assert len(config.dois) == 1
        assert config.dois[0] == "10.1234/example"


class TestPythonPlugin:
    """Tests for PythonPlugin model."""

    def test_valid_plugin(self) -> None:
        """Should create PythonPlugin with valid inputs."""
        plugin = PythonPlugin(
            module="src.tools.custom",
            tools=["tool1", "tool2"],
        )
        assert plugin.module == "src.tools.custom"
        assert plugin.tools == ["tool1", "tool2"]

    def test_plugin_without_tools(self) -> None:
        """Should allow None for tools (import all)."""
        plugin = PythonPlugin(module="src.tools.custom")
        assert plugin.tools is None


class TestMcpServer:
    """Tests for McpServer model."""

    def test_valid_local_server(self) -> None:
        """Should create McpServer with command (local)."""
        server = McpServer(name="test-server", command=["uvx", "test-mcp"])
        assert server.name == "test-server"
        assert server.command == ["uvx", "test-mcp"]
        assert server.url is None

    def test_valid_remote_server(self) -> None:
        """Should create McpServer with URL (remote)."""
        server = McpServer(name="test-server", url="https://example.com/mcp")
        assert server.name == "test-server"
        assert server.url is not None
        assert server.command is None

    def test_requires_command_or_url(self) -> None:
        """Should require either command or url."""
        with pytest.raises(ValidationError, match="either 'command'.*or 'url'"):
            McpServer(name="test-server")

    def test_rejects_both_command_and_url(self) -> None:
        """Should reject both command and url."""
        with pytest.raises(ValidationError, match="cannot have both"):
            McpServer(
                name="test-server",
                command=["uvx", "test"],
                url="https://example.com/mcp",
            )

    def test_rejects_empty_command(self) -> None:
        """Should reject empty command list."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            McpServer(name="test-server", command=[])

    def test_rejects_empty_command_parts(self) -> None:
        """Should reject empty strings in command."""
        with pytest.raises(ValidationError, match="cannot be empty strings"):
            McpServer(name="test-server", command=["uvx", ""])

        with pytest.raises(ValidationError, match="cannot be empty strings"):
            McpServer(name="test-server", command=["", "test"])


class TestExtensionsConfig:
    """Tests for ExtensionsConfig model."""

    def test_valid_extensions(self) -> None:
        """Should create ExtensionsConfig with valid inputs."""
        config = ExtensionsConfig(
            python_plugins=[
                PythonPlugin(module="src.tools.custom"),
            ]
        )
        assert len(config.python_plugins) == 1

    def test_empty_defaults(self) -> None:
        """Should default to empty lists."""
        config = ExtensionsConfig()
        assert config.python_plugins == []
        assert config.mcp_servers == []

    def test_rejects_duplicate_plugin_modules(self) -> None:
        """Should reject duplicate plugin modules."""
        with pytest.raises(ValidationError, match="Duplicate plugin modules"):
            ExtensionsConfig(
                python_plugins=[
                    PythonPlugin(module="src.tools.custom"),
                    PythonPlugin(module="src.tools.custom"),
                ]
            )

    def test_rejects_duplicate_server_names(self) -> None:
        """Should reject duplicate MCP server names."""
        with pytest.raises(ValidationError, match="Duplicate MCP server names"):
            ExtensionsConfig(
                mcp_servers=[
                    McpServer(name="server1", command=["uvx", "test"]),
                    McpServer(name="server1", url="https://example.com/mcp"),
                ]
            )

    def test_allows_unique_extensions(self) -> None:
        """Should allow unique plugins and servers."""
        config = ExtensionsConfig(
            python_plugins=[
                PythonPlugin(module="src.tools.one"),
                PythonPlugin(module="src.tools.two"),
            ],
            mcp_servers=[
                McpServer(name="server1", command=["uvx", "test1"]),
                McpServer(name="server2", command=["uvx", "test2"]),
            ],
        )
        assert len(config.python_plugins) == 2
        assert len(config.mcp_servers) == 2


class TestBudgetConfig:
    """Tests for BudgetConfig model."""

    def test_valid_budget_config(self) -> None:
        """Should create BudgetConfig with valid inputs."""
        config = BudgetConfig(
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert config.daily_limit_usd == 5.0
        assert config.monthly_limit_usd == 50.0
        assert config.alert_threshold_pct == 80.0

    def test_default_alert_threshold(self) -> None:
        """Should default alert_threshold_pct to 80.0."""
        config = BudgetConfig(daily_limit_usd=5.0, monthly_limit_usd=50.0)
        assert config.alert_threshold_pct == 80.0

    def test_rejects_zero_daily_limit(self) -> None:
        """Should reject zero daily limit."""
        with pytest.raises(ValidationError):
            BudgetConfig(daily_limit_usd=0.0, monthly_limit_usd=50.0)

    def test_rejects_negative_daily_limit(self) -> None:
        """Should reject negative daily limit."""
        with pytest.raises(ValidationError):
            BudgetConfig(daily_limit_usd=-1.0, monthly_limit_usd=50.0)

    def test_rejects_zero_monthly_limit(self) -> None:
        """Should reject zero monthly limit."""
        with pytest.raises(ValidationError):
            BudgetConfig(daily_limit_usd=5.0, monthly_limit_usd=0.0)

    def test_rejects_negative_threshold(self) -> None:
        """Should reject negative alert threshold."""
        with pytest.raises(ValidationError):
            BudgetConfig(
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                alert_threshold_pct=-1.0,
            )

    def test_rejects_threshold_over_100(self) -> None:
        """Should reject alert threshold over 100."""
        with pytest.raises(ValidationError):
            BudgetConfig(
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                alert_threshold_pct=101.0,
            )

    def test_rejects_extra_fields(self) -> None:
        """Should reject extra fields (strict schema)."""
        with pytest.raises(ValidationError):
            BudgetConfig(
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                unknown_field="value",  # type: ignore
            )


class TestCommunityConfigBudget:
    """Tests for CommunityConfig.budget field."""

    def test_budget_none_by_default(self) -> None:
        """Should default budget to None."""
        config = CommunityConfig(id="test", name="Test", description="Test")
        assert config.budget is None

    def test_budget_config_set(self) -> None:
        """Should accept valid budget config."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            budget=BudgetConfig(
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
            ),
        )
        assert config.budget is not None
        assert config.budget.daily_limit_usd == 5.0

    def test_budget_from_yaml_dict(self) -> None:
        """Should parse budget from YAML-like dict."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            budget={
                "daily_limit_usd": 10.0,
                "monthly_limit_usd": 100.0,
                "alert_threshold_pct": 90.0,
            },
        )
        assert config.budget.daily_limit_usd == 10.0
        assert config.budget.alert_threshold_pct == 90.0


class TestCommunityConfig:
    """Tests for CommunityConfig model."""

    def test_valid_community(self) -> None:
        """Should create CommunityConfig with valid inputs."""
        config = CommunityConfig(
            id="test",
            name="Test Community",
            description="A test community",
        )
        assert config.id == "test"
        assert config.name == "Test Community"
        assert config.status == "available"

    def test_full_community_config(self) -> None:
        """Should create CommunityConfig with all fields."""
        config = CommunityConfig(
            id="hed",
            name="HED",
            description="HED annotation",
            status="available",
            documentation=[
                DocSource(
                    title="HED Resources",
                    url="https://hedtags.org/hed-resources/",
                    type="sphinx",
                ),
            ],
            github=GitHubConfig(repos=["hed-standard/hed-python"]),
            citations=CitationConfig(
                queries=["HED annotation"],
                dois=["10.1234/hed"],
            ),
            extensions=ExtensionsConfig(
                python_plugins=[
                    PythonPlugin(module="src.assistants.hed.tools"),
                ]
            ),
        )
        assert len(config.documentation) == 1
        assert config.github is not None
        assert len(config.github.repos) == 1
        assert config.citations is not None
        assert len(config.citations.queries) == 1
        assert config.enable_page_context is True  # Default value

    def test_get_sync_config(self) -> None:
        """Should generate sync_config dict from community config."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            github=GitHubConfig(repos=["org/repo"]),
            citations=CitationConfig(
                queries=["query"],
                dois=["10.1234/doi"],
            ),
        )
        sync = config.get_sync_config()
        assert sync["github_repos"] == ["org/repo"]
        assert sync["paper_queries"] == ["query"]
        assert sync["paper_dois"] == ["10.1234/doi"]

    def test_get_sync_config_empty(self) -> None:
        """Should return empty dict when no sync-related config."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
        )
        sync = config.get_sync_config()
        assert sync == {}

    def test_invalid_status_raises_error(self) -> None:
        """Should reject invalid status values."""
        with pytest.raises(ValidationError):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                status="invalid",  # type: ignore
            )

    def test_validates_kebab_case_id(self) -> None:
        """Should validate ID is kebab-case."""
        # Valid kebab-case IDs
        valid_ids = ["hed", "bids-validator", "eeglab-2024", "my-tool"]
        for id_val in valid_ids:
            config = CommunityConfig(id=id_val, name="Test", description="Test")
            assert config.id == id_val

    def test_rejects_invalid_id_format(self) -> None:
        """Should reject non-kebab-case IDs."""
        with pytest.raises(ValidationError, match="kebab-case"):
            CommunityConfig(id="InvalidCase", name="Test", description="Test")

        with pytest.raises(ValidationError, match="kebab-case"):
            CommunityConfig(id="with spaces", name="Test", description="Test")

        with pytest.raises(ValidationError, match="kebab-case"):
            CommunityConfig(id="with_underscores", name="Test", description="Test")

        with pytest.raises(ValidationError, match="kebab-case"):
            CommunityConfig(id="with.dots", name="Test", description="Test")

    def test_rejects_empty_id(self) -> None:
        """Should reject empty ID."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            CommunityConfig(id="", name="Test", description="Test")

        with pytest.raises(ValidationError, match="cannot be empty"):
            CommunityConfig(id="  ", name="Test", description="Test")


class TestCommunityConfigCorsOrigins:
    """Tests for CommunityConfig.cors_origins validation."""

    def test_valid_exact_origins(self) -> None:
        """Should accept valid exact origin URLs."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=[
                "https://example.org",
                "https://www.hedtags.org",
                "http://localhost:3000",
                "https://my-site.example.com:8080",
            ],
        )
        assert len(config.cors_origins) == 4

    def test_valid_wildcard_origins(self) -> None:
        """Should accept valid wildcard subdomain patterns."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=[
                "https://*.pages.dev",
                "https://*.osa-demo.pages.dev",
                "http://*.localhost:3000",
            ],
        )
        assert len(config.cors_origins) == 3

    def test_defaults_to_empty(self) -> None:
        """Should default to empty list."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
        )
        assert config.cors_origins == []

    def test_rejects_origin_without_scheme(self) -> None:
        """Should reject origins missing http/https scheme."""
        with pytest.raises(ValidationError, match="Invalid CORS origin"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=["example.org"],
            )

    def test_rejects_origin_with_path(self) -> None:
        """Should reject origins with paths."""
        with pytest.raises(ValidationError, match="Invalid CORS origin"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=["https://example.org/path"],
            )

    def test_rejects_invalid_wildcard_position(self) -> None:
        """Should reject wildcards not at subdomain position."""
        with pytest.raises(ValidationError, match="Invalid CORS origin"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=["https://example.*.com"],
            )

    def test_deduplicates_origins(self) -> None:
        """Should deduplicate origin entries."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=[
                "https://example.org",
                "https://example.org",
                "https://other.org",
            ],
        )
        assert len(config.cors_origins) == 2

    def test_strips_whitespace(self) -> None:
        """Should strip whitespace from origins."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=["  https://example.org  "],
        )
        assert config.cors_origins == ["https://example.org"]

    def test_skips_empty_strings(self) -> None:
        """Should skip empty strings."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=["", "  ", "https://example.org"],
        )
        assert config.cors_origins == ["https://example.org"]

    def test_rejects_too_long_origin(self) -> None:
        """Should reject origins longer than 255 characters."""
        long_origin = "https://" + "a" * 248
        with pytest.raises(ValidationError, match="too long"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=[long_origin],
            )

    def test_accepts_single_char_subdomain_labels(self) -> None:
        """Should accept origins with single-character subdomain labels."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=[
                "https://a.example.org",
                "https://1.example.org",
            ],
        )
        assert len(config.cors_origins) == 2

    def test_rejects_leading_hyphen_in_domain(self) -> None:
        """Should reject origins with leading hyphens in domain labels."""
        with pytest.raises(ValidationError, match="Invalid CORS origin"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=["https://-example.org"],
            )

    def test_accepts_numeric_only_domain(self) -> None:
        """Should accept origins with numeric-only domain labels."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=["https://123.456.789:8080"],
        )
        assert len(config.cors_origins) == 1


class TestCommunityConfigMaintainers:
    """Tests for CommunityConfig.maintainers validation."""

    def test_valid_maintainers(self) -> None:
        """Should accept valid GitHub usernames."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            maintainers=["octocat", "jane-doe", "user123"],
        )
        assert config.maintainers == ["octocat", "jane-doe", "user123"]

    def test_defaults_to_empty(self) -> None:
        """Should default to empty list."""
        config = CommunityConfig(id="test", name="Test", description="Test")
        assert config.maintainers == []

    def test_rejects_invalid_username_with_special_chars(self) -> None:
        """Should reject usernames with special characters."""
        with pytest.raises(ValidationError, match="Invalid GitHub username"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                maintainers=["bad@user"],
            )

    def test_rejects_username_starting_with_hyphen(self) -> None:
        """Should reject usernames starting with hyphen."""
        with pytest.raises(ValidationError, match="Invalid GitHub username"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                maintainers=["-badstart"],
            )

    def test_rejects_username_ending_with_hyphen(self) -> None:
        """Should reject usernames ending with hyphen."""
        with pytest.raises(ValidationError, match="Invalid GitHub username"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                maintainers=["badend-"],
            )

    def test_deduplicates_maintainers(self) -> None:
        """Should remove duplicate usernames."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            maintainers=["octocat", "octocat", "jane"],
        )
        assert config.maintainers == ["octocat", "jane"]

    def test_strips_whitespace(self) -> None:
        """Should strip whitespace from usernames."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            maintainers=["  octocat  ", " jane "],
        )
        assert config.maintainers == ["octocat", "jane"]

    def test_single_char_username(self) -> None:
        """Should accept single-character usernames."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            maintainers=["a"],
        )
        assert config.maintainers == ["a"]


class TestCommunitiesConfig:
    """Tests for CommunitiesConfig model."""

    def test_empty_communities(self) -> None:
        """Should allow empty communities list."""
        config = CommunitiesConfig()
        assert config.communities == []

    def test_get_community(self) -> None:
        """Should find community by ID."""
        config = CommunitiesConfig(
            communities=[
                CommunityConfig(id="one", name="One", description="First"),
                CommunityConfig(id="two", name="Two", description="Second"),
            ]
        )
        assert config.get_community("one") is not None
        assert config.get_community("one").name == "One"
        assert config.get_community("nonexistent") is None

    def test_rejects_duplicate_community_ids(self) -> None:
        """Should reject duplicate community IDs."""
        with pytest.raises(ValidationError, match="Duplicate community IDs"):
            CommunitiesConfig(
                communities=[
                    CommunityConfig(id="test", name="Test 1", description="First"),
                    CommunityConfig(id="test", name="Test 2", description="Second"),
                ]
            )

    def test_allows_unique_community_ids(self) -> None:
        """Should allow unique community IDs."""
        config = CommunitiesConfig(
            communities=[
                CommunityConfig(id="one", name="One", description="First"),
                CommunityConfig(id="two", name="Two", description="Second"),
                CommunityConfig(id="three", name="Three", description="Third"),
            ]
        )
        assert len(config.communities) == 3

    def test_from_yaml(self) -> None:
        """Should load configuration from YAML file."""
        yaml_content = """
communities:
  - id: test
    name: Test Community
    description: A test
    status: available
    github:
      repos:
        - org/repo
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            config = CommunitiesConfig.from_yaml(yaml_path)
            assert len(config.communities) == 1
            assert config.communities[0].id == "test"
            assert config.communities[0].github is not None
            assert config.communities[0].github.repos == ["org/repo"]
        finally:
            yaml_path.unlink()

    def test_from_yaml_empty_file(self) -> None:
        """Should handle empty YAML file."""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            yaml_path = Path(f.name)

        try:
            config = CommunitiesConfig.from_yaml(yaml_path)
            assert config.communities == []
        finally:
            yaml_path.unlink()

    def test_from_yaml_missing_file(self) -> None:
        """Should raise error for missing file."""
        with pytest.raises(FileNotFoundError):
            CommunitiesConfig.from_yaml(Path("/nonexistent/path.yaml"))

    def test_from_yaml_invalid_structure(self) -> None:
        """Should raise validation error for invalid YAML structure."""
        yaml_content = """
communities:
  - id: 123  # Should be string
    name: Test
    description: Test
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            # Pydantic does NOT coerce integers to strings in strict mode
            with pytest.raises(ValidationError):
                CommunitiesConfig.from_yaml(yaml_path)
        finally:
            yaml_path.unlink()

    def test_extra_fields_rejected(self) -> None:
        """Should reject extra fields (strict schema)."""
        yaml_content = """
communities:
  - id: test
    name: Test
    description: Test
    unknown_field: value
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            with pytest.raises(ValidationError):
                CommunitiesConfig.from_yaml(yaml_path)
        finally:
            yaml_path.unlink()

    def test_from_yaml_malformed_syntax(self) -> None:
        """Should raise YAMLError for malformed YAML syntax."""
        import yaml

        yaml_content = "bad: yaml: syntax: error:"
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            with pytest.raises(yaml.YAMLError, match="Failed to parse YAML"):
                CommunitiesConfig.from_yaml(yaml_path)
        finally:
            yaml_path.unlink()

    def test_from_yaml_null_communities(self) -> None:
        """Should reject YAML with null communities field."""
        yaml_content = "communities: null"
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            with pytest.raises(ValidationError, match="should be a valid list"):
                CommunitiesConfig.from_yaml(yaml_path)
        finally:
            yaml_path.unlink()

    def test_load_actual_communities_yaml(self) -> None:
        """Should successfully load the real communities.yaml from the repo."""
        project_root = Path(__file__).parent.parent.parent.parent
        yaml_path = project_root / "registries" / "communities.yaml"

        # Skip if YAML doesn't exist (e.g., in isolated test environments)
        if not yaml_path.exists():
            pytest.skip(f"communities.yaml not found at {yaml_path}")

        config = CommunitiesConfig.from_yaml(yaml_path)

        # Basic validation
        assert len(config.communities) > 0, "Should have at least one community"

        for community in config.communities:
            # Validate structure
            assert community.id, "Community missing id"
            assert community.name, f"Community {community.id} missing name"
            assert community.description, f"Community {community.id} missing description"

            # If documentation URLs are provided, they should be valid
            for doc in community.documentation:
                assert doc.url, f"Doc in {community.id} missing URL"


class TestEnvVarNameValidation:
    """Tests for openrouter_api_key_env_var validation (Issue #64)."""

    def test_valid_env_var_names(self) -> None:
        """Should accept valid OPENROUTER_API_KEY_* patterns."""
        valid_names = [
            "OPENROUTER_API_KEY_HED",
            "OPENROUTER_API_KEY_BIDS",
            "OPENROUTER_API_KEY_TEST",
            "OPENROUTER_API_KEY_MY_COMMUNITY",
            "OPENROUTER_API_KEY_123",
        ]
        for name in valid_names:
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                openrouter_api_key_env_var=name,
            )
            assert config.openrouter_api_key_env_var == name

    def test_allows_none(self) -> None:
        """Should allow None (use platform key)."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            openrouter_api_key_env_var=None,
        )
        assert config.openrouter_api_key_env_var is None

    def test_rejects_arbitrary_env_vars(self) -> None:
        """Should reject non-OPENROUTER_API_KEY_* patterns (prevents secret access)."""
        invalid_names = [
            "AWS_SECRET_KEY",
            "DATABASE_PASSWORD",
            "SOME_OTHER_SECRET",
            "OPENROUTER_KEY",  # Missing API_KEY part
            "API_KEY_HED",  # Missing OPENROUTER part
            "openrouter_api_key_hed",  # Lowercase not allowed
        ]
        for name in invalid_names:
            with pytest.raises(ValidationError, match="Invalid environment variable name"):
                CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    openrouter_api_key_env_var=name,
                )

    def test_rejects_env_var_with_special_chars(self) -> None:
        """Should reject env var names with special characters."""
        with pytest.raises(ValidationError, match="Invalid environment variable name"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                openrouter_api_key_env_var="OPENROUTER_API_KEY-HED",  # Hyphen not allowed
            )

    def test_strips_whitespace_from_env_var(self) -> None:
        """Should strip whitespace from env var names."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            openrouter_api_key_env_var="  OPENROUTER_API_KEY_HED  ",
        )
        assert config.openrouter_api_key_env_var == "OPENROUTER_API_KEY_HED"


class TestSSRFProtection:
    """Tests for source_url SSRF protection (Issue #66)."""

    def test_valid_public_urls(self) -> None:
        """Should accept valid public HTTP/HTTPS URLs."""
        valid_urls = [
            "https://raw.githubusercontent.com/org/repo/main/docs/file.md",
            "https://docs.example.com/content.md",
            "http://public-site.org/documentation.html",
        ]
        for url in valid_urls:
            doc = DocSource(
                title="Test Doc",
                url="https://example.com",
                source_url=url,
            )
            assert doc.source_url == url

    def test_rejects_localhost(self) -> None:
        """Should reject localhost URLs (prevents local probing)."""
        localhost_urls = [
            "http://localhost/file.md",
            "http://127.0.0.1/file.md",
            "http://[::1]/file.md",
        ]
        for url in localhost_urls:
            with pytest.raises(ValidationError, match="Cannot fetch from localhost"):
                DocSource(
                    title="Test Doc",
                    url="https://example.com",
                    source_url=url,
                )

    def test_rejects_private_ips(self) -> None:
        """Should reject private IP addresses (prevents internal network probing)."""
        private_ips = [
            "http://10.0.0.1/file.md",  # 10.0.0.0/8
            "http://172.16.0.1/file.md",  # 172.16.0.0/12
            "http://192.168.1.1/file.md",  # 192.168.0.0/16
        ]
        for url in private_ips:
            with pytest.raises(ValidationError, match="Cannot fetch from private IP"):
                DocSource(
                    title="Test Doc",
                    url="https://example.com",
                    source_url=url,
                )

    def test_rejects_aws_metadata_service(self) -> None:
        """Should reject AWS metadata service (link-local 169.254.0.0/16)."""
        with pytest.raises(ValidationError, match="link-local address"):
            DocSource(
                title="Test Doc",
                url="https://example.com",
                source_url="http://169.254.169.254/latest/meta-data/",
            )

    def test_rejects_non_http_schemes(self) -> None:
        """Should reject non-HTTP/HTTPS schemes."""
        invalid_schemes = [
            "file:///etc/passwd",
            "ftp://example.com/file.md",
            "gopher://example.com/",
            "data:text/plain,content",
        ]
        for url in invalid_schemes:
            with pytest.raises(ValidationError, match="Invalid URL scheme"):
                DocSource(
                    title="Test Doc",
                    url="https://example.com",
                    source_url=url,
                )

    def test_allows_none_source_url(self) -> None:
        """Should allow None for source_url."""
        doc = DocSource(
            title="Test Doc",
            url="https://example.com",
            source_url=None,
        )
        assert doc.source_url is None

    def test_accepts_public_hostnames(self) -> None:
        """Should accept public hostnames (not IPs)."""
        doc = DocSource(
            title="Test Doc",
            url="https://example.com",
            source_url="https://public-docs.example.org/file.md",
        )
        assert doc.source_url == "https://public-docs.example.org/file.md"


class TestModelNameValidation:
    """Tests for default_model validation (Issue #68)."""

    def test_valid_model_names(self) -> None:
        """Should accept valid provider/model-name format."""
        valid_models = [
            "anthropic/claude-3.5-sonnet",
            "openai/gpt-4",
            "google/gemini-pro",
            "provider/model-name-v2.0",
            "provider_name/model_name",
        ]
        for model in valid_models:
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                default_model=model,
            )
            assert config.default_model == model

    def test_allows_none(self) -> None:
        """Should allow None (use platform default)."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            default_model=None,
        )
        assert config.default_model is None

    def test_rejects_invalid_format(self) -> None:
        """Should reject model names not matching provider/model-name."""
        invalid_models = [
            "just-a-model-name",  # No provider
            "provider/",  # No model name
            "/model-name",  # No provider
            "provider model",  # Space instead of slash
            "provider\\model",  # Backslash
            "provider/model/extra",  # Too many slashes
        ]
        for model in invalid_models:
            with pytest.raises(ValidationError, match="Invalid model name format"):
                CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    default_model=model,
                )

    def test_rejects_too_long_model_name(self) -> None:
        """Should reject model names longer than 100 characters."""
        long_model = "provider/" + "x" * 100
        with pytest.raises(ValidationError, match="too long"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                default_model=long_model,
            )

    def test_strips_whitespace(self) -> None:
        """Should strip whitespace from model names."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            default_model="  anthropic/claude-3.5-sonnet  ",
        )
        assert config.default_model == "anthropic/claude-3.5-sonnet"


class TestCostManipulationProtection:
    """Tests for cost manipulation guards (Issue #67)."""

    def test_allows_expensive_model_with_byok(self) -> None:
        """Should allow ultra-expensive models when BYOK is configured."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            default_model="anthropic/claude-opus-4",
            openrouter_api_key_env_var="OPENROUTER_API_KEY_TEST",
        )
        assert config.default_model == "anthropic/claude-opus-4"
        assert config.openrouter_api_key_env_var is not None

    def test_rejects_ultra_expensive_model_without_byok(self) -> None:
        """Should reject ultra-expensive models without BYOK (prevents surprise billing)."""
        ultra_expensive_models = [
            "openai/o1",
            "openai/o1-preview",
            "anthropic/claude-opus-4",
            "anthropic/claude-3-opus",
        ]
        for model in ultra_expensive_models:
            with pytest.raises(ValidationError, match="requires BYOK"):
                CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    default_model=model,
                    # No openrouter_api_key_env_var set
                )

    def test_allows_moderate_models_without_byok(self) -> None:
        """Should allow moderate-cost models without BYOK."""
        moderate_models = [
            "anthropic/claude-sonnet-4.5",
            "anthropic/claude-haiku-4.5",
            "openai/gpt-4",
            "google/gemini-pro",
        ]
        for model in moderate_models:
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                default_model=model,
                # No BYOK - should still work for moderate models
            )
            assert config.default_model == model

    def test_allows_no_model_without_byok(self) -> None:
        """Should allow no model specified without BYOK."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            # No default_model, no BYOK
        )
        assert config.default_model is None

    def test_allows_cheaper_variants_with_expensive_prefix(self) -> None:
        """Should allow cheaper model variants that share prefix with expensive models."""
        cheaper_variants = [
            "openai/o1-mini",  # Cheaper than o1, but starts with "openai/o1"
            "openai/o1-mini-2024-09-12",  # Dated version of o1-mini
        ]
        for model in cheaper_variants:
            # Should NOT require BYOK for cheaper variants
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                default_model=model,
                # No BYOK - should work for cheaper models
            )
            assert config.default_model == model
