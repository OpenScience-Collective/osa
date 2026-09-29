"""Tests for tagged-source citations (models with no native search_result support)."""

import random

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.content import (
    CitationAssembler,
    classify_content_blocks_with_indices,
    encode_citation_markers,
    normalize_citation_markers,
)
from src.core.services.tagged_citations import (
    CITATION_INSTRUCTION,
    MARKER_PATTERN,
    CitePiece,
    MarkerStream,
    SourceRegistry,
    TextPiece,
    best_passage,
    pieces_to_blocks,
    prepare_messages,
    rewrite_content,
)
from src.tools.citations import build_search_result

SCHEMA_TEXT = (
    "HED tags are assembled from a schema. The schema is versioned and published "
    "on GitHub. Validate annotations before you share a dataset."
)
SENSORY_TEXT = "The Sensory-event tag marks a sensory stimulus presented to the participant."


def _registry() -> SourceRegistry:
    registry = SourceRegistry()
    registry.add("https://hedtags.org/schema", "The HED schema", SCHEMA_TEXT)
    registry.add("https://hedtags.org/sensory", "Sensory-event", SENSORY_TEXT)
    return registry


def _tool_message(*results: tuple[str, str, str], call_id: str = "call-1") -> ToolMessage:
    return ToolMessage(
        content=[build_search_result(source, title, text) for source, title, text in results],
        tool_call_id=call_id,
    )


def _run(text: str, registry: SourceRegistry, chunk_sizes: list[int] | None = None):
    """Feed ``text`` through a MarkerStream in pieces; return every emitted piece."""
    stream = MarkerStream(registry)
    pieces: list = []
    if chunk_sizes is None:
        pieces += stream.feed(text)
    else:
        position = 0
        for size in chunk_sizes:
            pieces += stream.feed(text[position : position + size])
            position += size
        pieces += stream.feed(text[position:])
    return pieces + stream.finish()


def _merged(pieces) -> list:
    """Join adjacent text pieces of one segment: chunking may split text, never change it."""
    merged: list = []
    for piece in pieces:
        previous = merged[-1] if merged else None
        if (
            isinstance(piece, TextPiece)
            and isinstance(previous, TextPiece)
            and previous.segment == piece.segment
        ):
            merged[-1] = TextPiece(piece.segment, previous.text + piece.text)
        else:
            merged.append(piece)
    return merged


def _visible(pieces) -> str:
    return "".join(p.text for p in pieces if isinstance(p, TextPiece))


class TestSourceRegistry:
    def test_tags_follow_first_appearance(self) -> None:
        registry = _registry()
        assert registry.by_source("https://hedtags.org/schema").tag == 1
        assert registry.by_source("https://hedtags.org/sensory").tag == 2

    def test_one_source_keeps_one_tag(self) -> None:
        """Two results from one page are one citation, as on the Anthropic path."""
        registry = _registry()
        again = registry.add("https://hedtags.org/schema", "The HED schema", "More text.")
        assert again.tag == 1
        assert len(registry) == 2
        assert "More text." in again.texts

    def test_unknown_tag_has_no_source(self) -> None:
        assert _registry().by_tag(7) is None

    def test_citation_has_the_shape_the_api_layer_reads(self) -> None:
        citation = _registry().citation(1, "HED tags are assembled from a schema.")
        assert citation["source"] == "https://hedtags.org/schema"
        assert citation["title"] == "The HED schema"
        assert citation["type"] == "search_result_location"
        assert citation["cited_text"] == "HED tags are assembled from a schema."


class TestBestPassage:
    def test_picks_the_sentence_that_shares_the_most_words(self) -> None:
        claim = "Annotations should be validated before a dataset is shared."
        assert (
            best_passage(claim, [SCHEMA_TEXT]) == "Validate annotations before you share a dataset."
        )

    def test_no_overlap_opens_the_source(self) -> None:
        assert best_passage("Unrelated remark.", [SCHEMA_TEXT]).startswith("HED tags are assembled")

    def test_result_is_capped(self) -> None:
        assert len(best_passage("word", ["word " * 500], limit=50)) <= 53

    def test_empty_sources_give_empty_text(self) -> None:
        assert best_passage("anything", []) == ""


class TestPrepareMessages:
    def test_a_search_result_becomes_tagged_text(self) -> None:
        tool = _tool_message(("https://hedtags.org/schema", "The HED schema", SCHEMA_TEXT))
        prepared, registry = prepare_messages([HumanMessage(content="q"), tool])

        block = prepared[1].content[0]
        assert block["type"] == "text"
        assert block["text"].startswith(
            "[src:1] The HED schema\nSource: https://hedtags.org/schema\n"
        )
        assert SCHEMA_TEXT in block["text"]
        assert registry.by_tag(1).source == "https://hedtags.org/schema"

    def test_the_callers_messages_are_not_mutated(self) -> None:
        tool = _tool_message(("https://hedtags.org/schema", "The HED schema", SCHEMA_TEXT))
        prepare_messages([tool])
        assert tool.content[0]["type"] == "search_result"

    def test_messages_with_nothing_to_tag_pass_through_unchanged(self) -> None:
        human = HumanMessage(content="q")
        plain_tool = ToolMessage(content="plain text result", tool_call_id="c")
        prepared, registry = prepare_messages([human, plain_tool])
        assert prepared == [human, plain_tool]
        assert len(registry) == 0

    def test_tags_stay_put_as_the_conversation_grows(self) -> None:
        """A tag that changed between calls would break prompt caching's byte-exact prefix."""
        first = _tool_message(("https://a.example", "A", "Alpha text."), call_id="c1")
        second = _tool_message(
            ("https://b.example", "B", "Beta text."),
            ("https://a.example", "A", "Alpha text."),
            call_id="c2",
        )
        early, _ = prepare_messages([first])
        late, registry = prepare_messages([first, AIMessage(content="ok"), second])

        assert early[0].content == late[0].content
        assert registry.by_source("https://a.example").tag == 1
        assert registry.by_source("https://b.example").tag == 2
        assert late[2].content[1]["text"].startswith("[src:1] A")

    def test_an_earlier_reply_is_shown_with_its_tags(self) -> None:
        """The model should keep seeing the format it was asked to write."""
        tool = _tool_message(("https://hedtags.org/schema", "The HED schema", SCHEMA_TEXT))
        cited = AIMessage(
            content=[
                {
                    "type": "text",
                    "text": "HED tags come from a schema.",
                    "citations": [_registry().citation(1, "schema")],
                },
                {"type": "text", "text": " Uncited aside."},
            ]
        )
        prepared, _ = prepare_messages([tool, cited])

        assert prepared[1].content[0] == {
            "type": "text",
            "text": "HED tags come from a schema.[src:1]",
        }
        assert prepared[1].content[1] == {"type": "text", "text": " Uncited aside."}

    def test_a_citation_of_an_unknown_source_adds_no_tag(self) -> None:
        cited = AIMessage(
            content=[
                {
                    "type": "text",
                    "text": "Claim.",
                    "citations": [{"source": "https://gone.example", "title": "Gone"}],
                }
            ]
        )
        prepared, _ = prepare_messages([cited])
        assert prepared[0].content[0]["text"] == "Claim."


class TestMarkerPattern:
    @pytest.mark.parametrize(
        "written",
        ["[src:3]", "[SRC:3]", "[src: 3]", "[src:1,2]", "[src:1, 2]", "[src:1, src:2]", " [src:3]"],
    )
    def test_accepts_the_variants_models_write(self, written: str) -> None:
        assert MARKER_PATTERN.fullmatch(written)

    @pytest.mark.parametrize("written", ["[3]", "[source:3]", "[src:]", "[src:a]", "[link](x)"])
    def test_rejects_things_that_are_not_tags(self, written: str) -> None:
        assert not MARKER_PATTERN.fullmatch(written)

    def test_the_instruction_shows_the_syntax_the_pattern_reads(self) -> None:
        assert MARKER_PATTERN.search("assembled from a schema.[src:3]")
        assert "[src:3]" in CITATION_INSTRUCTION
        assert "[src:1][src:2]" in CITATION_INSTRUCTION


class TestMarkerStream:
    def test_a_tag_becomes_a_citation_on_the_text_before_it(self) -> None:
        pieces = _run("HED tags come from a schema.[src:1] Next.", _registry())
        assert [type(p).__name__ for p in pieces] == ["TextPiece", "CitePiece", "TextPiece"]
        assert pieces[0] == TextPiece(0, "HED tags come from a schema.")
        assert pieces[1] == CitePiece(0, [1], "HED tags come from a schema.")
        assert pieces[2] == TextPiece(1, " Next.")

    def test_the_space_before_a_tag_goes_with_it(self) -> None:
        assert _visible(_run("A claim. [src:1]", _registry())) == "A claim."
        assert _visible(_run("A claim. [src:1]", _registry())) == "A claim."

    def test_consecutive_tags_share_one_citation_group(self) -> None:
        pieces = _run("A claim.[src:1][src:2] More.", _registry())
        cites = [p for p in pieces if isinstance(p, CitePiece)]
        assert [c.tags for c in cites] == [[1], [2]]
        assert {c.segment for c in cites} == {0}

    def test_tags_with_a_space_between_them_share_a_group_too(self) -> None:
        pieces = _run("A claim. [src:1] [src:2] More.", _registry())
        assert {p.segment for p in pieces if isinstance(p, CitePiece)} == {0}
        assert _visible(pieces) == "A claim. More."

    def test_a_tag_naming_no_source_is_dropped_not_shown(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            pieces = _run("Invented.[src:9] Real.[src:1]", _registry())
        assert [p.tags for p in pieces if isinstance(p, CitePiece)] == [[1]]
        assert "[src:9]" not in _visible(pieces)
        assert "no such source" in caplog.text

    def test_a_tag_with_no_text_before_it_is_dropped(self) -> None:
        pieces = _run("[src:1] Starts with a tag.", _registry())
        assert not any(isinstance(p, CitePiece) for p in pieces)
        assert _visible(pieces).strip() == "Starts with a tag."

    def test_brackets_that_are_not_tags_are_kept(self) -> None:
        text = "See [1] and [the docs](https://x.example) for an [src] note."
        assert _visible(_run(text, _registry())) == text

    def test_an_open_bracket_at_the_end_is_released_at_finish(self) -> None:
        assert _visible(_run("Ends with [src:", _registry())) == "Ends with [src:"

    def test_nothing_is_held_back_that_cannot_be_a_tag(self) -> None:
        stream = MarkerStream(_registry())
        released = stream.feed("A plain sentence with no brackets")
        assert _visible(released) == "A plain sentence with no brackets"

    def test_a_partial_tag_is_held_until_it_resolves(self) -> None:
        stream = MarkerStream(_registry())
        assert _visible(stream.feed("Claim.[sr")) == "Claim."
        released = stream.feed("c:1] After")
        assert [type(p).__name__ for p in released] == ["CitePiece", "TextPiece"]

    @pytest.mark.parametrize(
        "text",
        [
            "HED tags come from a schema.[src:1] The Sensory-event tag marks a stimulus. [src:2] End.",
            "One.[src:1][src:2] Two. [src:1, 2] Three [1] and [link](u).",
            "Invented.[src:9] Real. [src:2]",
            "Trailing tag.[src:1]",
        ],
    )
    def test_chunking_never_changes_the_result(self, text: str) -> None:
        """Streaming delivers text in arbitrary pieces; the parse must not depend on them."""
        registry = _registry()
        whole = _merged(_run(text, registry))
        rng = random.Random(7)
        for _ in range(200):
            sizes = [rng.randint(1, 6) for _ in range(rng.randint(1, len(text)))]
            assert _merged(_run(text, registry, sizes)) == whole
        assert _merged(_run(text, registry, [1] * len(text))) == whole


class TestReachesTheApiLayerUnchanged:
    """The point of the tagged path: downstream code cannot tell it from native citations."""

    ANSWER = (
        "HED tags are assembled from a schema.[src:1] "
        "The Sensory-event tag marks a stimulus.[src:2] "
        "The schema is versioned.[src:1]"
    )

    def _assemble(self, blocks: list[dict]) -> tuple[str, list]:
        assembler = CitationAssembler()
        parts: list[str] = []
        for block, index in classify_content_blocks_with_indices(blocks):
            if block.kind != "text":
                continue
            parts.append(block.text)
            marker_text, _ = assembler.add_block(block, index)
            if marker_text:
                parts.append(encode_citation_markers(marker_text))
        assembler.finish_model_run()
        return normalize_citation_markers("".join(parts), assembler.marks), assembler.marks

    def test_streamed_blocks_give_numbered_markers_and_a_source_list(self) -> None:
        registry = _registry()
        blocks = pieces_to_blocks(_run(self.ANSWER, registry), registry, index_base=1)
        answer, marks = self._assemble(blocks)

        assert answer == (
            "HED tags are assembled from a schema.[1] "
            "The Sensory-event tag marks a stimulus.[2] "
            "The schema is versioned.[1]"
        )
        assert [(m.marker, m.source) for m in marks] == [
            (1, "https://hedtags.org/schema"),
            (2, "https://hedtags.org/sensory"),
        ]

    def test_a_complete_reply_gives_the_same_answer_and_sources(self) -> None:
        registry = _registry()
        streamed = self._assemble(pieces_to_blocks(_run(self.ANSWER, registry), registry, 1))
        whole = self._assemble(rewrite_content(self.ANSWER, registry))
        assert whole == streamed

    def test_reasoning_and_tool_blocks_pass_through_a_complete_reply(self) -> None:
        registry = _registry()
        reasoning = {"type": "reasoning_content", "reasoning_content": {"text": "hmm"}}
        tool_use = {"type": "tool_use", "id": "t1", "name": "search", "input": {}}
        rewritten = rewrite_content(
            [reasoning, {"type": "text", "text": "Yes.[src:1]"}, tool_use], registry
        )

        assert rewritten[0] == reasoning
        assert rewritten[1]["text"] == "Yes."
        assert rewritten[1]["citations"][0]["source"] == "https://hedtags.org/schema"
        assert rewritten[2] == tool_use

    def test_a_reply_without_tags_is_returned_untouched(self) -> None:
        registry = _registry()
        assert rewrite_content("No sources were used.", registry) == "No sources were used."
        blocks = [{"type": "text", "text": "Also plain."}]
        assert rewrite_content(blocks, registry) == blocks

    def test_each_cited_claim_gets_its_own_block_and_index(self) -> None:
        registry = _registry()
        blocks = pieces_to_blocks(_run(self.ANSWER, registry), registry, index_base=2)
        indexes = {b["index"] for b in blocks}
        assert len(indexes) == 3
        assert all(2000 <= i < 3000 for i in indexes)
