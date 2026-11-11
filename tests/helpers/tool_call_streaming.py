"""Fixtures shared by the tool-call streaming tests (issue #538) and the modules that reuse them.

`tests/test_api/test_tool_call_streaming.py` drives the real graph and router with a
scripted chat model. Several other test modules need the same pieces: the community
config and its constants, the two real server-executed tools, the chunk builders in each
provider adapter's shape, the assistant wrapper around the scripted model, and the
`/chat/resume` client with its parked session. They live here so that a change to that
test file cannot break the modules that import them.

Nothing in this module is a test; the names keep their leading underscores from the
module they were moved out of.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.tools import tool

from src.api.routers.community import (
    AssistantWithMetrics,
    ChatSession,
    _get_session_store,
    create_community_router,
)
from src.api.tool_results import PendingClientCall
from src.assistants.community import CommunityAssistant
from src.core.config.community import CommunityConfig
from src.core.services.anthropic_models import HAIKU
from tests.helpers.chat_models import StreamingScriptedChatModel

COMMUNITY = "toolcallstream"
ALLOWED_ORIGIN = "https://toolcall.example"
SEARCH_CALL_ID = "toolu_01searchsearchsearchsea"
DOCS_CALL_ID = "toolu_01docsdocsdocsdocsdocsd"
CODE_CALL_ID = "toolu_01codecodecodecodecodec"
SECRET_QUERY = "a query the reader typed, which must never ride on tool_call"


@tool
def nemar_search_datasets(query: str) -> str:
    """Search datasets. A real, server-executed tool named like NEMAR's MCP tool."""
    return f"3 datasets match {query!r}"


@tool
def retrieve_toolcallstream_docs(url: str) -> str:
    """Fetch a documentation page. A real, server-executed tool."""
    return f"the page at {url}"


def _config() -> CommunityConfig:
    return CommunityConfig(
        id=COMMUNITY,
        name="Tool Call Stream",
        description="A community whose replies call tools",
        extensions={
            "client_tools": [
                {
                    "name": "execute_code",
                    "runtime": "python",
                    "requires_permission": True,
                    "description": "Run Python in the user's browser.",
                }
            ]
        },
        runtime={"python": {"pyodide_version": "314.0.6", "lockfile": "runtime/l.json"}},
        cors_origins=[ALLOWED_ORIGIN],
    )


# ---------------------------------------------------------------------------
# Chunks in each provider adapter's shape
# ---------------------------------------------------------------------------


def _pieces(text: str, size: int = 7) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _text(text: str, index: int = 0) -> AIMessageChunk:
    return AIMessageChunk(content=[{"type": "text", "text": text, "index": index}])


def _anthropic_call(
    name: str, call_id: str, args: dict[str, Any], index: int = 1
) -> list[AIMessageChunk]:
    """langchain-anthropic: a `tool_use` start block naming the call, then
    `input_json_delta` blocks carrying only the block index and the argument text."""
    chunks = [
        AIMessageChunk(
            content=[
                {"type": "tool_use", "id": call_id, "name": name, "input": {}, "index": index}
            ],
            tool_call_chunks=[tool_call_chunk(name=name, id=call_id, args="", index=index)],
        )
    ]
    for piece in _pieces(json.dumps(args)):
        chunks.append(
            AIMessageChunk(
                content=[{"type": "input_json_delta", "partial_json": piece, "index": index}],
                tool_call_chunks=[tool_call_chunk(name=None, id=None, args=piece, index=index)],
            )
        )
    return chunks


def _bedrock_call(
    name: str, call_id: str, args: dict[str, Any], index: int = 1
) -> list[AIMessageChunk]:
    """langchain-aws `ChatBedrockConverse`: `contentBlockStart` names the call (no
    input), each `contentBlockDelta` is a `tool_use` block with only its input text."""
    chunks = [
        AIMessageChunk(
            content=[{"type": "tool_use", "name": name, "id": call_id, "index": index}],
            tool_call_chunks=[tool_call_chunk(name=name, id=call_id, args=None, index=index)],
        )
    ]
    for piece in _pieces(json.dumps(args)):
        chunks.append(
            AIMessageChunk(
                content=[{"type": "tool_use", "input": piece, "id": None, "index": index}],
                tool_call_chunks=[tool_call_chunk(name=None, id=None, args=piece, index=index)],
            )
        )
    return chunks


def _openai_calls(
    calls: list[tuple[str, str, dict[str, Any]]], *, repeat_name: bool = False
) -> list[AIMessageChunk]:
    """OpenAI-style deltas, as langchain-litellm builds them for OpenRouter: the call's
    position is its `index`, and the id and name come on its first delta (some
    providers repeat the name on every delta, which `repeat_name` reproduces)."""
    chunks: list[AIMessageChunk] = []
    for position, (name, call_id, args) in enumerate(calls):
        chunks.append(
            AIMessageChunk(
                content="",
                tool_call_chunks=[tool_call_chunk(name=name, id=call_id, args="", index=position)],
            )
        )
        for piece in _pieces(json.dumps(args)):
            chunks.append(
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        tool_call_chunk(
                            name=name if repeat_name else None,
                            id=None,
                            args=piece,
                            index=position,
                        )
                    ],
                )
            )
    return chunks


ANSWER = "Three datasets match: nm000103, nm000132 and nm000140."


def _answer() -> list[AIMessageChunk]:
    return [_text(piece) for piece in _pieces(ANSWER, 11)]


# ---------------------------------------------------------------------------
# Driving the router
# ---------------------------------------------------------------------------


def _assistant(script: list[list[AIMessageChunk]]) -> AssistantWithMetrics:
    model = StreamingScriptedChatModel(chunk_script=script)
    assistant = CommunityAssistant(
        model=model,
        config=_config(),
        preload_docs=False,
        additional_tools=[nemar_search_datasets, retrieve_toolcallstream_docs],
        declared_client_tools={"execute_code"},
    )
    return AssistantWithMetrics(assistant=assistant, model=HAIKU, key_source="platform")


SEARCH_ARGS = {"query": SECRET_QUERY}


# ---------------------------------------------------------------------------
# /chat/resume, through the real endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def resume_client(monkeypatch):
    from src.api.config import get_settings
    from src.assistants.registry import registry

    # What is under test is the stream, not the API key check. The settings are cached
    # for the whole run, and tests that ran earlier leave them with authentication on
    # (with a local .env that supplies keys, this endpoint then answers 401), so this
    # test builds the settings it needs and puts the cache back as it found it.
    monkeypatch.setenv("REQUIRE_API_AUTH", "false")
    get_settings.cache_clear()
    registry.register_from_config(_config())
    _get_session_store(COMMUNITY).clear()
    app = FastAPI()
    app.include_router(create_community_router(COMMUNITY))
    yield TestClient(app)
    _get_session_store(COMMUNITY).clear()
    registry._assistants.pop(COMMUNITY, None)
    monkeypatch.undo()
    get_settings.cache_clear()


def _parked_session() -> ChatSession:
    session = ChatSession("sess-parked-toolcall", COMMUNITY)
    session.add_user_message("Plot the alpha power.")
    session.messages.append(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute_code",
                    "args": {"code": "x"},
                    "id": CODE_CALL_ID,
                    "type": "tool_call",
                }
            ],
        )
    )
    session.set_pending_call(
        PendingClientCall.from_state(
            {
                "call_id": CODE_CALL_ID,
                "tool": "execute_code",
                "args": {"code": "x"},
                "requires_permission": True,
            }
        )
    )
    _get_session_store(COMMUNITY)[session.session_id] = session
    return session
