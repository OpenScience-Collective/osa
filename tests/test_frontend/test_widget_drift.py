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
from src.core.config.community import (
    DEFAULT_LAUNCHER_OFFSET,
    DEFAULT_LAUNCHER_OPEN_SIZE,
    DEFAULT_LAUNCHER_SIZE,
    LAUNCHER_GEOMETRY_FIELDS,
    LAUNCHER_OFFSET_MAX,
    LAUNCHER_OPEN_RATIO,
    LAUNCHER_SIZE_MAX,
    LAUNCHER_SIZE_MIN,
    WidgetConfig,
)
from src.core.logging import SecureFormatter
from src.core.services.anthropic_llm import MODEL_ALIASES, OFFERED_MODELS
from src.core.services.anthropic_models import BEDROCK_MODELS, SUGGESTED_MODELS

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

    Deliberately excludes X-OpenAI-API-Key: there is no OpenAI code path any
    more (issue #363), so "openai" is not a valid ByokCredential.provider and
    the backend no longer reads that header at all (issue #393 removed the last
    place it did). The worker correctly omits it, and a drift test that
    required the worker to carry it would be wrong.
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


def _extract_platform_only_models(widget_source: str) -> set[str]:
    """Parse the widget's ``PLATFORM_ONLY_MODELS`` array into a set of ids."""
    match = re.search(r"const PLATFORM_ONLY_MODELS = \[(.*?)\];", widget_source, re.DOTALL)
    assert match, "Could not find PLATFORM_ONLY_MODELS in osa-chat-widget.js"
    entries = re.findall(r"'([^']+)'", match.group(1))
    assert entries, "PLATFORM_ONLY_MODELS parsed to zero entries"
    return set(entries)


class TestPlatformOnlyModelsMatchBackend:
    """The widget's fallback for "which models refuse the reader's own Anthropic key"
    must be the backend's Bedrock-served models, or the fallback menu offers pairings
    the server answers with a 403."""

    def test_platform_only_models_are_the_bedrock_models(self) -> None:
        assert _extract_platform_only_models(_widget_source()) == set(BEDROCK_MODELS)


def _extract_suggested_models(widget_source: str) -> tuple[str, ...]:
    """Parse the widget's ``SUGGESTED_MODELS`` array into a tuple of ids, in order."""
    match = re.search(r"const SUGGESTED_MODELS = \[(.*?)\];", widget_source, re.DOTALL)
    assert match, "Could not find SUGGESTED_MODELS in osa-chat-widget.js"
    entries = re.findall(r"'([^']+)'", match.group(1))
    assert entries, "SUGGESTED_MODELS parsed to zero entries"
    return tuple(entries)


class TestSuggestedModelsMatchBackend:
    """The model a failed request names comes from the server when it can, and from the
    widget when the widget timed out on its own; they have to name the same models in the
    same order, or the same failure would offer a different model by where it was noticed."""

    def test_the_widget_suggests_what_the_backend_does(self) -> None:
        assert _extract_suggested_models(_widget_source()) == SUGGESTED_MODELS


def _extract_retired_model_ids(widget_source: str) -> dict[str, str]:
    """Parse the widget's ``RETIRED_MODEL_IDS`` map into an old-id -> new-id dict."""
    match = re.search(r"const RETIRED_MODEL_IDS = \{(.*?)\};", widget_source, re.DOTALL)
    assert match, "Could not find RETIRED_MODEL_IDS in osa-chat-widget.js"
    entries = re.findall(r"'([^']+)'\s*:\s*'([^']+)'", match.group(1))
    assert entries, "RETIRED_MODEL_IDS parsed to zero entries"
    return dict(entries)


class TestRetiredModelIdsMatchBackendAliases:
    """A saved widget setting naming a retired model is moved to its replacement.

    The backend keeps resolving a retired id through MODEL_ALIASES, but the
    settings dropdown lists only offered models, so a stale saved id would
    show as "Custom". RETIRED_MODEL_IDS is the widget's own copy of those
    aliases, and a wrong target would move users to a model the backend does
    not resolve the same way.
    """

    def test_every_retired_id_maps_where_the_backend_resolves_it(self) -> None:
        for retired, replacement in _extract_retired_model_ids(_widget_source()).items():
            assert MODEL_ALIASES[retired] == replacement

    def test_the_widget_knows_every_alias_the_backend_does(self) -> None:
        """An alias the widget lacks shows as "Custom" and cannot be saved without a key.

        The server accepts every alias in ``MODEL_ALIASES``, with the reader's own
        Anthropic key too, so the widget's table is that table, not a subset of it.
        """
        assert _extract_retired_model_ids(_widget_source()) == MODEL_ALIASES

    def test_every_replacement_is_offered(self) -> None:
        for replacement in _extract_retired_model_ids(_widget_source()).values():
            assert replacement in OFFERED_MODELS

    def test_no_retired_id_is_still_offered(self) -> None:
        for retired in _extract_retired_model_ids(_widget_source()):
            assert retired not in OFFERED_MODELS


def _extract_launcher_limits(widget_source: str) -> dict[str, float]:
    """Parse the widget's ``LAUNCHER_LIMITS`` object into a name -> number dict."""
    match = re.search(r"const LAUNCHER_LIMITS = \{(.*?)\};", widget_source, re.DOTALL)
    assert match, "Could not find LAUNCHER_LIMITS in osa-chat-widget.js"
    entries = re.findall(r"(\w+):\s*([0-9.]+)", match.group(1))
    assert entries, "LAUNCHER_LIMITS parsed to zero entries"
    return {name: float(value) for name, value in entries}


def _extract_launcher_geometry_keys(widget_source: str) -> list[tuple[str, str]]:
    """Parse the widget's ``LAUNCHER_GEOMETRY_KEYS`` pairs (community field, CONFIG key)."""
    match = re.search(r"const LAUNCHER_GEOMETRY_KEYS = \[(.*?)\];", widget_source, re.DOTALL)
    assert match, "Could not find LAUNCHER_GEOMETRY_KEYS in osa-chat-widget.js"
    pairs = re.findall(r"\['(\w+)',\s*'(\w+)'\]", match.group(1))
    assert pairs, "LAUNCHER_GEOMETRY_KEYS parsed to zero entries"
    return pairs


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(word.title() for word in rest)


class TestLauncherGeometryMatchesBackend:
    """The widget's launcher limits must be the server's (#553).

    The server refuses a launcher size or offset outside its ranges when it loads a
    community; the widget checks the same values for a page's ``setConfig`` and for a
    config remembered from before the limits last changed. If the ranges drifted
    apart, a value one accepts would be silently dropped by the other, and the
    launcher would not be where the community's config puts it.
    """

    def test_limits_and_defaults_are_the_servers(self) -> None:
        limits = _extract_launcher_limits(_widget_source())
        assert limits["sizeMin"] == LAUNCHER_SIZE_MIN
        assert limits["sizeMax"] == LAUNCHER_SIZE_MAX
        assert limits["offsetMax"] == LAUNCHER_OFFSET_MAX
        assert limits["bubbleSize"] == DEFAULT_LAUNCHER_SIZE["bubble"]
        assert limits["capsuleSize"] == DEFAULT_LAUNCHER_SIZE["capsule"]

    def test_open_ratio_is_the_servers(self) -> None:
        """The widget alone derives an open size from a closed one; the ratio is the
        number the docs and the server's docstring give."""
        assert _extract_launcher_limits(_widget_source())["openRatio"] == LAUNCHER_OPEN_RATIO

    def test_stylesheet_defaults_are_the_defaults(self) -> None:
        """The stylesheet's fallbacks are what an unconfigured launcher is drawn at, and
        the reason a community that sets nothing is drawn as it always was: the closed
        and open sizes of the bubble and the capsule, the 58 / 46 scale, and the 20px
        offsets, on a desktop and at 600px wide and narrower."""
        source = _widget_source()

        def sizes(pattern: str, where: str) -> tuple[int, int]:
            match = re.search(pattern, source)
            assert match, f"Could not find the {where} launcher sizes in the stylesheet"
            return int(match.group(1)), int(match.group(2))

        bubble = sizes(
            r"--osa-closed: var\(--osa-size-closed, (\d+)px\);\s*"
            r"--osa-open: var\(--osa-size-open, (\d+)px\);",
            "bubble",
        )
        capsule = sizes(
            r"\.osa-chat-widget\.osa-capsule \{\s*"
            r"--osa-closed: var\(--osa-size-closed, (\d+)px\);\s*"
            r"--osa-open: var\(--osa-size-open, (\d+)px\);",
            "capsule",
        )
        assert bubble == (DEFAULT_LAUNCHER_SIZE["bubble"], DEFAULT_LAUNCHER_OPEN_SIZE["bubble"])
        assert capsule == (DEFAULT_LAUNCHER_SIZE["capsule"], DEFAULT_LAUNCHER_OPEN_SIZE["capsule"])

        scale = re.search(r"--osa-scale: var\(--osa-closed-scale, calc\((\d+) / (\d+)\)\);", source)
        assert scale, "Could not find the capsule's default scale in the stylesheet"
        assert (int(scale.group(1)), int(scale.group(2))) == capsule

        offsets = re.findall(
            r"var\(--osa-edge-[xy](?:-narrow)?, (?:var\(--osa-edge-[xy], )?(\d+)px", source
        )
        assert len(offsets) == 4, (
            f"Expected four offset fallbacks in the stylesheet, found {offsets}"
        )
        assert {int(offset) for offset in offsets} == {DEFAULT_LAUNCHER_OFFSET}

    def test_widget_reads_every_field_the_server_sends(self) -> None:
        """Each launcher field the server can send is one the widget reads, under the
        camelCase name a page passes to setConfig."""
        pairs = _extract_launcher_geometry_keys(_widget_source())
        server_fields = {"launcher_position", *LAUNCHER_GEOMETRY_FIELDS}
        assert {field for field, _ in pairs} == server_fields
        assert all(key == _camel(field) for field, key in pairs)

    def test_widget_positions_are_the_servers(self) -> None:
        match = re.search(r"const LAUNCHER_POSITIONS = \[(.*?)\];", _widget_source())
        assert match, "Could not find LAUNCHER_POSITIONS in osa-chat-widget.js"
        widget_positions = set(re.findall(r"'([^']+)'", match.group(1)))
        assert widget_positions == set(
            typing.get_args(WidgetConfig.model_fields["launcher_position"].annotation)
        )


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
    href/target/rel/title. CI now runs frontend/test-citation-markers.js and
    frontend/test-streaming.js with Bun (see the frontend-tests job), but
    against a minimal ``document`` stub, not a real DOM like happy-dom; the
    happy-dom verification above was a one-time manual check. So what is
    enforced here, continuously, is the source-level invariant rather than
    an actual DOM result.
    """

    # Attribute values built from data the widget did not generate itself.
    # Anything else interpolated into an attribute needs a reason listed below.
    _SAFE_UNESCAPED_INTERPOLATIONS = {
        "blockId",  # getCodeBlockId(), an internal "osa-code-N" counter
        "codeId",  # read back from the data-code-id this file just wrote
        "msgIndex",  # array index into messages, a number
        "runIndex",  # array index into one reply's executions, a number
        "fb",  # compared against string literals, yields a boolean
        "progress.step",  # onRuntimeProgress only sets this from Number.isInteger checks
        "progress.steps",  # same guard; both are null or a positive integer, never a string
        "EXECUTION_FIELD_LIMITS.code",  # a fixed numeric constant, never a string
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
