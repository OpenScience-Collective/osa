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
            url="https://docs.example.com/",
            type="sphinx",
            source_repo="org/repo",
        )
        assert str(doc.url) == "https://docs.example.com/"
        assert doc.type == "sphinx"
        assert doc.source_repo == "org/repo"

    def test_doc_source_defaults(self) -> None:
        """Should use default values for optional fields."""
        doc = DocSource(url="https://docs.example.com/")
        assert doc.type == "html"
        assert doc.source_repo is None
        assert doc.description is None

    def test_invalid_url_raises_error(self) -> None:
        """Should reject invalid URLs."""
        with pytest.raises(ValidationError):
            DocSource(url="not-a-url")

    def test_invalid_type_raises_error(self) -> None:
        """Should reject invalid documentation types."""
        with pytest.raises(ValidationError):
            DocSource(url="https://docs.example.com/", type="invalid")


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
                DocSource(url="https://hedtags.org/hed-resources/", type="sphinx"),
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
