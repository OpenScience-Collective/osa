"""Tests for HED Assistant.

These tests verify the HED assistant structure, system prompt generation,
and tool integration without making actual LLM API calls for unit tests.
Integration tests that require real APIs are marked accordingly.
"""

import pytest
from langchain_core.language_models import FakeListChatModel
from langchain_core.messages import AIMessage

from src.agents.hed import (
    HED_SYSTEM_PROMPT_TEMPLATE,
    HEDAssistant,
    _format_ondemand_section,
    _format_preloaded_section,
    retrieve_hed_docs,
)
from src.tools.hed import HED_DOCS


class TestHEDAssistantInitialization:
    """Tests for HEDAssistant initialization."""

    def test_hed_assistant_initialization_without_preload(self) -> None:
        """HEDAssistant should initialize without preloading docs."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = HEDAssistant(model=model, preload_docs=False)

        assert assistant.model is not None
        # Should have 4 tools: retrieve_hed_docs, validate_hed_string, suggest_hed_tags, get_hed_schema_versions
        assert len(assistant.tools) == 4
        tool_names = [t.name for t in assistant.tools]
        assert "retrieve_hed_docs" in tool_names
        assert "validate_hed_string" in tool_names
        assert "suggest_hed_tags" in tool_names
        assert "get_hed_schema_versions" in tool_names
        assert assistant.preloaded_doc_count == 0

    def test_hed_assistant_available_doc_count(self) -> None:
        """HEDAssistant should report correct available doc count."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = HEDAssistant(model=model, preload_docs=False)

        # Should match the HED_DOCS registry
        assert assistant.available_doc_count == len(HED_DOCS.docs)
        # Should have a reasonable number of docs (preloaded + on-demand)
        assert assistant.available_doc_count > 0

    def test_hed_assistant_builds_graph(self) -> None:
        """HEDAssistant should build a valid LangGraph workflow."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = HEDAssistant(model=model, preload_docs=False)
        graph = assistant.build_graph()

        assert graph is not None


class TestHEDSystemPrompt:
    """Tests for HED system prompt generation."""

    def test_system_prompt_template_has_placeholders(self) -> None:
        """System prompt template should have the required placeholders."""
        assert "{preloaded_docs}" in HED_SYSTEM_PROMPT_TEMPLATE
        assert "{ondemand_docs}" in HED_SYSTEM_PROMPT_TEMPLATE

    def test_system_prompt_contains_hed_references(self) -> None:
        """System prompt should contain key HED references."""
        assert "hedtags.org" in HED_SYSTEM_PROMPT_TEMPLATE
        assert "hed-standard" in HED_SYSTEM_PROMPT_TEMPLATE
        assert "retrieve_hed_docs" in HED_SYSTEM_PROMPT_TEMPLATE

    def test_system_prompt_without_preload(self) -> None:
        """System prompt should handle case without preloaded docs."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = HEDAssistant(model=model, preload_docs=False)

        prompt = assistant.get_system_prompt()

        assert "Preloaded documents not available" in prompt
        assert "retrieve_hed_docs" in prompt

    def test_format_ondemand_section(self) -> None:
        """On-demand section should list non-preloaded docs by category."""
        section = _format_ondemand_section()

        # Should contain on-demand doc categories
        assert "Specification" in section or "specification" in section.lower()
        assert "Tools" in section or "tools" in section.lower()

        # Should contain specific on-demand docs
        assert "HED formats" in section
        assert "HED python tools" in section

        # Should NOT contain preloaded docs
        assert "HED annotation semantics" not in section

    def test_format_preloaded_section_empty(self) -> None:
        """Preloaded section should handle empty content dict."""
        section = _format_preloaded_section({})
        assert section == ""

    def test_format_preloaded_section_with_content(self) -> None:
        """Preloaded section should format docs with content."""
        # Mock some preloaded content
        preloaded_content = {
            "https://www.hedtags.org/hed-resources/HedAnnotationSemantics.html": "# Test Content\n\nThis is test content."
        }

        section = _format_preloaded_section(preloaded_content)

        assert "HED annotation semantics" in section
        assert "Test Content" in section


class TestRetrieveHedDocsTool:
    """Tests for the retrieve_hed_docs LangChain tool."""

    def test_retrieve_hed_docs_tool_exists(self) -> None:
        """retrieve_hed_docs should be a valid LangChain tool."""
        assert retrieve_hed_docs.name == "retrieve_hed_docs"
        assert "HED documentation" in retrieve_hed_docs.description

    def test_retrieve_hed_docs_unknown_url(self) -> None:
        """retrieve_hed_docs should return error for unknown URLs."""
        result = retrieve_hed_docs.invoke({"url": "https://example.com/unknown"})

        assert "Error" in result or "not found" in result.lower()

    @pytest.mark.integration
    def test_retrieve_hed_docs_valid_url(self) -> None:
        """retrieve_hed_docs should fetch valid HED docs.

        This test requires network access.
        """
        # Use a doc with verified working source URL
        url = "https://www.hedtags.org/hed-specification/02_Terminology.html"
        result = retrieve_hed_docs.invoke({"url": url})

        assert "HED terminology" in result
        # Check it's not an error response (starts with "Error retrieving")
        assert not result.startswith("Error retrieving")


class TestHEDDocsRegistry:
    """Tests for the HED documentation registry integration.

    These tests are dynamic - they verify consistency and behavior
    rather than hardcoded values. This allows the registry configuration
    to change without breaking tests.
    """

    def test_preloaded_and_ondemand_partition_total(self) -> None:
        """Preloaded + on-demand should equal total docs (no overlap, no gaps)."""
        preloaded = HED_DOCS.get_preloaded()
        ondemand = HED_DOCS.get_on_demand()

        assert len(preloaded) + len(ondemand) == len(HED_DOCS.docs)

        # No overlap between sets
        preloaded_urls = {d.url for d in preloaded}
        ondemand_urls = {d.url for d in ondemand}
        assert len(preloaded_urls & ondemand_urls) == 0

    def test_preloaded_docs_have_correct_flag(self) -> None:
        """All docs returned by get_preloaded() should have preload=True."""
        for doc in HED_DOCS.get_preloaded():
            assert doc.preload is True, f"Doc '{doc.title}' has preload={doc.preload}"

    def test_ondemand_docs_have_correct_flag(self) -> None:
        """All docs returned by get_on_demand() should have preload=False."""
        for doc in HED_DOCS.get_on_demand():
            assert doc.preload is False, f"Doc '{doc.title}' has preload={doc.preload}"

    def test_all_docs_have_required_fields(self) -> None:
        """All documents should have required fields for the tool system."""
        for doc in HED_DOCS.docs:
            # Required for retrieval
            assert doc.url, f"Doc '{doc.title}' missing url"
            assert doc.source_url, f"Doc '{doc.title}' missing source_url"
            assert doc.source_url.startswith("https://"), f"Invalid source_url: {doc.source_url}"

            # Required for agent discovery
            assert doc.title, "Doc missing title"
            assert doc.description, f"Doc '{doc.title}' missing description"
            assert doc.category, f"Doc '{doc.title}' missing category"


class TestHEDAssistantInvocation:
    """Tests for HEDAssistant invocation (without real LLM)."""

    def test_invoke_returns_ai_message(self) -> None:
        """Invoking HEDAssistant should return an AI message."""
        model = FakeListChatModel(
            responses=["HED (Hierarchical Event Descriptors) is a standard for annotating events."]
        )
        assistant = HEDAssistant(model=model, preload_docs=False)

        result = assistant.invoke("What is HED?")

        assert "messages" in result
        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)

    def test_invoke_tracks_state(self) -> None:
        """Invoking HEDAssistant should track state properly."""
        model = FakeListChatModel(responses=["Here's the info about HED."])
        assistant = HEDAssistant(model=model, preload_docs=False)

        result = assistant.invoke("Tell me about HED annotations")

        assert "retrieved_docs" in result
        assert "tool_calls" in result

    @pytest.mark.asyncio
    async def test_ainvoke_works(self) -> None:
        """Async invocation should work."""
        model = FakeListChatModel(responses=["HED helps with event annotation."])
        assistant = HEDAssistant(model=model, preload_docs=False)

        result = await assistant.ainvoke("How does HED work?")

        assert "messages" in result
        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)


@pytest.mark.integration
class TestHEDAssistantWithPreload:
    """Integration tests that require network access for preloading."""

    def test_preload_fetches_documents(self) -> None:
        """HEDAssistant should fetch preloaded docs on init."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = HEDAssistant(model=model, preload_docs=True)

        # Should have fetched at least some docs
        assert assistant.preloaded_doc_count > 0

    def test_system_prompt_includes_preloaded_content(self) -> None:
        """System prompt should include preloaded document content."""
        model = FakeListChatModel(responses=["Hello!"])
        assistant = HEDAssistant(model=model, preload_docs=True)

        prompt = assistant.get_system_prompt()

        # Should contain actual doc content, not the placeholder
        assert "Preloaded documents not available" not in prompt
        # Should contain content from HED annotation semantics or similar
        assert "HED" in prompt
