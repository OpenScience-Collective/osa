"""What a reply used and cost, as the reader is told (issue #582)."""

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.services.anthropic_models import OFFERED_MODELS, OPENROUTER_MODEL_IDS
from src.metrics.cost import MODEL_PRICING
from src.metrics.reply_usage import ReplyUsage, reply_usage

USAGE_LINES = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "usage_lines.json").read_text()
)


class TestPricing:
    def test_a_cached_reply_is_priced_by_hand(self) -> None:
        """GPT-6 Luna: $0.11 and $0.55 per million input and output tokens, and a cache
        read costs a tenth of an input token. 200 fresh input tokens, 800 cache reads and
        200 output tokens come to (22 + 8.8 + 110) / 1,000,000 dollars."""
        usage = reply_usage("openai.gpt-6-luna", 1000, 200, cache_read_tokens=800)

        assert usage == ReplyUsage(
            input_tokens=1000,
            output_tokens=200,
            cache_read_tokens=800,
            cache_creation_tokens=0,
            estimated_cost=0.000141,
            partial=False,
        )

    def test_a_cache_write_costs_a_quarter_more_than_an_input_token(self) -> None:
        """claude-haiku-4-5 ($1 and $5 per million): 500 fresh input tokens, 500 written to
        the cache, 100 output tokens: (500 + 625 + 500) / 1,000,000 dollars."""
        usage = reply_usage("claude-haiku-4-5", 1000, 100, cache_creation_tokens=500)

        assert usage is not None and usage.estimated_cost == 0.001625

    @pytest.mark.parametrize("model", sorted(OFFERED_MODELS))
    def test_every_offered_model_has_a_price(self, model: str) -> None:
        """A model that is offered but not priced would show the reader tokens and no cost,
        and the dashboard would price it at the fallback rate."""
        assert model in MODEL_PRICING
        usage = reply_usage(model, 100, 10)
        assert usage is not None and usage.estimated_cost is not None

    def test_a_reply_some_of_whose_runs_reported_nothing_says_it_is_partial(self) -> None:
        usage = reply_usage("claude-haiku-4-5", 100, 10, partial=True)

        assert usage is not None and usage.partial is True


class TestWhenThereIsNothingToTell:
    def test_a_request_with_no_tokens_is_not_a_free_one(self) -> None:
        assert reply_usage("claude-haiku-4-5", 0, 0) is None

    @pytest.mark.parametrize(
        "model",
        [
            "qwen/qwen3-235b-a22b-2507",
            "anthropic/claude-haiku-4.5",
            "some-custom-model",
            None,
        ],
    )
    def test_a_model_id_that_is_not_an_offered_one_reports_nothing(self, model: str | None) -> None:
        """A request served through OpenRouter carries the OpenRouter slug as its model id,
        which is never an offered id, even for a model OSA offers (the second case)."""
        assert reply_usage(model, 100, 10) is None


class TestTheGateIsTheRoute:
    """``reply_usage`` leaves OpenRouter out by looking at the model id, which is the
    OpenRouter slug on that route. That holds only while no offered id is also a slug."""

    @pytest.mark.parametrize("model", sorted(OFFERED_MODELS))
    def test_an_offered_model_goes_to_openrouter_under_a_slug_that_is_not_offered(
        self, model: str
    ) -> None:
        slug = OPENROUTER_MODEL_IDS[model]

        assert slug not in OFFERED_MODELS
        assert reply_usage(slug, 100, 10) is None


class TestWhatCannotBeUsage:
    """Counts that say something a provider cannot mean raise, and the router turns that
    into no usage and a log line, not into a wrong figure."""

    @pytest.mark.parametrize(
        "counts",
        [
            {"input_tokens": -1},
            {"output_tokens": -5000},
            {"cache_read_tokens": -1},
            {"cache_creation_tokens": -1},
            {"cache_read_tokens": 900},
            {"cache_read_tokens": 60, "cache_creation_tokens": 60},
        ],
        ids=lambda c: next(iter(c)) + "=" + str(next(iter(c.values()))),
    )
    def test_negative_counts_and_cached_tokens_above_the_input_are_refused(
        self, counts: dict[str, int]
    ) -> None:
        arguments = {"input_tokens": 100, "output_tokens": 10} | counts

        with pytest.raises(ValidationError):
            reply_usage(
                "claude-haiku-4-5",
                arguments["input_tokens"],
                arguments["output_tokens"],
                arguments.get("cache_read_tokens", 0),
                arguments.get("cache_creation_tokens", 0),
            )

    @pytest.mark.parametrize("cost", [-0.01, math.nan, math.inf])
    def test_a_cost_that_is_negative_or_not_a_number_is_refused(self, cost: float) -> None:
        with pytest.raises(ValidationError):
            ReplyUsage(
                input_tokens=100,
                output_tokens=10,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                estimated_cost=cost,
                partial=False,
            )

    def test_all_the_input_may_be_cached(self) -> None:
        usage = reply_usage("claude-haiku-4-5", 100, 10, cache_read_tokens=100)

        assert usage is not None and usage.cache_read_tokens == 100


class TestTheSharedTable:
    @pytest.mark.parametrize(
        "case",
        [
            c
            for c in USAGE_LINES
            if isinstance(c["usage"], dict) and "cache_read_tokens" in c["usage"]
        ],
        ids=lambda c: c["name"],
    )
    def test_the_usage_objects_in_it_are_the_shape_the_server_sends(self, case: dict) -> None:
        """The CLI and the widget read these objects by key, so a field renamed here would
        pass their tests and quietly drop part of the line."""
        usage = case["usage"]

        assert ReplyUsage.model_validate(usage).model_dump(mode="json") == usage


def test_the_usage_is_the_shape_the_events_carry() -> None:
    usage = reply_usage("claude-haiku-4-5", 100, 10)

    assert usage is not None
    assert set(usage.model_dump()) == {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "estimated_cost",
        "partial",
    }
