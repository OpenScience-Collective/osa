"""Tests for EEGLAB assistant configuration.

Tests cover:
- EEGLAB assistant registration and metadata
- Configuration validation (GitHub repos, papers, documentation)
- Assistant creation via CommunityAssistant
- System prompt customization
- Knowledge discovery tools
- Documentation sources and categories
"""

from unittest.mock import MagicMock

import pytest


class TestEEGLABRegistration:
    """Tests for EEGLAB assistant registration."""

    @pytest.fixture(autouse=True)
    def setup_registry(self) -> None:
        """Ensure registry is populated before tests."""
        from src.assistants import discover_assistants, registry

        registry._assistants.clear()
        discover_assistants()

    def test_eeglab_registered(self) -> None:
        """EEGLAB should be registered in the registry."""
        from src.assistants import registry

        assert "eeglab" in registry

    def test_eeglab_has_correct_metadata(self) -> None:
        """EEGLAB should have correct name and description."""
        from src.assistants import registry

        info = registry.get("eeglab")
        assert info is not None
        assert info.name == "EEGLAB"
        assert info.description == "EEG signal processing and analysis toolbox"
        assert info.status == "available"

    def test_eeglab_has_community_config(self) -> None:
        """EEGLAB should have community config from YAML."""
        from src.assistants import registry

        info = registry.get("eeglab")
        assert info is not None
        assert info.community_config is not None
        assert info.community_config.id == "eeglab"


class TestEEGLABConfiguration:
    """Tests for EEGLAB configuration details."""

    @pytest.fixture(autouse=True)
    def setup_registry(self) -> None:
        """Ensure registry is populated before tests."""
        from src.assistants import discover_assistants, registry

        registry._assistants.clear()
        discover_assistants()

    def test_github_repos_configured(self) -> None:
        """EEGLAB should have 6 GitHub repos configured."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.github is not None

        repos = config.github.repos
        assert len(repos) == 6

        # Verify expected repos
        expected_repos = {
            "sccn/eeglab",
            "sccn/ICLabel",
            "sccn/clean_rawdata",
            "sccn/EEG-BIDS",
            "sccn/labstreaminglayer",
            "sccn/liblsl",
        }
        assert set(repos) == expected_repos

    def test_paper_dois_configured(self) -> None:
        """EEGLAB should have 3 core paper DOIs configured."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.citations is not None
        assert config.citations.dois is not None

        dois = config.citations.dois
        assert len(dois) == 3

        # Verify expected DOIs
        expected_dois = {
            "10.1016/j.jneumeth.2003.10.009",  # EEGLAB paper
            "10.1016/j.neuroimage.2019.05.026",  # ICLabel paper
            "10.3389/fninf.2015.00016",  # PREP pipeline paper
        }
        assert set(dois) == expected_dois

    def test_citation_queries_configured(self) -> None:
        """EEGLAB should have 6+ citation search queries configured."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.citations is not None
        assert config.citations.queries is not None

        queries = config.citations.queries
        assert len(queries) >= 6

        # Verify some expected queries
        assert "EEGLAB tutorial" in queries
        assert "ICLabel artifact classification" in queries
        assert "ICA EEG analysis" in queries

    def test_documentation_sources_configured(self) -> None:
        """EEGLAB should have 25+ documentation sources configured."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.documentation is not None

        docs = config.documentation
        assert len(docs) >= 25

    def test_preloaded_documentation(self) -> None:
        """EEGLAB should have exactly 2 preloaded documents."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.documentation is not None

        preloaded = [doc for doc in config.documentation if doc.preload]
        assert len(preloaded) == 2

        # Verify preloaded docs
        preloaded_titles = {doc.title for doc in preloaded}
        assert "EEGLAB quickstart" in preloaded_titles
        assert "Dataset management" in preloaded_titles

    def test_documentation_categories(self) -> None:
        """EEGLAB documentation should be organized into proper categories."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.documentation is not None

        categories = {doc.category for doc in config.documentation}

        # Expected categories from the plan
        expected_categories = {
            "quickstart",
            "setup",
            "data_import",
            "preprocessing",
            "artifacts",
            "epoching",
            "visualization",
            "group_analysis",
            "scripting",
            "integration",
        }
        assert expected_categories.issubset(categories)


class TestEEGLABAssistantCreation:
    """Tests for creating EEGLAB assistant instance."""

    @pytest.fixture(autouse=True)
    def setup_registry(self) -> None:
        """Ensure registry is populated before tests."""
        from src.assistants import discover_assistants, registry

        registry._assistants.clear()
        discover_assistants()

    def test_creates_community_assistant(self) -> None:
        """EEGLAB should create CommunityAssistant instance."""
        from src.assistants import registry
        from src.assistants.community import CommunityAssistant

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        # Should be CommunityAssistant (not a custom EEGLABAssistant)
        assert isinstance(assistant, CommunityAssistant)
        assert assistant.config.id == "eeglab"
        assert assistant.config.name == "EEGLAB"

    def test_has_custom_system_prompt(self) -> None:
        """EEGLAB should use custom system_prompt from YAML."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        prompt = assistant.get_system_prompt()

        # Should have EEGLAB-specific content
        assert "EEGLAB" in prompt
        assert "EEG" in prompt or "electrophysiological" in prompt
        assert "sccn.github.io" in prompt or "sccn.ucsd.edu" in prompt
        assert "ICA" in prompt  # Key EEGLAB concept

    def test_system_prompt_has_workflow_guidance(self) -> None:
        """System prompt should include EEGLAB workflow guidance."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        prompt = assistant.get_system_prompt()

        # Should mention key workflow concepts
        assert "preprocessing" in prompt.lower() or "preprocess" in prompt.lower()
        assert "ICLabel" in prompt or "artifact" in prompt.lower()

    def test_system_prompt_has_plugin_recommendations(self) -> None:
        """System prompt should recommend key EEGLAB plugins."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        prompt = assistant.get_system_prompt()

        # Should mention key plugins
        plugins_mentioned = sum(
            [
                "ICLabel" in prompt,
                "clean_rawdata" in prompt,
                "PREP" in prompt,
            ]
        )
        assert plugins_mentioned >= 2  # At least 2 of the 3 key plugins


class TestEEGLABKnowledgeTools:
    """Tests for EEGLAB knowledge discovery tools."""

    @pytest.fixture(autouse=True)
    def setup_registry(self) -> None:
        """Ensure registry is populated before tests."""
        from src.assistants import discover_assistants, registry

        registry._assistants.clear()
        discover_assistants()

    def test_has_documentation_tool(self) -> None:
        """EEGLAB should have documentation retrieval tool."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        tool_names = [t.name for t in assistant.tools]
        assert "retrieve_eeglab_docs" in tool_names

    def test_has_github_discussion_tool(self) -> None:
        """EEGLAB should have GitHub discussion search tool."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        tool_names = [t.name for t in assistant.tools]
        assert "search_eeglab_discussions" in tool_names

    def test_has_recent_activity_tool(self) -> None:
        """EEGLAB should have recent activity listing tool."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        tool_names = [t.name for t in assistant.tools]
        assert "list_eeglab_recent" in tool_names

    def test_has_paper_search_tool(self) -> None:
        """EEGLAB should have paper search tool."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        tool_names = [t.name for t in assistant.tools]
        assert "search_eeglab_papers" in tool_names

    def test_has_all_expected_tools(self) -> None:
        """EEGLAB should have all 4 expected knowledge tools."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        tool_names = [t.name for t in assistant.tools]

        expected_tools = {
            "retrieve_eeglab_docs",
            "search_eeglab_discussions",
            "list_eeglab_recent",
            "search_eeglab_papers",
        }

        assert expected_tools.issubset(set(tool_names))


class TestEEGLABDocumentation:
    """Tests for EEGLAB documentation configuration."""

    @pytest.fixture(autouse=True)
    def setup_registry(self) -> None:
        """Ensure registry is populated before tests."""
        from src.assistants import discover_assistants, registry

        registry._assistants.clear()
        discover_assistants()

    def test_has_ica_documentation(self) -> None:
        """EEGLAB should have ICA-related documentation."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.documentation is not None

        doc_titles = {doc.title.lower() for doc in config.documentation}

        # Should have ICA-related docs
        ica_docs = [title for title in doc_titles if "ica" in title or "component" in title]
        assert len(ica_docs) >= 2

    def test_has_preprocessing_documentation(self) -> None:
        """EEGLAB should have preprocessing documentation."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.documentation is not None

        categories = [doc.category for doc in config.documentation]
        assert "preprocessing" in categories

    def test_has_artifacts_documentation(self) -> None:
        """EEGLAB should have artifact removal documentation."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.documentation is not None

        categories = [doc.category for doc in config.documentation]
        assert "artifacts" in categories

    def test_has_visualization_documentation(self) -> None:
        """EEGLAB should have visualization documentation."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.documentation is not None

        categories = [doc.category for doc in config.documentation]
        assert "visualization" in categories

    def test_documentation_has_valid_urls(self) -> None:
        """All documentation URLs should be properly formatted."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.documentation is not None

        for doc in config.documentation:
            # Verify URL format
            assert str(doc.url).startswith("http")

            # Verify source_url if provided
            if doc.source_url:
                assert doc.source_url.startswith("http")

                # Verify GitHub raw URLs for source
                if "github" in doc.source_url:
                    assert "raw.githubusercontent.com" in doc.source_url


class TestEEGLABSyncConfiguration:
    """Tests for EEGLAB sync configuration (GitHub and papers)."""

    @pytest.fixture(autouse=True)
    def setup_registry(self) -> None:
        """Ensure registry is populated before tests."""
        from src.assistants import discover_assistants, registry

        registry._assistants.clear()
        discover_assistants()

    def test_has_sync_config(self) -> None:
        """EEGLAB should have sync configuration (GitHub or citations)."""
        from src.assistants import registry

        info = registry.get("eeglab")
        assert info is not None
        assert bool(info.sync_config) is True

    def test_github_config_complete(self) -> None:
        """GitHub configuration should be complete and valid."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.github is not None
        assert len(config.github.repos) > 0

        # All repos should be in owner/repo format
        for repo in config.github.repos:
            assert "/" in repo
            parts = repo.split("/")
            assert len(parts) == 2
            assert parts[0] == "sccn"  # EEGLAB org

    def test_citations_config_complete(self) -> None:
        """Citations configuration should be complete and valid."""
        from src.assistants import registry

        config = registry.get_community_config("eeglab")
        assert config is not None
        assert config.citations is not None

        # Should have both DOIs and queries
        assert config.citations.dois is not None
        assert len(config.citations.dois) > 0
        assert config.citations.queries is not None
        assert len(config.citations.queries) > 0

        # DOIs should be properly formatted
        for doi in config.citations.dois:
            assert doi.startswith("10.")  # DOI prefix
