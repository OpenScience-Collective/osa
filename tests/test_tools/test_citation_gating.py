"""The citability rule for knowledge tool factories, and its consequences.

The rule (this is the important part; the mechanics below just enforce it):

    A tool is citable if and only if its description permits answering
    from its content.

Citable today: search_{id}_code_docs, get_{id}_full_docstring,
search_{id}_faq. These are documentation, API docstrings, and curated
community answers -- things the assistant is already expected to answer
from. Making their results carry inline citations just attributes an
answer OSA already gives.

Never citable: search_{id}_discussions, list_{id}_recent,
search_{id}_papers, search_{id}_papers_live, search_{id}_forum. Every one
of these has a description that says the opposite: "This is for
DISCOVERY, not answering" / "Do NOT use ... content to formulate
answers". Whether a source is authoritative enough to answer from is a
product decision that belongs to the community/user, not something a
citations phase should grant as a side effect of adding markers to
answers. Concretely: an earlier version of this phase made
search_{id}_forum citable and rewrote its description to justify it --
exactly the kind of decision this file exists to keep out of a phase
whose only job is to attribute answers already given, not decide which
answers may be given.

TestCitabilityMatchesDescription (below) checks the rule directly and
dynamically: it derives which tools should be citable from each tool's
own description text (not a hardcoded name list), so a new discovery
tool added later without a description update -- or a new citable tool
added without wiring the switch -- fails this test automatically.

TestProviderGating covers the currently-citable tools individually with
real seeded data, verifying the block *content* (source/title/text) is
correct, which the description-only check above cannot see. Kept
alongside rather than instead of it: the two tests do not overlap.
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
    upsert_docstring,
    upsert_faq_entry,
    upsert_github_item,
    upsert_paper,
)
from src.tools.knowledge import (
    create_get_full_docstring_tool,
    create_knowledge_tools,
    create_search_faq_tool,
)

# The exact phrases every discovery-only tool's description already uses
# (see create_search_discussions_tool, create_list_recent_tool,
# create_search_papers_tool, create_search_papers_live_tool,
# create_search_discourse_tool). A tool carrying either phrase is asserting,
# in its own words, that it must not be used to formulate an answer -- so it
# must never be citable, on either path.
_DISCOVERY_ONLY_MARKERS = ("DISCOVERY, not answering", "Do NOT use")


def _is_discovery_only(description: str) -> bool:
    """True if a tool's own description forbids answering from its content."""
    return any(marker in description for marker in _DISCOVERY_ONLY_MARKERS)


class TestCitabilityMatchesDescription:
    """The citability rule, checked against every tool create_knowledge_tools produces.

    Seeds one real row into every backing table these tools query, then
    builds the full tool set with citations=True and a shared keyword
    ("widget-annotation-keyword") planted in every seeded row so every
    tool's search actually matches something. For each tool, "should this
    be citable" is derived from its own description (not a hardcoded
    list), then checked against what it actually returned.

    A future tool is covered automatically as long as it follows the
    existing convention (a discovery-only tool states so in its own
    description); a tool that is citable but was never given a seed+query
    in _INVOKE for it fails loudly rather than being silently skipped, so
    adding a new citable tool here is a forcing function, not an
    afterthought.
    """

    KEYWORD = "widget-annotation-keyword"

    @pytest.fixture
    def seeded_tools(self, tmp_path: Path) -> list[Any]:
        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            with get_connection("test") as conn:
                upsert_github_item(
                    conn,
                    repo="test-org/test-repo",
                    item_type="issue",
                    number=1,
                    title=f"Discussion about {self.KEYWORD}",
                    first_message="A GitHub issue discussing the topic.",
                    status="open",
                    url="https://github.com/test-org/test-repo/issues/1",
                    created_at="2024-01-01T00:00:00Z",
                )
                upsert_paper(
                    conn,
                    source="openalex",
                    external_id="W1",
                    title=f"A paper about {self.KEYWORD}",
                    first_message="An abstract discussing the topic in detail.",
                    url="https://doi.org/10.1234/test-paper",
                    created_at="2024-01-01",
                )
                upsert_docstring(
                    conn,
                    repo="test-org/test-repo",
                    file_path="src/example.py",
                    language="python",
                    symbol_name="example_function",
                    symbol_type="function",
                    docstring=f"example_function(...)\n\nDocumentation about {self.KEYWORD}.",
                )
                upsert_faq_entry(
                    conn,
                    list_name="test-list",
                    thread_id="thread-1",
                    thread_url="https://mailman.example.org/thread/1",
                    question=f"How does {self.KEYWORD} work?",
                    answer="Here is a detailed answer about the topic.",
                    tags=["test"],
                    category="how-to",
                    message_count=4,
                    participant_count=2,
                    first_message_date="2024-01-01",
                    quality_score=0.9,
                    summary_model="test-model",
                )
                upsert_discourse_topic(
                    conn,
                    forum_url="https://forum.example.org",
                    topic_id=1,
                    title=f"Forum topic about {self.KEYWORD}",
                    first_post="A forum post discussing the topic.",
                    accepted_answer=None,
                    category_name="general",
                    tags=None,
                    reply_count=1,
                    like_count=0,
                    views=5,
                    url="https://forum.example.org/t/1",
                    created_at="2024-01-01T00:00:00Z",
                    last_posted_at=None,
                )
                conn.commit()

            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                tools = create_knowledge_tools(
                    "test",
                    "Test Community",
                    repos=["test-org/test-repo"],
                    include_discussions=True,
                    include_recent=True,
                    include_papers=True,
                    include_docstrings=True,
                    include_faq=True,
                    include_discourse=True,
                    citations=True,
                )

                # Invoke every tool now, while the get_db_path patch is
                # still active, and return (tool, result) pairs.
                invoke_args: dict[str, dict[str, Any]] = {
                    "search_test_discussions": {"query": self.KEYWORD},
                    "list_test_recent": {},
                    "search_test_papers": {"query": self.KEYWORD},
                    "search_test_code_docs": {"query": self.KEYWORD},
                    "get_test_full_docstring": {"symbol_name": "example_function"},
                    "search_test_faq": {"query": self.KEYWORD},
                    "search_test_forum": {"query": self.KEYWORD},
                }
                missing = [t.name for t in tools if t.name not in invoke_args]
                assert not missing, (
                    f"No seed/invocation wired for new tool(s) {missing} in "
                    "TestCitabilityMatchesDescription.seeded_tools -- add one so "
                    "this test can prove its citability, rather than skipping it."
                )
                return [(t, t.invoke(invoke_args[t.name])) for t in tools]

    def test_discovery_only_tools_always_return_a_string(self, seeded_tools) -> None:
        discovery_tools = [(t, r) for t, r in seeded_tools if _is_discovery_only(t.description)]
        assert discovery_tools, "Expected at least one discovery-only tool in this fixture"

        for tool, result in discovery_tools:
            assert isinstance(result, str), (
                f"{tool.name} describes itself as discovery-only but returned "
                f"{type(result).__name__} with citations=True; a discovery tool "
                "must never become citable."
            )

    def test_non_discovery_tools_return_citable_blocks(self, seeded_tools) -> None:
        answerable_tools = [
            (t, r) for t, r in seeded_tools if not _is_discovery_only(t.description)
        ]
        assert answerable_tools, "Expected at least one non-discovery tool in this fixture"

        for tool, result in answerable_tools:
            assert isinstance(result, list), (
                f"{tool.name}'s description permits answering from its content, "
                f"so citations=True should return search_result blocks, got "
                f"{type(result).__name__}"
            )
            assert result, f"{tool.name} returned an empty block list despite seeded data"
            for block in result:
                assert block["type"] == "search_result"
                assert block["citations"] == {"enabled": True}
                assert block["content"][0]["text"]

    def test_known_non_citable_tool_names_match_the_rule(self, seeded_tools) -> None:
        """Names, purely as documentation of today's set -- the rule above is authoritative."""
        non_citable_names = {t.name for t, _ in seeded_tools if _is_discovery_only(t.description)}
        assert non_citable_names == {
            "search_test_discussions",
            "list_test_recent",
            "search_test_papers",
            "search_test_forum",
        }

    def test_known_citable_tool_names_match_the_rule(self, seeded_tools) -> None:
        citable_names = {t.name for t, _ in seeded_tools if not _is_discovery_only(t.description)}
        assert citable_names == {
            "search_test_code_docs",
            "get_test_full_docstring",
            "search_test_faq",
        }


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


def _seed_docstring(community_id: str) -> dict[str, Any]:
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
        name="search_faq",
        factory=create_search_faq_tool,
        seed=_seed_faq,
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
    """Block content (not just type) for each currently-citable tool, with real seeded data."""

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


class TestDiscoveryToolsHaveNoCitationCapability:
    """Discussion, recent-activity, papers, live-papers, and forum tools take no `citations` kwarg.

    Not just "defaults to returning a string" -- there must be no switch to
    flip at all, so a future call site cannot silently start requesting
    citations from a tool whose description forbids answering from it.
    """

    def test_none_of_the_discovery_factories_accept_citations(self) -> None:
        import inspect

        from src.tools.knowledge import (
            create_list_recent_tool,
            create_search_discourse_tool,
            create_search_discussions_tool,
            create_search_papers_live_tool,
            create_search_papers_tool,
        )

        discovery_factories = [
            create_search_discussions_tool,
            create_list_recent_tool,
            create_search_papers_tool,
            create_search_papers_live_tool,
            create_search_discourse_tool,
        ]
        offenders = [
            f.__name__
            for f in discovery_factories
            if "citations" in inspect.signature(f).parameters
        ]
        assert not offenders, f"Discovery-only factories must not accept citations: {offenders}"


class TestCitablePayloadsAreBounded:
    """A citable tool must not put far more on the wire than its string path.

    The other two citable tools pass search snippets that
    src/knowledge/search.py has already truncated, but a FAQ answer comes
    straight from the database, capped only at ingest (5000 chars,
    src/knowledge/db.py). Uncapped, one search_faq call at the default
    limit=5 would carry 25k chars on the Anthropic path against 2.5k on the
    OpenRouter path for the identical query: a size and cost difference
    visible only to whichever provider happens to be paying.
    """

    LONG_ANSWER_SENTENCE = "Reject the ICA component correlated with the EOG channel. "

    def _seed_long_faq(self, community_id: str, entries: int) -> None:
        """Seed `entries` FAQ rows whose answers exceed the ingest cap."""
        long_answer = self.LONG_ANSWER_SENTENCE * 200  # ~11k chars, capped to 5000 at ingest
        with get_connection(community_id) as conn:
            for n in range(entries):
                upsert_faq_entry(
                    conn,
                    list_name="eeglab-list",
                    thread_id=f"thread-{n}",
                    thread_url=f"https://mailman.example.org/thread/{n}",
                    question=f"How do I remove artifacts from EEG data, case {n}?",
                    answer=long_answer,
                    tags=["artifacts", "ica"],
                    category="how-to",
                    message_count=4,
                    participant_count=2,
                    first_message_date="2024-01-01",
                    quality_score=0.9,
                    summary_model="test-model",
                )
            conn.commit()

    def test_faq_citation_blocks_cap_each_answer(self, tmp_path: Path) -> None:
        """One block's text stays within the citable cap, not the 5000-char row."""
        from src.tools.citations import DEFAULT_TRUNCATION_SUFFIX
        from src.tools.knowledge import _MAX_CITABLE_FAQ_ANSWER_CHARS

        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            self._seed_long_faq("test", entries=1)
            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                tool = create_search_faq_tool("test", "Test Community", citations=True)
                result = tool.invoke({"query": "remove artifacts"})

        assert isinstance(result, list)
        text = result[0]["content"][0]["text"]
        assert len(text) == _MAX_CITABLE_FAQ_ANSWER_CHARS + len(DEFAULT_TRUNCATION_SUFFIX)
        assert text.endswith(DEFAULT_TRUNCATION_SUFFIX)

    def test_faq_citation_payload_stays_near_the_string_payload(self, tmp_path: Path) -> None:
        """Whole-call size, at the default limit, on both paths.

        Asserted as a ratio rather than an absolute size: the citable path is
        allowed to carry more context, since a citation points at an exact
        span and a span cut mid-sentence is worse than no citation, but not
        an order of magnitude more.
        """
        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            self._seed_long_faq("test", entries=5)
            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                citable = create_search_faq_tool("test", "Test Community", citations=True)
                blocks = citable.invoke({"query": "remove artifacts"})
                plain = create_search_faq_tool("test", "Test Community", citations=False)
                string_result = plain.invoke({"query": "remove artifacts"})

        assert isinstance(blocks, list) and len(blocks) == 5
        assert isinstance(string_result, str)
        citable_chars = sum(len(b["content"][0]["text"]) for b in blocks)
        assert citable_chars < 5 * len(string_result), (
            f"citable path carried {citable_chars} chars against "
            f"{len(string_result)} on the string path"
        )

    def test_short_faq_answers_are_not_truncated(self, tmp_path: Path) -> None:
        """The cap must not clip an ordinary answer; most FAQ answers are short."""
        db_path = tmp_path / "knowledge" / "test.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db("test")
            _seed_faq("test")
            with patch("src.tools.knowledge.get_db_path", return_value=db_path):
                tool = create_search_faq_tool("test", "Test Community", citations=True)
                result = tool.invoke({"query": "remove artifacts"})

        assert isinstance(result, list)
        assert result[0]["content"][0]["text"] == (
            "Use ICA decomposition and reject components correlated with EOG channels."
        )
