"""Cross-language drift tests for the widget and worker (Phase 3, issue #363).

Three constants are hand-duplicated across Python, the widget JS, and the
Cloudflare Worker JS, with nothing keeping them in sync. Each is correct as
of this writing, and each can drift silently: a Python-side change (adding
a model, changing a key redaction pattern, adding/removing a BYOK header)
has no compiler or import to catch a JS file that was not updated to match.
The worker case in particular would only surface as a real browser's CORS
preflight rejecting a request; every Python test would stay green.

These parse the JS files as text via regex (there is no JS test harness in
this project, see CLAUDE.md / .rules) and derive the expected values from
the real Python source at test time, the same way
tests/test_deploy/test_deploy_config.py parses deployment files.
"""

import re
import typing
from pathlib import Path

from src.api import security
from src.core.logging import SecureFormatter
from src.core.services.anthropic_llm import OFFERED_MODELS

REPO_ROOT = Path(__file__).resolve().parents[2]
WIDGET_PATH = REPO_ROOT / "frontend" / "osa-chat-widget.js"
WORKER_PATH = REPO_ROOT / "workers" / "osa-worker" / "index.js"


def _widget_source() -> str:
    return WIDGET_PATH.read_text()


def _worker_source() -> str:
    return WORKER_PATH.read_text()


def _extract_default_models(widget_source: str) -> dict[str, str]:
    """Parse the widget's ``DEFAULT_MODELS`` fallback array into an id->label dict."""
    match = re.search(r"const DEFAULT_MODELS = \[(.*?)\];", widget_source, re.DOTALL)
    assert match, "Could not find DEFAULT_MODELS array in osa-chat-widget.js"
    entries = re.findall(r"\{\s*value:\s*'([^']+)'\s*,\s*label:\s*'([^']+)'\s*\}", match.group(1))
    assert entries, "DEFAULT_MODELS array parsed to zero entries"
    return dict(entries)


def _extract_key_pattern(widget_source: str, const_name: str) -> str:
    """Parse the body of a `const NAME = /^.../i;` regex literal in the widget."""
    match = re.search(rf"const {const_name} = /\^(.*?)\$/i;", widget_source)
    assert match, f"Could not find {const_name} in osa-chat-widget.js"
    return match.group(1)


def _expected_byok_headers() -> set[str]:
    """BYOK header names the backend actually resolves into a usable credential.

    Derived from ByokCredential.provider's Literal values (src/api/security.py),
    the type that represents "a BYOK header that was read and turned into a
    real credential"), each mapped back to its APIKeyHeader name via the
    `{provider}_key_header` naming convention used in that module.

    Deliberately excludes X-OpenAI-API-Key: security.py still declares
    openai_key_header and still checks its truthiness in the server-auth
    bypass, but "openai" is not a valid ByokCredential.provider and that
    header's value is never turned into a real credential (there is no
    OpenAI code path any more; see issue #363). It is a header the backend
    no longer meaningfully reads, so the worker correctly omits it, and a
    drift test that required the worker to carry it would be wrong.
    """
    providers = typing.get_args(typing.get_type_hints(security.ByokCredential)["provider"])
    return {getattr(security, f"{provider}_key_header").model.name for provider in providers}


def _cors_allowed_headers(worker_source: str) -> set[str]:
    match = re.search(r"'Access-Control-Allow-Headers':\s*'([^']+)'", worker_source)
    assert match, "Could not find Access-Control-Allow-Headers in workers/osa-worker/index.js"
    return {h.strip() for h in match.group(1).split(",")}


def _worker_byok_headers(worker_source: str) -> set[str]:
    match = re.search(r"const byokHeaders = \[([^\]]+)\];", worker_source)
    assert match, "Could not find byokHeaders array in workers/osa-worker/index.js"
    return set(re.findall(r"'([^']+)'", match.group(1)))


class TestDefaultModelsMatchesOfferedModels:
    """The widget's DEFAULT_MODELS fallback must equal the backend's OFFERED_MODELS.

    DEFAULT_MODELS is what a browser's settings dropdown shows before
    fetchCommunityConfig's offered_models response arrives, and forever
    after for any backend that ever omits the field (see the finding B
    fallback in fetchCommunityConfig). If it drifts from OFFERED_MODELS
    (src/core/services/anthropic_llm.py) -- a model renamed, added, or
    removed on the backend without updating the widget -- a widget user
    could see a stale label, select a model id the backend rejects, or
    never see a newly offered model, and nothing but a real browser
    session would notice.
    """

    def test_default_models_matches_offered_models(self) -> None:
        widget_models = _extract_default_models(_widget_source())
        assert widget_models == OFFERED_MODELS


class TestKeyPatternsMatchBackendRedaction:
    """The widget's BYOK key regexes must match the backend's redaction patterns.

    ANTHROPIC_KEY_PATTERN and OPENROUTER_KEY_PATTERN (osa-chat-widget.js)
    gate which keys the widget's settings modal will accept and save.
    API_KEY_PATTERN (src/core/logging.py) is what the backend uses to
    redact keys from logs. If the widget's patterns drift looser than the
    backend's, a user could save a key shape the backend's own log
    redaction does not recognize, so a real key could be written unredacted
    into logs. If they drift stricter, a real, valid key could be rejected
    by the widget with no way for the user to save it.
    """

    def test_anthropic_pattern_matches_backend_alternative(self) -> None:
        alternatives = SecureFormatter.API_KEY_PATTERN.pattern.split("|")
        anthropic_alt = next(a for a in alternatives if a.startswith("sk-ant-"))
        widget_pattern = _extract_key_pattern(_widget_source(), "ANTHROPIC_KEY_PATTERN")
        assert widget_pattern == anthropic_alt

    def test_openrouter_pattern_matches_backend_alternative(self) -> None:
        alternatives = SecureFormatter.API_KEY_PATTERN.pattern.split("|")
        openrouter_alt = next(a for a in alternatives if a.startswith("sk-or-v1-"))
        widget_pattern = _extract_key_pattern(_widget_source(), "OPENROUTER_KEY_PATTERN")
        assert widget_pattern == openrouter_alt


class TestWidgetAttributeEscaping:
    """Values interpolated into HTML attributes must be escaped, quotes included.

    The widget builds HTML by string concatenation, so any value placed inside
    a quoted attribute has to have its quote characters escaped: an unescaped
    one closes the attribute and everything after it is parsed as further
    attributes, an event handler included. Serializing a text node (the
    ``div.textContent`` trick ``escapeHtml`` is built on) escapes ``&``, ``<``
    and ``>`` but deliberately leaves both quote characters alone, so that
    trick alone is not sufficient and the gap is invisible by inspection.

    This matters most for citations, whose ``title`` attribute carries
    ``cited_text``: a verbatim span of a retrieved community document, which
    is the least trusted input the widget renders.

    Verified in a real DOM (happy-dom) while writing these: before the
    ``replace`` calls in ``escapeHtml``, a ``cited_text`` of
    ``'prefix" onmouseover="..."'`` produced an anchor with a live
    ``onmouseover`` attribute; after, the anchor carries exactly
    href/target/rel/title. There is no JS harness in CI (issue #377), so what
    is enforced here is the source-level invariant rather than the DOM result.
    """

    # Attribute values built from data the widget did not generate itself.
    # Anything else interpolated into an attribute needs a reason listed below.
    _SAFE_UNESCAPED_INTERPOLATIONS = {
        "blockId",  # getCodeBlockId(), an internal "osa-code-N" counter
        "codeId",  # read back from the data-code-id this file just wrote
        "msgIndex",  # array index into messages, a number
        "fb",  # compared against string literals, yields a boolean
    }

    def test_escape_html_escapes_both_quote_characters(self) -> None:
        """``escapeHtml`` must escape ``"`` and ``'`` on top of the textContent pass."""
        widget_source = _widget_source()
        match = re.search(r"function escapeHtml\(text\) \{(.*?)\n  \}", widget_source, re.DOTALL)
        assert match, "Could not find escapeHtml in osa-chat-widget.js"
        body = match.group(1)
        assert "&quot;" in body, "escapeHtml does not escape double quotes"
        assert "&#39;" in body or "&apos;" in body, "escapeHtml does not escape single quotes"

    def test_every_attribute_interpolation_is_escaped(self) -> None:
        """Each value interpolated into a quoted attribute goes through escapeHtml.

        Covers both forms the widget uses: ``attr="' + expr + '"`` inside
        single-quoted concatenation, and ``attr="${expr}"`` inside a template
        literal. The leading identifier of each interpolated expression must be
        ``escapeHtml`` or be listed as safe with its reason.
        """
        widget_source = _widget_source()
        concat = re.findall(r"""=\\?"'\s*\+\s*([A-Za-z_$][\w.$\[\]]*)""", widget_source)
        template = re.findall(r"""=\\?"\$\{\s*([A-Za-z_$][\w.$\[\]]*)""", widget_source)
        found = set(concat) | set(template)
        assert found, "Found no attribute interpolations at all; the patterns above have rotted"

        unescaped = {expr for expr in found if expr != "escapeHtml"}
        assert unescaped <= self._SAFE_UNESCAPED_INTERPOLATIONS, (
            "HTML attribute values interpolated without escapeHtml: "
            f"{sorted(unescaped - self._SAFE_UNESCAPED_INTERPOLATIONS)}. "
            "Wrap them in escapeHtml, or add them to _SAFE_UNESCAPED_INTERPOLATIONS "
            "with the reason they cannot carry a quote character."
        )


class TestWorkerByokHeaderDrift:
    """The worker's CORS allow-list and BYOK forwarding list must match the backend.

    workers/osa-worker/index.js hand-lists which headers a browser preflight
    may send (Access-Control-Allow-Headers) and which headers get forwarded
    to the backend (byokHeaders). src/api/security.py is the source of truth
    for which BYOK headers the backend actually resolves into a credential
    (ByokCredential.provider). If a header is added there but not to the
    worker's lists, a real browser's CORS preflight silently strips it and
    that BYOK path is unreachable in production even though every Python
    test (which never goes through the worker) stays green. If a stale
    header lingers in the worker's lists after the backend stops reading
    it, that is dead configuration that should be caught before someone
    debugs a "why does this header do nothing" question.
    """

    def test_cors_allows_every_byok_credential_header(self) -> None:
        expected = _expected_byok_headers()
        cors_headers = _cors_allowed_headers(_worker_source())
        missing = expected - cors_headers
        assert not missing, f"Access-Control-Allow-Headers is missing BYOK headers: {missing}"

    def test_worker_forwards_every_byok_credential_header(self) -> None:
        expected = _expected_byok_headers()
        forwarded = _worker_byok_headers(_worker_source())
        missing = expected - forwarded
        assert not missing, f"byokHeaders does not forward BYOK headers: {missing}"

    def test_no_stale_key_style_headers_in_worker_lists(self) -> None:
        """Neither list may carry a credential-shaped header the backend no longer reads.

        Filters both lists down to header names shaped like a per-provider
        API key header (ending in "Key", excluding the server's own
        X-API-Key, which is a different header declared outside the BYOK
        group in security.py) and requires that filtered set to be exactly
        the set of headers the backend still resolves into a credential.
        """
        server_key_header = security.api_key_header.model.name
        expected = _expected_byok_headers()

        worker_source = _worker_source()
        for actual in (_cors_allowed_headers(worker_source), _worker_byok_headers(worker_source)):
            key_style = {h for h in actual if h.endswith("Key") and h != server_key_header}
            assert key_style == expected, (
                f"Found stale or unexpected credential-shaped headers: {key_style - expected}"
            )
