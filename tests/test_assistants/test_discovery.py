"""Tests for assistant discovery from per-community config.yaml files.

Tests the discovery process that scans src/assistants/*/config.yaml.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.assistants import discover_assistants, registry
from src.assistants.registry import AssistantRegistry
from src.core.config.community import CommunityConfig


class TestDiscoverAssistants:
    """Tests for discover_assistants function."""

    @pytest.fixture(autouse=True)
    def clear_registry(self) -> None:
        """Clear registry before each test."""
        registry._assistants.clear()

    def test_discovers_hed_from_config_yaml(self) -> None:
        """Should discover HED from its config.yaml file."""
        discovered = discover_assistants()

        assert "hed" in discovered
        assert "hed" in registry

    def test_discovered_assistant_has_community_config(self) -> None:
        """Discovered assistants should have community config."""
        discover_assistants()

        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert info.community_config.id == "hed"

    def test_returns_list_of_discovered_ids(self) -> None:
        """Should return list of discovered community IDs."""
        discovered = discover_assistants()

        assert isinstance(discovered, list)
        assert len(discovered) > 0
        # At least HED should be discovered
        assert "hed" in discovered


class TestCommunityConfigFromYaml:
    """Tests for CommunityConfig.from_yaml() method."""

    def test_loads_valid_yaml(self, tmp_path: Path) -> None:
        """Should load valid YAML file."""
        yaml_content = """
id: test-community
name: Test Community
description: A test community
"""
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml_content)

        config = CommunityConfig.from_yaml(yaml_path)

        assert config.id == "test-community"
        assert config.name == "Test Community"
        assert config.description == "A test community"

    def test_loads_yaml_with_documentation(self, tmp_path: Path) -> None:
        """Should load YAML with documentation section."""
        yaml_content = """
id: docs-test
name: Docs Test
description: Test with docs
documentation:
  - title: Test Doc
    url: https://example.com/docs
    preload: false
"""
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml_content)

        config = CommunityConfig.from_yaml(yaml_path)

        assert len(config.documentation) == 1
        assert config.documentation[0].title == "Test Doc"

    def test_loads_yaml_with_github_config(self, tmp_path: Path) -> None:
        """Should load YAML with GitHub configuration."""
        yaml_content = """
id: github-test
name: GitHub Test
description: Test with GitHub
github:
  repos:
    - org/repo1
    - org/repo2
"""
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml_content)

        config = CommunityConfig.from_yaml(yaml_path)

        assert config.github is not None
        assert len(config.github.repos) == 2
        assert "org/repo1" in config.github.repos

    def test_loads_yaml_with_extensions(self, tmp_path: Path) -> None:
        """Should load YAML with extensions configuration."""
        yaml_content = """
id: ext-test
name: Extensions Test
description: Test with extensions
extensions:
  python_plugins:
    - module: some.module
      tools:
        - tool_one
        - tool_two
"""
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml_content)

        config = CommunityConfig.from_yaml(yaml_path)

        assert config.extensions is not None
        assert len(config.extensions.python_plugins) == 1
        assert config.extensions.python_plugins[0].module == "some.module"

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError for missing file."""
        yaml_path = tmp_path / "nonexistent.yaml"

        with pytest.raises(FileNotFoundError):
            CommunityConfig.from_yaml(yaml_path)

    def test_raises_for_invalid_yaml(self, tmp_path: Path) -> None:
        """Should raise error for invalid YAML syntax."""
        yaml_content = """
id: invalid
name: Invalid
  bad indentation
"""
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(yaml_content)

        with pytest.raises((yaml.YAMLError, ValidationError)):
            CommunityConfig.from_yaml(yaml_path)


class TestRegistryFromConfig:
    """Tests for registry.register_from_config method."""

    def test_registers_from_community_config(self) -> None:
        """Should register assistant from CommunityConfig."""
        temp_registry = AssistantRegistry()

        config = CommunityConfig(
            id="test-reg",
            name="Test Registration",
            description="Testing register_from_config",
        )

        temp_registry.register_from_config(config)

        assert "test-reg" in temp_registry
        info = temp_registry.get("test-reg")
        assert info is not None
        assert info.name == "Test Registration"
        assert info.community_config == config

    def test_overwrites_existing_registration(self) -> None:
        """Should overwrite existing registration with warning."""
        temp_registry = AssistantRegistry()

        config1 = CommunityConfig(
            id="overwrite-test",
            name="First Version",
            description="First registration",
        )
        config2 = CommunityConfig(
            id="overwrite-test",
            name="Second Version",
            description="Second registration",
        )

        temp_registry.register_from_config(config1)
        temp_registry.register_from_config(config2)

        info = temp_registry.get("overwrite-test")
        assert info is not None
        assert info.name == "Second Version"

    def test_populates_sync_config(self) -> None:
        """Should populate sync_config from community config."""
        temp_registry = AssistantRegistry()

        config = CommunityConfig(
            id="sync-test",
            name="Sync Test",
            description="Testing sync config",
            github={"repos": ["org/repo"]},
            citations={"queries": ["test query"], "dois": ["10.1234/test"]},
        )

        temp_registry.register_from_config(config)

        info = temp_registry.get("sync-test")
        assert info is not None
        assert info.sync_config.get("github_repos") == ["org/repo"]
        assert info.sync_config.get("paper_queries") == ["test query"]
        assert info.sync_config.get("paper_dois") == ["10.1234/test"]


class TestDiscoveryWithActualConfig:
    """Tests using the actual HED config.yaml file."""

    @pytest.fixture(autouse=True)
    def clear_and_discover(self) -> None:
        """Clear registry and run discovery."""
        registry._assistants.clear()
        discover_assistants()

    def test_hed_config_loaded(self) -> None:
        """HED config should be loaded from config.yaml."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None

    def test_hed_has_documentation_config(self) -> None:
        """HED should have documentation configured."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert len(info.community_config.documentation) > 0

    def test_hed_has_github_repos(self) -> None:
        """HED should have GitHub repos configured."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert info.community_config.github is not None
        assert len(info.community_config.github.repos) > 0

    def test_hed_has_extensions(self) -> None:
        """HED should have Python plugin extensions configured."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert info.community_config.extensions is not None
        assert len(info.community_config.extensions.python_plugins) > 0
