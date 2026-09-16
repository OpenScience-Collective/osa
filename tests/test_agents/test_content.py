"""Tests for src.agents.content: extracting text from Anthropic block-list content.

No mocks: these exercise the pure functions directly against real content
shapes (plain strings and the block-list shape langchain-anthropic produces
when thinking is enabled or tools are bound).
"""

from src.agents.content import classify_content_blocks, extract_text


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


class TestClassifyContentBlocks:
    """Tests for classify_content_blocks()."""

    def test_plain_string(self):
        assert classify_content_blocks("hello") == [("text", "hello")]

    def test_empty_string_yields_no_pairs(self):
        assert classify_content_blocks("") == []

    def test_text_block(self):
        content = [{"type": "text", "text": "hello"}]
        assert classify_content_blocks(content) == [("text", "hello")]

    def test_thinking_block_carries_no_text(self):
        """A thinking chunk classifies as thinking with an empty payload -- never reasoning text."""
        content = [{"type": "thinking", "thinking": "reasoning content", "signature": "abc"}]
        pairs = classify_content_blocks(content)
        assert pairs == [("thinking", "")]
        # The reasoning text itself must never appear anywhere in the result.
        assert not any("reasoning content" in text for _kind, text in pairs)

    def test_redacted_thinking_block_classified_as_thinking(self):
        content = [{"type": "redacted_thinking", "data": "encrypted"}]
        assert classify_content_blocks(content) == [("thinking", "")]

    def test_mixed_blocks_preserve_order(self):
        content = [
            {"type": "thinking", "thinking": "step 1", "signature": "abc"},
            {"type": "text", "text": "part one. "},
            {"type": "thinking", "thinking": "step 2", "signature": "def"},
            {"type": "text", "text": "part two."},
        ]
        assert classify_content_blocks(content) == [
            ("thinking", ""),
            ("text", "part one. "),
            ("thinking", ""),
            ("text", "part two."),
        ]

    def test_tool_use_block_is_skipped_entirely(self):
        """tool_use blocks are handled by dedicated tool-call events, not here."""
        content = [
            {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {}},
            {"type": "text", "text": "ok"},
        ]
        assert classify_content_blocks(content) == [("text", "ok")]

    def test_empty_text_block_skipped(self):
        content = [{"type": "text", "text": ""}, {"type": "text", "text": "ok"}]
        assert classify_content_blocks(content) == [("text", "ok")]

    def test_non_dict_block_skipped(self):
        """Defensive: a malformed non-dict block entry is ignored, not an error."""
        content = ["not a dict", {"type": "text", "text": "ok"}]
        assert classify_content_blocks(content) == [("text", "ok")]

    def test_empty_list_yields_no_pairs(self):
        assert classify_content_blocks([]) == []
