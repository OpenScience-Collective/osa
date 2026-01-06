"""Markdown and rich text cleaning utilities.

This module provides utilities for cleaning and normalizing markdown and other
rich text formats to make them more suitable for LLM consumption.
"""

import re
from html.parser import HTMLParser


class HTMLStripper(HTMLParser):
    """HTML parser that strips all tags and keeps only text content."""

    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text = []

    def handle_data(self, data: str):
        """Handle text data between tags."""
        self.text.append(data)

    def get_data(self) -> str:
        """Get the accumulated text data."""
        return "".join(self.text)


def strip_html_tags(text: str) -> str:
    """Remove all HTML tags from text, keeping only content.

    Args:
        text: Text possibly containing HTML tags

    Returns:
        Text with HTML tags removed
    """
    stripper = HTMLStripper()
    stripper.feed(text)
    return stripper.get_data()


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace in text.

    - Replaces multiple spaces with single space
    - Removes leading/trailing whitespace from lines
    - Limits consecutive blank lines to 2

    Args:
        text: Text to normalize

    Returns:
        Text with normalized whitespace
    """
    # Replace tabs with spaces
    text = text.replace("\t", "    ")

    # Remove trailing whitespace from each line
    lines = [line.rstrip() for line in text.split("\n")]

    # Limit consecutive blank lines to 2
    cleaned_lines = []
    blank_count = 0
    for line in lines:
        if not line.strip():
            blank_count += 1
            if blank_count <= 2:
                cleaned_lines.append(line)
        else:
            blank_count = 0
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def clean_markdown_links(text: str) -> str:
    """Simplify markdown links to make them more readable.

    Converts [text](url) to "text (url)" format.

    Args:
        text: Markdown text

    Returns:
        Text with simplified links
    """
    # Pattern: [link text](url)
    pattern = r"\[([^\]]+)\]\(([^)]+)\)"

    def replace_link(match):
        link_text = match.group(1)
        url = match.group(2)
        # If link text is the same as URL, just show URL
        if link_text == url or link_text.strip() == url.strip():
            return url
        # Otherwise show both
        return f"{link_text} ({url})"

    return re.sub(pattern, replace_link, text)


def clean_markdown_headers(text: str) -> str:
    """Normalize markdown headers.

    Ensures consistent spacing around headers.

    Args:
        text: Markdown text

    Returns:
        Text with normalized headers
    """
    lines = text.split("\n")
    cleaned_lines = []

    for i, line in enumerate(lines):
        # Check if this line is a header
        if line.strip().startswith("#"):
            # Add blank line before header if not at start
            if i > 0 and cleaned_lines and cleaned_lines[-1].strip():
                cleaned_lines.append("")
            cleaned_lines.append(line)
            # Add blank line after header if next line is not blank
            if i < len(lines) - 1 and lines[i + 1].strip():
                cleaned_lines.append("")
        else:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def remove_markdown_images(text: str) -> str:
    """Remove markdown image syntax.

    Replaces ![alt](url) with just the alt text.

    Args:
        text: Markdown text

    Returns:
        Text with images removed
    """
    # Pattern: ![alt text](url)
    pattern = r"!\[([^\]]*)\]\([^)]+\)"
    return re.sub(pattern, r"[Image: \1]", text)


def clean_code_blocks(text: str) -> str:
    """Clean and normalize code blocks.

    Args:
        text: Markdown text

    Returns:
        Text with normalized code blocks
    """
    # Ensure blank lines around fenced code blocks
    text = re.sub(r"([^\n])\n```", r"\1\n\n```", text)
    text = re.sub(r"```\n([^\n])", r"```\n\n\1", text)

    return text


def clean_markdown(
    text: str,
    *,
    strip_html: bool = True,
    normalize_ws: bool = True,
    simplify_links: bool = True,
    normalize_headers: bool = True,
    remove_images: bool = True,
    clean_code: bool = True,
) -> str:
    """Clean and normalize markdown text for LLM consumption.

    Args:
        text: Markdown text to clean
        strip_html: Whether to strip HTML tags
        normalize_ws: Whether to normalize whitespace
        simplify_links: Whether to simplify markdown links
        normalize_headers: Whether to normalize header spacing
        remove_images: Whether to remove image syntax
        clean_code: Whether to clean code blocks

    Returns:
        Cleaned markdown text
    """
    if strip_html:
        text = strip_html_tags(text)

    # Remove images BEFORE cleaning links, since image syntax ![...](...)
    # can be matched by link pattern [...](...)
    if remove_images:
        text = remove_markdown_images(text)

    if simplify_links:
        text = clean_markdown_links(text)

    if clean_code:
        text = clean_code_blocks(text)

    if normalize_headers:
        text = clean_markdown_headers(text)

    if normalize_ws:
        text = normalize_whitespace(text)

    return text


def extract_first_sentences(text: str, num_sentences: int = 3) -> str:
    """Extract first N sentences from text for use as description.

    Args:
        text: Text to extract from
        num_sentences: Number of sentences to extract

    Returns:
        First N sentences
    """
    # First, filter out header lines
    lines = text.split("\n")
    non_header_lines = [line for line in lines if not line.strip().startswith("#")]
    text_without_headers = " ".join(non_header_lines)

    # Split into sentences using lookbehind to preserve punctuation
    # Splits after . ! ? when followed by space, but keeps the punctuation
    sentences = re.split(r"(?<=[.!?])\s+", text_without_headers)

    # Take first N non-empty sentences
    selected = []
    for sent in sentences:
        sent = sent.strip()
        if sent:
            selected.append(sent)
            if len(selected) >= num_sentences:
                break

    return " ".join(selected) if selected else ""
