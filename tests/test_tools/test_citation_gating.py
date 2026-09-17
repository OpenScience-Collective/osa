"""Provider-gated citation behavior for the knowledge tool factories.

With citations=False every citable tool must keep returning exactly the
string it returns today (the OpenRouter BYOK path is untouched). With
citations=True, and real matching data in the database, it must return a
list of search_result blocks instead, so Claude can attach inline
citations to claims drawn from them.

This is dynamic over a registry of tool specs (one per citable factory in
src/tools/knowledge.py) rather than one hardcoded test per tool, so a new
citable tool added later without wiring the switch fails this test instead
of silently shipping without citations. search_papers_live is exercised
separately (tests/test_tools/test_knowledge_tools.py has no analog either,
since it needs a real network call to opencite); see
TestSearchPapersLiveCitations below.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.knowledge.db import (
    get_connection,
    init_db,
    upsert_discourse_topic,
    upsert_faq_entry,
    upsert_paper,
)
from src.tools.knowledge import (
    create_get_full_docstring_tool,
    create_search_discourse_tool,
    create_search_faq_tool,
    create_search_papers_tool,
)


def _seed_paper(community_id: str) -> dict[str, Any]:
    with get_connection(community_id) as conn:
        upsert_paper(
            conn,
            source="openalex",
            external_id="W1",
            title="HED Annotation Framework",
            first_message="This paper describes the HED annotation framework in detail.",
            url="https://doi.org/10.1234/hed-paper",
            created_at="2024-01-01",
        )
        conn.commit()
    return {"query": "HED annotation", "expected_source": "https://doi.org/10.1234/hed-paper"}


def _seed_faq(community_id: str) -> dict[str, Any]:
    with get_connection(community_id) as conn:
        upsert_faq_entry(
            conn,
            list_name="eeglab-list",
            thread_id="thread-1",
            thread_url="https://mailman.example.org/thread/1",
            question="How do I remove artifacts from EEG data?",
            answer="Use ICA decomposition and reject components correlated with EOG channels.",
            tags=["artifacts", "ica"],
            category="how-to",
            message_count=4,
            participant_count=2,
            first_message_date="2024-01-01",
            quality_score=0.9,
            summary_model="test-model",
        )
        conn.commit()
    return {
        "query": "remove artifacts",
        "expected_source": "https://mailman.example.org/thread/1",
    }


def _seed_discourse(community_id: str) -> dict[str, Any]:
    with get_connection(community_id) as conn:
        upsert_discourse_topic(
            conn,
            forum_url="https://forum.example.org",
            topic_id=1,
            title="Epoch rejection best practices",
            first_post="A discussion of how to reject noisy epochs before ICA.",
            accepted_answer="Use an amplitude threshold and visual inspection together.",
            category_name="preprocessing",
            tags=["epochs"],
            reply_count=3,
            like_count=1,
            views=10,
            url="https://forum.example.org/t/1",
            created_at="2024-01-01T00:00:00Z",
            last_posted_at="2024-01-02T00:00:00Z",
        )
        conn.commit()
    return {"query": "epoch rejection", "expected_source": "https://forum.example.org/t/1"}


def _seed_docstring(community_id: str) -> dict[str, Any]:
    from src.knowledge.db import upsert_docstring

    with get_connection(community_id) as conn:
        upsert_docstring(
            conn,
            repo="test-org/test-repo",
            file_path="src/example.py",
            language="python",
            symbol_name="pop_select",
            symbol_type="function",
            docstring="pop_select(EEG, ...)\n\nSelect a subset of channels or trials.",
        )
        conn.commit()
    return {"symbol_name": "pop_select", "expected_source_contains": "test-org/test-repo"}


@dataclass
class CitableToolSpec:
    """One citable tool factory, plus how to seed it and invoke it."""

    name: str
    factory: Callable[..., Any]
    seed: Callable[[str], dict[str, Any]]
    invoke_args: Callable[[dict[str, Any]], dict[str, Any]]


CITABLE_TOOL_SPECS: list[CitableToolSpec] = [
    CitableToolSpec(
        name="search_papers",
        factory=create_search_papers_tool,
        seed=_seed_paper,
        invoke_args=lambda seeded: {"query": seeded["query"]},
    ),
    CitableToolSpec(
        name="search_faq",
        factory=create_search_faq_tool,
        seed=_seed_faq,
        invoke_args=lambda seeded: {"query": seeded["query"]},
    ),
    CitableToolSpec(
        name="search_discourse",
        factory=create_search_discourse_tool,
        seed=_seed_discourse,
        invoke_args=lambda seeded: {"query": seeded["query"]},
    ),
    CitableToolSpec(
        name="get_full_docstring",
        factory=create_get_full_docstring_tool,
        seed=_seed_docstring,
        invoke_args=lambda seeded: {"symbol_name": seeded["symbol_name"]},
    ),
]


@pytest.mark.parametrize("spec", CITABLE_TOOL_SPECS, ids=lambda s: s.name)
class TestProviderGating:
    """Every citable knowledge tool switches on its own citations flag."""

    def test_citations_false_returns_string(self, spec: CitableToolSpec, tmp_path: Path) -> None:
        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            seeded = spec.seed("test")
            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                tool = spec.factory("test", "Test Community", citations=False)
                result = tool.invoke(spec.invoke_args(seeded))

        assert isinstance(result, str), f"{spec.name} with citations=False must return str"

    def test_citations_true_returns_blocks(self, spec: CitableToolSpec, tmp_path: Path) -> None:
        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            seeded = spec.seed("test")
            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                tool = spec.factory("test", "Test Community", citations=True)
                result = tool.invoke(spec.invoke_args(seeded))

        assert isinstance(result, list), f"{spec.name} with citations=True must return a list"
        assert len(result) >= 1
        for block in result:
            assert block["type"] == "search_result"
            assert block["citations"] == {"enabled": True}
            assert block["content"][0]["text"]

        if "expected_source" in seeded:
            assert result[0]["source"] == seeded["expected_source"]
        elif "expected_source_contains" in seeded:
            assert seeded["expected_source_contains"] in result[0]["source"]

    def test_citations_true_with_no_results_returns_string(
        self, spec: CitableToolSpec, tmp_path: Path
    ) -> None:
        """Nothing to cite (no matching rows) falls back to the plain string."""
        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                tool = spec.factory("test", "Test Community", citations=True)
                if spec.name == "get_full_docstring":
                    result = tool.invoke({"symbol_name": "xyznonexistent123"})
                else:
                    result = tool.invoke({"query": "xyznonexistent123"})

        assert isinstance(result, str)


class TestSearchPapersLiveCitations:
    """Live opencite search's citation switch (real network, see class docstring).

    Consistent with tests/test_knowledge/test_papers_sync.py's
    TestLivePaperSearch, which already makes a real call to opencite for
    the underlying search function. Kept out of the parametrized
    TestProviderGating above because seeding it deterministically would
    require faking the opencite response, which is exactly the kind of
    mocked business logic this project's test rules forbid.
    """

    @pytest.mark.network
    def test_citations_true_returns_blocks_when_results_found(self, tmp_path: Path) -> None:
        from src.tools.knowledge import create_search_papers_live_tool

        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            tool = create_search_papers_live_tool("test", "Test Community", citations=True)
            result = tool.invoke({"query": "EEGLAB EEG independent component analysis", "limit": 3})

        if isinstance(result, str):
            pytest.skip("Live opencite search returned no results (network-dependent)")

        assert isinstance(result, list)
        for block in result:
            assert block["type"] == "search_result"
            assert block["citations"] == {"enabled": True}
            assert block["content"][0]["text"]

    @pytest.mark.network
    def test_citations_false_returns_string_or_no_results_message(self, tmp_path: Path) -> None:
        from src.tools.knowledge import create_search_papers_live_tool

        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            tool = create_search_papers_live_tool("test", "Test Community", citations=False)
            result = tool.invoke({"query": "EEGLAB EEG independent component analysis", "limit": 3})

        assert isinstance(result, str)
