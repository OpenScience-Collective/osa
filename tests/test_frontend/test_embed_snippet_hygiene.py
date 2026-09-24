"""Every published widget embed snippet must be safe to copy-paste as-is.

Docs audit, 2026-09-23 ("OSA repository, wrong now"). Several places
hand-write a reader-facing `<script>` embed for osa-chat-widget.js, each
independently: the release-notes generator (`.github/workflows/release.yml`,
"Append widget SRI hash to release notes"), `scripts/widget-sri.py`'s
printed output, the two "Add to Your Site" / "Security-sensitive
environments" snippets in `frontend/index.html`, the copy-paste card in
`src/api/routers/widget_test.py`, and any fenced ```html block under
`docs/**/*.md`. Nothing keeps them consistent with each other or with how
the widget actually boots, so each has drifted independently before, and a
brand new place (`docs/community-widget.md`, added by #463) drifted the
moment it was written because nothing was scanning for one.

Three ways a snippet breaks silently, all measured in a real Chrome tab
(see `.context` / the docs audit for the full write-up):

1. `defer` (or `async`) on the widget's own `<script src=...>` tag, combined
   with an inline `OSAChatWidget.setConfig(...)` call that assumes
   `window.OSAChatWidget` already exists. `osa-chat-widget.js` defines
   `window.OSAChatWidget` only once it finishes running (see the bottom of
   the file), so a deferred load means the inline call runs first and
   throws `ReferenceError: OSAChatWidget is not defined` -- silently, since
   the page still renders, just with the default community (HED) instead of
   whichever one the embedder asked for. Measured 2026-09-23.
2. An inline `setConfig(...)` call placed BEFORE the widget's `<script
   src>` tag in document order. This throws the identical
   `ReferenceError`, with no `defer`/`async` involved at all: a browser
   simply has not run the widget script yet, so `window.OSAChatWidget`
   does not exist regardless of how the later tag is loaded. A checker
   that only looks at `defer`/`async` misses this entirely, which is
   exactly how a reviewer's swap of the two `<script>` blocks in
   `frontend/index.html` passed all of this file's tests before this was
   added (PR #467 review, item 2).
3. A widget script `src` naming a host that does not actually serve
   `osa-chat-widget.js`: only `https://demo.osc.earth/...` (always latest),
   the jsDelivr GitHub mirror pinned to a release tag
   (`https://cdn.jsdelivr.net/gh/OpenScience-Collective/osa@...`), or a
   path relative to the embedding page itself (the widget-test page loads
   its own copy this way) actually resolve. `https://osa.osc.earth/...`
   (never existed) and `https://widget.osc.earth/osa/osa-chat-widget.js`
   (the API edge, 400 `Invalid community ID format`; #464 tracks actually
   serving the script there) are exactly the kind of invented host this
   guards against -- the second one is a real example that shipped in
   `docs/community-widget.md` via #463 because nothing scanned that file
   for a widget `<script>` tag at all (PR #467 review, item 3; see
   `test_every_widget_script_reference_is_accounted_for` below).

`find_snippet_violations` is the one rule; every source below is reduced to
plain markup and checked against it, so a new invented host, a
reintroduced `defer`, or a reordered `setConfig` fails here regardless of
which published place it turns up in.
`TestEveryWidgetScriptTagIsAccountedFor` is what keeps "which published
place" from silently growing without this file noticing: it scans every
git-tracked text file for an osa-chat-widget.js `<script>` tag and requires
each hit to be a source this file already parses, a docs/**/*.md fenced
block (parsed generically), or a documented dev-harness exception.
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
DOCS_DIR = REPO_ROOT / "docs"

# Repo-relative (posix) paths whose osa-chat-widget.js <script> tag(s) the
# functions above already extract and check. docs/**/*.md is handled
# separately, generically, below (any fenced ```html block), rather than by
# naming each doc page here.
_KNOWN_PARSED_PATHS = frozenset(
    {
        "frontend/index.html",
        ".github/workflows/release.yml",
        "scripts/widget-sri.py",
        "src/api/routers/widget_test.py",
    }
)

# Dev-only harness pages that load the widget by a path relative to their
# own directory: never published as embed guidance, so find_snippet_violations
# has nothing useful to say about them (a relative src is always "allowed"),
# and they are exempted here by name rather than silently unmatched.
_RELATIVE_PATH_HARNESS_ALLOWLIST = {
    "test-streaming.html": (
        "a manual page at the repo root for exercising the widget against a "
        "live backend by hand; loads frontend/osa-chat-widget.js by a "
        "relative path, never published as embed guidance"
    ),
    "frontend/browser-harness/widget-e2e.html": (
        "the widget's own Chrome browser-harness page, driven by "
        "frontend/browser-harness/chrome.js; loads ../osa-chat-widget.js by "
        "a relative path from its own directory, never published as embed "
        "guidance"
    ),
}

# A <script ...osa-chat-widget.js...> tag, literal or HTML-entity-escaped
# (widget_test.py writes &lt;script&gt; as literal text for its copy-paste
# card, and so does frontend/index.html for its display samples).
_SCRIPT_TAG_WIDGET_RE = re.compile(
    r"(?:<|&lt;)script\b[^>]*osa-chat-widget\.js[^>]*(?:>|&gt;)", re.IGNORECASE
)

# Extensions this scan does not try to decode as text; UnicodeDecodeError is
# also caught per-file as a fallback for anything not listed here.
_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".whl",
        ".zip",
        ".pdf",
        ".wasm",
    }
)

# The built runtime bundle: large, generated, and not a place anyone hand-writes
# or reads an embed snippet.
_BUNDLE_PATH = "frontend/osa-runtime.bundle.js"

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
    widget_indices = [
        i for i, e in enumerate(elements) if "osa-chat-widget.js" in (e.attrs.get("src") or "")
    ]

    if not widget_indices:
        return ["no <script src=...osa-chat-widget.js> tag found in the snippet"]

    violations: list[str] = []
    for i in widget_indices:
        src = elements[i].attrs.get("src") or ""
        if not _is_allowed_src(src):
            violations.append(
                f"widget script src {src!r} is not demo.osc.earth, the jsDelivr mirror "
                "(cdn.jsdelivr.net/gh/OpenScience-Collective/osa@...), or a relative path"
            )

    setconfig_indices = [
        i for i, e in enumerate(elements) if e.attrs.get("src") is None and "setConfig(" in e.text
    ]
    for j in setconfig_indices:
        # Safe only if some widget tag before this call, in document order,
        # is synchronous (no defer/async): that is the only ordering that
        # guarantees window.OSAChatWidget already exists by the time this
        # call runs. A deferred/async widget tag has not run yet even if it
        # is textually earlier, and no preceding widget tag at all means
        # OSAChatWidget was never going to exist regardless of defer/async.
        preceding = [i for i in widget_indices if i < j]
        preceding_sync = [
            i
            for i in preceding
            if "defer" not in elements[i].attrs and "async" not in elements[i].attrs
        ]
        if preceding_sync:
            continue
        if not preceding:
            violations.append(
                "an inline setConfig call appears before the widget's <script src> tag "
                "in the document; OSAChatWidget is not defined yet, regardless of defer/async"
            )
        else:
            deferred_via = (
                "defer" if any("defer" in elements[i].attrs for i in preceding) else "async"
            )
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


def _docs_fenced_html_blocks(path: Path) -> list[str]:
    """Every fenced ```html block in a docs/ Markdown file that mentions the widget.

    Plain markdown text, not HTML-entity-escaped, so no ``unescape`` step:
    a docs page writes a real ``<script>`` tag inside the fence, the way a
    reader would copy it.
    """
    source = path.read_text()
    blocks = re.findall(r"```html\n(.*?)```", source, re.DOTALL)
    return [block for block in blocks if "osa-chat-widget.js" in block]


def _tracked_text_files() -> list[str]:
    """Every git-tracked file, repo-relative posix paths, as ``git ls-files`` lists them.

    Real git introspection rather than a filesystem walk, so a file that is
    gitignored (node_modules, build output not committed) is never scanned,
    and a file that IS committed can never be missed by drifting from
    whatever this function's own idea of "the source tree" is.
    """
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=True
    )
    return [line for line in result.stdout.splitlines() if line]


# --- Tripwire: a new place must be seen, not silently missed ---------------


class TestEveryWidgetScriptTagIsAccountedFor:
    """Every osa-chat-widget.js `<script>` tag anywhere in the tree is either
    checked by this file or explicitly, reason-fully exempted.

    `docs/community-widget.md` shipped `https://widget.osc.earth/osa/osa-chat-widget.js`
    (the API edge, which 400s) in #463, and no test here noticed, because
    nothing was scanning for a widget `<script>` tag outside the four sources
    `find_snippet_violations` was already wired up to. This closes that gap
    at the level of "does anything unaccounted-for exist", so the next new
    place is caught even before anyone thinks to add a dedicated test for it.

    Skips `tests/` (this suite's own fixtures include deliberately-broken
    example snippets as plain strings, not published guidance), `node_modules/`
    (never committed, but excluded defensively), the built runtime bundle
    (generated, not hand-written), and anything with a binary-ish extension.
    """

    def test_every_widget_script_reference_is_accounted_for(self) -> None:
        unaccounted: list[str] = []
        checked_docs_pages = 0

        for relpath in _tracked_text_files():
            if relpath.startswith("tests/") or relpath.startswith("node_modules/"):
                continue
            if relpath == _BUNDLE_PATH:
                continue
            path = REPO_ROOT / relpath
            if path.suffix.lower() in _BINARY_SUFFIXES:
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            if not _SCRIPT_TAG_WIDGET_RE.search(text):
                continue

            if relpath in _RELATIVE_PATH_HARNESS_ALLOWLIST:
                continue
            if relpath in _KNOWN_PARSED_PATHS:
                continue
            if relpath.startswith("docs/") and relpath.endswith(".md"):
                blocks = _docs_fenced_html_blocks(path)
                if not blocks:
                    unaccounted.append(
                        f"{relpath}: has an osa-chat-widget.js <script> tag that is not "
                        "inside a fenced ```html block, so nothing parses it"
                    )
                    continue
                checked_docs_pages += 1
                for block in blocks:
                    violations = find_snippet_violations(block)
                    if violations:
                        unaccounted.append(f"{relpath}: {violations}\n\n{block}")
                continue

            unaccounted.append(
                f"{relpath}: a new osa-chat-widget.js <script> tag was found here. Add it to "
                "_KNOWN_PARSED_PATHS in tests/test_frontend/test_embed_snippet_hygiene.py "
                "(with a check that runs find_snippet_violations on it), or to "
                "_RELATIVE_PATH_HARNESS_ALLOWLIST with a reason if it is a dev-only harness "
                "page loading the widget by a relative path."
            )

        assert not unaccounted, "\n\n".join(unaccounted)
        # A vacuous pass (no docs page ever matched) would prove nothing about
        # the docs/ branch above, so require at least one real hit -- this
        # is exactly what community-widget.md's fenced snippet provides today.
        assert checked_docs_pages > 0, (
            "found no docs/**/*.md page with an osa-chat-widget.js snippet"
        )


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

    def test_setconfig_before_the_widget_tag_is_flagged_with_no_defer_at_all(self) -> None:
        """PR #467 review, item 2: order matters independently of defer/async.

        Neither script tag here carries defer or async; the widget script is
        simply placed second. A checker that only looks at defer/async would
        pass this, and a reviewer's swap of frontend/index.html's two
        <script> blocks did exactly that against the previous version of
        this checker.
        """
        violations = find_snippet_violations(
            "<script>OSAChatWidget.setConfig({ communityId: 'hed' });</script>\n"
            '<script src="https://demo.osc.earth/osa-chat-widget.js"></script>'
        )
        assert violations
        assert any("before" in v for v in violations)

    def test_setconfig_after_a_synchronous_widget_tag_is_allowed(self) -> None:
        assert not find_snippet_violations(
            '<script src="https://demo.osc.earth/osa-chat-widget.js"></script>\n'
            "<script>OSAChatWidget.setConfig({ communityId: 'hed' });</script>"
        )

    def test_reordering_the_real_frontend_index_html_snippet_is_caught(self) -> None:
        """The exact mutation a reviewer applied to frontend/index.html and reported
        as passing all 13 tests before this rule existed: swap the two <script>
        blocks so setConfig comes first. Run against the real extracted snippet,
        not a hand-written stand-in.
        """
        real_snippets = _index_html_snippets()
        standard = next(s for s in real_snippets if "demo.osc.earth" in s and "setConfig" in s)
        widget_tag_match = re.search(
            r'<script src="[^"]*osa-chat-widget\.js"[^>]*></script>', standard
        )
        setconfig_match = re.search(
            r"<script>\s*OSAChatWidget\.setConfig.*?</script>", standard, re.DOTALL
        )
        assert widget_tag_match and setconfig_match
        reordered = (
            standard[: widget_tag_match.start()]
            + setconfig_match.group(0)
            + "\n"
            + widget_tag_match.group(0)
            + standard[setconfig_match.end() :]
        )
        assert find_snippet_violations(reordered)
