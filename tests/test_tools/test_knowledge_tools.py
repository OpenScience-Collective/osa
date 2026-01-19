"""Tests for knowledge discovery tools.

Tests cover:
- Database initialization checks
- Result formatting
- No results handling
- Tool creation with different configurations
"""

from pathlib import Path
from unittest.mock import patch

from src.knowledge.db import get_connection, init_db, upsert_github_item, upsert_paper
from src.tools.knowledge import (
    create_knowledge_tools,
    create_list_recent_tool,
    create_search_discussions_tool,
    create_search_papers_tool,
)


class TestSearchDiscussionsTool:
    """Tests for search discussions tool."""

    def test_returns_error_when_db_not_exists(self, tmp_path: Path) -> None:
        """Should return initialization message when DB doesn't exist."""
        tool = create_search_discussions_tool("test", "Test Community")

        nonexistent_path = tmp_path / "nonexistent.db"
        with patch("src.tools.knowledge.get_db_path", return_value=nonexistent_path):
            result = tool.invoke({"query": "validation"})
            assert "not initialized" in result
            assert "osa sync init" in result

    def test_returns_no_results_message(self, tmp_path: Path) -> None:
        """Should return 'no results' message for non-matching query."""
        tool = create_search_discussions_tool("test", "Test Community")

        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            # Patch both locations for search to work
            with (
                patch("src.tools.knowledge.get_db_path", return_value=db_path),
            ):
                result = tool.invoke({"query": "xyznonexistent123"})
                assert "No related discussions found" in result

    def test_formats_results_correctly(self, tmp_path: Path) -> None:
        """Should format GitHub items with type, status, and URL."""
        tool = create_search_discussions_tool("test", "Test Community")

        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")

            with get_connection("test") as conn:
                upsert_github_item(
                    conn,
                    repo="test-org/test-repo",
                    item_type="issue",
                    number=1,
                    title="Validation error with nested groups",
                    first_message="I'm getting a validation error.",
                    status="open",
                    url="https://github.com/test-org/test-repo/issues/1",
                    created_at="2024-01-01T00:00:00Z",
                )
                conn.commit()

            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                result = tool.invoke({"query": "validation"})
                assert "[Issue]" in result
                assert "(open)" in result
                assert "https://github.com" in result
                assert "Validation error" in result

    def test_tool_has_correct_name(self) -> None:
        """Tool should have community-specific name."""
        tool = create_search_discussions_tool("hed", "HED")
        assert tool.name == "search_hed_discussions"

        tool = create_search_discussions_tool("bids", "BIDS")
        assert tool.name == "search_bids_discussions"

    def test_tool_description_includes_repos(self) -> None:
        """Tool description should include repo list when provided."""
        repos = ["org/repo1", "org/repo2"]
        tool = create_search_discussions_tool("test", "Test", repos=repos)
        assert "repo1" in tool.description
        assert "repo2" in tool.description


class TestSearchPapersTool:
    """Tests for search papers tool."""

    def test_returns_error_when_db_not_exists(self, tmp_path: Path) -> None:
        """Should return initialization message when DB doesn't exist."""
        tool = create_search_papers_tool("test", "Test Community")

        nonexistent_path = tmp_path / "nonexistent.db"
        with patch("src.tools.knowledge.get_db_path", return_value=nonexistent_path):
            result = tool.invoke({"query": "HED annotation"})
            assert "not initialized" in result
            assert "osa sync" in result

    def test_returns_no_results_message(self, tmp_path: Path) -> None:
        """Should return 'no results' message for non-matching query."""
        tool = create_search_papers_tool("test", "Test Community")

        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                result = tool.invoke({"query": "xyznonexistent123"})
                assert "No related papers found" in result

    def test_formats_results_correctly(self, tmp_path: Path) -> None:
        """Should format papers with title, source, and URL."""
        tool = create_search_papers_tool("test", "Test Community")

        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")

            with get_connection("test") as conn:
                upsert_paper(
                    conn,
                    source="openalex",
                    external_id="W12345",
                    title="HED Annotation Framework",
                    first_message="This paper describes the HED framework.",
                    url="https://doi.org/10.1234/hed",
                    created_at="2024-01-01",
                )
                conn.commit()

            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                result = tool.invoke({"query": "HED"})
                assert "HED Annotation Framework" in result
                assert "[openalex]" in result
                assert "https://doi.org" in result

    def test_tool_has_correct_name(self) -> None:
        """Tool should have community-specific name."""
        tool = create_search_papers_tool("hed", "HED")
        assert tool.name == "search_hed_papers"

        tool = create_search_papers_tool("bids", "BIDS")
        assert tool.name == "search_bids_papers"


class TestListRecentTool:
    """Tests for list recent activity tool."""

    def test_returns_error_when_db_not_exists(self, tmp_path: Path) -> None:
        """Should return initialization message when DB doesn't exist."""
        tool = create_list_recent_tool("test", "Test Community")

        nonexistent_path = tmp_path / "nonexistent.db"
        with patch("src.tools.knowledge.get_db_path", return_value=nonexistent_path):
            result = tool.invoke({})
            assert "not initialized" in result
            assert "osa sync" in result

    def test_returns_no_results_message(self, tmp_path: Path) -> None:
        """Should return 'no items' message for empty DB."""
        tool = create_list_recent_tool("test", "Test Community")

        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                result = tool.invoke({})
                # Check for either format of "no results" message
                assert "No" in result and ("activity" in result or "items" in result)

    def test_formats_results_correctly(self, tmp_path: Path) -> None:
        """Should format recent items with type, status, and URL."""
        tool = create_list_recent_tool("test", "Test Community")

        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")

            with get_connection("test") as conn:
                upsert_github_item(
                    conn,
                    repo="test-org/test-repo",
                    item_type="pr",
                    number=42,
                    title="Add new feature",
                    first_message="This PR adds a new feature.",
                    status="open",
                    url="https://github.com/test-org/test-repo/pull/42",
                    created_at="2024-01-01T00:00:00Z",
                )
                conn.commit()

            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                result = tool.invoke({})
                assert "[PR]" in result
                assert "(open)" in result
                assert "https://github.com" in result
                assert "Add new feature" in result

    def test_tool_has_correct_name(self) -> None:
        """Tool should have community-specific name."""
        tool = create_list_recent_tool("hed", "HED")
        assert tool.name == "list_hed_recent"

        tool = create_list_recent_tool("bids", "BIDS")
        assert tool.name == "list_bids_recent"


class TestCreateKnowledgeTools:
    """Tests for the create_knowledge_tools factory function."""

    def test_creates_all_tools_by_default(self) -> None:
        """Should create all three tools by default."""
        tools = create_knowledge_tools("test", "Test Community")

        tool_names = [t.name for t in tools]
        assert "search_test_discussions" in tool_names
        assert "list_test_recent" in tool_names
        assert "search_test_papers" in tool_names
        assert len(tools) == 3

    def test_can_exclude_discussions(self) -> None:
        """Should allow excluding discussions tool."""
        tools = create_knowledge_tools("test", "Test", include_discussions=False)

        tool_names = [t.name for t in tools]
        assert "search_test_discussions" not in tool_names
        assert "list_test_recent" in tool_names
        assert "search_test_papers" in tool_names
        assert len(tools) == 2

    def test_can_exclude_recent(self) -> None:
        """Should allow excluding recent activity tool."""
        tools = create_knowledge_tools("test", "Test", include_recent=False)

        tool_names = [t.name for t in tools]
        assert "search_test_discussions" in tool_names
        assert "list_test_recent" not in tool_names
        assert "search_test_papers" in tool_names
        assert len(tools) == 2

    def test_can_exclude_papers(self) -> None:
        """Should allow excluding papers tool."""
        tools = create_knowledge_tools("test", "Test", include_papers=False)

        tool_names = [t.name for t in tools]
        assert "search_test_discussions" in tool_names
        assert "list_test_recent" in tool_names
        assert "search_test_papers" not in tool_names
        assert len(tools) == 2

    def test_passes_repos_to_tools(self) -> None:
        """Should pass repos list to tool descriptions."""
        repos = ["org/repo1", "org/repo2"]
        tools = create_knowledge_tools("test", "Test", repos=repos)

        # Check that repos appear in discussion tool description
        discussion_tool = next(t for t in tools if "discussions" in t.name)
        assert "repo1" in discussion_tool.description


class TestHEDKnowledgeToolsIntegration:
    """Integration tests for HED knowledge tools via registry."""

    def test_hed_assistant_has_knowledge_tools(self) -> None:
        """HED assistant should have all knowledge tools."""
        from langchain_core.language_models import FakeListChatModel

        from src.assistants import discover_assistants, registry

        discover_assistants()

        model = FakeListChatModel(responses=["Test"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        tool_names = [t.name for t in assistant.tools]

        # Should have HED-specific knowledge tools
        assert "search_hed_discussions" in tool_names
        assert "list_hed_recent" in tool_names
        assert "search_hed_papers" in tool_names
