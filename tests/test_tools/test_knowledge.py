"""Tests for the generic knowledge tool factories."""

from langchain_core.tools import BaseTool

from src.tools.knowledge import (
    create_knowledge_tools,
    create_list_recent_tool,
    create_search_discussions_tool,
    create_search_papers_tool,
)


class TestCreateSearchDiscussionsTool:
    """Tests for create_search_discussions_tool factory."""

    def test_creates_tool_with_correct_name(self) -> None:
        """Should create tool with community-specific name."""
        tool = create_search_discussions_tool("test-id", "Test Name")
        assert tool.name == "search_test-id_discussions"

    def test_creates_tool_with_community_in_description(self) -> None:
        """Should include community name in description."""
        tool = create_search_discussions_tool("test-id", "Test Name")
        assert "Test Name" in tool.description

    def test_includes_repos_in_description_when_provided(self) -> None:
        """Should include repo list in description when repos provided."""
        repos = ["org/repo1", "org/repo2"]
        tool = create_search_discussions_tool("test-id", "Test Name", repos=repos)
        assert "org/repo1" in tool.description
        assert "org/repo2" in tool.description

    def test_tool_is_base_tool_instance(self) -> None:
        """Should return a BaseTool instance."""
        tool = create_search_discussions_tool("test-id", "Test Name")
        assert isinstance(tool, BaseTool)

    def test_tool_returns_db_not_initialized_message(self) -> None:
        """Should return appropriate message when DB doesn't exist."""
        tool = create_search_discussions_tool("nonexistent-id", "Nonexistent")
        result = tool.invoke({"query": "test"})
        assert "not initialized" in result


class TestCreateListRecentTool:
    """Tests for create_list_recent_tool factory."""

    def test_creates_tool_with_correct_name(self) -> None:
        """Should create tool with community-specific name."""
        tool = create_list_recent_tool("test-id", "Test Name")
        assert tool.name == "list_test-id_recent"

    def test_creates_tool_with_community_in_description(self) -> None:
        """Should include community name in description."""
        tool = create_list_recent_tool("test-id", "Test Name")
        assert "Test Name" in tool.description

    def test_includes_repos_in_description_when_provided(self) -> None:
        """Should include repo list in description when repos provided."""
        repos = ["org/repo1", "org/repo2"]
        tool = create_list_recent_tool("test-id", "Test Name", repos=repos)
        assert "org/repo1" in tool.description
        assert "org/repo2" in tool.description

    def test_tool_is_base_tool_instance(self) -> None:
        """Should return a BaseTool instance."""
        tool = create_list_recent_tool("test-id", "Test Name")
        assert isinstance(tool, BaseTool)

    def test_tool_returns_db_not_initialized_message(self) -> None:
        """Should return appropriate message when DB doesn't exist."""
        tool = create_list_recent_tool("nonexistent-id", "Nonexistent")
        result = tool.invoke({})
        assert "not initialized" in result


class TestCreateSearchPapersTool:
    """Tests for create_search_papers_tool factory."""

    def test_creates_tool_with_correct_name(self) -> None:
        """Should create tool with community-specific name."""
        tool = create_search_papers_tool("test-id", "Test Name")
        assert tool.name == "search_test-id_papers"

    def test_creates_tool_with_community_in_description(self) -> None:
        """Should include community name in description."""
        tool = create_search_papers_tool("test-id", "Test Name")
        assert "Test Name" in tool.description

    def test_tool_is_base_tool_instance(self) -> None:
        """Should return a BaseTool instance."""
        tool = create_search_papers_tool("test-id", "Test Name")
        assert isinstance(tool, BaseTool)

    def test_tool_returns_db_not_initialized_message(self) -> None:
        """Should return appropriate message when DB doesn't exist."""
        tool = create_search_papers_tool("nonexistent-id", "Nonexistent")
        result = tool.invoke({"query": "test"})
        assert "not initialized" in result


class TestCreateKnowledgeTools:
    """Tests for create_knowledge_tools convenience function."""

    def test_creates_all_tools_by_default(self) -> None:
        """Should create all three tools by default."""
        tools = create_knowledge_tools("test-id", "Test Name")
        assert len(tools) == 3

        tool_names = [t.name for t in tools]
        assert "search_test-id_discussions" in tool_names
        assert "list_test-id_recent" in tool_names
        assert "search_test-id_papers" in tool_names

    def test_excludes_discussions_when_disabled(self) -> None:
        """Should not create discussions tool when disabled."""
        tools = create_knowledge_tools("test-id", "Test Name", include_discussions=False)
        tool_names = [t.name for t in tools]
        assert "search_test-id_discussions" not in tool_names
        assert len(tools) == 2

    def test_excludes_recent_when_disabled(self) -> None:
        """Should not create recent tool when disabled."""
        tools = create_knowledge_tools("test-id", "Test Name", include_recent=False)
        tool_names = [t.name for t in tools]
        assert "list_test-id_recent" not in tool_names
        assert len(tools) == 2

    def test_excludes_papers_when_disabled(self) -> None:
        """Should not create papers tool when disabled."""
        tools = create_knowledge_tools("test-id", "Test Name", include_papers=False)
        tool_names = [t.name for t in tools]
        assert "search_test-id_papers" not in tool_names
        assert len(tools) == 2

    def test_passes_repos_to_tools(self) -> None:
        """Should pass repos to discussion and recent tools."""
        repos = ["org/repo1", "org/repo2"]
        tools = create_knowledge_tools("test-id", "Test Name", repos=repos)

        for tool in tools:
            if "discussions" in tool.name or "recent" in tool.name:
                assert "org/repo1" in tool.description

    def test_returns_empty_list_when_all_disabled(self) -> None:
        """Should return empty list when all tools disabled."""
        tools = create_knowledge_tools(
            "test-id",
            "Test Name",
            include_discussions=False,
            include_recent=False,
            include_papers=False,
        )
        assert tools == []

    def test_all_tools_are_base_tool_instances(self) -> None:
        """All returned tools should be BaseTool instances."""
        tools = create_knowledge_tools("test-id", "Test Name")
        for tool in tools:
            assert isinstance(tool, BaseTool)
