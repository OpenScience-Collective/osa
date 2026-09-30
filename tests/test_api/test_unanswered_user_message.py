"""A question whose stream failed stays in the session with no reply (release review, follow-up 8).

Whatever ends a streamed turn before it has an answer (a failed model call, an empty reply,
a reply cut off with no text) leaves the reader's message in the session and nothing after
it, and the next message lands right behind it: two human messages in a row. Providers
differ in what they do with that, so this drives each one's real client stack through the
real ``/chat`` endpoint twice, the first request failing, and reads the request the second
one sent.

What is real: the endpoint, the session store, the graph, and each provider's client
(botocore for Bedrock, the Anthropic SDK, LiteLLM against a local OpenRouter stand-in).
What stands in: the network under each client, and the router's ``create_community_assistant``
(which would build a client from settings this suite has no keys for).
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from src.api.config import Settings
from src.api.routers.community import (
    AssistantWithMetrics,
    _get_session_store,
    create_community_router,
)
from src.assistants.community import CommunityAssistant
from src.core.services.anthropic_llm import create_anthropic_llm
from src.core.services.anthropic_models import BEDROCK_MODELS, DEFAULT_MODEL
from src.core.services.bedrock_llm import _bedrock_client, create_bedrock_llm
from src.core.services.litellm_llm import DEFAULT_MODEL as OPENROUTER_MODEL
from src.core.services.litellm_llm import create_openrouter_llm
from src.metrics.db import init_metrics_db
from tests.helpers.anthropic_wire import message_stream, refusal_with, served_by
from tests.helpers.bedrock_wire import EVENT_STREAM, Wire, converse_stream, refusal
from tests.helpers.openrouter import FakeOpenRouter, HttpError, stream_of
from tests.helpers.provider_replies import ANSWER, COMMUNITY, ORIGIN, community_config

FIRST = "What does Sensory-event mark?"
SECOND = "And what about Agent-action?"
SESSION = "sess-unanswered"
BEDROCK_MODEL = sorted(BEDROCK_MODELS)[0]


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    init_metrics_db()


@pytest.fixture(autouse=True)
def _fresh_bedrock_clients():
    yield
    _bedrock_client.cache_clear()


@pytest.fixture
def client(monkeypatch):
    from src.api.config import get_settings
    from src.assistants.registry import registry

    monkeypatch.setenv("REQUIRE_API_AUTH", "false")
    get_settings.cache_clear()
    registry.register_from_config(community_config())
    _get_session_store(COMMUNITY).clear()
    app = FastAPI()
    app.include_router(create_community_router(COMMUNITY))
    yield TestClient(app)
    _get_session_store(COMMUNITY).clear()
    registry._assistants.pop(COMMUNITY, None)
    monkeypatch.undo()
    get_settings.cache_clear()


def _ask(client: TestClient, question: str) -> list[dict]:
    """One streamed ``/chat`` request on the shared session; its events."""
    response = client.post(
        f"/{COMMUNITY}/chat",
        headers={"Origin": ORIGIN},
        json={"message": question, "session_id": SESSION, "stream": True},
    )
    assert response.status_code == 200, response.text
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _serve(monkeypatch, llm: Any, model: str) -> None:
    assistant = CommunityAssistant(model=llm, config=community_config(), preload_docs=False)
    wrapped = AssistantWithMetrics(assistant=assistant, model=model, key_source="platform")
    monkeypatch.setattr(
        "src.api.routers.community.create_community_assistant", lambda *_a, **_k: wrapped
    )


def _bedrock_llm():
    _bedrock_client.cache_clear()
    return create_bedrock_llm(
        BEDROCK_MODEL,
        settings=Settings(
            _env_file=None,
            bedrock_api_key="test-bedrock-key",
            bedrock_region="us-east-2",
            bedrock_max_output_tokens=16000,
        ),
    )


def _texts(content: Any) -> list[str]:
    """The text pieces of one message's content, whether it is a string or blocks."""
    if isinstance(content, str):
        return [content]
    return [block["text"] for block in content if block.get("type") == "text"]


def _stored() -> list:
    return _get_session_store(COMMUNITY)[SESSION].messages


class TestWhatTheSessionHoldsAfterAFailedStream:
    def test_the_message_stays_and_the_next_one_lands_behind_it(
        self, client: TestClient, monkeypatch
    ) -> None:
        failing = _bedrock_llm()
        Wire(failing, **refusal("ThrottlingException", "Too many requests"))
        _serve(monkeypatch, failing, BEDROCK_MODEL)

        events = _ask(client, FIRST)

        assert events[-1]["event"] == "error"
        assert [type(m) for m in _stored()] == [HumanMessage], "the question has no reply"

        working = _bedrock_llm()
        Wire(working, converse_stream([ANSWER]), EVENT_STREAM)
        _serve(monkeypatch, working, BEDROCK_MODEL)
        events = _ask(client, SECOND)

        assert events[-1]["event"] == "done" and events[-1]["content"] == ANSWER
        assert [type(m) for m in _stored()] == [HumanMessage, HumanMessage, AIMessage]
        assert [m.content for m in _stored() if isinstance(m, HumanMessage)] == [FIRST, SECOND]


class TestWhatEachProviderSendsForTwoHumanMessagesInARow:
    def test_bedrock_merges_them_into_one_user_message(
        self, client: TestClient, monkeypatch
    ) -> None:
        """``langchain-aws`` runs ``merge_message_runs`` over the messages it converts."""
        failing = _bedrock_llm()
        Wire(failing, **refusal("ThrottlingException", "Too many requests"))
        _serve(monkeypatch, failing, BEDROCK_MODEL)
        _ask(client, FIRST)
        working = _bedrock_llm()
        wire = Wire(working, converse_stream([ANSWER]), EVENT_STREAM)
        _serve(monkeypatch, working, BEDROCK_MODEL)

        _ask(client, SECOND)

        sent = wire.body["messages"]
        assert [m["role"] for m in sent] == ["user"]
        # ``merge_message_runs`` joins two string contents with a newline.
        assert [block["text"] for block in sent[0]["content"] if "text" in block] == [
            f"{FIRST}\n{SECOND}"
        ]

    def test_anthropic_merges_them_into_one_user_message(
        self, client: TestClient, monkeypatch
    ) -> None:
        """``langchain-anthropic`` merges consecutive human messages in its own
        ``_merge_messages`` (it does not call ``merge_message_runs``; the effect is the
        same)."""
        sent: list[dict] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            sent.append(json.loads(request.content))
            if len(sent) == 1:
                return refusal_with(400, "invalid_request_error", "no")(request)
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=message_stream([ANSWER]),
            )

        with served_by(handler):
            llm = create_anthropic_llm(
                DEFAULT_MODEL, api_key="sk-ant-test", settings=Settings(_env_file=None)
            )
            _serve(monkeypatch, llm, DEFAULT_MODEL)
            assert _ask(client, FIRST)[-1]["event"] == "error"

            assert _ask(client, SECOND)[-1]["event"] == "done"

        messages = sent[-1]["messages"]
        assert [m["role"] for m in messages] == ["user"]
        assert _texts(messages[0]["content"]) == [FIRST, SECOND]

    def test_openrouter_sends_them_as_two_user_messages(
        self, client: TestClient, monkeypatch
    ) -> None:
        """Nothing on this path merges them (not OSA, ``langchain-litellm`` or LiteLLM):
        they go out as two consecutive user messages, which the OpenAI-style chat format
        allows. What the providers behind OpenRouter do with them is theirs to say; this
        only pins what is sent."""
        server = FakeOpenRouter()
        monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
        try:
            server.reply(HttpError(400, {"error": {"message": "no"}}), stream_of(ANSWER))
            llm = create_openrouter_llm(model=OPENROUTER_MODEL, api_key="sk-or-test")
            _serve(monkeypatch, llm, OPENROUTER_MODEL)
            assert _ask(client, FIRST)[-1]["event"] == "error"

            assert _ask(client, SECOND)[-1]["event"] == "done"

            messages = server.requests[-1]["messages"]
        finally:
            server.close()

        conversation = [m for m in messages if m["role"] != "system"]
        assert [m["role"] for m in conversation] == ["user", "user"]
        assert [_texts(m["content"]) for m in conversation] == [[FIRST], [SECOND]]
