"""Tests for assistant discovery and YAML loading integration.

Tests the full discovery process including YAML loading and Python
package registration.
"""

from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock

import pytest

from src.assistants import discover_assistants, get_communities_yaml_path, registry


class TestGetCommunitiesYamlPath:
    """Tests for get_communities_yaml_path function."""

    def test_finds_project_root_yaml(self) -> None:
        """Should find YAML in project root."""
        path = get_communities_yaml_path()
        assert path.exists()
        assert path.name == "communities.yaml"
        assert "registries" in str(path)


class TestDiscoverAssistants:
    """Tests for discover_assistants integration."""

    def test_loads_yaml_and_python_registrations(self) -> None:
        """Should load communities from YAML and Python packages."""
        yaml_content = """
communities:
  - id: test-yaml-only
    name: YAML Only
    description: Only defined in YAML
    status: coming_soon
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            # Clear registry for clean test
            registry._assistants.clear()

            # Run discovery with test YAML
            discovered = discover_assistants(yaml_path)

            # Should have loaded YAML-only community
            assert "test-yaml-only" in registry
            yaml_info = registry.get("test-yaml-only")
            assert yaml_info is not None
            assert yaml_info.factory is None  # No Python implementation
            assert yaml_info.community_config is not None
            assert yaml_info.name == "YAML Only"

            # discover_assistants returns list of discovered Python packages
            # (empty in this test since no assistants/ subpackages loaded)
            assert isinstance(discovered, list)
        finally:
            yaml_path.unlink()

    def test_merges_yaml_with_existing_decorator(self) -> None:
        """Should merge YAML config with existing decorator registration."""
        yaml_content = """
communities:
  - id: merge-test
    name: Merge Test
    description: From YAML
    github:
      repos:
        - yaml-org/yaml-repo
    citations:
      queries:
        - "yaml query"
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            # Clear registry
            registry._assistants.clear()

            # First register via decorator (simulates Python package)
            @registry.register(
                id="merge-test",
                name="Merge Test",
                description="From decorator",
            )
            def create_merge_test(_model):
                return MagicMock()

            # Load YAML (should merge)
            registry.load_from_yaml(yaml_path)

            # Check merged result
            info = registry.get("merge-test")
            assert info is not None
            assert info.factory is not None  # From decorator
            assert info.community_config is not None  # From YAML
            assert info.sync_config["github_repos"] == ["yaml-org/yaml-repo"]
        finally:
            yaml_path.unlink()
            registry._assistants.clear()

    def test_handles_missing_yaml_gracefully(self) -> None:
        """Should handle missing YAML file without crashing."""
        # Clear registry
        registry._assistants.clear()

        # Discovery with non-existent file should not crash
        discovered = discover_assistants("/nonexistent/path.yaml")

        # Should return empty or just Python packages
        assert isinstance(discovered, list)


class TestDiscoveryWithActualYaml:
    """Tests using the actual communities.yaml file."""

    def test_actual_yaml_loads_hed(self) -> None:
        """Should load HED from actual communities.yaml."""
        # Clear registry
        registry._assistants.clear()

        # Use default YAML path
        yaml_path = get_communities_yaml_path()
        if not yaml_path.exists():
            pytest.skip("communities.yaml not found")

        # Load
        registry.load_from_yaml(yaml_path)

        # HED should be loaded from YAML
        hed_info = registry.get("hed")
        assert hed_info is not None
        assert hed_info.name == "HED (Hierarchical Event Descriptors)"
        assert hed_info.community_config is not None
        assert hed_info.sync_config.get("github_repos")
        assert len(hed_info.sync_config["github_repos"]) > 0

    def test_full_discovery_with_real_yaml(self) -> None:
        """Should run full discovery with real YAML."""
        # Clear registry
        registry._assistants.clear()

        # Run full discovery
        discover_assistants()

        # Should have at least HED from YAML
        assert "hed" in registry

        # If Python hed package loaded, should have factory
        hed_info = registry.get("hed")
        if hed_info.factory is not None:
            # Full merge happened
            assert hed_info.community_config is not None
