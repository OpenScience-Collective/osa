"""Widget invariants for Phase 4 inline citations (issue #350 / #364).

CI has no JS runtime (see CLAUDE.md / .rules), so these parse
osa-chat-widget.js as text via regex, the same approach
test_widget_drift.py uses. Where possible, expectations are derived from
the real Python source (CitationInfo's own field names) rather than
hardcoded, so a field rename on the backend is caught here instead of
shipping a widget that silently stops rendering it.

Reading the source has a limit worth stating: it caught none of the
marker-scanning bug that ``frontend/test-citation-markers.js`` was written
for, because every regex involved looked correct on its own and only their
combination was wrong. Run that file with ``bun`` for actual behavior; the
checks here pin the shape it depends on, so a regression still fails in CI.
"""

import re
from pathlib import Path

from src.api.routers.community import CitationInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
WIDGET_PATH = REPO_ROOT / "frontend" / "osa-chat-widget.js"


def _widget_source() -> str:
    return WIDGET_PATH.read_text()


class TestCitationFieldsMatchBackend:
    """Every CitationInfo field must be referenced somewhere in the widget.

    Dynamic over CitationInfo.model_fields rather than a hardcoded list:
    if a field is renamed or a new one is added on the backend without a
    matching widget change, this fails instead of the widget silently
    dropping the new data.
    """

    def test_every_citation_info_field_appears_in_widget_source(self) -> None:
        source = _widget_source()
        fields = list(CitationInfo.model_fields.keys())
        assert fields, "CitationInfo has no fields to check against"

        missing = [f for f in fields if f not in source]
        assert not missing, (
            f"CitationInfo field(s) {missing} not referenced anywhere in "
            f"{WIDGET_PATH.name}; the widget cannot be reading them"
        )


class TestWidgetHandlesCitationSseEvents:
    """The streaming handler must recognize the citation event and field."""

    def test_handles_citation_event_type(self) -> None:
        assert "event.event === 'citation'" in _widget_source()

    def test_done_event_reads_citations_array(self) -> None:
        source = _widget_source()
        assert "event.citations" in source
        # Guards against a bare assignment that would crash on a malformed
        # or missing citations field from an older/misbehaving backend.
        assert "Array.isArray(event.citations)" in source

    def test_non_streaming_response_reads_citations_field(self) -> None:
        """The /chat non-streaming fallback path also carries citations."""
        source = _widget_source()
        assert "Array.isArray(data.citations)" in source


class TestThinkingPlaceholderRendering:
    """The transient assistant entry must not render beside the loader."""

    def test_empty_streaming_assistant_is_skipped_while_loading(self) -> None:
        source = _widget_source()
        guard = (
            "if (isLoading && msg.role === 'assistant' && !msg.content "
            "&& msgIndex === messages.length - 1)"
        )
        assert guard in source
        assert "loading bubble below is the assistant" in source

    def test_empty_completed_stream_is_removed(self) -> None:
        source = _widget_source()
        assert "messages.splice(messageIndex, 1);" in source


class TestRenderInlineMarkdownCitationSupport:
    """renderInlineMarkdown must accept and use a citations lookup."""

    def test_function_signature_accepts_citations_param(self) -> None:
        source = _widget_source()
        match = re.search(r"function renderInlineMarkdown\(([^)]*)\)", source)
        assert match, "Could not find renderInlineMarkdown function signature"
        assert "citationsByMarker" in match.group(1)

    def test_bare_marker_pattern_excludes_real_links(self) -> None:
        """The citation regex must not swallow a real [text](url) markdown link."""
        source = _widget_source()
        # The citation match uses a negative lookahead for '(' so it never
        # collides with the existing [text](url) link pattern. Checking for
        # the literal regex source (not compiling it) since this is Python
        # code reading JavaScript text, not executing it.
        assert r"/\[(\d+)\](?!\()/" in source, (
            "Expected a citation-marker regex literal with a (?!\\() "
            "lookahead, to avoid matching markdown links"
        )

    def test_marker_scan_considers_every_bracketed_number(self) -> None:
        """The scan must be exhaustive, not first-match.

        With a single ``.match()``, an unknown bracketed number earlier in
        the run (the model's own "see item [42]", or an "arr[0]") ends the
        search, so a real marker after it renders as dead plain text and,
        if nothing else in that run matched either, the remainder is emitted
        unparsed. Verified by execution in frontend/test-citation-markers.js;
        pinned here because CI cannot run that file.
        """
        source = _widget_source()
        assert r"matchAll(/\[(\d+)\](?!\()/g)" in source, (
            "Expected the marker scan to iterate every candidate via matchAll with the /g flag"
        )
        assert r".match(/\[(\d+)\](?!\()/)" not in source, (
            "A single .match() only sees the first bracketed number, so an "
            "unrelated one earlier in the text hides a real marker after it"
        )


class TestMarkdownToHtmlThreadsCitations:
    """markdownToHtml must pass citationsByMarker to every renderInlineMarkdown call.

    A future call site added inside markdownToHtml without threading the
    parameter would silently render that context's citations as plain
    "[1]" text instead of a superscript link -- this fails loudly instead.
    """

    def _markdown_to_html_body(self) -> str:
        source = _widget_source()
        start = source.index("function markdownToHtml(")
        # The next top-level "function " after this one marks the end of
        # this function's body (both are 2-space-indented module functions).
        end = source.index("\n  function ", start + 1)
        return source[start:end]

    def test_function_signature_accepts_citations_param(self) -> None:
        source = _widget_source()
        match = re.search(r"function markdownToHtml\(([^)]*)\)", source)
        assert match, "Could not find markdownToHtml function signature"
        assert "citationsByMarker" in match.group(1)

    def test_every_render_inline_markdown_call_threads_citations(self) -> None:
        # Line-based rather than a paren-matching regex: some call sites
        # pass an expression that itself contains parens (e.g.
        # `cell.trim()`), which would truncate a naive
        # `renderInlineMarkdown\(([^)]*)\)` capture at that inner `)`.
        # Every call site in this file is single-line, so checking each
        # line that invokes it is both simpler and correct.
        lines = [
            line
            for line in self._markdown_to_html_body().splitlines()
            if "renderInlineMarkdown(" in line
        ]
        assert len(lines) >= 4, f"Expected several renderInlineMarkdown call sites, found {lines}"
        missing = [line.strip() for line in lines if "citationsByMarker" not in line]
        assert not missing, (
            f"renderInlineMarkdown call(s) not threading citationsByMarker: {missing}"
        )


class TestCitationStyling:
    """CSS classes referenced by the citation-rendering JS must be defined."""

    def test_inline_citation_class_defined(self) -> None:
        source = _widget_source()
        assert ".osa-citation" in source
        assert re.search(r"\.osa-citation\s*\{", source)

    def test_source_list_class_defined(self) -> None:
        source = _widget_source()
        assert re.search(r"\.osa-message-sources\s*\{", source)

    def test_rendered_markup_uses_defined_classes(self) -> None:
        """The JS that builds the DOM markup uses the same class names as the CSS."""
        source = _widget_source()
        assert 'class="osa-citation"' in source
        assert "osa-message-sources" in source
        assert "osa-source-marker" in source
