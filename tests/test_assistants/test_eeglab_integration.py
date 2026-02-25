"""Integration tests for EEGLab assistant."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.assistants import discover_assistants
from src.assistants.registry import registry
from src.knowledge.db import get_connection, init_db
from src.tools.knowledge import create_search_docstrings_tool, create_search_faq_tool


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

    def test_config_has_docstrings(self):
        """Test that docstrings config is present for auto-generation."""
        info = registry.get("eeglab")
        assert info.community_config.docstrings is not None
        assert len(info.community_config.docstrings.repos) > 0

    def test_config_has_faq_generation(self):
        """Test that FAQ generation config is present for auto-generation."""
        info = registry.get("eeglab")
        assert info.community_config.faq_generation is not None

    def test_config_no_extensions(self):
        """Test that EEGLAB no longer uses custom extensions (migrated to generic)."""
        info = registry.get("eeglab")
        # Extensions should be None or have no python_plugins
        if info.community_config.extensions is not None:
            assert (
                info.community_config.extensions.python_plugins is None
                or len(info.community_config.extensions.python_plugins) == 0
            )


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

    def test_assistant_has_docstring_tool(self, mock_model):
        """Test that generic docstring search tool is auto-generated from config."""
        assistant = registry.create_assistant("eeglab", model=mock_model)
        tool_names = [t.name for t in assistant.tools]

        # Generic factory tool name
        assert "search_eeglab_code_docs" in tool_names

    def test_assistant_has_faq_tool(self, mock_model):
        """Test that generic FAQ search tool is auto-generated from config."""
        assistant = registry.create_assistant("eeglab", model=mock_model)
        tool_names = [t.name for t in assistant.tools]

        # Generic factory tool name
        assert "search_eeglab_faq" in tool_names

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

        # Verify auto-generated tools from config
        required_auto = {
            "search_eeglab_code_docs",
            "search_eeglab_faq",
        }
        assert required_auto.issubset(tool_names), (
            f"Missing auto-generated tools: {required_auto - tool_names}"
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
        assert assistant is not None
        assert len(assistant.tools) > 0

    def test_question_remove_artifacts(self, assistant):
        """Test: What's the best way to remove artifacts?"""
        faq_tool = next((t for t in assistant.tools if "faq" in t.name), None)
        assert faq_tool is not None

    def test_question_iclabel_usage(self, assistant):
        """Test: How do I use ICLabel?"""
        docstring_tool = next((t for t in assistant.tools if "code_docs" in t.name), None)
        assert docstring_tool is not None


class TestToolImplementations:
    """Test generic tool factory implementations."""

    def test_docstring_tool_handles_empty_db(self, tmp_path: Path):
        """Test docstring tool with empty database."""
        tool = create_search_docstrings_tool("eeglab", "EEGLAB")

        assert hasattr(tool, "name")
        assert tool.name == "search_eeglab_code_docs"

        # Point to non-existent DB to ensure "not initialized" response
        fake_db = tmp_path / "knowledge" / "eeglab.db"
        with patch("src.tools.knowledge.get_db_path", return_value=fake_db):
            result = tool.invoke({"query": "pop_loadset"})
        assert isinstance(result, str)
        assert "not initialized" in result.lower()

    def test_docstring_tool_with_populated_db(self, populated_test_db):  # noqa: ARG002
        """Test docstring search returns and formats results correctly."""
        tool = create_search_docstrings_tool("eeglab", "EEGLAB")

        result = tool.invoke({"query": "pop_loadset"})

        assert isinstance(result, str)
        assert "pop_loadset" in result
        assert "github.com" in result.lower() or "View source" in result

    def test_docstring_tool_handles_no_results(self, populated_test_db):  # noqa: ARG002
        """Test docstring search with query that returns no results."""
        tool = create_search_docstrings_tool("eeglab", "EEGLAB")

        result = tool.invoke({"query": "nonexistent_function_xyz"})

        assert isinstance(result, str)
        assert "No code documentation found" in result

    def test_faq_tool_handles_empty_db(self, tmp_path: Path):
        """Test FAQ tool with empty database."""
        tool = create_search_faq_tool("eeglab", "EEGLAB")

        assert hasattr(tool, "name")
        assert tool.name == "search_eeglab_faq"

        # Point to non-existent DB to ensure "not initialized" response
        fake_db = tmp_path / "knowledge" / "eeglab.db"
        with patch("src.tools.knowledge.get_db_path", return_value=fake_db):
            result = tool.invoke({"query": "artifact removal"})
        assert isinstance(result, str)
        assert "not initialized" in result.lower()

    def test_faq_tool_with_populated_db(self, populated_test_db):  # noqa: ARG002
        """Test FAQ search returns and formats results correctly."""
        tool = create_search_faq_tool("eeglab", "EEGLAB")

        result = tool.invoke({"query": "artifacts"})

        assert isinstance(result, str)
        assert "How do I remove artifacts?" in result

    def test_faq_tool_handles_no_results(self, populated_test_db):  # noqa: ARG002
        """Test FAQ search with query that returns no results."""
        tool = create_search_faq_tool("eeglab", "EEGLAB")

        result = tool.invoke({"query": "nonexistent_topic_xyz"})

        assert isinstance(result, str)
        assert "No FAQ entries found" in result

    def test_tools_have_descriptions(self):
        """Test that generic factory tools have comprehensive descriptions."""
        docstring_tool = create_search_docstrings_tool("eeglab", "EEGLAB")
        faq_tool = create_search_faq_tool("eeglab", "EEGLAB")

        assert hasattr(docstring_tool, "description")
        assert len(docstring_tool.description) > 50
        assert "EEGLAB" in docstring_tool.description

        assert hasattr(faq_tool, "description")
        assert len(faq_tool.description) > 50
        assert "EEGLAB" in faq_tool.description
