"""Every published widget embed snippet must be safe to copy-paste as-is.

Docs audit, 2026-09-23 ("OSA repository, wrong now"). Four places hand-write
a reader-facing `<script>` embed for osa-chat-widget.js, each independently:
the release-notes generator (`.github/workflows/release.yml`, "Append widget
SRI hash to release notes"), `scripts/widget-sri.py`'s printed output, the
two "Add to Your Site" / "Security-sensitive environments" snippets in
`frontend/index.html`, and the copy-paste card in
`src/api/routers/widget_test.py`. Nothing keeps them consistent with each
other or with how the widget actually boots, so each has drifted
independently before.

Two ways a snippet breaks silently, both measured in a real Chrome tab on
2026-09-23 (see `.context` / the docs audit for the full write-up):

1. `defer` (or `async`) on the widget's own `<script src=...>` tag, combined
   with an inline `OSAChatWidget.setConfig(...)` call that assumes
   `window.OSAChatWidget` already exists. `osa-chat-widget.js` defines
   `window.OSAChatWidget` only once it finishes running (see the bottom of
   the file), so a deferred load means the inline call runs first and
   throws `ReferenceError: OSAChatWidget is not defined` -- silently, since
   the page still renders, just with the default community (HED) instead of
   whichever one the embedder asked for.
2. A widget script `src` naming a host that does not actually serve
   `osa-chat-widget.js`: only `https://demo.osc.earth/...` (always latest),
   the jsDelivr GitHub mirror pinned to a release tag
   (`https://cdn.jsdelivr.net/gh/OpenScience-Collective/osa@...`), or a
   path relative to the embedding page itself (the widget-test page loads
   its own copy this way) actually resolve. `https://osa.osc.earth/...`
   (never existed) and `https://api.osc.earth/osa/widget.js` (404) are
   exactly the kind of invented host this guards against.

`find_snippet_violations` is the one rule; every source below is reduced to
plain markup and checked against it, so a new invented host or a
reintroduced `defer` fails here regardless of which of the four places it
turns up in.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.api.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML_PATH = REPO_ROOT / "frontend" / "index.html"
RELEASE_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "release.yml"
WIDGET_SRI_SCRIPT = REPO_ROOT / "scripts" / "widget-sri.py"

# The only src prefixes that actually serve osa-chat-widget.js today.
# A relative path (no scheme, no leading "//") is separately allowed --
# that is how the widget-test page loads its own copy.
_ALLOWED_SRC_PREFIXES = (
    "https://demo.osc.earth/",
    "https://cdn.jsdelivr.net/gh/OpenScience-Collective/osa@",
)
_ABSOLUTE_URL_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*:)?//")


def _is_allowed_src(src: str) -> bool:
    if any(src.startswith(prefix) for prefix in _ALLOWED_SRC_PREFIXES):
        return True
    # Anything else that looks like an absolute URL (has a scheme, or is
    # protocol-relative "//host/...") is an invented or foreign host.
    return not _ABSOLUTE_URL_RE.match(src)


class _ScriptElement:
    __slots__ = ("attrs", "text")

    def __init__(self, attrs: dict[str, str | None]) -> None:
        self.attrs = attrs
        self.text = ""


class _ScriptTagCollector(HTMLParser):
    """Collects every ``<script>`` element's attributes and inline text.

    A real HTML tokenizer (stdlib ``html.parser``) rather than a regex over
    markup: attribute order, quoting and whitespace all vary across the four
    sources, and a hand-rolled regex is exactly the kind of thing that looks
    right until one of those varies.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[_ScriptElement] = []
        self._current: _ScriptElement | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self._current = _ScriptElement(dict(attrs))
            self.elements.append(self._current)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.elements.append(_ScriptElement(dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.text += data


def find_snippet_violations(snippet: str) -> list[str]:
    """Every rule a published embed snippet must satisfy.

    Returns a human-readable violation per broken rule; empty means the
    snippet is safe to publish as-is.
    """
    parser = _ScriptTagCollector()
    parser.feed(snippet)
    parser.close()

    elements = parser.elements
    widget_elements = [e for e in elements if "osa-chat-widget.js" in (e.attrs.get("src") or "")]

    if not widget_elements:
        return ["no <script src=...osa-chat-widget.js> tag found in the snippet"]

    has_inline_setconfig = any(
        e.attrs.get("src") is None and "setConfig(" in e.text for e in elements
    )

    violations: list[str] = []
    for element in widget_elements:
        src = element.attrs.get("src") or ""
        if not _is_allowed_src(src):
            violations.append(
                f"widget script src {src!r} is not demo.osc.earth, the jsDelivr mirror "
                "(cdn.jsdelivr.net/gh/OpenScience-Collective/osa@...), or a relative path"
            )
        if ("defer" in element.attrs or "async" in element.attrs) and has_inline_setconfig:
            deferred_via = "defer" if "defer" in element.attrs else "async"
            violations.append(
                f"widget script tag has {deferred_via!r} alongside an inline setConfig call; "
                "OSAChatWidget is not defined yet when that call runs (ReferenceError)"
            )
    return violations


# --- Extraction: one function per published source ------------------------


def _widget_test_integration_card(community_id: str = "hed") -> str:
    """The copy-paste card rendered by src/api/routers/widget_test.py, for real."""
    client = TestClient(app)
    response = client.get(f"/communities/{community_id}/widget-test")
    assert response.status_code == 200, response.text
    match = re.search(r'<pre><code>(.*?)</code><button class="copy-btn"', response.text, re.DOTALL)
    assert match, "could not find the Integration Code <pre><code> block in the widget-test page"
    return unescape(match.group(1))


def _index_html_snippets() -> list[str]:
    """Every osa-chat-widget.js embed snippet on the frontend/index.html demo page."""
    source = INDEX_HTML_PATH.read_text()
    blocks = re.findall(r"<pre><code>(.*?)</code></pre>", source, re.DOTALL)
    assert blocks, "found no <pre><code> blocks in frontend/index.html"
    snippets = [unescape(b) for b in blocks]
    widget_snippets = [s for s in snippets if "osa-chat-widget.js" in s]
    assert widget_snippets, "found no osa-chat-widget.js embed snippets in frontend/index.html"
    return widget_snippets


def _release_workflow_widget_embedding_section() -> str:
    """Run the real 'Append widget SRI hash to release notes' step's Python heredoc.

    Parsed from the workflow YAML (so it reflects whatever is actually
    committed, indentation included) rather than copied by hand, then
    executed for real: this is the exact code GitHub Actions runs, just
    invoked directly instead of through a tag push.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW_PATH.read_text())
    steps = workflow["jobs"]["release"]["steps"]
    step = next(s for s in steps if s.get("name") == "Append widget SRI hash to release notes")
    run_script = step["run"]

    heredoc_match = re.search(r"python3 - <<'PYEOF'\n(.*?)\nPYEOF", run_script, re.DOTALL)
    assert heredoc_match, "could not find the python3 heredoc in the SRI release-notes step"
    heredoc_source = heredoc_match.group(1)

    notes_path = Path("/tmp/release_notes.md")
    notes_path.write_text("## What's Changed\n\n- test fixture commit\n")
    try:
        result = subprocess.run(
            [sys.executable, "-c", heredoc_source],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "OSA_VERSION": "9.9.9", "OSA_SRI": "sha384-test-integrity-hash"},
        )
        assert result.returncode == 0, (
            f"the release-notes heredoc failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        return notes_path.read_text()
    finally:
        notes_path.unlink(missing_ok=True)


# --- The four published sources --------------------------------------------


class TestPublishedEmbedSnippetsAreSafe:
    """No published embed snippet may defer the widget past an inline
    setConfig call, or point at a host that does not serve it."""

    def test_widget_test_integration_card(self) -> None:
        snippet = _widget_test_integration_card()
        violations = find_snippet_violations(snippet)
        assert not violations, f"src/api/routers/widget_test.py:\n{violations}\n\n{snippet}"

    def test_frontend_index_html_snippets(self) -> None:
        for snippet in _index_html_snippets():
            violations = find_snippet_violations(snippet)
            assert not violations, f"frontend/index.html:\n{violations}\n\n{snippet}"

    def test_frontend_index_html_keeps_the_standard_demo_snippet(self) -> None:
        """The always-latest demo.osc.earth snippet must not be replaced or dropped."""
        snippets = _index_html_snippets()
        assert any(
            '<script src="https://demo.osc.earth/osa-chat-widget.js"></script>' in s
            for s in snippets
        )

    def test_release_workflow_widget_embedding_section(self) -> None:
        section = _release_workflow_widget_embedding_section()
        assert "## Widget Embedding" in section
        widget_section = section.split("## Widget Embedding", 1)[1]
        violations = find_snippet_violations(widget_section)
        assert not violations, f".github/workflows/release.yml:\n{violations}\n\n{widget_section}"

    def test_release_workflow_keeps_the_standard_demo_snippet(self) -> None:
        section = _release_workflow_widget_embedding_section()
        assert '<script src="https://demo.osc.earth/osa-chat-widget.js"></script>' in section


@pytest.mark.network
class TestWidgetSriScriptOutputIsSafe:
    """scripts/widget-sri.py's printed 'Versioned embed snippet', for a real release.

    Requires fetching the real, already-published v0.8.13 tag from jsDelivr
    (the script's own network fetch, not a mock of one), so this is marked
    `network` and deselected from the main pytest sweep like the rest of
    this suite's live-network tests; it still runs in the `network`-marked
    CI job.
    """

    def test_versioned_snippet_is_safe(self) -> None:
        tag = "v0.8.13"
        result = subprocess.run(
            [sys.executable, str(WIDGET_SRI_SCRIPT), tag],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"widget-sri.py {tag} failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "Versioned embed snippet" in result.stdout, result.stdout

        violations = find_snippet_violations(result.stdout)
        assert not violations, f"scripts/widget-sri.py {tag}:\n{violations}\n\n{result.stdout}"


# --- Direct unit coverage of the checker, from the bug this exists to catch ---


class TestFindSnippetViolationsCatchesTheKnownBug:
    """The checker must catch the exact defect the docs audit found.

    This is the pinned jsDelivr snippet as it was published before this fix
    (byte for byte except for the version and hash): measured in a real
    Chrome tab on 2026-09-23, the inline `setConfig({communityId:
    'fieldtrip'})` that follows a `defer`red widget script threw
    `ReferenceError: OSAChatWidget is not defined`, and the page silently
    fell back to the default community (HED) instead of FieldTrip.
    """

    _KNOWN_BAD_SNIPPET = (
        '<script src="https://cdn.jsdelivr.net/gh/OpenScience-Collective/osa@v0.8.13'
        '/frontend/osa-chat-widget.js"\n'
        '        integrity="sha384-abc123"\n'
        '        crossorigin="anonymous"\n'
        "        defer></script>\n"
        "<script>\n"
        "  OSAChatWidget.setConfig({communityId: 'fieldtrip'});\n"
        "</script>"
    )

    def test_defer_with_inline_setconfig_is_flagged(self) -> None:
        violations = find_snippet_violations(self._KNOWN_BAD_SNIPPET)
        assert violations
        assert any("defer" in v for v in violations)

    def test_async_with_inline_setconfig_is_also_flagged(self) -> None:
        bad = self._KNOWN_BAD_SNIPPET.replace("defer></script>", "async></script>")
        violations = find_snippet_violations(bad)
        assert violations
        assert any("async" in v for v in violations)

    def test_the_same_snippet_without_defer_is_clean(self) -> None:
        fixed = self._KNOWN_BAD_SNIPPET.replace("\n        defer></script>", "></script>")
        assert not find_snippet_violations(fixed)

    def test_an_invented_host_is_flagged(self) -> None:
        violations = find_snippet_violations(
            '<script src="https://osa.osc.earth/frontend/osa-chat-widget.js"></script>'
        )
        assert any("osa.osc.earth" in v for v in violations)

    def test_a_relative_path_is_allowed(self) -> None:
        assert not find_snippet_violations('<script src="/frontend/osa-chat-widget.js"></script>')

    def test_demo_host_without_setconfig_is_allowed(self) -> None:
        assert not find_snippet_violations(
            '<script src="https://demo.osc.earth/osa-chat-widget.js"></script>'
        )

    def test_demo_host_with_deferred_setconfig_is_still_flagged(self) -> None:
        """defer is unsafe with an inline setConfig regardless of which host serves it."""
        violations = find_snippet_violations(
            '<script src="https://demo.osc.earth/osa-chat-widget.js" defer></script>\n'
            "<script>OSAChatWidget.setConfig({ communityId: 'hed' });</script>"
        )
        assert violations
