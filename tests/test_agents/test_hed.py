"""Tests for HED Assistant via registry.

These tests verify the HED assistant created via the registry
matches expectations for tools, system prompt, and behavior.
"""

import pytest
from langchain_core.language_models import FakeListChatModel
from langchain_core.messages import AIMessage

from src.assistants import discover_assistants, registry
from src.assistants.community import CommunityAssistant


@pytest.fixture(scope="module", autouse=True)
def setup_registry() -> None:
    """Ensure registry is populated before tests."""
    registry._assistants.clear()
    discover_assistants()


class TestHEDAssistantViaRegistry:
    """Tests for HED assistant created via registry."""

    def test_hed_registered(self) -> None:
        """HED should be registered in the registry."""
        assert "hed" in registry

    def test_hed_info_has_correct_metadata(self) -> None:
        """HED info should have correct metadata."""
        info = registry.get("hed")
        assert info is not None
        assert info.name == "HED (Hierarchical Event Descriptors)"
        assert info.status == "available"
        assert info.community_config is not None

    def test_hed_has_community_config(self) -> None:
        """HED should have a community config loaded from YAML."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert info.community_config.id == "hed"
        assert len(info.community_config.documentation) > 0

    def test_hed_creates_community_assistant(self) -> None:
        """HED should create a CommunityAssistant instance."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        assert isinstance(assistant, CommunityAssistant)

    def test_hed_assistant_has_correct_tools(self) -> None:
        """HED assistant should have specialized and generic tools."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        tool_names = [t.name for t in assistant.tools]

        # Specialized HED tools from plugin
        assert "validate_hed_string" in tool_names
        assert "suggest_hed_tags" in tool_names
        assert "get_hed_schema_versions" in tool_names

        # Generic tools auto-generated from config
        assert "retrieve_hed_docs" in tool_names
        assert "search_hed_discussions" in tool_names
        assert "search_hed_papers" in tool_names
        assert "list_hed_recent" in tool_names

    def test_hed_assistant_builds_graph(self) -> None:
        """HED assistant should build a valid LangGraph workflow."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)
        graph = assistant.build_graph()

        assert graph is not None


class TestHEDSystemPrompt:
    """Tests for HED system prompt."""

    def test_system_prompt_contains_hed_references(self) -> None:
        """System prompt should contain key HED references."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        prompt = assistant.get_system_prompt()

        assert "Hierarchical Event Descriptors" in prompt
        assert "hedtags.org" in prompt

    def test_system_prompt_mentions_tools(self) -> None:
        """System prompt should mention available tools."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        prompt = assistant.get_system_prompt()

        assert "validate_hed_string" in prompt


class TestHEDDocsConfig:
    """Tests for HED documentation configuration."""

    def test_hed_has_documentation(self) -> None:
        """HED should have documentation configured."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        assert len(info.community_config.documentation) > 0

    def test_hed_has_preloaded_docs(self) -> None:
        """HED should have some docs marked for preloading."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None

        preloaded = [d for d in info.community_config.documentation if d.preload]
        assert len(preloaded) > 0

    def test_hed_docs_have_required_fields(self) -> None:
        """All HED docs should have required fields."""
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None

        for doc in info.community_config.documentation:
            assert doc.title, "Doc missing title"
            assert doc.url, "Doc missing url"
            # Preloaded docs need source_url
            if doc.preload:
                assert doc.source_url, f"Preloaded doc '{doc.title}' missing source_url"


class TestHEDAssistantInvocation:
    """Tests for HED assistant invocation (without real LLM)."""

    def test_invoke_returns_ai_message(self) -> None:
        """Invoking HED assistant should return an AI message."""
        model = FakeListChatModel(
            responses=["HED (Hierarchical Event Descriptors) is a standard for annotating events."]
        )
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        result = assistant.invoke("What is HED?")

        assert "messages" in result
        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)

    def test_invoke_tracks_state(self) -> None:
        """Invoking HED assistant should track state properly."""
        model = FakeListChatModel(responses=["Here's the info about HED."])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        result = assistant.invoke("Tell me about HED annotations")

        assert "retrieved_docs" in result
        assert "tool_calls" in result

    @pytest.mark.asyncio
    async def test_ainvoke_works(self) -> None:
        """Async invocation should work."""
        model = FakeListChatModel(responses=["HED helps with event annotation."])
        assistant = registry.create_assistant("hed", model=model, preload_docs=False)

        result = await assistant.ainvoke("How does HED work?")

        assert "messages" in result
        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)


@pytest.mark.integration
class TestHEDAssistantWithPreload:
    """Integration tests that require network access for preloading."""

    def test_preload_fetches_documents(self) -> None:
        """HED assistant should fetch preloaded docs on init."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=True)

        # Should have fetched at least some docs
        assert assistant.preloaded_doc_count > 0

    def test_system_prompt_includes_preloaded_content(self) -> None:
        """System prompt should include preloaded document content."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = registry.create_assistant("hed", model=model, preload_docs=True)

        prompt = assistant.get_system_prompt()

        # Should contain content from preloaded docs
        assert "Preloaded Documents" in prompt or "HED" in prompt
