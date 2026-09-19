"""Tests for src.agents.content: extracting text and citations from block-list content.

No mocks: these exercise the pure functions directly against real content
shapes (plain strings, the block-list shape langchain-anthropic produces
when thinking is enabled or tools are bound, and -- for the citation
extraction tests -- a recorded real response payload; see
TestExtractCitationsAgainstRecordedPayload for how it was captured. Phase 2
shipped a bug that every test hand-built a shape the API never actually
produces, so the citation-carrying shape is asserted against that real
payload rather than a hand-built dict).
"""

import json
import logging
from pathlib import Path

from src.agents.content import (
    CitationAssembler,
    CitationMark,
    CitationTracker,
    ContentBlock,
    classify_content_blocks,
    encode_citation_markers,
    extract_citations,
    extract_text,
    normalize_citation_markers,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _load_citation_response() -> list[dict]:
    """Load the recorded real Claude Platform response (see its module note)."""
    return json.loads((FIXTURES_DIR / "citation_response.json").read_text())


class TestExtractText:
    """Tests for extract_text()."""

    def test_plain_string_passthrough(self):
        """A plain string (no thinking, no bound tools) is returned unchanged."""
        assert extract_text("Hello, world!") == "Hello, world!"

    def test_empty_string(self):
        assert extract_text("") == ""

    def test_single_text_block(self):
        content = [{"type": "text", "text": "Hello, world!"}]
        assert extract_text(content) == "Hello, world!"

    def test_thinking_and_text_blocks_yields_only_text(self):
        """A list of thinking and text blocks yields only the text (no reasoning)."""
        content = [
            {"type": "thinking", "thinking": "Let me think about this...", "signature": "abc"},
            {"type": "text", "text": "The answer is 42."},
        ]
        result = extract_text(content)
        assert result == "The answer is 42."
        assert "Let me think" not in result

    def test_multiple_text_blocks_concatenated(self):
        content = [
            {"type": "text", "text": "Hello, "},
            {"type": "text", "text": "world!"},
        ]
        assert extract_text(content) == "Hello, world!"

    def test_tool_use_block_contributes_nothing(self):
        content = [
            {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"q": "HED"}},
            {"type": "text", "text": "Searching for HED."},
        ]
        assert extract_text(content) == "Searching for HED."

    def test_redacted_thinking_block_contributes_nothing(self):
        content = [
            {"type": "redacted_thinking", "data": "encrypted-blob"},
            {"type": "text", "text": "Final answer."},
        ]
        assert extract_text(content) == "Final answer."

    def test_only_thinking_blocks_yields_empty_string(self):
        content = [{"type": "thinking", "thinking": "reasoning...", "signature": "abc"}]
        assert extract_text(content) == ""

    def test_empty_list(self):
        assert extract_text([]) == ""

    def test_never_leaks_stringified_list(self):
        """Regression: the pre-Phase-2 bug stringified the whole block list."""
        content = [
            {"type": "thinking", "thinking": "secret reasoning", "signature": "abc"},
            {"type": "text", "text": "public answer"},
        ]
        result = extract_text(content)
        assert "'type'" not in result
        assert "secret reasoning" not in result
        assert result == "public answer"

    def test_ignores_citations_on_text_blocks(self):
        """A cited text block still contributes only its text, unchanged."""
        content = [
            {
                "type": "text",
                "text": "Tags go in events.tsv.",
                "citations": [{"source": "https://example.com/doc", "title": "Doc"}],
            }
        ]
        assert extract_text(content) == "Tags go in events.tsv."


class TestClassifyContentBlocks:
    """Tests for classify_content_blocks()."""

    def test_plain_string(self):
        assert classify_content_blocks("hello") == [("text", "hello", [])]

    def test_empty_string_yields_no_pairs(self):
        assert classify_content_blocks("") == []

    def test_text_block(self):
        content = [{"type": "text", "text": "hello"}]
        assert classify_content_blocks(content) == [("text", "hello", [])]

    def test_thinking_block_carries_no_text(self):
        """A thinking chunk classifies as thinking with an empty payload -- never reasoning text."""
        content = [{"type": "thinking", "thinking": "reasoning content", "signature": "abc"}]
        blocks = classify_content_blocks(content)
        assert blocks == [("thinking", "", [])]
        # The reasoning text itself must never appear anywhere in the result.
        assert not any("reasoning content" in b.text for b in blocks)

    def test_redacted_thinking_block_classified_as_thinking(self):
        content = [{"type": "redacted_thinking", "data": "encrypted"}]
        assert classify_content_blocks(content) == [("thinking", "", [])]

    def test_mixed_blocks_preserve_order(self):
        content = [
            {"type": "thinking", "thinking": "step 1", "signature": "abc"},
            {"type": "text", "text": "part one. "},
            {"type": "thinking", "thinking": "step 2", "signature": "def"},
            {"type": "text", "text": "part two."},
        ]
        assert classify_content_blocks(content) == [
            ("thinking", "", []),
            ("text", "part one. ", []),
            ("thinking", "", []),
            ("text", "part two.", []),
        ]

    def test_tool_use_block_is_skipped_entirely(self):
        """tool_use blocks are handled by dedicated tool-call events, not here."""
        content = [
            {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {}},
            {"type": "text", "text": "ok"},
        ]
        assert classify_content_blocks(content) == [("text", "ok", [])]

    def test_empty_text_block_skipped(self):
        content = [{"type": "text", "text": ""}, {"type": "text", "text": "ok"}]
        assert classify_content_blocks(content) == [("text", "ok", [])]

    def test_non_dict_block_skipped(self):
        """Defensive: a malformed non-dict block entry is ignored, not an error."""
        content = ["not a dict", {"type": "text", "text": "ok"}]
        assert classify_content_blocks(content) == [("text", "ok", [])]

    def test_empty_list_yields_no_pairs(self):
        assert classify_content_blocks([]) == []

    def test_text_block_missing_text_key(self, caplog):
        """A {"type": "text"} block with no "text" key at all (e.g. a citations
        delta) must not raise and must not warn -- it is a recognized type,
        just with nothing to contribute yet.
        """
        content = [{"type": "text"}, {"type": "text", "text": "ok"}]
        assert classify_content_blocks(content) == [("text", "ok", [])]
        assert "unrecognized content block type" not in caplog.text

    def test_unrecognized_block_type_warns(self, caplog):
        """An unrecognized block type logs a warning naming the type.

        Regression for item 6: a future langchain-anthropic block type that
        carries answer text would otherwise truncate answers with no error.
        """
        content = [{"type": "some_future_block", "data": "x"}, {"type": "text", "text": "ok"}]
        with caplog.at_level("WARNING"):
            result = classify_content_blocks(content)

        assert result == [("text", "ok", [])]
        assert "some_future_block" in caplog.text
        assert "unrecognized content block type" in caplog.text

    def test_unrecognized_block_type_warns_once_per_call(self, caplog):
        """Repeats of the same unrecognized type in one call warn only once."""
        content = [
            {"type": "weird_block"},
            {"type": "weird_block"},
            {"type": "weird_block"},
            {"type": "text", "text": "ok"},
        ]
        with caplog.at_level("WARNING"):
            classify_content_blocks(content)

        assert caplog.text.count("weird_block") == 1

    def test_tool_use_block_never_warns(self, caplog):
        """tool_use is a known, intentionally-skipped type -- no warning."""
        content = [{"type": "tool_use", "id": "t1", "name": "search", "input": {}}]
        with caplog.at_level("WARNING"):
            classify_content_blocks(content)

        assert caplog.text == ""

    def test_text_block_with_citations_but_no_text_is_surfaced(self):
        """A citations-only block (no "text" key) is not dropped as 'empty'.

        This is exactly the shape of a streamed citation-delta chunk (see
        the module docstring): the citations arrive on their own, in a
        chunk that carries no text of its own.
        """
        content = [{"type": "text", "citations": [{"source": "https://example.com"}]}]
        blocks = classify_content_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].kind == "text"
        assert blocks[0].text == ""
        assert blocks[0].citations == [{"source": "https://example.com"}]

    def test_text_block_without_citations_key_has_empty_citations_list(self):
        content = [{"type": "text", "text": "no citations here"}]
        block = classify_content_blocks(content)[0]
        assert block.citations == []


class TestExtractCitationsAgainstRecordedPayload:
    """extract_citations() against a real Claude Platform response payload.

    tests/fixtures/citation_response.json is the raw `.content` of a real
    AIMessage, captured by binding a tool that returns a
    build_search_result() block (see src/tools/citations.py) and asking a
    live claude-haiku-4-5 call (through this repo's own
    create_anthropic_llm) to answer using it. Not hand-built: Phase 2's
    cache-token bug shipped precisely because every test hand-built a shape
    the API never produces, and citations are exactly the kind of nested,
    easy-to-guess-wrong shape that class of bug comes from.
    """

    def test_extracts_the_recorded_citation(self):
        content = _load_citation_response()
        citations = extract_citations(content)

        assert len(citations) == 1
        citation = citations[0]
        assert citation["type"] == "search_result_location"
        assert citation["source"] == (
            "https://www.hedtags.org/hed-resources/HedAnnotationQuickstart.html"
        )
        assert citation["title"] == "HED Annotation Quickstart"
        assert "HED" in citation["cited_text"]
        assert citation["search_result_index"] == 0
        assert isinstance(citation["start_block_index"], int)
        assert isinstance(citation["end_block_index"], int)

    def test_classify_content_blocks_surfaces_the_same_citation(self):
        """classify_content_blocks() carries the citation on its text block."""
        content = _load_citation_response()
        blocks = classify_content_blocks(content)

        assert len(blocks) == 1
        assert blocks[0].kind == "text"
        assert len(blocks[0].text) > 0
        assert len(blocks[0].citations) == 1
        assert blocks[0].citations[0]["source"] == (
            "https://www.hedtags.org/hed-resources/HedAnnotationQuickstart.html"
        )

    def test_extract_text_still_returns_only_the_answer(self):
        """The citation metadata never leaks into the plain answer text."""
        content = _load_citation_response()
        text = extract_text(content)

        assert "search_result_location" not in text
        assert "annotate an event" in text.lower() or "hed tags" in text.lower()

    def test_no_citations_case_returns_empty_list(self):
        content = [{"type": "text", "text": "An answer with nothing cited."}]
        assert extract_citations(content) == []

    def test_plain_string_returns_empty_list(self):
        assert extract_citations("plain string answer") == []


class TestCitationTracker:
    """Marker numbering: one marker per unique source, in first-appearance order.

    These use hand-built citation dicts on purpose: unlike the extraction
    tests above (which must prove the real API shape parses correctly),
    this is testing the tracker's own numbering algorithm, a pure function
    of whatever "source" strings it is given.
    """

    def test_first_citation_gets_marker_one(self):
        tracker = CitationTracker()
        marker_text, new_marks = tracker.record_block(
            [{"source": "https://a.example", "title": "A", "cited_text": "a text"}]
        )
        assert marker_text == "[1]"
        assert len(new_marks) == 1
        assert new_marks[0] == CitationMark(1, "https://a.example", "A", "a text")

    def test_repeated_source_shares_marker(self):
        tracker = CitationTracker()
        tracker.record_block([{"source": "https://a.example", "title": "A", "cited_text": "x"}])
        marker_text, new_marks = tracker.record_block(
            [{"source": "https://a.example", "title": "A", "cited_text": "y"}]
        )
        assert marker_text == "[1]"
        assert new_marks == []  # not newly discovered the second time

    def test_ordering_follows_first_appearance(self):
        tracker = CitationTracker()
        tracker.record_block([{"source": "https://b.example", "title": "B"}])
        tracker.record_block([{"source": "https://a.example", "title": "A"}])
        tracker.record_block([{"source": "https://b.example", "title": "B"}])

        markers = [m.marker for m in tracker.marks]
        sources = [m.source for m in tracker.marks]
        assert markers == [1, 2]
        assert sources == ["https://b.example", "https://a.example"]

    def test_multiple_distinct_sources_in_one_block(self):
        tracker = CitationTracker()
        marker_text, new_marks = tracker.record_block(
            [
                {"source": "https://a.example", "title": "A"},
                {"source": "https://b.example", "title": "B"},
            ]
        )
        assert marker_text == "[1][2]"
        assert [m.marker for m in new_marks] == [1, 2]

    def test_same_source_twice_in_one_block_renders_marker_once(self):
        tracker = CitationTracker()
        marker_text, _ = tracker.record_block(
            [
                {"source": "https://a.example", "title": "A"},
                {"source": "https://a.example", "title": "A"},
            ]
        )
        assert marker_text == "[1]"

    def test_no_citations_yields_no_markers_and_empty_marks_list(self):
        tracker = CitationTracker()
        marker_text, new_marks = tracker.record_block([])
        assert marker_text == ""
        assert new_marks == []
        assert tracker.marks == []

    def test_citation_missing_source_is_ignored(self):
        """A malformed citation with no source cannot be attributed; skip it."""
        tracker = CitationTracker()
        marker_text, new_marks = tracker.record_block([{"title": "No source"}])
        assert marker_text == ""
        assert new_marks == []

    def test_citation_missing_source_is_logged(self, caplog):
        """Dropping it silently would look like an answer that cited less.

        The model did attach a citation here, so a tool emitting blank
        sources shows up as answers with fewer markers than sources, with
        nothing anywhere saying why. The warning is the only trace.
        """
        tracker = CitationTracker()
        with caplog.at_level(logging.WARNING, logger="src.agents.content"):
            tracker.record_block([{"title": "No source", "cited_text": "some span"}])

        assert "no source" in caplog.text

    def test_marks_property_returns_a_copy(self):
        tracker = CitationTracker()
        tracker.record_block([{"source": "https://a.example", "title": "A"}])
        marks = tracker.marks
        marks.append(CitationMark(99, "fake", "fake", "fake"))
        assert len(tracker.marks) == 1


class TestNormalizeCitationMarkers:
    """Generated citation markers are moved to sentence boundaries."""

    def test_moves_marker_out_of_the_middle_of_a_word(self):
        marks = [CitationMark(1, "https://a.example", "A", "runica.m")]

        result = normalize_citation_markers(
            "Infomax is implemented in ru"
            + encode_citation_markers("[1]")
            + "nica.m, a MATLAB version.",
            marks,
        )

        assert result == "Infomax is implemented in runica.m, a MATLAB version.[1]"

    def test_moves_leading_marker_after_the_sentence(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            encode_citation_markers("[1]") + "The cited claim.", marks
        )

        assert result == "The cited claim.[1]"

    def test_keeps_marker_after_the_sentence_it_already_follows(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "First claim. " + encode_citation_markers("[1]") + " Next claim.", marks
        )

        assert result == "First claim.[1] Next claim."

    def test_keeps_marker_after_the_last_of_multiple_completed_sentences(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "First. Second." + encode_citation_markers("[1]") + " Next.", marks
        )

        assert result == "First. Second.[1] Next."

    def test_leaves_unknown_bracketed_numbers_untouched(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "arr[1] contains a cited sentence." + encode_citation_markers("[1]"),
            marks,
        )

        assert result == "arr[1] contains a cited sentence.[1]"

    def test_removes_space_when_marker_arrives_before_sentence_punctuation(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "The cited claim " + encode_citation_markers("[1]") + ".", marks
        )

        assert result == "The cited claim.[1]"

    def test_does_not_rewrite_markdown_links_or_array_indices(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "See [1](https://example.com) and arr[1]. "
            "The cited claim." + encode_citation_markers("[1]"),
            marks,
        )

        assert result == ("See [1](https://example.com) and arr[1]. The cited claim.[1]")

    def test_does_not_move_marker_across_a_markdown_list_item(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "- First claim " + encode_citation_markers("[1]") + "\n- Second claim.",
            marks,
        )

        assert result == "- First claim[1]\n- Second claim."

    def test_places_marker_after_markdown_sentence_closers(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        for text, expected in [
            (
                "**Claim" + encode_citation_markers("[1]") + ".** Next.",
                "**Claim.**[1] Next.",
            ),
            (
                "`Claim" + encode_citation_markers("[1]") + ".` Next.",
                "`Claim.`[1] Next.",
            ),
            (
                "[Claim" + encode_citation_markers("[1]") + ".](https://example.com) Next.",
                "[Claim.](https://example.com)[1] Next.",
            ),
        ]:
            assert normalize_citation_markers(text, marks) == expected


class TestNormalizeCitationMarkersAbbreviations:
    """A marker landed right after an abbreviation (Dr., etc., et al., ...)
    is left there rather than relocated by a guess, except "vs." (see the
    module note above ``_ALWAYS_SKIPPED_ABBREVIATION_WORDS`` in
    src/agents/content.py). Two earlier heuristics each fixed one direction
    of misplacement while introducing the opposite one -- relocating a
    marker into a different, unrelated sentence -- so every case here
    covers both directions per abbreviation family, using domain-flavored
    HED/BIDS/EEGLAB-style prose where that risk concentrates (lowercase
    filenames/identifiers, capitalized tool names, CLI flags).
    """

    def test_title_before_a_name_does_not_relocate_into_the_next_sentence(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "Dr." + encode_citation_markers("[1]") + " Smith conducted the study.",
            marks,
        )

        assert result == "Dr.[1] Smith conducted the study."

    def test_title_used_as_a_standalone_noun_stays_with_its_own_sentence(self):
        """Regression: a title-only heuristic (skip forward whenever the
        next word is capitalized) fails here, since "Her" is capitalized
        too but is not a name -- it starts a genuinely new sentence."""
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "After years of study, she finally became a Dr."
            + encode_citation_markers("[1]")
            + " Her family celebrated the achievement.",
            marks,
        )

        assert result == (
            "After years of study, she finally became a Dr.[1] "
            "Her family celebrated the achievement."
        )

    def test_et_al_continuing_the_same_sentence_does_not_relocate(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "As shown by Smith et al." + encode_citation_markers("[1]") + " in the trial.",
            marks,
        )

        assert result == "As shown by Smith et al.[1] in the trial."

    def test_et_al_genuinely_ending_a_sentence_does_not_relocate_into_the_next(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "The dataset was annotated by three coders et al."
            + encode_citation_markers("[1]")
            + " Validation followed a separate protocol.",
            marks,
        )

        assert result == (
            "The dataset was annotated by three coders et al.[1] "
            "Validation followed a separate protocol."
        )

    def test_etc_ending_a_sentence_with_a_lowercase_identifier_next(self):
        """Regression for a reported bug: a next-character-case heuristic
        relocated this marker into the following, unrelated sentence
        because "eeg_data.set" happens to start lowercase."""
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "The pipeline logs timestamps, channel counts, etc."
            + encode_citation_markers("[1]")
            + " eeg_data.set was used for the validation run.",
            marks,
        )

        assert result == (
            "The pipeline logs timestamps, channel counts, etc.[1] "
            "eeg_data.set was used for the validation run."
        )

    def test_etc_continuing_the_same_sentence_in_enumerative_technical_prose(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "EEGLAB provides filtering, referencing, ICA decomposition, etc."
            + encode_citation_markers("[1]")
            + " which are documented in the plugin manager.",
            marks,
        )

        assert result == (
            "EEGLAB provides filtering, referencing, ICA decomposition, etc.[1] "
            "which are documented in the plugin manager."
        )

    def test_eg_continuing_with_a_capitalized_tool_name_does_not_relocate(self):
        """Regression: a lowercase/uppercase heuristic accepted "e.g." as a
        real sentence end here because "MATLAB" is capitalized, then left
        the marker stuck too early instead of leaving it consistently."""
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "Various tools, e.g."
            + encode_citation_markers("[1]")
            + " MATLAB toolboxes, are available.",
            marks,
        )

        assert result == "Various tools, e.g.[1] MATLAB toolboxes, are available."

    def test_eg_continuing_with_a_non_alphabetic_flag_does_not_relocate(self):
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "Configure the tool via flags, e.g."
            + encode_citation_markers("[1]")
            + " --verbose enables extra logging.",
            marks,
        )

        assert result == ("Configure the tool via flags, e.g.[1] --verbose enables extra logging.")

    def test_vs_already_at_boundary_still_skips_forward(self):
        """ "vs." is the sole exception: across two review passes nobody
        could construct natural prose where it genuinely ends a sentence."""
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "We evaluated ICA vs."
            + encode_citation_markers("[1]")
            + " PCA for artifact removal in this study.",
            marks,
        )

        assert result == "We evaluated ICA vs. PCA for artifact removal in this study.[1]"

    def test_vs_mid_sentence_still_skips_forward_during_the_search(self):
        """Same exception, but exercised via the forward-search loop rather
        than the already-at-a-boundary check: the marker starts well
        before "vs.", so the search must pass over its period en route to
        the real sentence end."""
        marks = [CitationMark(1, "https://a.example", "A", "claim")]

        result = normalize_citation_markers(
            "We compared A" + encode_citation_markers("[1]") + " vs. B for accuracy.",
            marks,
        )

        assert result == "We compared A vs. B for accuracy.[1]"


class TestCitationAssembler:
    """Citation markers follow the text block they annotate."""

    def test_citation_before_text_is_buffered_until_that_block_text(self):
        assembler = CitationAssembler()
        citation = {"source": "https://a.example", "title": "A", "cited_text": "claim"}

        marker_text, new_marks = assembler.add_block(
            ContentBlock("text", "", [citation]), block_index=0
        )
        assert marker_text == ""
        assert new_marks == []

        marker_text, new_marks = assembler.add_block(
            ContentBlock("text", "The claim is supported.", []), block_index=0
        )
        assert marker_text == "[1]"
        assert [mark.source for mark in new_marks] == ["https://a.example"]
        assert assembler.marks[0].source == "https://a.example"

    def test_citation_after_text_keeps_inline_stream_position(self):
        assembler = CitationAssembler()
        citation = {"source": "https://a.example", "title": "A"}

        assembler.add_block(ContentBlock("text", "The claim.", []), block_index=0)
        marker_text, _new_marks = assembler.add_block(
            ContentBlock("text", "", [citation]), block_index=0
        )

        assert marker_text == "[1]"

    def test_model_run_reset_prevents_reused_block_index_from_leading(self):
        assembler = CitationAssembler()
        first = {"source": "https://a.example", "title": "A"}
        second = {"source": "https://b.example", "title": "B"}

        marker_text, _new_marks = assembler.add_block(
            ContentBlock("text", "First claim.", [first]), block_index=0
        )
        assert marker_text == "[1]"
        assembler.finish_model_run()

        marker_text, _new_marks = assembler.add_block(
            ContentBlock("text", "", [second]), block_index=0
        )
        assert marker_text == ""

        marker_text, new_marks = assembler.add_block(
            ContentBlock("text", "Second claim.", []), block_index=0
        )
        assert marker_text == "[2]"
        assert [mark.source for mark in new_marks] == ["https://b.example"]
        assert [mark.source for mark in assembler.marks] == [
            "https://a.example",
            "https://b.example",
        ]

    def test_unresolved_citation_is_logged_and_discarded_at_model_run_end(self, caplog):
        assembler = CitationAssembler()
        assembler.add_block(
            ContentBlock(
                "text",
                "",
                [{"source": "https://a.example", "title": "A"}],
            ),
            block_index=0,
        )

        with caplog.at_level(logging.WARNING, logger="src.agents.content"):
            unresolved = assembler.finish_model_run()

        assert unresolved == 1
        assert "matching text block" in caplog.text
        assert assembler.marks == []


class TestContentBlockShape:
    """ContentBlock is a plain tuple, so existing 2/3-tuple comparisons keep working."""

    def test_is_a_named_tuple_with_three_fields(self):
        block = ContentBlock("text", "hi", [])
        assert block.kind == "text"
        assert block.text == "hi"
        assert block.citations == []
        assert block == ("text", "hi", [])
        kind, text, citations = block
        assert (kind, text, citations) == ("text", "hi", [])
