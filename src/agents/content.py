"""Helpers for extracting text and citations from Anthropic's block-list content.

An assistant message's ``content`` is not always a plain string. Whenever
extended thinking is enabled, or whenever tools are bound to the model,
langchain-anthropic stops coercing content to a string: both the final
message and every streamed chunk instead carry a list of typed content
blocks, e.g. ``{"type": "text", "text": "..."}`` alongside
``{"type": "thinking", "thinking": "...", "signature": "..."}``.

Treating that list as a string (``str(content)``) puts a stringified Python
list into the answer; concatenating it onto a string accumulator
(``full_response += chunk``) raises ``TypeError``. These helpers are the one
place that understands the block-list shape, so call sites do not each need
to re-derive it -- and so streamed reasoning never leaks to a client: the
product decision is that clients see a content-free ``thinking`` signal
(the model is working), never the reasoning text itself.

A text block returned when the request used citable ``search_result`` tool
content (see ``src/tools/citations.py``) can also carry a ``citations`` list,
e.g. ``{"type": "search_result_location", "cited_text": ..., "source": ...,
"title": ..., "search_result_index": int, "start_block_index": int,
"end_block_index": int}``. ``classify_content_blocks`` surfaces those
alongside the block's text, and ``CitationTracker`` assigns each unique
``source`` a stable ``[n]`` marker (in order of first appearance) so callers
can build an answer with inline citation markers identically whether the
content came from a single final message or a stream of chunks.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

logger = logging.getLogger(__name__)

# Block "type" values that carry reasoning rather than answer text.
# `redacted_thinking` is Anthropic's encrypted-reasoning variant (returned
# when a thinking block is flagged, e.g. by safety systems); it carries no
# usable text either way, so it is classified the same as `thinking`.
_THINKING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})

# Non-text block types with their own dedicated handling elsewhere (tool-call
# metadata streamed via separate events), so silently contributing nothing
# here is expected, not a sign of a problem.
_KNOWN_NON_TEXT_BLOCK_TYPES = frozenset({"tool_use"})

BlockKind = Literal["text", "thinking"]


class ContentBlock(NamedTuple):
    """One classified content block: its kind, text, and any citations.

    A plain 3-tuple (kind, text, citations), so existing call sites that
    unpack ``classify_content_blocks()`` as pairs only need to add the third
    element; new call sites can use the named fields instead.
    """

    kind: BlockKind
    text: str
    citations: list[dict[str, Any]]


def extract_text(content: str | list[Any]) -> str:
    """Return only the answer text of a message's ``content``.

    The text-side counterpart of :func:`extract_citations`; both flatten one
    kind of signal out of :func:`classify_content_blocks`. It is deliberately
    not the answer-assembly path: since the API layer began interleaving
    ``[n]`` markers with the text (``_build_answer_with_citations`` in
    ``src/api/routers/community.py``), this function has no production
    caller, because an answer assembled through it would carry citations no
    client could place. Use it when plain text really is what you want, such
    as inspecting what a response said.

    Args:
        content: A message's ``content``: a plain string (the common case,
            when thinking is off and no tools are bound), or a list of
            content blocks (thinking enabled, or tools bound; see the
            module docstring).

    Returns:
        The concatenated text of every ``"text"`` block, in order. Thinking
        blocks and any other non-text block (e.g. ``tool_use``) contribute
        nothing: reasoning is never surfaced as answer text, and non-text
        blocks are handled by their own dedicated paths (tool-call
        metadata), not the answer string.
    """
    if isinstance(content, str):
        return content
    return "".join(block.text for block in classify_content_blocks(content) if block.kind == "text")


def classify_content_blocks(content: str | list[Any]) -> list[ContentBlock]:
    """Classify a message's or streamed chunk's content into blocks.

    Args:
        content: A message's or streamed chunk's ``content``.

    Returns:
        A list of :class:`ContentBlock` in block order, skipping blocks with
        no useful signal at all (an empty text block with no citations, and
        non-text/non-thinking blocks such as ``tool_use``, which are already
        handled by their own dedicated event streams). ``"thinking"`` blocks
        always carry empty text and no citations: callers use that kind only
        as a liveness signal (e.g. an SSE ``thinking`` event with no
        payload), never as text. A text block is still surfaced when it
        carries citations but no text of its own -- this is exactly the
        shape of a streamed citation-delta chunk (see the module docstring).
        A plain string is returned as a single ``ContentBlock("text",
        content, [])`` (or ``[]`` for an empty string), matching the
        pre-citations shape.
    """
    return [block for block, _index in classify_content_blocks_with_indices(content)]


def classify_content_blocks_with_indices(
    content: str | list[Any],
) -> list[tuple[ContentBlock, int | None]]:
    """Classify blocks while retaining Anthropic's streamed block index.

    Anthropic sends citation deltas on the active text block. Most streams
    deliver the text before its citation, but a provider adapter can surface
    the citation-only delta first. Keeping the block index lets the response
    assembler hold that citation until the matching text arrives without
    changing the three-field ``ContentBlock`` tuple used by existing callers.
    """
    if isinstance(content, str):
        return [(ContentBlock("text", content, []), None)] if content else []

    blocks: list[tuple[ContentBlock, int | None]] = []
    warned_block_types: set[Any] = set()
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        block_index = block.get("index")
        if not isinstance(block_index, int):
            block_index = None
        if block_type == "text":
            text = block.get("text", "")
            citations = block.get("citations") or []
            if text or citations:
                blocks.append((ContentBlock("text", text, citations), block_index))
        elif block_type in _THINKING_BLOCK_TYPES:
            blocks.append((ContentBlock("thinking", "", []), block_index))
        elif block_type in _KNOWN_NON_TEXT_BLOCK_TYPES:
            # tool_use, etc. -- intentionally not surfaced here; handled by
            # their own dedicated events elsewhere.
            pass
        elif block_type not in warned_block_types:
            # A future langchain-anthropic block type that carries answer
            # text would otherwise truncate answers with no error -- the
            # same invisible-failure class the caching layer already guards
            # against. Dedupe per call so one odd chunk (or a whole streamed
            # response full of them) cannot flood the log.
            warned_block_types.add(block_type)
            logger.warning(
                "classify_content_blocks: unrecognized content block type %r; "
                "any text it carries is dropped, not surfaced as answer text.",
                block_type,
            )
    return blocks


def extract_citations(content: str | list[Any]) -> list[dict[str, Any]]:
    """Return every citation attached to any text block in ``content``, in order.

    A low-level primitive: it does not number markers or dedupe by source
    (see :class:`CitationTracker` for that), it just flattens the raw
    ``search_result_location`` dicts the API attaches to text blocks, in
    block order and then in each block's own order. Useful on its own for
    inspecting what a response cited, and as the layer tested directly
    against a recorded real response payload.

    Args:
        content: A message's or streamed chunk's ``content`` (see
            ``classify_content_blocks``).

    Returns:
        The ``citations`` entries from every text block, concatenated. A
        plain string, or content with no cited text block, returns ``[]``.
    """
    citations: list[dict[str, Any]] = []
    for block in classify_content_blocks(content):
        if block.kind == "text":
            citations.extend(block.citations)
    return citations


@dataclass(frozen=True)
class CitationMark:
    """A citation source, with the stable ``[n]`` marker assigned to it."""

    marker: int
    source: str
    title: str
    cited_text: str


class CitationTracker:
    """Assigns stable ``[n]`` markers to citation sources across a response.

    One marker per unique ``source``, assigned in order of first
    appearance, so three claims drawn from the same document all render
    ``[1]``. The same tracker instance is used across an entire response
    (streamed or not) so numbering is identical between the two paths.
    """

    def __init__(self) -> None:
        self._marker_by_source: dict[str, int] = {}
        self._marks: list[CitationMark] = []

    def _record(self, citation: dict[str, Any]) -> tuple[CitationMark, bool] | None:
        """Record one citation. Returns (its mark, is_new), or None if unusable."""
        source = citation.get("source")
        if not source:
            # Nothing to attribute the claim to, so no marker can be shown.
            # Logged because the model did mean to cite something here: a tool
            # that starts emitting blank sources would otherwise just quietly
            # produce fewer citations than the answer actually relies on.
            logger.warning("Dropping a citation with no source: %s", citation)
            return None
        existing_marker = self._marker_by_source.get(source)
        if existing_marker is not None:
            return self._marks[existing_marker - 1], False
        marker = len(self._marker_by_source) + 1
        self._marker_by_source[source] = marker
        mark = CitationMark(
            marker=marker,
            source=source,
            title=citation.get("title", ""),
            cited_text=citation.get("cited_text", ""),
        )
        self._marks.append(mark)
        return mark, True

    def record_block(self, citations: list[dict[str, Any]]) -> tuple[str, list[CitationMark]]:
        """Record every citation carried by one content block, in order.

        Args:
            citations: The block's raw citation dicts (a text block's
                ``citations`` field, or a streamed citation-delta chunk's).

        Returns:
            A tuple of:
              - The inline marker text for this block (e.g. ``"[1][2]"``),
                with each unique marker rendered once, in the order its
                source first appears within this block. Appending this
                directly after the block's own text places the marker at
                the end of the span it supports.
              - The list of newly discovered marks in this call (sources
                not seen in any earlier call on this tracker), for callers
                that need to announce only what is new (e.g. a streaming
                ``citation`` SSE event per newly seen source).
        """
        marker_order: list[int] = []
        seen_markers: set[int] = set()
        new_marks: list[CitationMark] = []
        for citation in citations:
            recorded = self._record(citation)
            if recorded is None:
                continue
            mark, is_new = recorded
            if is_new:
                new_marks.append(mark)
            if mark.marker not in seen_markers:
                seen_markers.add(mark.marker)
                marker_order.append(mark.marker)
        marker_text = "".join(f"[{n}]" for n in marker_order)
        return marker_text, new_marks

    @property
    def marks(self) -> list[CitationMark]:
        """Every distinct citation mark recorded so far, in marker order."""
        return list(self._marks)


class CitationAssembler:
    """Place citation markers after the text block they annotate.

    A citation delta belongs to Anthropic's current text block. In the normal
    order, the block carries text first and the marker can be emitted
    immediately. If a framework adapter exposes the citation-only delta first,
    keep it pending for that block index and emit it after the block's text.
    Citations arriving after text on an already-seen block retain the existing
    behavior and are emitted at that point.
    """

    def __init__(self) -> None:
        self._tracker = CitationTracker()
        self._text_seen: set[int | None] = set()
        self._pending: dict[int | None, list[dict[str, Any]]] = {}

    def add_block(
        self,
        block: ContentBlock,
        block_index: int | None = None,
    ) -> tuple[str, list[CitationMark]]:
        """Add one classified block and return marker text plus new marks."""
        if block.kind != "text":
            return "", []

        citations: list[dict[str, Any]] = []
        if block.text:
            # A citation-only delta that preceded this block is attached to
            # this same block and must be rendered after its text.
            citations.extend(self._pending.pop(block_index, []))
            citations.extend(block.citations)
            self._text_seen.add(block_index)
        elif block.citations:
            if block_index in self._text_seen:
                # The usual streaming shape: text delta(s), then the
                # citations_delta for that same text block.
                citations.extend(block.citations)
            else:
                # The adapter exposed the citation before the text for this
                # block. Do not create a leading marker; wait for its text.
                self._pending.setdefault(block_index, []).extend(block.citations)

        return self._tracker.record_block(citations)

    def finish_model_run(self) -> int:
        """Discard unresolved block-local citations before the next model run.

        Anthropic block indices are scoped to one model invocation. The
        response-wide tracker must retain its marker numbering, but pending
        and seen-block state must not leak into a later tool-loop invocation
        where the provider can reuse the same indices.

        Returns:
            The number of citation payloads discarded because their matching
            text block never arrived.
        """
        unresolved = sum(len(citations) for citations in self._pending.values())
        if unresolved:
            logger.warning(
                "Dropping %d citation(s) without a matching text block at model-run end",
                unresolved,
            )
        self._pending.clear()
        self._text_seen.clear()
        return unresolved

    @property
    def marks(self) -> list[CitationMark]:
        """Every citation that has been attached to emitted answer text."""
        return self._tracker.marks
