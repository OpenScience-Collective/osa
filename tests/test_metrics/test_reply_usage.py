"""What a reply used and cost, as the reader is told (issue #582)."""

import pytest

from src.core.services.anthropic_models import OFFERED_MODELS
from src.metrics.cost import MODEL_PRICING
from src.metrics.reply_usage import ReplyUsage, reply_usage


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


class TestWhenThereIsNothingToTell:
    def test_a_request_with_no_tokens_is_not_a_free_one(self) -> None:
        assert reply_usage("claude-haiku-4-5", 0, 0) is None

    @pytest.mark.parametrize("model", ["qwen/qwen3-235b-a22b-2507", "some-custom-model", None])
    def test_a_model_osa_does_not_offer_itself_reports_nothing(self, model: str | None) -> None:
        """OpenRouter-served requests are left out for now: their cache figures are an
        approximation, not something to put in front of a reader as a cost."""
        assert reply_usage(model, 100, 10) is None


def test_the_usage_is_the_shape_the_events_carry() -> None:
    usage = reply_usage("claude-haiku-4-5", 100, 10)

    assert usage is not None
    assert set(usage.model_dump()) == {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "estimated_cost",
    }
