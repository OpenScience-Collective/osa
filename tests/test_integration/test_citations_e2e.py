"""End-to-end citation test against the real Claude Platform on AWS endpoint.

This is the Definition of Done for issue #364 (Phase 4 of the citations
epic, #360): a real request that retrieves a document through the full
agent graph and returns an answer carrying at least one citation whose
source is that document's URL.

Real, paid API call (claude-haiku-4-5, capped at a few thousand tokens).
Skip condition mirrors test_anthropic_platform.py: ANTHROPIC_API_KEY (like
ANTHROPIC_BASE_URL and ANTHROPIC_WORKSPACE_ID) lives only in the .env file
read by pydantic-settings, never exported into the process environment, so
the skip check reads Settings rather than os.getenv.
"""

import pytest

from src.api.config import get_settings
from src.api.routers.community import _extract_agent_result
from src.assistants import discover_assistants, registry
from src.core.services.anthropic_llm import create_anthropic_llm

pytestmark = [
    pytest.mark.llm,
    pytest.mark.integration,
    pytest.mark.skipif(
        not get_settings().anthropic_api_key,
        reason="ANTHROPIC_API_KEY is not configured (server mode requires it)",
    ),
]

discover_assistants()


class TestCitationsEndToEnd:
    """A real retrieve_hed_docs call must produce an inline citation."""

    async def test_retrieved_document_is_cited_with_its_own_url(self) -> None:
        info = registry.get("hed")
        assert info is not None
        assert info.community_config is not None
        on_demand_docs = [d for d in info.community_config.documentation if not d.preload]
        assert on_demand_docs, "HED config has no on-demand documents to retrieve and cite"
        target_doc = on_demand_docs[0]
        target_url = str(target_doc.url)

        llm = create_anthropic_llm(model="claude-haiku-4-5", max_tokens=3000)
        assistant = registry.create_assistant("hed", model=llm, preload_docs=False, citations=True)

        question = (
            f"Use retrieve_hed_docs to fetch {target_url}, then answer in 2-3 sentences: "
            f"what is this document about? Base your answer only on that document."
        )
        result = await assistant.ainvoke(question)

        tools_called = [tc.get("name", "") for tc in result.get("tool_calls", [])]
        assert "retrieve_hed_docs" in tools_called, (
            f"Model did not call retrieve_hed_docs; tools called: {tools_called}"
        )

        ar = _extract_agent_result(result)

        assert ar.response_content.strip(), "Expected a non-empty answer"
        assert "[1]" in ar.response_content, (
            f"Expected an inline [1] marker in the answer, got: {ar.response_content!r}"
        )
        assert ar.citations, "Expected at least one citation"
        assert ar.citations[0].marker == 1
        assert ar.citations[0].source == target_url, (
            f"Expected the citation source to be the retrieved document's URL "
            f"({target_url}), got {ar.citations[0].source!r}"
        )
        assert ar.citations[0].cited_text, "cited_text must be a non-empty span of the source"
