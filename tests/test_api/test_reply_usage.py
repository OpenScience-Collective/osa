"""A reply tells the reader what it used and cost (issue #582).

Every stream's ``done`` event, a parked browser run's ``tool_request`` event and the
responses to requests that are not streamed carry a ``usage`` object: input, output and
cache tokens and an estimated cost. It comes from the same counters the request metrics
use. As in ``test_missing_usage.py``, the chat model is a scripted one yielding each
adapter's chunks, through the real graph and the real streams.

A request served through OpenRouter gets `usage: null` (see src/metrics/reply_usage.py for
why).
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk

from src.api.routers.community import ChatSession, _stream_chat_response
from src.metrics.cost import MODEL_PRICING
from tests.helpers.provider_replies import (
    ANSWER,
    COMMUNITY,
    ORIGIN,
    PROVIDERS,
    QUESTION,
    USAGE,
    Provider,
    assistant_for,
    collect,
    real_request,
    scripted_reply,
)
from tests.test_api.test_missing_usage import _ask, _chat, client, metrics_db  # noqa: F401
from tests.test_api.test_tool_call_streaming import COMMUNITY as BROWSER_COMMUNITY
from tests.test_api.test_tool_call_streaming import _anthropic_call
from tests.test_api.test_tool_call_streaming import _assistant as browser_assistant

OFFERED = [p for p in PROVIDERS if p.name != "openrouter"]
OPENROUTER = next(p for p in PROVIDERS if p.name == "openrouter")
offered = pytest.mark.parametrize("provider", OFFERED, ids=lambda p: p.name)


def _cost(
    model: str, input_tokens: int, output_tokens: int, read: int = 0, write: int = 0
) -> float:
    """The cost by hand from the price table: fresh input at the model's rate, cache
    writes at 1.25 times it and cache reads at a tenth of it. The multipliers are written
    out here, so a change to them fails a test."""
    rate = MODEL_PRICING[model]
    fresh = input_tokens - read - write
    dollars = (
        fresh * rate.input_per_1m
        + write * rate.input_per_1m * 1.25
        + read * rate.input_per_1m * 0.1
        + output_tokens * rate.output_per_1m
    )
    return round(dollars / 1_000_000, 6)


def _usage(model: str) -> dict:
    """What the scripted replies' ``USAGE`` (120 in, 30 out, nothing cached) comes to."""
    return {
        "input_tokens": USAGE["input_tokens"],
        "output_tokens": USAGE["output_tokens"],
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "estimated_cost": _cost(model, USAGE["input_tokens"], USAGE["output_tokens"]),
        "partial": False,
    }


class TestTheDoneEvent:
    @offered
    async def test_a_chat_reply_carries_its_usage(self, provider: Provider) -> None:
        events = await _chat(provider, [scripted_reply(provider, ANSWER)])

        assert events[-1]["event"] == "done"
        assert events[-1]["usage"] == _usage(provider.model)

    @offered
    async def test_an_ask_reply_carries_its_usage(self, provider: Provider) -> None:
        events = await _ask(provider, [scripted_reply(provider, ANSWER)])

        assert events[-1]["event"] == "done"
        assert events[-1]["usage"] == _usage(provider.model)

    @offered
    async def test_cached_tokens_are_counted_and_priced_at_their_own_rates(
        self, provider: Provider
    ) -> None:
        cached = AIMessageChunk(
            content="",
            usage_metadata={
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "input_token_details": {"cache_read": 80, "cache_creation": 10},
            },
        )
        script = [[*scripted_reply(provider, ANSWER, usage=False), cached]]

        events = await _chat(provider, script)

        assert events[-1]["usage"] == {
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_tokens": 80,
            "cache_creation_tokens": 10,
            "estimated_cost": _cost(provider.model, 120, 30, read=80, write=10),
            "partial": False,
        }

    @offered
    async def test_a_request_whose_provider_reported_no_usage_says_so(
        self, provider: Provider
    ) -> None:
        """Not a free reply: no number is better than a zero."""
        events = await _chat(provider, [scripted_reply(provider, ANSWER, usage=False)])

        assert events[-1]["event"] == "done"
        assert events[-1]["usage"] is None

    async def test_openrouter_is_left_out(self) -> None:
        events = await _chat(OPENROUTER, [scripted_reply(OPENROUTER, ANSWER)])

        assert events[-1]["event"] == "done"
        assert events[-1]["usage"] is None


class TestAParkedBrowserRun:
    async def test_the_run_that_ends_on_a_browser_call_reports_its_own_usage(self) -> None:
        """The reply goes on in the next run, whose ``done`` carries that run's; a client
        adds them up."""
        call = _anthropic_call("execute_code", "toolu_01usage", {"code": "x", "description": "d"})
        run = [*call, AIMessageChunk(content=[], usage_metadata=USAGE)]
        session = ChatSession("sess-parked-usage", BROWSER_COMMUNITY)
        session.add_user_message(QUESTION)
        with patch(
            "src.api.routers.community.create_community_assistant",
            return_value=browser_assistant([run]),
        ):
            events = await collect(
                _stream_chat_response(
                    BROWSER_COMMUNITY,
                    session,
                    None,
                    None,
                    None,
                    http_request=real_request("req-usage"),
                    declared_client_tools={"execute_code"},
                )
            )

        assert events[-1]["event"] == "tool_request"
        assert events[-1]["usage"] == _usage("claude-haiku-4-5")


def _serve(monkeypatch: pytest.MonkeyPatch, provider: Provider, script: list) -> None:
    """Make the next request's assistant the real one over a model that replays ``script``."""
    monkeypatch.setattr(
        "src.api.routers.community.create_community_assistant",
        lambda *_a, **_k: assistant_for(provider, script),
    )


def _ask_without_streaming(client: TestClient):  # noqa: F811
    return client.post(
        f"/{COMMUNITY}/ask",
        headers={"Origin": ORIGIN},
        json={"question": QUESTION, "stream": False},
    )


class TestWithoutStreaming:
    @offered
    def test_an_ask_response_carries_its_usage(
        self,
        provider: Provider,
        client: TestClient,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, provider, [scripted_reply(provider, ANSWER)])

        response = _ask_without_streaming(client)

        assert response.status_code == 200
        assert response.json()["usage"] == _usage(provider.model)

    @offered
    def test_a_chat_response_carries_this_turns_usage_only(
        self,
        provider: Provider,
        client: TestClient,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def post():
            return client.post(
                f"/{COMMUNITY}/chat",
                headers={"Origin": ORIGIN},
                json={"message": QUESTION, "session_id": "sess-usage-turns", "stream": False},
            )

        _serve(monkeypatch, provider, [scripted_reply(provider, ANSWER)])
        assert post().json()["usage"] == _usage(provider.model)

        _serve(monkeypatch, provider, [scripted_reply(provider, ANSWER)])
        second = post()

        assert second.status_code == 200
        assert second.json()["usage"] == _usage(provider.model), "not the two turns added up"

    def test_openrouter_is_left_out(
        self,
        client: TestClient,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, OPENROUTER, [scripted_reply(OPENROUTER, ANSWER)])

        response = _ask_without_streaming(client)

        assert response.status_code == 200
        assert response.json()["usage"] is None


LOOKUP = _anthropic_call("lookup_scriptedreply_docs", "toolu_01partial", {"query": "tags"})


class TestARunThatReportedNothing:
    """A reply of several model runs where one reported no tokens: the others' figures are
    a lower bound, and the reader is told so, not shown them as the total."""

    @offered
    async def test_a_stream_says_the_usage_is_partial(self, provider: Provider) -> None:
        events = await _chat(
            provider,
            [
                [*LOOKUP, *scripted_reply(provider, "", usage=False)],
                scripted_reply(provider, ANSWER),
            ],
        )

        assert events[-1]["event"] == "done"
        assert events[-1]["usage"] == _usage(provider.model) | {"partial": True}

    @offered
    async def test_a_reply_whose_runs_all_reported_is_not_partial(self, provider: Provider) -> None:
        first = [*LOOKUP, *scripted_reply(provider, "")]
        events = await _chat(provider, [first, scripted_reply(provider, ANSWER)])

        assert events[-1]["usage"]["partial"] is False
        assert events[-1]["usage"]["input_tokens"] == 2 * USAGE["input_tokens"]

    @offered
    def test_a_response_that_is_not_streamed_says_so_too(
        self,
        provider: Provider,
        client: TestClient,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(
            monkeypatch,
            provider,
            [
                [*LOOKUP, *scripted_reply(provider, "", usage=False)],
                scripted_reply(provider, ANSWER),
            ],
        )

        response = _ask_without_streaming(client)

        assert response.status_code == 200
        assert response.json()["usage"] == _usage(provider.model) | {"partial": True}


class TestABadCountDoesNotCostTheReaderTheAnswer:
    @offered
    async def test_cached_tokens_above_the_input_leave_the_answer_and_drop_the_usage(
        self, provider: Provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An adapter that counts the way Anthropic's own API does (an input count without
        the cached tokens) reports 80 cached tokens of 20 input: no usage can say that. The
        reply still goes out whole, and the operator gets a warning that names the counts."""
        counted_wrong = AIMessageChunk(
            content="",
            usage_metadata={
                "input_tokens": 20,
                "output_tokens": 30,
                "total_tokens": 50,
                "input_token_details": {"cache_read": 80},
            },
        )
        script = [[*scripted_reply(provider, ANSWER, usage=False), counted_wrong]]

        with caplog.at_level(logging.WARNING):
            events = await _chat(provider, script)

        assert events[-1]["event"] == "done"
        assert events[-1]["content"] == ANSWER
        assert events[-1]["usage"] is None
        (record,) = [r for r in caplog.records if "cannot be told as usage" in r.getMessage()]
        assert record.levelno == logging.WARNING
        text = record.getMessage()
        for expected in (COMMUNITY, provider.model, "req-usage", "input=20", "cache_read=80"):
            assert expected in text, f"{expected!r} missing from {text!r}"
