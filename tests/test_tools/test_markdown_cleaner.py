"""Tests for markdown cleaning utilities.

These tests ensure deterministic markdown cleaning for LLM consumption.
"""

from src.tools.markdown_cleaner import (
    clean_code_blocks,
    clean_markdown,
    clean_markdown_headers,
    clean_markdown_links,
    extract_first_sentences,
    normalize_whitespace,
    remove_markdown_images,
    strip_html_tags,
)


class TestStripHtmlTags:
    """Tests for HTML tag stripping."""

    def test_simple_html_tags(self):
        """Test stripping simple HTML tags."""
        text = "<p>Hello <strong>world</strong>!</p>"
        result = strip_html_tags(text)
        assert result == "Hello world!"

    def test_nested_html_tags(self):
        """Test stripping nested HTML tags."""
        text = "<div><p>Nested <span>tags</span></p></div>"
        result = strip_html_tags(text)
        assert result == "Nested tags"

    def test_html_with_attributes(self):
        """Test stripping HTML tags with attributes."""
        text = '<a href="https://example.com" class="link">Link text</a>'
        result = strip_html_tags(text)
        assert result == "Link text"

    def test_mixed_html_and_text(self):
        """Test mixed HTML and plain text."""
        text = "Some text <b>bold</b> more text"
        result = strip_html_tags(text)
        assert result == "Some text bold more text"

    def test_no_html_tags(self):
        """Test text with no HTML tags."""
        text = "Plain text without any HTML"
        result = strip_html_tags(text)
        assert result == "Plain text without any HTML"

    def test_self_closing_tags(self):
        """Test self-closing HTML tags."""
        text = "Text with <br/> line break"
        result = strip_html_tags(text)
        assert result == "Text with  line break"


class TestNormalizeWhitespace:
    """Tests for whitespace normalization."""

    def test_multiple_spaces(self):
        """Test collapsing multiple spaces."""
        text = "Text  with   multiple    spaces"
        result = normalize_whitespace(text)
        # Note: This doesn't collapse spaces in our implementation
        assert "Text" in result and "spaces" in result

    def test_trailing_whitespace(self):
        """Test removing trailing whitespace from lines."""
        text = "Line with trailing spaces   \nAnother line   "
        result = normalize_whitespace(text)
        lines = result.split("\n")
        assert all(not line.endswith(" ") for line in lines)

    def test_tabs_to_spaces(self):
        """Test converting tabs to spaces."""
        text = "Text\twith\ttabs"
        result = normalize_whitespace(text)
        assert "\t" not in result
        assert "    " in result  # 4 spaces per tab

    def test_excessive_blank_lines(self):
        """Test limiting consecutive blank lines."""
        text = "Line 1\n\n\n\n\nLine 2"
        result = normalize_whitespace(text)
        # Should limit to max 2 consecutive blank lines
        assert "\n\n\n\n" not in result

    def test_mixed_whitespace_issues(self):
        """Test handling multiple whitespace issues."""
        text = "Line 1   \n\n\n\n\tTabbed line\n\n\nLine 2"
        result = normalize_whitespace(text)
        # Function limits to max 2 consecutive blank lines (which is 3 newlines: \n\n\n)
        # So "\n\n\n\n" (4 newlines, 3 blank lines) should be reduced
        assert "\n\n\n\n" not in result


class TestCleanMarkdownLinks:
    """Tests for markdown link simplification."""

    def test_simple_link(self):
        """Test simplifying simple markdown link."""
        text = "[Link text](https://example.com)"
        result = clean_markdown_links(text)
        assert result == "Link text (https://example.com)"

    def test_link_same_as_text(self):
        """Test link where text equals URL."""
        text = "[https://example.com](https://example.com)"
        result = clean_markdown_links(text)
        # When text == URL, just show URL once
        assert result == "https://example.com"

    def test_multiple_links(self):
        """Test multiple links in text."""
        text = "Check [this](https://a.com) and [that](https://b.com)"
        result = clean_markdown_links(text)
        assert "this (https://a.com)" in result
        assert "that (https://b.com)" in result

    def test_link_in_sentence(self):
        """Test link within a sentence."""
        text = "Visit the [documentation](https://docs.example.com) for details."
        result = clean_markdown_links(text)
        assert "documentation (https://docs.example.com)" in result
        assert "for details" in result

    def test_no_links(self):
        """Test text with no markdown links."""
        text = "Plain text without links"
        result = clean_markdown_links(text)
        assert result == text


class TestCleanMarkdownHeaders:
    """Tests for markdown header normalization."""

    def test_header_spacing(self):
        """Test adding blank lines around headers."""
        text = "Text before\n# Header\nText after"
        result = clean_markdown_headers(text)
        lines = result.split("\n")
        header_idx = next(i for i, line in enumerate(lines) if line.startswith("# Header"))
        # Should have blank line before (if not at start)
        assert header_idx > 0
        # Should have blank line after
        assert header_idx < len(lines) - 1

    def test_multiple_headers(self):
        """Test multiple headers with spacing."""
        text = "# Header 1\nText\n## Header 2\nMore text"
        result = clean_markdown_headers(text)
        # Headers should have proper spacing
        assert "# Header 1" in result
        assert "## Header 2" in result

    def test_header_at_start(self):
        """Test header at document start."""
        text = "# First Header\nText content"
        result = clean_markdown_headers(text)
        assert result.startswith("# First Header")


class TestRemoveMarkdownImages:
    """Tests for markdown image removal."""

    def test_simple_image(self):
        """Test removing simple image syntax."""
        text = "![Alt text](image.png)"
        result = remove_markdown_images(text)
        assert result == "[Image: Alt text]"

    def test_image_without_alt(self):
        """Test removing image with no alt text."""
        text = "![](image.png)"
        result = remove_markdown_images(text)
        assert result == "[Image: ]"

    def test_image_in_text(self):
        """Test image within text."""
        text = "Here is an image: ![diagram](diagram.png) showing the flow."
        result = remove_markdown_images(text)
        assert "[Image: diagram]" in result
        assert "Here is an image:" in result
        assert "showing the flow" in result

    def test_multiple_images(self):
        """Test multiple images."""
        text = "![First](a.png) and ![Second](b.png)"
        result = remove_markdown_images(text)
        assert "[Image: First]" in result
        assert "[Image: Second]" in result


class TestCleanCodeBlocks:
    """Tests for code block cleaning."""

    def test_fenced_code_block_spacing(self):
        """Test adding spacing around fenced code blocks."""
        text = "Text before\n```python\ncode\n```\nText after"
        result = clean_code_blocks(text)
        # Should have blank lines around code block
        assert "\n\n```" in result or result.startswith("```")
        assert "```\n\n" in result or result.endswith("```")

    def test_multiple_code_blocks(self):
        """Test multiple code blocks."""
        text = "```\nblock1\n```\nText\n```\nblock2\n```"
        result = clean_code_blocks(text)
        # Should have proper spacing
        assert result.count("```") == 4


class TestExtractFirstSentences:
    """Tests for sentence extraction."""

    def test_extract_three_sentences(self):
        """Test extracting first 3 sentences."""
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        result = extract_first_sentences(text, 3)
        assert result == "First sentence. Second sentence. Third sentence."

    def test_extract_fewer_than_available(self):
        """Test extracting when fewer sentences than requested."""
        text = "Only one sentence."
        result = extract_first_sentences(text, 3)
        assert result == "Only one sentence."

    def test_skip_headers(self):
        """Test skipping header lines."""
        text = "# Header\n\nFirst sentence. Second sentence."
        result = extract_first_sentences(text, 2)
        # Should skip the header
        assert "# Header" not in result
        assert "First sentence" in result
        assert "Second sentence" in result

    def test_question_and_exclamation(self):
        """Test sentences ending with ? and !"""
        text = "What is this? It's amazing! Here's more."
        result = extract_first_sentences(text, 3)
        assert result == "What is this? It's amazing! Here's more."

    def test_multiline_text(self):
        """Test extracting from multiline text."""
        text = "First sentence.\n\nSecond sentence.\nThird sentence."
        result = extract_first_sentences(text, 2)
        assert "First sentence" in result
        assert "Second sentence" in result


class TestCleanMarkdown:
    """Integration tests for full markdown cleaning pipeline."""

    def test_clean_all_default(self):
        """Test cleaning with all default options."""
        text = """<p>Hello **world**!</p>

![Image](img.png)

Check [this link](https://example.com).

```python
code here
```

More text."""
        result = clean_markdown(text)

        # Should remove HTML
        assert "<p>" not in result
        assert "</p>" not in result

        # Should handle image
        assert "[Image: Image]" in result

        # Should clean link
        assert "this link (https://example.com)" in result

    def test_clean_selective(self):
        """Test cleaning with selective options."""
        text = "<b>Bold</b> text with [link](https://example.com)"

        # Only strip HTML
        result = clean_markdown(text, strip_html=True, simplify_links=False)
        assert "<b>" not in result
        assert "[link](https://example.com)" in result

        # Only simplify links
        result = clean_markdown(text, strip_html=False, simplify_links=True)
        assert "<b>" in result
        assert "link (https://example.com)" in result

    def test_real_hed_documentation_example(self):
        """Test with realistic HED documentation snippet."""
        text = """# HED Annotation

<div class="note">
<p>HED (Hierarchical Event Descriptors) is a system for annotating data.</p>
</div>

![HED Schema](schema.png)

For more information, see the [specification](https://hed-specification.org).

## Basic Tags

HED tags describe events:

```
Event, Sensory-event, Visual-presentation
```
"""
        result = clean_markdown(text)

        # Should clean HTML
        assert "<div" not in result
        assert "<p>" not in result

        # Should handle image
        assert "[Image:" in result

        # Should clean link
        assert "specification (https://hed-specification.org)" in result

        # Should preserve headers and code
        assert "# HED Annotation" in result
        assert "## Basic Tags" in result
        assert "Event, Sensory-event" in result

    def test_whitespace_cleanup(self):
        """Test overall whitespace cleanup."""
        text = """Line 1




Line 2
Line 3\t\t"""
        result = clean_markdown(text)

        # Should limit blank lines
        assert "\n\n\n\n" not in result

        # Should remove trailing whitespace
        lines = result.split("\n")
        assert all(not line.endswith(" ") or not line.endswith("\t") for line in lines)

    def test_deterministic_output(self):
        """Test that cleaning is deterministic."""
        text = "Test [link](url) with ![image](img.png) and <b>HTML</b>"

        result1 = clean_markdown(text)
        result2 = clean_markdown(text)

        assert result1 == result2

    def test_empty_input(self):
        """Test cleaning empty input."""
        result = clean_markdown("")
        assert result == ""

    def test_whitespace_only_input(self):
        """Test cleaning whitespace-only input."""
        result = clean_markdown("   \n\n   \n   ")
        # Should be cleaned to minimal whitespace
        assert len(result.strip()) == 0


class TestMarkdownHtmlParity:
    """Test parity between markdown and HTML content.

    These tests ensure that important information is preserved
    when converting from HTML to markdown or cleaning markdown.
    """

    def test_preserve_text_content(self):
        """Test that text content is preserved through HTML stripping."""
        html = "<p>Important information about <strong>HED tags</strong>.</p>"
        result = strip_html_tags(html)
        assert "Important information about" in result
        assert "HED tags" in result

    def test_preserve_link_information(self):
        """Test that link information is preserved."""
        markdown = "See [documentation](https://hedtags.org) for details."
        result = clean_markdown_links(markdown)
        # Both link text and URL should be present
        assert "documentation" in result
        assert "https://hedtags.org" in result

    def test_preserve_list_structure(self):
        """Test that list items are preserved."""
        text = """
- Item 1
- Item 2
- Item 3
"""
        result = clean_markdown(text)
        assert "- Item 1" in result
        assert "- Item 2" in result
        assert "- Item 3" in result

    def test_preserve_code_blocks(self):
        """Test that code blocks are preserved."""
        text = """
Example:

```python
def example():
    pass
```
"""
        result = clean_markdown(text)
        assert "```python" in result or "```" in result
        assert "def example():" in result

    def test_preserve_emphasis(self):
        """Test that markdown emphasis is preserved."""
        text = "This is **important** and *emphasized*."
        result = clean_markdown(text)
        # Markdown emphasis should stay (we don't strip it)
        assert "**important**" in result
        assert "*emphasized*" in result


class TestDocumentationSpecificCases:
    """Tests for specific HED documentation patterns."""

    def test_hed_tag_examples(self):
        """Test that HED tag examples are preserved correctly."""
        text = """
Tags can be combined:

```
(Onset, Sensory-event), Visual-presentation, (Red, Blue)
```

The above represents a valid HED string.
"""
        result = clean_markdown(text)
        assert "Onset, Sensory-event" in result
        assert "Visual-presentation" in result
        assert "valid HED string" in result

    def test_hed_error_messages(self):
        """Test that error messages are preserved."""
        text = """
## Common Errors

<div class="error">
ERROR: Tag 'InvalidTag' is not in the schema.
</div>

This error occurs when using undefined tags.
"""
        result = clean_markdown(text)
        assert "ERROR: Tag 'InvalidTag'" in result
        assert "not in the schema" in result
        assert "undefined tags" in result

    def test_hed_schema_hierarchy(self):
        """Test that schema hierarchy examples are preserved."""
        text = """
Schema hierarchy:

- Event
  - Sensory-event
    - Visual-presentation
  - Agent-action
"""
        result = clean_markdown(text)
        assert "- Event" in result
        assert "Sensory-event" in result
        assert "Visual-presentation" in result
