"""Tagged-source citations, for models with no native ``search_result`` citations.

Claude attaches an inline citation to a claim when the claim came from a
``search_result`` block (see ``src/tools/citations.py``); the API layer turns
those into the numbered ``[n]`` markers and source list the widget shows. The
models served from Amazon Bedrock reject that block outright ("This model
doesn't support the searchResult field"), and OpenRouter's OpenAI-style chat API
has no such block at all, so this module gives them the same result by
convention, at the model boundary:

1. **Out.** Before a request, each ``search_result`` block in a tool result is
   rewritten as plain text that opens with a tag, ``[src:3] Title``. Tags are
   numbered by order of first appearance across the whole conversation, so the
   same source keeps its tag from one model call to the next and the prompt
   prefix stays byte-stable for prompt caching. An assistant turn that carried
   citations is shown to the model with its tags restored, so the model keeps
   seeing its own format.
2. **In.** The system prompt (``CITATION_INSTRUCTION``) asks the model to write
   the tag after each claim it draws from a source. On the way back, ``[src:N]``
   markers are removed from the text and become ``citations`` on the text block,
   in the exact shape the Anthropic path produces, so everything downstream
   (``CitationAssembler``, the SSE ``citation`` events, the ``[n]`` placement)
   runs unchanged.

What this is not: a model that cites by convention can cite the wrong source, so
a tag that names no source in the conversation is dropped rather than shown, and
``cited_text`` is the passage of the source that best matches the claim, not a
span the provider vouches for. Claude's native citations are the stronger
guarantee, and the Anthropic path is unchanged.
"""

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeGuard

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk

from src.tools.citations import truncate

logger = logging.getLogger(__name__)

#: Longest ``cited_text`` handed to the client, in characters.
CITED_TEXT_LIMIT = 300

#: The system-prompt section that asks a model to write tags. It is the other half
#: of the syntax ``MARKER_PATTERN`` parses, kept here so the two cannot drift.
CITATION_INSTRUCTION = (
    "## Citing Sources\n\n"
    "This section replaces any earlier instruction to link to your sources. Retrieved "
    "documents, discussions, FAQ entries, forum posts and papers arrive tagged like "
    "`[src:3] Title`. After each sentence that states a fact drawn from one, write its "
    "tag immediately after the sentence's final punctuation, for example: `HED tags are "
    "assembled from a schema.[src:3]`. Write `[src:1][src:2]` when a claim rests on "
    "several sources. Use only tags that appear in the tool results and never invent "
    "one. Do not write a markdown link or a URL for these sources, and never write a bare "
    "`[3]`: the reader's screen turns each tag into a numbered, clickable citation. A "
    "fact that did not come from a tool result gets no tag. If the tool results do not "
    "answer the question, say so and cite nothing."
)

#: Horizontal whitespace a model may leave before a tag; it goes with the tag.
_HSPACE = " \t  "

#: The most whitespace a tag takes with it, and so the most held back in case one
#: follows. A longer run is released as text: nothing legitimate pads a tag with that
#: much, and the bound keeps a whitespace loop from stalling the event loop (an
#: unbounded ``[ ]*`` before the tag made ``re.search`` quadratic in the run's length).
_HELD_WHITESPACE_LIMIT = 64

#: A complete tag, with the horizontal whitespace before it. Tolerates the small
#: variations models produce: ``[src: 3]``, ``[src:1,2]``, ``[src:1, src:2]``.
MARKER_PATTERN = re.compile(
    rf"[{_HSPACE}]{{0,{_HELD_WHITESPACE_LIMIT}}}"
    r"\[\s*src\s*:\s*(\d+(?:\s*[,;]\s*(?:src\s*:\s*)?\d+)*)\s*\]",
    re.IGNORECASE,
)

#: What a tag can look like before it is finished. Text ending this way is held
#: back until the next chunk shows whether it becomes a tag.
_PARTIAL_MARKER = re.compile(r"\[\s*(?:s(?:r(?:c(?:\s*(?::[^\]]*)?)?)?)?)?", re.IGNORECASE)
_PARTIAL_MARKER_LIMIT = 48

_WORD = re.compile(r"[a-z0-9]{4,}")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class TaggedSource:
    """One retrieved source, as the model sees it."""

    tag: int
    source: str
    title: str
    texts: list[str] = field(default_factory=list)


class SourceRegistry:
    """The sources in a conversation, numbered by first appearance."""

    def __init__(self) -> None:
        """Start with no sources."""
        self._by_source: dict[str, TaggedSource] = {}
        self._by_tag: dict[int, TaggedSource] = {}

    def add(self, source: str, title: str, text: str) -> TaggedSource:
        """Register a source, or add another passage to one already registered.

        Two results from the same page share a tag, matching how the Anthropic
        path gives one ``[n]`` marker per distinct source.
        """
        existing = self._by_source.get(source)
        if existing is not None:
            if text and text not in existing.texts:
                existing.texts.append(text)
            return existing
        entry = TaggedSource(tag=len(self._by_source) + 1, source=source, title=title)
        if text:
            entry.texts.append(text)
        self._by_source[source] = entry
        self._by_tag[entry.tag] = entry
        return entry

    def by_source(self, source: str) -> TaggedSource | None:
        """The source registered under ``source``, if any."""
        return self._by_source.get(source)

    def by_tag(self, tag: int) -> TaggedSource | None:
        """The source that carries ``tag``, if any."""
        return self._by_tag.get(tag)

    def __len__(self) -> int:
        """Number of distinct sources."""
        return len(self._by_source)

    def citation(self, tag: int, claim: str) -> dict[str, Any]:
        """Build the citation dict for ``tag`` in the shape the API layer reads.

        The keys are those of Anthropic's ``search_result_location`` citation,
        which ``CitationTracker`` reads (``source``, ``title``, ``cited_text``).

        Args:
            tag: A tag present in the registry.
            claim: The text the tag supports, used to pick the closest passage.
        """
        entry = self._by_tag[tag]
        return {
            "type": "search_result_location",
            "source": entry.source,
            "title": entry.title,
            "cited_text": best_passage(claim, entry.texts),
            "search_result_index": tag - 1,
            "start_block_index": 0,
            "end_block_index": 1,
        }


def best_passage(claim: str, texts: Iterable[str], limit: int = CITED_TEXT_LIMIT) -> str:
    """The sentence of ``texts`` that shares the most words with ``claim``.

    A stand-in for the exact span Claude's native citations return. Words of four
    or more letters are compared, so common short words do not decide the match.
    With no overlap the first sentence stands in, which at least opens the source.
    """
    claim_words = set(_WORD.findall(claim.lower()))
    best = ""
    best_score = -1
    for text in texts:
        for sentence in _SENTENCE_SPLIT.split(text):
            sentence = sentence.strip()
            if not sentence:
                continue
            score = len(claim_words & set(_WORD.findall(sentence.lower())))
            if score > best_score:
                best, best_score = sentence, score
    return truncate(best, limit)


def _is_search_result(block: Any) -> TypeGuard[dict[str, Any]]:
    return isinstance(block, dict) and block.get("type") == "search_result"


def _search_result_text(block: dict[str, Any]) -> str:
    parts = block.get("content") or []
    return "\n".join(part["text"] for part in parts if isinstance(part, dict) and part.get("text"))


#: A tag as a document might spell it: ASCII or fullwidth brackets and colon.
_TAG_LOOKALIKE = re.compile(r"[\[［](\s*src\s*[:：][^\]\[］\n]{0,40})[\]］]", re.IGNORECASE)
_TAG_OPENING = re.compile(r"[\[［](?=\s*src\s*[:：])", re.IGNORECASE)


def _defang(text: str) -> str:
    """Make text that reads like a citation tag stop reading like one.

    A retrieved document is untrusted text, and the tags that number the sources
    are plain text too: a forum post that contains ``[src:1] HED specification``
    on a line of its own, with a ``Source:`` line under it, is indistinguishable
    from the header this module writes, and the model may cite the post under the
    specification's tag. Native ``search_result`` blocks cannot be forged this way
    because their boundaries are structural. Here the boundary is kept by never
    letting a document write a tag: ``[src:1]`` in a document becomes ``(src:1)``.
    """
    return _TAG_OPENING.sub("(", _TAG_LOOKALIKE.sub(r"(\1)", text))


def _one_line(text: str) -> str:
    """A heading field: defanged, on one line."""
    return " ".join(_defang(text).split())


def _render_tagged(entry: TaggedSource, text: str) -> str:
    heading = f"[src:{entry.tag}] {_one_line(entry.title)}".rstrip()
    reminder = f"(End of [src:{entry.tag}]. Cite a claim drawn from it by writing [src:{entry.tag}] after the sentence.)"
    return f"{heading}\nSource: {_one_line(entry.source)}\n{_defang(text)}\n\n{reminder}"


def _restore_tags(block: dict[str, Any], registry: SourceRegistry) -> dict[str, Any]:
    """Turn an assistant text block's ``citations`` back into tags after its text."""
    tags: list[int] = []
    for citation in block.get("citations") or []:
        entry = registry.by_source(citation.get("source", ""))
        if entry is not None and entry.tag not in tags:
            tags.append(entry.tag)
    rebuilt = {key: value for key, value in block.items() if key != "citations"}
    rebuilt["text"] = block.get("text", "") + "".join(f"[src:{tag}]" for tag in tags)
    return rebuilt


def _is_cited_text_block(block: Any) -> TypeGuard[dict[str, Any]]:
    return isinstance(block, dict) and block.get("type") == "text" and bool(block.get("citations"))


def _tag_tool_message(message: ToolMessage, registry: SourceRegistry) -> ToolMessage:
    """Rewrite the ``search_result`` blocks of one tool result as tagged text."""
    if not isinstance(message.content, list):
        return message
    if not any(_is_search_result(block) for block in message.content):
        return message
    rewritten: list[Any] = []
    for block in message.content:
        entry = registry.by_source(block.get("source", "")) if _is_search_result(block) else None
        if entry is None or not _is_search_result(block):
            rewritten.append(block)
        else:
            rewritten.append(
                {"type": "text", "text": _render_tagged(entry, _search_result_text(block))}
            )
    return message.model_copy(update={"content": rewritten})


def _restore_ai_message(message: AIMessage, registry: SourceRegistry) -> AIMessage:
    """Show an earlier reply to the model with the tags it wrote, not its citations."""
    if not isinstance(message.content, list):
        return message
    if not any(_is_cited_text_block(block) for block in message.content):
        return message
    rewritten = [
        _restore_tags(block, registry) if _is_cited_text_block(block) else block
        for block in message.content
    ]
    return message.model_copy(update={"content": rewritten})


def prepare_messages(messages: Sequence[BaseMessage]) -> tuple[list[BaseMessage], SourceRegistry]:
    """Rewrite a conversation for a model that cannot take ``search_result`` blocks.

    Args:
        messages: The conversation, as the graph holds it.

    Returns:
        The messages to send (new objects where anything changed; the caller's
        messages are never mutated) and the registry that numbers the sources,
        which is needed to read the model's reply.
    """
    registry = SourceRegistry()
    for message in messages:
        if isinstance(message, ToolMessage) and isinstance(message.content, list):
            for block in message.content:
                if _is_search_result(block):
                    registry.add(
                        block.get("source", ""),
                        block.get("title", ""),
                        _search_result_text(block),
                    )

    prepared: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            message = _tag_tool_message(message, registry)
        elif isinstance(message, AIMessage):
            message = _restore_ai_message(message, registry)
        prepared.append(message)
    return prepared, registry


@dataclass(frozen=True)
class TextPiece:
    """Text with its tags removed, belonging to segment ``segment``."""

    segment: int
    text: str


@dataclass(frozen=True)
class CitePiece:
    """The tags that close segment ``segment``, and the claim text they support."""

    segment: int
    tags: list[int]
    claim: str


class MarkerStream:
    """Cuts ``[src:N]`` tags out of text that arrives in arbitrary pieces.

    A *segment* is a run of text closed by the tags that follow it. The Anthropic
    path emits one text block per cited claim, and this mirrors that: each segment
    is its own block, so the citation stays beside the sentence it supports and the
    client can place its ``[n]`` marker there.

    Text is held back only when it could still turn into a tag: an open ``[``
    that looks like the start of one, and whitespace at the very end (a tag takes
    the space before it along). Everything else is released at once, so the reader
    sees the answer stream.
    """

    def __init__(self, registry: SourceRegistry) -> None:
        """Start a stream against ``registry``, which decides which tags are real."""
        self._registry = registry
        self._buffer = ""
        self._segment = 0
        self._segment_text = ""
        self._closed = False
        self._held_whitespace = ""

    def feed(self, text: str) -> list[TextPiece | CitePiece]:
        """Take the next piece of text; return what can be released."""
        self._buffer += text
        pieces: list[TextPiece | CitePiece] = []
        while True:
            match = MARKER_PATTERN.search(self._buffer)
            if match is None:
                cut = self._safe_cut(self._buffer)
                self._emit_text(self._buffer[:cut], pieces)
                self._buffer = self._buffer[cut:]
                return pieces
            self._emit_text(self._buffer[: match.start()], pieces)
            self._buffer = self._buffer[match.end() :]
            self._handle_marker(match.group(1), pieces)

    def finish(self) -> list[TextPiece | CitePiece]:
        """End of the text: release whatever is still held."""
        pieces: list[TextPiece | CitePiece] = []
        self._emit_text(self._buffer, pieces)
        self._buffer = ""
        return pieces

    @staticmethod
    def _safe_cut(buffer: str) -> int:
        """Where the held-back tail of ``buffer`` begins."""
        cut = len(buffer)
        bracket = buffer.rfind("[")
        if (
            bracket != -1
            and len(buffer) - bracket <= _PARTIAL_MARKER_LIMIT
            and _PARTIAL_MARKER.fullmatch(buffer[bracket:])
        ):
            cut = bracket
        held = 0
        while cut > 0 and buffer[cut - 1] in _HSPACE and held < _HELD_WHITESPACE_LIMIT:
            cut -= 1
            held += 1
        return cut

    def _emit_text(self, text: str, pieces: list[TextPiece | CitePiece]) -> None:
        if not text:
            return
        if self._closed:
            if not text.strip():
                self._held_whitespace += text
                return
            self._segment += 1
            self._closed = False
            self._segment_text = ""
            text = self._held_whitespace + text
            self._held_whitespace = ""
        self._segment_text += text
        pieces.append(TextPiece(self._segment, text))

    def _handle_marker(self, tag_text: str, pieces: list[TextPiece | CitePiece]) -> None:
        requested = [int(tag) for tag in re.findall(r"\d+", tag_text)]
        tags = [
            tag
            for i, tag in enumerate(requested)
            if tag not in requested[:i] and self._registry.by_tag(tag) is not None
        ]
        unknown = [tag for tag in requested if self._registry.by_tag(tag) is None]
        if unknown:
            logger.warning(
                "Dropping citation tag(s) %s: no such source in this conversation", unknown
            )
        if not tags:
            return
        if not self._segment_text.strip():
            logger.debug("Dropping citation tag(s) %s: no text before them", tags)
            return
        pieces.append(CitePiece(self._segment, tags, self._segment_text))
        self._closed = True


#: Added to every index this module gives a block, so it can never equal the index
#: of a block the provider numbered itself. Providers number blocks by position (0, 1,
#: 2, ...); a text block split into segments 0..k would otherwise take the indexes of
#: the blocks after it, and a tool-use block at index 1 was merged into the second
#: segment's text when the streamed chunks were added together.
SEGMENT_INDEX_OFFSET = 1_000_000


def pieces_to_blocks(
    pieces: Iterable[TextPiece | CitePiece],
    registry: SourceRegistry,
    index_base: int | None,
) -> list[dict[str, Any]]:
    """Streaming shape: text blocks, each followed by a citation-only block.

    This is what ``langchain-anthropic`` produces while streaming, and what
    ``CitationAssembler`` reads: the citation arrives on the same ``index`` as the
    text it follows. Each segment gets an index of its own, so merging the chunks
    keeps one block per cited claim.

    Args:
        pieces: Output of ``MarkerStream``.
        registry: Resolves tags to sources.
        index_base: The provider's index for the text block, or None when the
            provider gave none. Segment indexes are
            ``SEGMENT_INDEX_OFFSET + index_base * 1000 + segment``.
    """
    blocks: list[dict[str, Any]] = []
    for piece in pieces:
        index = SEGMENT_INDEX_OFFSET + (index_base or 0) * 1000 + piece.segment
        if isinstance(piece, TextPiece):
            blocks.append({"type": "text", "text": piece.text, "index": index})
        else:
            blocks.append(
                {
                    "type": "text",
                    "text": "",
                    "citations": [registry.citation(tag, piece.claim) for tag in piece.tags],
                    "index": index,
                }
            )
    return blocks


class ChunkRetagger:
    """Rewrites the text of a chat model's streamed chunks, one chunk at a time.

    A chunk's text is replaced by tag-free text and citation blocks (see
    ``pieces_to_blocks``); every other block (reasoning, tool use) and every other
    field of the chunk (tool-call chunks, usage) passes through as it came. Text
    that might be the start of a tag is held back until the next chunk shows
    whether it is one, so call ``finish`` once the reply is over.

    One instance serves one model call: it keeps a ``MarkerStream`` per text block.
    """

    def __init__(self, registry: SourceRegistry) -> None:
        self._registry = registry
        self._streams: dict[int, MarkerStream] = {}

    def feed(self, chunk: ChatGenerationChunk) -> ChatGenerationChunk:
        """Return ``chunk`` with its text rewritten, or as it came if it has none."""
        content = chunk.message.content
        if isinstance(content, str):
            content = [{"type": "text", "text": content, "index": 0}] if content else []
        blocks: list[Any] = []
        changed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                index = block.get("index") or 0
                stream = self._streams.setdefault(index, MarkerStream(self._registry))
                pieces = stream.feed(block.get("text", ""))
                blocks.extend(pieces_to_blocks(pieces, self._registry, index))
                changed = True
            else:
                blocks.append(block)
        if not changed:
            return chunk
        message = chunk.message.model_copy(update={"content": blocks})
        return chunk.model_copy(update={"message": message})

    def finish(self) -> list[ChatGenerationChunk]:
        """The text held back in case it became a tag, once the reply is over."""
        chunks: list[ChatGenerationChunk] = []
        for index, stream in self._streams.items():
            blocks = pieces_to_blocks(stream.finish(), self._registry, index)
            if blocks:
                chunks.append(ChatGenerationChunk(message=AIMessageChunk(content=blocks)))
        return chunks


def pieces_to_whole_blocks(
    pieces: Iterable[TextPiece | CitePiece], registry: SourceRegistry
) -> list[dict[str, Any]]:
    """Non-streaming shape: one text block per segment, with its ``citations`` inline."""
    blocks: list[dict[str, Any]] = []
    for piece in pieces:
        if isinstance(piece, TextPiece):
            if blocks and blocks[-1].get("_segment") == piece.segment:
                blocks[-1]["text"] += piece.text
            else:
                blocks.append({"type": "text", "text": piece.text, "_segment": piece.segment})
        else:
            if not blocks or blocks[-1].get("_segment") != piece.segment:
                blocks.append({"type": "text", "text": "", "_segment": piece.segment})
            blocks[-1].setdefault("citations", []).extend(
                registry.citation(tag, piece.claim) for tag in piece.tags
            )
    for block in blocks:
        block.pop("_segment", None)
    return blocks


def rewrite_content(content: str | list[Any], registry: SourceRegistry) -> str | list[Any]:
    """Cut the tags out of a complete reply and attach them as citations.

    Args:
        content: A finished ``AIMessage.content``: a string, or a block list that
            may hold reasoning and tool-use blocks alongside the text.
        registry: The sources of the conversation the reply answers.

    Returns:
        ``content`` unchanged when it has no tags. Otherwise text blocks are
        replaced by one block per cited claim; every other block passes through.
    """
    if isinstance(content, str):
        pieces = _parse(content, registry)
        if not any(isinstance(piece, CitePiece) for piece in pieces):
            return content
        return pieces_to_whole_blocks(pieces, registry)

    rewritten: list[Any] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            pieces = _parse(block.get("text", ""), registry)
            if any(isinstance(piece, CitePiece) for piece in pieces):
                rewritten.extend(pieces_to_whole_blocks(pieces, registry))
                continue
        rewritten.append(block)
    return rewritten


def _parse(text: str, registry: SourceRegistry) -> list[TextPiece | CitePiece]:
    stream = MarkerStream(registry)
    return stream.feed(text) + stream.finish()
