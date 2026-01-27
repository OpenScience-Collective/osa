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


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    """Ensure registry is populated before tests."""
    from src.assistants import discover_assistants, registry

    registry._assistants.clear()
    discover_assistants()


@pytest.fixture
def eeglab_config():
    """Get EEGLAB community config."""
    from src.assistants import registry

    config = registry.get_community_config("eeglab")
    assert config is not None
    return config


@pytest.fixture
def eeglab_assistant():
    """Create EEGLAB assistant instance with mock model."""
    from src.assistants import registry

    mock_model = MagicMock()
    return registry.create_assistant("eeglab", model=mock_model, preload_docs=False)


class TestEEGLABRegistration:
    """Tests for EEGLAB assistant registration."""

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

    def test_github_repos_configured(self, eeglab_config) -> None:
        """EEGLAB should have 6 GitHub repos configured."""
        assert eeglab_config.github is not None

        repos = eeglab_config.github.repos
        assert len(repos) == 6

        expected_repos = {
            "sccn/eeglab",
            "sccn/ICLabel",
            "sccn/clean_rawdata",
            "sccn/EEG-BIDS",
            "sccn/labstreaminglayer",
            "sccn/liblsl",
        }
        assert set(repos) == expected_repos

    def test_paper_dois_configured(self, eeglab_config) -> None:
        """EEGLAB should have 3 core paper DOIs configured."""
        assert eeglab_config.citations is not None
        assert eeglab_config.citations.dois is not None

        dois = eeglab_config.citations.dois
        assert len(dois) == 3

        expected_dois = {
            "10.1016/j.jneumeth.2003.10.009",  # EEGLAB paper
            "10.1016/j.neuroimage.2019.05.026",  # ICLabel paper
            "10.3389/fninf.2015.00016",  # PREP pipeline paper
        }
        assert set(dois) == expected_dois

    def test_citation_queries_configured(self, eeglab_config) -> None:
        """EEGLAB should have 6+ citation search queries configured."""
        assert eeglab_config.citations is not None
        assert eeglab_config.citations.queries is not None

        queries = eeglab_config.citations.queries
        assert len(queries) >= 6

        assert "EEGLAB tutorial" in queries
        assert "ICLabel artifact classification" in queries
        assert "ICA EEG analysis" in queries

    def test_documentation_sources_configured(self, eeglab_config) -> None:
        """EEGLAB should have 25+ documentation sources configured."""
        assert eeglab_config.documentation is not None
        assert len(eeglab_config.documentation) >= 25

    def test_preloaded_documentation(self, eeglab_config) -> None:
        """EEGLAB should have exactly 2 preloaded documents."""
        assert eeglab_config.documentation is not None

        preloaded = [doc for doc in eeglab_config.documentation if doc.preload]
        assert len(preloaded) == 2

        preloaded_titles = {doc.title for doc in preloaded}
        assert "EEGLAB quickstart" in preloaded_titles
        assert "Dataset management" in preloaded_titles

    def test_documentation_categories(self, eeglab_config) -> None:
        """EEGLAB documentation should be organized into proper categories."""
        assert eeglab_config.documentation is not None

        categories = {doc.category for doc in eeglab_config.documentation}

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

    def test_creates_community_assistant(self, eeglab_assistant) -> None:
        """EEGLAB should create CommunityAssistant instance."""
        from src.assistants.community import CommunityAssistant

        assert isinstance(eeglab_assistant, CommunityAssistant)
        assert eeglab_assistant.config.id == "eeglab"
        assert eeglab_assistant.config.name == "EEGLAB"

    def test_has_custom_system_prompt(self, eeglab_assistant) -> None:
        """EEGLAB should use custom system_prompt from YAML."""
        prompt = eeglab_assistant.get_system_prompt()

        assert "EEGLAB" in prompt
        assert "EEG" in prompt or "electrophysiological" in prompt
        assert "sccn.github.io" in prompt or "sccn.ucsd.edu" in prompt
        assert "ICA" in prompt

    def test_system_prompt_has_workflow_guidance(self, eeglab_assistant) -> None:
        """System prompt should include EEGLAB workflow guidance."""
        prompt = eeglab_assistant.get_system_prompt()
        prompt_lower = prompt.lower()

        has_preprocessing = "preprocessing" in prompt_lower or "preprocess" in prompt_lower
        has_artifacts = "ICLabel" in prompt or "artifact" in prompt_lower

        assert has_preprocessing
        assert has_artifacts

    def test_system_prompt_has_plugin_recommendations(self, eeglab_assistant) -> None:
        """System prompt should recommend key EEGLAB plugins."""
        prompt = eeglab_assistant.get_system_prompt()

        plugins_mentioned = sum(
            [
                "ICLabel" in prompt,
                "clean_rawdata" in prompt,
                "PREP" in prompt,
            ]
        )
        assert plugins_mentioned >= 2


class TestEEGLABKnowledgeTools:
    """Tests for EEGLAB knowledge discovery tools."""

    def test_has_documentation_tool(self, eeglab_assistant) -> None:
        """EEGLAB should have documentation retrieval tool."""
        tool_names = [t.name for t in eeglab_assistant.tools]
        assert "retrieve_eeglab_docs" in tool_names

    def test_has_github_discussion_tool(self, eeglab_assistant) -> None:
        """EEGLAB should have GitHub discussion search tool."""
        tool_names = [t.name for t in eeglab_assistant.tools]
        assert "search_eeglab_discussions" in tool_names

    def test_has_recent_activity_tool(self, eeglab_assistant) -> None:
        """EEGLAB should have recent activity listing tool."""
        tool_names = [t.name for t in eeglab_assistant.tools]
        assert "list_eeglab_recent" in tool_names

    def test_has_paper_search_tool(self, eeglab_assistant) -> None:
        """EEGLAB should have paper search tool."""
        tool_names = [t.name for t in eeglab_assistant.tools]
        assert "search_eeglab_papers" in tool_names

    def test_has_all_expected_tools(self, eeglab_assistant) -> None:
        """EEGLAB should have all 4 expected knowledge tools."""
        tool_names = {t.name for t in eeglab_assistant.tools}

        expected_tools = {
            "retrieve_eeglab_docs",
            "search_eeglab_discussions",
            "list_eeglab_recent",
            "search_eeglab_papers",
        }

        assert expected_tools.issubset(tool_names)


class TestEEGLABDocumentation:
    """Tests for EEGLAB documentation configuration."""

    def test_has_ica_documentation(self, eeglab_config) -> None:
        """EEGLAB should have ICA-related documentation."""
        assert eeglab_config.documentation is not None

        doc_titles = {doc.title.lower() for doc in eeglab_config.documentation}
        ica_docs = [title for title in doc_titles if "ica" in title or "component" in title]
        assert len(ica_docs) >= 2

    def test_has_preprocessing_documentation(self, eeglab_config) -> None:
        """EEGLAB should have preprocessing documentation."""
        assert eeglab_config.documentation is not None

        categories = [doc.category for doc in eeglab_config.documentation]
        assert "preprocessing" in categories

    def test_has_artifacts_documentation(self, eeglab_config) -> None:
        """EEGLAB should have artifact removal documentation."""
        assert eeglab_config.documentation is not None

        categories = [doc.category for doc in eeglab_config.documentation]
        assert "artifacts" in categories

    def test_has_visualization_documentation(self, eeglab_config) -> None:
        """EEGLAB should have visualization documentation."""
        assert eeglab_config.documentation is not None

        categories = [doc.category for doc in eeglab_config.documentation]
        assert "visualization" in categories

    def test_documentation_has_valid_urls(self, eeglab_config) -> None:
        """All documentation URLs should be properly formatted."""
        assert eeglab_config.documentation is not None

        for doc in eeglab_config.documentation:
            assert str(doc.url).startswith("http")

            if doc.source_url:
                assert doc.source_url.startswith("http")

                if "github" in doc.source_url:
                    assert "raw.githubusercontent.com" in doc.source_url


class TestEEGLABSyncConfiguration:
    """Tests for EEGLAB sync configuration (GitHub and papers)."""

    def test_has_sync_config(self) -> None:
        """EEGLAB should have sync configuration (GitHub or citations)."""
        from src.assistants import registry

        info = registry.get("eeglab")
        assert info is not None
        assert bool(info.sync_config) is True

    def test_github_config_complete(self, eeglab_config) -> None:
        """GitHub configuration should be complete and valid."""
        assert eeglab_config.github is not None
        assert len(eeglab_config.github.repos) > 0

        for repo in eeglab_config.github.repos:
            assert "/" in repo
            parts = repo.split("/")
            assert len(parts) == 2
            assert parts[0] == "sccn"

    def test_citations_config_complete(self, eeglab_config) -> None:
        """Citations configuration should be complete and valid."""
        assert eeglab_config.citations is not None
        assert eeglab_config.citations.dois is not None
        assert len(eeglab_config.citations.dois) > 0
        assert eeglab_config.citations.queries is not None
        assert len(eeglab_config.citations.queries) > 0

        for doi in eeglab_config.citations.dois:
            assert doi.startswith("10.")


class TestEEGLABErrorHandling:
    """Tests for error handling and edge cases."""

    def test_knowledge_tools_handle_missing_database(self, eeglab_assistant) -> None:
        """Knowledge tools should return helpful error when database doesn't exist."""
        from src.knowledge.db import get_db_path

        db_path = get_db_path("eeglab")

        if db_path.exists():
            pytest.skip("Database exists, cannot test missing database scenario")

        tool_names = {
            "search_eeglab_discussions",
            "list_eeglab_recent",
            "search_eeglab_papers",
        }

        for tool in eeglab_assistant.tools:
            if tool.name in tool_names:
                result = (
                    tool.invoke({"query": "test"}) if "search" in tool.name else tool.invoke({})
                )
                assert isinstance(result, str)
                assert "not initialized" in result.lower() or "knowledge" in result.lower()
                assert "sync" in result.lower()

    def test_system_prompt_has_no_unsubstituted_placeholders(self, eeglab_assistant) -> None:
        """System prompt should not contain unfilled template placeholders."""
        prompt = eeglab_assistant.get_system_prompt()

        assert "{repo_list}" not in prompt, "repo_list placeholder not substituted"
        assert "{paper_dois}" not in prompt, "paper_dois placeholder not substituted"
        assert "{preloaded_docs_section}" not in prompt, (
            "preloaded_docs_section placeholder not substituted"
        )
        assert "{available_docs_section}" not in prompt, (
            "available_docs_section placeholder not substituted"
        )


class TestEEGLABSecurityValidation:
    """Tests for security features (SSRF protection)."""

    def test_rejects_localhost_documentation_urls(self) -> None:
        """Should reject documentation with localhost URLs (SSRF protection)."""
        from pydantic import ValidationError

        from src.core.config.community import CommunityConfig

        malicious_configs = [
            "http://localhost/docs",
            "http://127.0.0.1/docs",
            "http://169.254.169.254/latest/meta-data",
            "http://192.168.1.1/internal",
            "http://10.0.0.1/internal",
        ]

        for bad_url in malicious_configs:
            with pytest.raises(ValidationError, match="localhost|private|metadata"):
                CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    status="available",
                    documentation=[
                        {
                            "title": "Bad Doc",
                            "url": "https://example.com",
                            "source_url": bad_url,
                            "preload": True,
                            "category": "test",
                            "description": "Test doc",
                        }
                    ],
                )

    def test_github_repo_format_validation(self) -> None:
        """Should reject invalid GitHub repo formats."""
        from pydantic import ValidationError

        from src.core.config.community import CommunityConfig

        invalid_repos = [
            ["not-a-repo"],
            ["/just-repo"],
            ["org/"],
            ["org/repo/extra"],
            [""],
        ]

        for bad_repos in invalid_repos:
            with pytest.raises(ValidationError):
                CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    status="available",
                    github={"repos": bad_repos},
                )


class TestEEGLABToolRobustness:
    """Tests for tool robustness with edge case inputs."""

    def test_knowledge_tools_handle_empty_queries(self, eeglab_assistant) -> None:
        """Knowledge tools should handle empty queries gracefully."""
        search_tools = [
            t for t in eeglab_assistant.tools if "search" in t.name and "eeglab" in t.name
        ]

        for tool in search_tools:
            result = tool.invoke({"query": ""})
            assert isinstance(result, str)
            assert len(result) > 0

    def test_knowledge_tools_handle_long_queries(self, eeglab_assistant) -> None:
        """Knowledge tools should handle very long queries without crashing."""
        search_tools = [
            t for t in eeglab_assistant.tools if "search" in t.name and "eeglab" in t.name
        ]

        long_query = "x" * 10000

        for tool in search_tools:
            result = tool.invoke({"query": long_query})
            assert isinstance(result, str)


class TestEEGLABPreloadHandling:
    """Tests for preloaded documentation handling."""

    def test_assistant_creation_succeeds_without_preload(self) -> None:
        """Assistant should create successfully even if preload is disabled."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant("eeglab", model=mock_model, preload_docs=False)

        assert assistant is not None
        assert assistant.config.id == "eeglab"

        prompt = assistant.get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
