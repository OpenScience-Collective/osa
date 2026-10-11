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

import asyncio
import json
import logging
import math
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk

from src.api.routers.community import ChatSession, _get_session_store, _stream_chat_response
from src.cli.output import format_usage
from src.core.services.anthropic_models import HAIKU
from src.metrics.cost import CACHE_READ_MULTIPLIER, CACHE_WRITE_MULTIPLIER, MODEL_PRICING
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
from tests.test_api.test_missing_usage import (  # noqa: F401
    _ask,
    _chat,
    _rows,
    client,
    metrics_db,
)
from tests.test_api.test_tool_call_streaming import ALLOWED_ORIGIN as BROWSER_ORIGIN
from tests.test_api.test_tool_call_streaming import (
    CODE_CALL_ID,
    _anthropic_call,
    resume_client,  # noqa: F401
)
from tests.test_api.test_tool_call_streaming import COMMUNITY as BROWSER_COMMUNITY
from tests.test_api.test_tool_call_streaming import _answer as browser_answer
from tests.test_api.test_tool_call_streaming import _assistant as browser_assistant

OFFERED = [p for p in PROVIDERS if p.name != "openrouter"]
OPENROUTER = next(p for p in PROVIDERS if p.name == "openrouter")
offered = pytest.mark.parametrize("provider", OFFERED, ids=lambda p: p.name)


def _cost(
    model: str, input_tokens: int, output_tokens: int, read: int = 0, write: int = 0
) -> float:
    """The cost by hand from the price table: fresh input at the model's rate, cache writes
    and reads at their multipliers of it."""
    rate = MODEL_PRICING[model]
    fresh = input_tokens - read - write
    dollars = (
        fresh * rate.input_per_1m
        + write * rate.input_per_1m * CACHE_WRITE_MULTIPLIER
        + read * rate.input_per_1m * CACHE_READ_MULTIPLIER
        + output_tokens * rate.output_per_1m
    )
    return round(dollars / 1_000_000, 6)


#: A run that read 80 input tokens from the cache and wrote 10 to it, of 120 in all.
CACHED = AIMessageChunk(
    content="",
    usage_metadata={
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "input_token_details": {"cache_read": 80, "cache_creation": 10},
    },
)


def _cached_usage(model: str) -> dict:
    """What ``CACHED`` comes to."""
    return {
        "input_tokens": 120,
        "output_tokens": 30,
        "cache_read_tokens": 80,
        "cache_creation_tokens": 10,
        "estimated_cost": _cost(model, 120, 30, read=80, write=10),
        "partial": False,
    }


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
    @pytest.mark.parametrize("drive", [_chat, _ask], ids=["chat", "ask"])
    async def test_cached_tokens_are_counted_and_priced_at_their_own_rates(
        self, provider: Provider, drive
    ) -> None:
        """On both endpoints, so cache reads and writes cannot trade places in one of them."""
        script = [[*scripted_reply(provider, ANSWER, usage=False), CACHED]]

        events = await drive(provider, script)

        assert events[-1]["usage"] == _cached_usage(provider.model)

    async def test_the_cache_write_langchain_anthropic_reports_per_lifetime_is_counted(
        self,
    ) -> None:
        """The real adapter zeroes ``cache_creation`` and reports the write under its
        lifetime (``ephemeral_5m_input_tokens``), so a reading of ``cache_creation`` alone
        would show no write on real Anthropic traffic."""
        anthropic = next(p for p in OFFERED if p.name == "anthropic")
        chunk = AIMessageChunk(
            content="",
            usage_metadata={
                "input_tokens": 570,
                "output_tokens": 30,
                "total_tokens": 600,
                "input_token_details": {
                    "cache_read": 70,
                    "cache_creation": 0,
                    "ephemeral_5m_input_tokens": 500,
                    "ephemeral_1h_input_tokens": 0,
                },
            },
        )

        events = await _chat(anthropic, [[*scripted_reply(anthropic, ANSWER, usage=False), chunk]])

        usage = events[-1]["usage"]
        assert (usage["cache_read_tokens"], usage["cache_creation_tokens"]) == (70, 500)

    @offered
    async def test_the_cli_can_read_what_the_server_sends(self, provider: Provider) -> None:
        events = await _chat(provider, [scripted_reply(provider, ANSWER)])

        line = format_usage(events[-1]["usage"])

        assert line is not None and line.startswith("120 in, 30 out, ")
        assert "about $" in line or "under $" in line, "and a cost"

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
        assert events[-1]["usage"] == _usage(HAIKU)

    async def test_its_cache_reads_and_writes_are_its_own(self) -> None:
        call = _anthropic_call("execute_code", "toolu_01cached", {"code": "x", "description": "d"})
        run = [*call, AIMessageChunk(content=[], usage_metadata=CACHED.usage_metadata)]
        session = ChatSession("sess-parked-cached", BROWSER_COMMUNITY)
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

        assert events[-1]["usage"] == _cached_usage(HAIKU)

    def test_each_run_of_a_reply_reports_only_its_own_through_the_real_endpoints(
        self,
        resume_client,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Run 1 parks on a browser call over ``/chat``, run 2 answers it over
        ``/chat/resume``. Each event carries its own run's usage: nothing held on the
        session adds them, which would be counted twice by a client that sums the runs."""
        run1 = [
            *_anthropic_call("execute_code", CODE_CALL_ID, {"code": "x", "description": "d"}),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "input_token_details": {"cache_read": 40, "cache_creation": 5},
                },
            ),
        ]
        run2 = [
            *browser_answer(),
            AIMessageChunk(
                content=[],
                usage_metadata={"input_tokens": 150, "output_tokens": 20, "total_tokens": 170},
            ),
        ]
        session = ChatSession("sess-two-runs", BROWSER_COMMUNITY)
        session.add_user_message("Plot the alpha power.")
        _get_session_store(BROWSER_COMMUNITY)[session.session_id] = session
        with patch(
            "src.api.routers.community.create_community_assistant",
            return_value=browser_assistant([run1]),
        ):
            first = asyncio.run(
                collect(
                    _stream_chat_response(
                        BROWSER_COMMUNITY,
                        session,
                        None,
                        None,
                        None,
                        declared_client_tools={"execute_code"},
                    )
                )
            )
        assert first[-1]["event"] == "tool_request"
        assert first[-1]["usage"]["input_tokens"] == 100
        assert first[-1]["usage"]["cache_read_tokens"] == 40
        monkeypatch.setattr(
            "src.api.routers.community.create_community_assistant",
            lambda *_a, **_k: browser_assistant([run2]),
        )

        response = resume_client.post(
            f"/{BROWSER_COMMUNITY}/chat/resume",
            headers={"Origin": BROWSER_ORIGIN},
            json={
                "session_id": "sess-two-runs",
                "result": {"call_id": first[-1]["call_id"], "status": "ok", "summary": "peak"},
                "client_tools": ["execute_code"],
            },
        )

        assert response.status_code == 200
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events[-1]["event"] == "done"
        assert events[-1]["usage"]["input_tokens"] == 150, "run 2's own, not run 1's added"
        assert events[-1]["usage"]["output_tokens"] == 20
        assert events[-1]["usage"]["cache_read_tokens"] == 0


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

    @offered
    @pytest.mark.parametrize("endpoint", ["ask", "chat"])
    def test_cached_tokens_are_each_in_their_own_field(
        self,
        provider: Provider,
        endpoint: str,
        client: TestClient,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, provider, [[*scripted_reply(provider, ANSWER, usage=False), CACHED]])
        body = (
            {"question": QUESTION, "stream": False}
            if endpoint == "ask"
            else {"message": QUESTION, "session_id": "sess-usage-cached", "stream": False}
        )

        response = client.post(f"/{COMMUNITY}/{endpoint}", headers={"Origin": ORIGIN}, json=body)

        assert response.status_code == 200
        assert response.json()["usage"] == _cached_usage(provider.model)

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
        assert not record.exc_info, "one line a reply, no traceback: the counts are in it"
        text = record.getMessage()
        for expected in (
            COMMUNITY,
            provider.model,
            "req-usage",
            "input=20",
            "cache_read=80",
            "cached tokens exceed",
        ):
            assert expected in text, f"{expected!r} missing from {text!r}"


class TestAPriceTableDefectIsNotTheProvidersFault:
    @offered
    async def test_a_price_that_is_not_a_number_is_logged_at_error_and_the_answer_goes_out(
        self, provider: Provider, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The counts are fine and the price is not: that is a defect in OSA's table, which
        would take the usage line from every reply, so it is an ERROR with its traceback,
        not a warning about a provider."""
        rate = MODEL_PRICING[provider.model]
        monkeypatch.setitem(MODEL_PRICING, provider.model, type(rate)(math.nan, rate.output_per_1m))

        with caplog.at_level(logging.WARNING):
            events = await _chat(provider, [scripted_reply(provider, ANSWER)])

        assert events[-1]["event"] == "done" and events[-1]["content"] == ANSWER
        assert events[-1]["usage"] is None
        (record,) = [
            r for r in caplog.records if "cost of a reply cannot be told" in r.getMessage()
        ]
        assert record.levelno == logging.ERROR and record.exc_info
        assert "estimated_cost" in record.getMessage(), "the line names the field that failed"


class TestWhatTheOperatorReads:
    """``_safe_reply_usage`` is called with whatever the provider reported. Its log line has to
    read as a sentence, and has to tell a provider's bad counts from a defect in OSA's own."""

    @staticmethod
    def _call(**counts: int):
        from src.api.routers.community import _safe_reply_usage
        from src.api.turn_outcome import ModelRuns

        arguments = {
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "longest_prompt_tokens": 100,
        } | counts
        return _safe_reply_usage(
            HAIKU,
            model_runs=ModelRuns(),
            community_id=COMMUNITY,
            request_id="req-log",
            **arguments,
        )

    def test_cached_tokens_above_the_input_are_named_as_usage_not_as_a_field(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A check on the whole object has no field to name; the line says "usage"."""
        with caplog.at_level(logging.WARNING):
            assert self._call(input_tokens=10, cache_read_tokens=50) is None

        (record,) = caplog.records
        assert "usage: Value error, cached tokens exceed the input" in record.getMessage()
        assert "(: " not in record.getMessage()

    def test_counts_that_also_make_the_cost_negative_are_still_the_providers(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A negative output count makes the cost negative too, but the counts were the
        problem: a warning about the provider, not an error about the price table."""
        with caplog.at_level(logging.WARNING):
            assert self._call(input_tokens=0, output_tokens=-1_000_000) is None

        (record,) = caplog.records
        assert record.levelno == logging.WARNING
        assert "output_tokens" in record.getMessage()


ANTHROPIC = next(p for p in PROVIDERS if p.name == "anthropic")


def _reported(input_tokens: int, output_tokens: int = 100) -> AIMessageChunk:
    """The usage a model run reports at its end."""
    return AIMessageChunk(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


def _runs(*prompts: int) -> list[list[AIMessageChunk]]:
    """A reply of one model run per prompt size: each run but the last calls a tool, as a
    tool loop does, and the last answers. Each run sends the conversation again, so a run's
    input is the size of its own prompt, and a run writes 100 tokens."""
    script = []
    for position, tokens in enumerate(prompts[:-1]):
        call = _anthropic_call(
            "lookup_scriptedreply_docs", f"toolu_01loop{position}", {"query": "x"}
        )
        script.append([*call, *scripted_reply(ANTHROPIC, "", usage=False), _reported(tokens)])
    script.append([*scripted_reply(ANTHROPIC, ANSWER, usage=False), _reported(prompts[-1])])
    return script


class TestTheLongPromptRateBelongsToOneModelRun:
    """Claude Haiku 5.5 is priced by the length of one prompt: $0.10 / $0.50 per million
    tokens, and $0.50 / $2.50 for a prompt over 100,000 tokens. A reply that calls the model
    several times sends the conversation each time, so its prompts add up past the line
    while none of them is near it. That reply is not a long prompt. The expected figures are
    written out from the published prices."""

    def test_the_provider_under_test_is_the_one_priced_by_prompt_length(self) -> None:
        assert ANTHROPIC.model == HAIKU

    async def test_a_stream_of_runs_that_are_each_under_the_line_is_billed_at_the_base_rates(
        self,
    ) -> None:
        events = await _chat(ANTHROPIC, _runs(40_000, 40_000, 40_000))

        usage = events[-1]["usage"]
        assert (usage["input_tokens"], usage["output_tokens"]) == (120_000, 300)
        assert usage["estimated_cost"] == pytest.approx(120_000 * 0.10 / 1e6 + 300 * 0.50 / 1e6)
        (row,) = _rows()
        assert row["input_tokens"] == 120_000
        assert row["estimated_cost"] == pytest.approx(usage["estimated_cost"])

    async def test_a_stream_with_one_run_over_the_line_is_billed_at_the_long_rates(self) -> None:
        events = await _chat(ANTHROPIC, _runs(10_000, 100_002))

        usage = events[-1]["usage"]
        assert (usage["input_tokens"], usage["output_tokens"]) == (110_002, 200)
        assert usage["estimated_cost"] == pytest.approx(110_002 * 0.50 / 1e6 + 200 * 2.50 / 1e6)
        (row,) = _rows()
        assert row["estimated_cost"] == pytest.approx(usage["estimated_cost"])

    def test_a_response_that_is_not_streamed_is_billed_at_the_base_rates_too(
        self,
        client: TestClient,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, ANTHROPIC, _runs(40_000, 40_000, 40_000))

        response = _ask_without_streaming(client)

        assert response.status_code == 200
        usage = response.json()["usage"]
        assert usage["input_tokens"] == 120_000
        assert usage["estimated_cost"] == pytest.approx(120_000 * 0.10 / 1e6 + 300 * 0.50 / 1e6)
        (row,) = _rows()
        assert row["estimated_cost"] == pytest.approx(usage["estimated_cost"])

    def test_a_response_that_is_not_streamed_with_a_run_over_the_line_is_billed_long(
        self,
        client: TestClient,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, ANTHROPIC, _runs(10_000, 100_002))

        response = _ask_without_streaming(client)

        assert response.status_code == 200
        usage = response.json()["usage"]
        assert usage["estimated_cost"] == pytest.approx(110_002 * 0.50 / 1e6 + 200 * 2.50 / 1e6)
        (row,) = _rows()
        assert row["estimated_cost"] == pytest.approx(usage["estimated_cost"])
