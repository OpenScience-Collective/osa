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
