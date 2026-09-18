"""Tests for the non-streaming citation assembly in src/api/routers/community.py.

_build_answer_with_citations() and _extract_agent_result() sit between
src/agents/content.py's block classification and the API's response
models; these tests exercise that assembly directly (constructing the
langgraph-shaped `result` dict a real agent invocation returns), including
against the same recorded real payload used in
tests/test_agents/test_content.py.
"""

import json
from pathlib import Path

from langchain_core.messages import AIMessage

from src.api.routers.community import AgentResult, CitationInfo, _extract_agent_result

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _load_citation_response() -> list[dict]:
    return json.loads((FIXTURES_DIR / "citation_response.json").read_text())


class TestBuildAnswerWithCitationsViaExtractAgentResult:
    """_extract_agent_result() drives _build_answer_with_citations() internally."""

    def test_plain_string_content_has_no_citations(self) -> None:
        result = {"messages": [AIMessage(content="A plain answer with no tools used.")]}
        ar = _extract_agent_result(result)

        assert ar.response_content == "A plain answer with no tools used."
        assert ar.citations == []

    def test_recorded_payload_produces_marker_and_citation(self) -> None:
        """Against the real recorded response: one block, one citation, one marker."""
        result = {"messages": [AIMessage(content=_load_citation_response())]}
        ar = _extract_agent_result(result)

        assert ar.response_content.endswith("[1]")
        assert "search_result_location" not in ar.response_content
        assert ar.citations == [
            CitationInfo(
                marker=1,
                source="https://www.hedtags.org/hed-resources/HedAnnotationQuickstart.html",
                title="HED Annotation Quickstart",
                cited_text=ar.citations[0].cited_text,
            )
        ]
        assert "HED" in ar.citations[0].cited_text

    def test_repeated_source_across_blocks_shares_one_marker(self) -> None:
        content = [
            {
                "type": "text",
                "text": "First claim.",
                "citations": [
                    {"source": "https://doc.example/a", "title": "Doc A", "cited_text": "x"}
                ],
            },
            {
                "type": "text",
                "text": " Second claim.",
                "citations": [
                    {"source": "https://doc.example/a", "title": "Doc A", "cited_text": "y"}
                ],
            },
        ]
        result = {"messages": [AIMessage(content=content)]}
        ar = _extract_agent_result(result)

        assert ar.response_content == "First claim.[1] Second claim.[1]"
        assert len(ar.citations) == 1
        assert ar.citations[0].marker == 1
        assert ar.citations[0].source == "https://doc.example/a"

    def test_two_distinct_sources_get_markers_in_first_appearance_order(self) -> None:
        content = [
            {
                "type": "text",
                "text": "Claim from the second doc.",
                "citations": [
                    {"source": "https://doc.example/b", "title": "Doc B", "cited_text": "b"}
                ],
            },
            {
                "type": "text",
                "text": " Claim from the first-seen-elsewhere doc.",
                "citations": [
                    {"source": "https://doc.example/a", "title": "Doc A", "cited_text": "a"}
                ],
            },
        ]
        result = {"messages": [AIMessage(content=content)]}
        ar = _extract_agent_result(result)

        assert ar.response_content == (
            "Claim from the second doc.[1] Claim from the first-seen-elsewhere doc.[2]"
        )
        markers_by_source = {c.source: c.marker for c in ar.citations}
        assert markers_by_source == {
            "https://doc.example/b": 1,
            "https://doc.example/a": 2,
        }

    def test_marker_lands_at_end_of_block_not_mid_sentence(self) -> None:
        """The marker is appended after the full block text, never inserted inside it."""
        content = [
            {
                "type": "text",
                "text": "Tags are stored in events.tsv.",
                "citations": [
                    {
                        "source": "https://doc.example/bids",
                        "title": "BIDS spec",
                        "cited_text": "events.tsv",
                    }
                ],
            }
        ]
        result = {"messages": [AIMessage(content=content)]}
        ar = _extract_agent_result(result)

        assert ar.response_content == "Tags are stored in events.tsv.[1]"

    def test_citation_delta_before_text_is_placed_after_text(self) -> None:
        """Provider adapters may surface a citation-only block before its text."""
        content = [
            {
                "type": "text",
                "index": 0,
                "citations": [
                    {
                        "source": "https://doc.example/amica",
                        "title": "AMICA paper",
                        "cited_text": "AMICA claim",
                    }
                ],
            },
            {"type": "text", "index": 0, "text": "AMICA claim."},
        ]
        result = {"messages": [AIMessage(content=content)]}
        ar = _extract_agent_result(result)

        assert ar.response_content == "AMICA claim.[1]"
        assert ar.citations[0].source == "https://doc.example/amica"

    def test_no_messages_key_returns_empty_result(self) -> None:
        ar = _extract_agent_result({})
        assert ar.response_content == ""
        assert ar.citations == []

    def test_non_ai_message_last_returns_empty_answer(self) -> None:
        from langchain_core.messages import HumanMessage

        ar = _extract_agent_result({"messages": [HumanMessage(content="hi")]})
        assert ar.response_content == ""
        assert ar.citations == []


class TestAgentResultDefaults:
    """AgentResult.citations defaults to an empty list when not passed."""

    def test_citations_defaults_to_empty_list(self) -> None:
        ar = AgentResult(
            response_content="",
            tool_calls_info=[],
            tools_called=[],
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )
        assert ar.citations == []

    def test_default_is_not_a_shared_mutable(self) -> None:
        """Two instances must not share the same list object."""
        a = AgentResult(
            response_content="",
            tool_calls_info=[],
            tools_called=[],
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )
        b = AgentResult(
            response_content="",
            tool_calls_info=[],
            tools_called=[],
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )
        a.citations.append(CitationInfo(marker=1, source="s", title="t", cited_text="c"))
        assert b.citations == []
