"""A reply tells the reader what it used and cost (issue #582).

Every stream's ``done`` event, a parked browser run's ``tool_request`` event and the
responses to requests that are not streamed carry a ``usage`` object: input, output and
cache tokens and an estimated cost. It comes from the same counters the request metrics
use. As in ``test_missing_usage.py``, the chat model is a scripted one yielding each
adapter's chunks, through the real graph and the real streams.

OpenRouter is left out for now: its requests report no usage.
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
from tests.test_api.test_tool_call_streaming import _anthropic_call

OFFERED = [p for p in PROVIDERS if p.name != "openrouter"]
OPENROUTER = next(p for p in PROVIDERS if p.name == "openrouter")
offered = pytest.mark.parametrize("provider", OFFERED, ids=lambda p: p.name)


def _cost(
    model: str, input_tokens: int, output_tokens: int, read: int = 0, write: int = 0
) -> float:
    """The cost by hand from the price table: fresh input at the model's rate, cache
    writes a quarter dearer, cache reads a tenth."""
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
        from tests.test_api.test_tool_call_streaming import COMMUNITY as BROWSER_COMMUNITY
        from tests.test_api.test_tool_call_streaming import _assistant as browser_assistant

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


class TestWithoutStreaming:
    @pytest.fixture(autouse=True)
    def _serve(self, monkeypatch: pytest.MonkeyPatch):
        self.serve = lambda provider, script: monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant",
            lambda *_a, **_k: assistant_for(provider, script),
        )

    @offered
    def test_an_ask_response_carries_its_usage(
        self,
        provider: Provider,
        client: TestClient,  # noqa: F811
    ) -> None:
        self.serve(provider, [scripted_reply(provider, ANSWER)])

        response = client.post(
            f"/{COMMUNITY}/ask",
            headers={"Origin": ORIGIN},
            json={"question": QUESTION, "stream": False},
        )

        assert response.status_code == 200
        assert response.json()["usage"] == _usage(provider.model)

    @offered
    def test_a_chat_response_carries_this_turns_usage_only(
        self,
        provider: Provider,
        client: TestClient,  # noqa: F811
    ) -> None:
        post = lambda: client.post(  # noqa: E731 (a one-line helper for two calls)
            f"/{COMMUNITY}/chat",
            headers={"Origin": ORIGIN},
            json={"message": QUESTION, "session_id": "sess-usage-turns", "stream": False},
        )
        self.serve(provider, [scripted_reply(provider, ANSWER)])
        assert post().json()["usage"] == _usage(provider.model)

        self.serve(provider, [scripted_reply(provider, ANSWER)])
        second = post()

        assert second.status_code == 200
        assert second.json()["usage"] == _usage(provider.model), "not the two turns added up"

    def test_openrouter_is_left_out(self, client: TestClient) -> None:  # noqa: F811
        self.serve(OPENROUTER, [scripted_reply(OPENROUTER, ANSWER)])

        response = client.post(
            f"/{COMMUNITY}/ask",
            headers={"Origin": ORIGIN},
            json={"question": QUESTION, "stream": False},
        )

        assert response.status_code == 200
        assert response.json()["usage"] is None


class TestABadCountDoesNotCostTheReaderTheAnswer:
    def test_a_fractional_token_count_is_no_usage_and_a_logged_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A provider that reports half a token: pydantic refuses it, and the reply still
        goes out, with no usage and a warning for the operator."""
        from src.api.routers.community import _usage_for_event

        assistant = assistant_for(OFFERED[0], [])

        with caplog.at_level(logging.WARNING):
            usage = _usage_for_event(assistant, 120.5, 30, 0, 0)  # ty: ignore[invalid-argument-type]

        assert usage is None
        assert [r.levelno for r in caplog.records if "usage of a reply" in r.getMessage()] == [
            logging.WARNING
        ]
