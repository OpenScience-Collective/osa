"""End-to-end citation tests against the real Claude Platform on AWS endpoint.

The first test is the Definition of Done for issue #364 (Phase 4 of the
citations epic, #360): a real request that retrieves a document through the
full agent graph and returns an answer carrying at least one citation whose
source is that document's URL.

The second covers the streaming path, which the unit tests can only reach
through a hand-built chunk shape (see tests/test_api/test_citation_streaming.py,
whose own docstring says so). Phase 2's cache-token bug shipped precisely
because every test hand-built a shape the API never produces, and streaming
citations rest on an assumption worth checking against the wire: that a
citation arrives as its own delta on an ``on_chat_model_stream`` chunk, at
the end of the span it annotates, rather than only on the final aggregated
message. If that assumption were wrong, the unit tests would stay green
while inline markers silently vanished from every streamed answer.

Real, paid API calls (claude-haiku-4-5, capped at a few thousand tokens).
Skip condition mirrors test_anthropic_platform.py: ANTHROPIC_API_KEY (like
ANTHROPIC_BASE_URL and ANTHROPIC_WORKSPACE_ID) lives only in the .env file
read by pydantic-settings, never exported into the process environment, so
the skip check reads Settings rather than os.getenv.
"""

import json

import pytest

from src.api.config import get_settings
from src.api.routers.community import _extract_agent_result, _stream_ask_response
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


def _first_on_demand_doc_url() -> str:
    """URL of a HED document that is retrieved on demand rather than preloaded.

    Preloaded documents live in the system prompt and cannot be cited, so a
    citation test has to target one the assistant fetches through a tool.
    """
    info = registry.get("hed")
    assert info is not None
    assert info.community_config is not None
    on_demand_docs = [d for d in info.community_config.documentation if not d.preload]
    assert on_demand_docs, "HED config has no on-demand documents to retrieve and cite"
    return str(on_demand_docs[0].url)


def _authorized_origin() -> str:
    """An origin HED's own config trusts, so the platform key may be used.

    Read from the config rather than hardcoded: if HED's cors_origins
    change, this follows them instead of silently starting to test the
    403 path.
    """
    info = registry.get("hed")
    assert info is not None
    assert info.community_config is not None
    exact = [o for o in info.community_config.cors_origins if "*" not in o]
    assert exact, "HED config has no wildcard-free cors_origins to use as an Origin"
    return exact[0]


class TestCitationsEndToEnd:
    """A real retrieve_hed_docs call must produce an inline citation."""

    async def test_retrieved_document_is_cited_with_its_own_url(self) -> None:
        target_url = _first_on_demand_doc_url()

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


class TestStreamedCitationsEndToEnd:
    """The SSE stream must carry citations, inline, from a real response."""

    async def test_streamed_answer_carries_inline_citation_events(self) -> None:
        """Drive the router's own SSE generator against the real endpoint.

        `_stream_ask_response` is used directly rather than through an HTTP
        client so the assertions can see event order, which is the whole
        point: a marker that arrives after the answer is finished is not an
        inline citation.

        An Origin is required, and taken from HED's own config rather than
        hardcoded: without one the platform key is refused (`_resolve_provider`
        only falls back to it for an authorized origin), and the citations
        flag is derived from the resolved provider, so origin, key and
        citations all hang together on this path.
        """
        target_url = _first_on_demand_doc_url()
        origin = _authorized_origin()
        question = (
            f"Use retrieve_hed_docs to fetch {target_url}, then answer in 2-3 sentences: "
            f"what is this document about? Base your answer only on that document."
        )

        events: list[dict] = []
        content_so_far = ""
        async for raw in _stream_ask_response(
            community_id="hed",
            question=question,
            byok=None,
            origin=origin,
            user_id=None,
            requested_model="claude-haiku-4-5",
        ):
            for line in raw.splitlines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line.removeprefix("data: "))
                events.append(event)
                if event.get("event") == "content":
                    content_so_far += event["content"]

        kinds = [e.get("event") for e in events]
        assert "error" not in kinds, f"Stream reported an error: {events}"

        tool_starts = [e.get("name") for e in events if e.get("event") == "tool_start"]
        assert "retrieve_hed_docs" in tool_starts, (
            f"Model did not call retrieve_hed_docs; tools started: {tool_starts}"
        )

        citation_events = [e for e in events if e.get("event") == "citation"]
        assert citation_events, (
            "No citation event in the stream. Either the model cited nothing, or a "
            "real streamed citation does not arrive as its own content-block delta "
            f"the way tests/test_api/test_citation_streaming.py assumes. Events: {kinds}"
        )

        first = citation_events[0]
        assert first["marker"] == 1
        assert first["source"] == target_url, (
            f"Expected the citation source to be the retrieved document's URL "
            f"({target_url}), got {first['source']!r}"
        )
        assert first["cited_text"], "cited_text must be a non-empty span of the source"

        assert "[1]" in content_so_far, (
            f"Expected an inline [1] marker in the streamed content: {content_so_far!r}"
        )

        # Metadata must precede the marker so a client can link the inline
        # token as soon as it is rendered. The marker remains an inline event,
        # not a trailing bundle of links.
        for citation_index, citation in enumerate(events):
            if citation.get("event") != "citation":
                continue
            marker = citation["marker"]
            marker_index = next(
                (
                    index
                    for index, event in enumerate(events[citation_index + 1 :], citation_index + 1)
                    if event.get("event") == "content" and event.get("content") == f"[{marker}]"
                ),
                None,
            )
            assert marker_index is not None, f"No inline marker event followed citation [{marker}]"
            assert citation_index < marker_index

        done = [e for e in events if e.get("event") == "done"]
        assert done, "Stream did not end with a done event"
        done_citations = done[-1].get("citations", [])
        assert done_citations, "done event must repeat the citation list"
        assert done_citations[0]["source"] == target_url
        assert {c["marker"] for c in done_citations} == {e["marker"] for e in citation_events}
