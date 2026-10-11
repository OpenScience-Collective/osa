"""Markdown and rich text cleaning utilities.

This module provides utilities for cleaning and normalizing markdown and other
rich text formats to make them more suitable for LLM consumption.
"""

import re
from html.parser import HTMLParser

# HTML elements that count as markup in a markdown source. An angle-bracket
# name outside this set is a placeholder (<subject_label>), a generic, an
# autolink (<https://example.org>) or a repr, and is left in the text.
# fmt: off
_HTML_ELEMENTS = frozenset({
    "a", "abbr", "acronym", "address", "applet", "area", "article", "aside", "audio", "b",
    "base", "basefont", "bdi", "bdo", "big", "blink", "blockquote", "body", "br", "button",
    "canvas", "caption", "center", "cite", "code", "col", "colgroup", "data", "datalist",
    "dd", "del", "details", "dfn", "dialog", "dir", "div", "dl", "dt", "em", "embed",
    "fieldset", "figcaption", "figure", "font", "footer", "form", "frame", "frameset",
    "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup", "hr", "html", "i",
    "iframe", "img", "input", "ins", "kbd", "label", "legend", "li", "link", "main", "map",
    "mark", "marquee", "menu", "meta", "meter", "nav", "nobr", "noscript", "object", "ol",
    "optgroup", "option", "output", "p", "param", "picture", "pre", "progress", "q", "rp",
    "rt", "ruby", "s", "samp", "script", "section", "select", "slot", "small", "source",
    "span", "strike", "strong", "style", "sub", "summary", "sup", "table", "tbody", "td",
    "template", "textarea", "tfoot", "th", "thead", "time", "title", "tr", "track", "tt",
    "u", "ul", "var", "video", "wbr",
})

# Elements whose tag is markup even when it stands alone, without attributes
# and without a closing tag anywhere in the text.
_STANDALONE_ELEMENTS = frozenset({
    "area", "base", "br", "col", "colgroup", "dd", "dt", "embed", "hr", "img", "input",
    "li", "link", "meta", "option", "p", "param", "source", "tbody", "td", "tfoot", "th",
    "thead", "tr", "track", "wbr",
})
# fmt: on

# Spans that are never HTML markup: a comment (dropped), a fenced code block
# and an inline code span (both kept as they are).
_COMMENT = r"(?P<comment><!--.*?-->)"
_FENCE = (
    r"(?P<fence>^(?P<fence_open>[ ]{0,3}(?P<fence_mark>`{3,}|~{3,})(?P<info>[^\n]*)\n)"
    r"(?P<fence_body>.*?)"
    r"(?P<fence_close>^[ ]{0,3}(?P=fence_mark)[`~]*[ \t]*$|\Z))"
)
_CODE_SPAN = r"(?P<span>(?<!`)(?P<ticks>`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)(?P=ticks)(?!`))"
_PROTECTED = re.compile(f"{_COMMENT}|{_FENCE}|{_CODE_SPAN}", re.DOTALL | re.MULTILINE)
_CLOSING_TAG = re.compile(r"</\s*([a-zA-Z][^\s/>]*)")

# A fence opened as ```{name} is a MyST directive. Its body is markdown unless the
# directive holds code.
_DIRECTIVE = re.compile(r"\{([^}\s]+)\}")
_CODE_DIRECTIVES = frozenset({"code", "code-block", "code-cell", "literalinclude", "sourcecode"})


class HTMLStripper(HTMLParser):
    """HTML parser that drops HTML tags and keeps text content.

    Anything that is not HTML markup is kept as written. A tag is markup when its name
    is an HTML element. A bare opening tag of an element that normally has a closing
    tag (``<label>``) counts only when the text closes it somewhere, so a placeholder
    that shares a name with an element stays.
    """

    def __init__(self, closed: frozenset[str] = frozenset()):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self._closed = closed

    def handle_data(self, data: str):
        """Handle text data between tags."""
        self.text.append(data)

    def _is_markup(self, tag: str, attrs: list, self_closing: bool = False) -> bool:
        if tag not in _HTML_ELEMENTS:
            return False
        return bool(attrs or self_closing or tag in _STANDALONE_ELEMENTS or tag in self._closed)

    def handle_starttag(self, tag: str, attrs: list):
        """Keep a start tag that is not markup."""
        if not self._is_markup(tag, attrs):
            self.text.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag: str, attrs: list):
        """Keep a self-closing tag that is not markup."""
        if not self._is_markup(tag, attrs, self_closing=True):
            self.text.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str):
        """Keep an end tag that is not markup."""
        if tag not in _HTML_ELEMENTS:
            self.text.append(f"</{tag}>")

    def get_data(self) -> str:
        """Get the accumulated text data."""
        return "".join(self.text)


def _strip_markup(text: str, closed: frozenset[str]) -> str:
    stripper = HTMLStripper(closed)
    stripper.feed(text)
    stripper.close()
    return stripper.get_data()


def _is_markdown_directive(info: str) -> bool:
    directive = _DIRECTIVE.match(info.strip())
    return directive is not None and directive.group(1) not in _CODE_DIRECTIVES


def strip_html_tags(text: str) -> str:
    """Remove HTML tags and comments from markdown, keeping content.

    Code is content: fenced code blocks and inline code spans are returned as written,
    and so are angle-bracket spans that are not HTML elements, such as placeholders
    (``sub-<label>``), autolinks and generics.

    Args:
        text: Text possibly containing HTML tags

    Returns:
        Text with HTML tags removed
    """
    closed = frozenset(name.lower() for name in _CLOSING_TAG.findall(text))
    parts: list[str] = []
    last = 0
    for match in _PROTECTED.finditer(text):
        parts.append(_strip_markup(text[last : match.start()], closed))
        if match.group("fence") is not None and _is_markdown_directive(match.group("info")):
            parts.append(_strip_markup(match.group("fence_open"), closed))
            parts.append(strip_html_tags(match.group("fence_body")))
            parts.append(match.group("fence_close"))
        elif not match.group("comment"):
            parts.append(match.group(0))
        last = match.end()
    parts.append(_strip_markup(text[last:], closed))
    return "".join(parts)


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
