"""Integration tests for EEGLab assistant."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.assistants import discover_assistants
from src.assistants.registry import registry
from src.knowledge.db import get_connection, init_db


@pytest.fixture(scope="module", autouse=True)
def discover_eeglab():
    """Discover and register all assistants before running tests."""
    discover_assistants()
    yield


@pytest.fixture
def populated_test_db(tmp_path: Path, monkeypatch):
    """Create a minimal test database with sample data."""
    # Create test database
    db_path = tmp_path / "knowledge" / "test-eeglab.db"

    # Patch get_db_path to return our test database
    def mock_get_db_path(_project: str):
        return db_path

    monkeypatch.setattr("src.knowledge.db.get_db_path", mock_get_db_path)

    # Initialize database
    init_db("test-eeglab")

    # Insert sample docstring
    with get_connection("test-eeglab") as conn:
        conn.execute(
            """
            INSERT INTO docstrings (symbol_name, docstring, file_path, repo, language, symbol_type, line_number, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pop_loadset",
                "Load an EEGLAB dataset file. This function loads .set files.",
                "functions/popfunc/pop_loadset.m",
                "sccn/eeglab",
                "matlab",
                "function",
                42,
                "2024-01-15T10:00:00Z",
            ),
        )
        # Insert into FTS5 table
        conn.execute(
            "INSERT INTO docstrings_fts(docstring) VALUES (?)",
            ("Load an EEGLAB dataset file. This function loads .set files.",),
        )

        # Insert sample FAQ
        import json

        conn.execute(
            """
            INSERT INTO faq_entries (list_name, thread_id, question, answer, tags, category, quality_score, thread_url, message_count, first_message_date, summarized_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "eeglablist",
                "thread123",
                "How do I remove artifacts?",
                "Use ICA decomposition and ICLabel for automatic artifact classification.",
                json.dumps(["artifacts", "ica", "iclabel"]),
                "how-to",
                0.85,
                "https://sccn.ucsd.edu/pipermail/eeglablist/2024/thread123.html",
                5,
                "2024-01-15",
                "2024-01-16T10:00:00Z",
            ),
        )
        # Insert into FTS5 table
        conn.execute(
            "INSERT INTO faq_entries_fts(question, answer) VALUES (?, ?)",
            (
                "How do I remove artifacts?",
                "Use ICA decomposition and ICLabel for automatic artifact classification.",
            ),
        )
        conn.commit()

    yield db_path

    # Cleanup
    if db_path.exists():
        db_path.unlink()


class TestEEGLabConfig:
    """Test EEGLab configuration loading."""

    def test_config_loads_successfully(self):
        """Test that EEGLab config loads without errors."""
        info = registry.get("eeglab")
        assert info is not None
        assert info.id == "eeglab"
        assert info.name == "EEGLAB"

    def test_config_has_github_repos(self):
        """Test that GitHub repos are configured."""
        info = registry.get("eeglab")
        assert len(info.community_config.github.repos) > 0
        repo_names = [repo.split("/")[-1] for repo in info.community_config.github.repos]
        assert "eeglab" in repo_names

    def test_config_has_mailman(self):
        """Test that Mailman config exists."""
        info = registry.get("eeglab")
        assert len(info.community_config.mailman) > 0
        assert info.community_config.mailman[0].list_name == "eeglablist"

    def test_config_has_documentation(self):
        """Test that documentation sources are configured."""
        info = registry.get("eeglab")
        assert len(info.community_config.documentation) > 0


class TestEEGLabTools:
    """Test EEGLab tool creation and registration."""

    @pytest.fixture
    def mock_model(self):
        """Create a mock LLM for testing."""
        return MagicMock()

    def test_assistant_creates_standard_tools(self, mock_model):
        """Test that standard knowledge tools are created."""
        assistant = registry.create_assistant("eeglab", model=mock_model)
        tool_names = [t.name for t in assistant.tools]

        assert "search_eeglab_discussions" in tool_names
        assert "list_eeglab_recent" in tool_names
        assert "search_eeglab_papers" in tool_names
        assert "retrieve_eeglab_docs" in tool_names

    def test_assistant_loads_plugin_tools(self, mock_model):
        """Test that plugin tools are loaded from eeglab.tools module."""
        assistant = registry.create_assistant("eeglab", model=mock_model)
        tool_names = [t.name for t in assistant.tools]

        # Phase 2 tool
        assert "search_eeglab_docstrings" in tool_names

        # Phase 3 tool
        assert "search_eeglab_faqs" in tool_names

    def test_system_prompt_includes_tools(self, mock_model):
        """Test that system prompt mentions available tools."""
        assistant = registry.create_assistant("eeglab", model=mock_model)
        prompt = assistant.get_system_prompt()

        assert "EEGLab" in prompt or "EEGLAB" in prompt
        assert "search_eeglab" in prompt.lower()

    def test_has_minimum_required_tools(self, mock_model):
        """Test that assistant has all required tools."""
        assistant = registry.create_assistant("eeglab", model=mock_model)
        tool_names = {t.name for t in assistant.tools}

        # Verify required standard tools
        required_standard = {
            "retrieve_eeglab_docs",
            "search_eeglab_discussions",
            "list_eeglab_recent",
            "search_eeglab_papers",
        }
        assert required_standard.issubset(tool_names), (
            f"Missing standard tools: {required_standard - tool_names}"
        )

        # Verify required plugin tools
        required_plugins = {
            "search_eeglab_docstrings",
            "search_eeglab_faqs",
        }
        assert required_plugins.issubset(tool_names), (
            f"Missing plugin tools: {required_plugins - tool_names}"
        )


class TestEEGLabRealQuestions:
    """Test assistant with real EEG researcher questions."""

    @pytest.fixture
    def assistant(self):
        """Create EEGLab assistant with mocked LLM."""
        mock_model = MagicMock()
        return registry.create_assistant("eeglab", model=mock_model)

    @pytest.mark.skipif(
        not registry.get("eeglab"),
        reason="EEGLab config not found",
    )
    def test_question_import_data(self, assistant):
        """Test: How do I import my EEG data?"""
        # This is a smoke test - verify assistant can be invoked
        # Real test would check tool invocation and response quality
        assert assistant is not None
        assert len(assistant.tools) > 0

    def test_question_remove_artifacts(self, assistant):
        """Test: What's the best way to remove artifacts?"""
        # FAQ search should be invoked for this common question
        faq_tool = next((t for t in assistant.tools if "faq" in t.name), None)
        assert faq_tool is not None

    def test_question_iclabel_usage(self, assistant):
        """Test: How do I use ICLabel?"""
        # Docstring search might be useful here
        docstring_tool = next((t for t in assistant.tools if "docstring" in t.name), None)
        assert docstring_tool is not None


class TestToolImplementations:
    """Test individual tool implementations."""

    def test_docstring_tool_handles_empty_db(self):
        """Test docstring tool with empty database."""
        from src.assistants.eeglab.tools import search_eeglab_docstrings

        # Tool should be a LangChain tool object
        assert hasattr(search_eeglab_docstrings, "name")
        assert search_eeglab_docstrings.name == "search_eeglab_docstrings"

        # Should return helpful error message when DB doesn't exist
        result = search_eeglab_docstrings.invoke({"query": "pop_loadset"})
        assert isinstance(result, str)
        assert "not initialized" in result.lower()

    def test_docstring_tool_with_populated_db(self, populated_test_db):  # noqa: ARG002
        """Test docstring search returns and formats results correctly."""
        from src.assistants.eeglab.tools import search_eeglab_docstrings

        result = search_eeglab_docstrings.invoke({"query": "pop_loadset"})

        # Verify no AttributeError (was the critical bug)
        assert isinstance(result, str)
        assert "Found" in result
        assert "pop_loadset" in result
        # Verify it uses correct SearchResult attributes
        assert "Language:" in result
        assert "matlab" in result
        assert "View source" in result or "github.com" in result.lower()

    def test_docstring_tool_handles_no_results(self, populated_test_db):  # noqa: ARG002
        """Test docstring search with query that returns no results."""
        from src.assistants.eeglab.tools import search_eeglab_docstrings

        result = search_eeglab_docstrings.invoke({"query": "nonexistent_function_xyz"})

        assert isinstance(result, str)
        assert "No function documentation found" in result

    def test_faq_tool_handles_empty_db(self):
        """Test FAQ tool with empty database."""
        from src.assistants.eeglab.tools import search_eeglab_faqs

        # Tool should be a LangChain tool object
        assert hasattr(search_eeglab_faqs, "name")
        assert search_eeglab_faqs.name == "search_eeglab_faqs"

        # Should return helpful error message when DB doesn't exist
        result = search_eeglab_faqs.invoke({"query": "artifact removal"})
        assert isinstance(result, str)
        assert "not initialized" in result.lower()

    def test_faq_tool_with_populated_db(self, populated_test_db):  # noqa: ARG002
        """Test FAQ search returns and formats results correctly."""
        from src.assistants.eeglab.tools import search_eeglab_faqs

        result = search_eeglab_faqs.invoke({"query": "artifacts"})

        # Verify correct formatting
        assert isinstance(result, str)
        assert "Found" in result
        assert "How do I remove artifacts?" in result
        assert "Category:" in result
        assert "Quality:" in result
        assert "Tags:" in result
        assert "View thread" in result

    def test_faq_tool_handles_no_results(self, populated_test_db):  # noqa: ARG002
        """Test FAQ search with query that returns no results."""
        from src.assistants.eeglab.tools import search_eeglab_faqs

        result = search_eeglab_faqs.invoke({"query": "nonexistent_topic_xyz"})

        assert isinstance(result, str)
        assert "No FAQ entries found" in result

    def test_plugin_tools_have_descriptions(self):
        """Test that plugin tools have comprehensive descriptions."""
        from src.assistants.eeglab.tools import search_eeglab_docstrings, search_eeglab_faqs

        # Check docstring tool description
        assert hasattr(search_eeglab_docstrings, "description")
        assert len(search_eeglab_docstrings.description) > 50
        assert (
            "MATLAB" in search_eeglab_docstrings.description
            or "Python" in search_eeglab_docstrings.description
        )

        # Check FAQ tool description
        assert hasattr(search_eeglab_faqs, "description")
        assert len(search_eeglab_faqs.description) > 50
        assert (
            "FAQ" in search_eeglab_faqs.description or "mailing" in search_eeglab_faqs.description
        )


class TestPluginIntegration:
    """Test plugin system integration."""

    def test_extensions_configured_correctly(self):
        """Test that extensions are properly configured in YAML."""
        info = registry.get("eeglab")
        assert info.community_config.extensions is not None
        assert info.community_config.extensions.python_plugins is not None
        assert len(info.community_config.extensions.python_plugins) > 0

        # Check plugin module is correct
        plugin = info.community_config.extensions.python_plugins[0]
        assert plugin.module == "src.assistants.eeglab.tools"
        assert "search_eeglab_docstrings" in plugin.tools
        assert "search_eeglab_faqs" in plugin.tools

    def test_plugin_tools_are_callable(self):
        """Test that plugin tools can be invoked."""
        from src.assistants.eeglab.tools import search_eeglab_docstrings, search_eeglab_faqs

        # Test docstring tool is callable
        assert callable(search_eeglab_docstrings.invoke)
        result = search_eeglab_docstrings.invoke({"query": "test"})
        assert isinstance(result, str)

        # Test FAQ tool is callable
        assert callable(search_eeglab_faqs.invoke)
        result = search_eeglab_faqs.invoke({"query": "test"})
        assert isinstance(result, str)
