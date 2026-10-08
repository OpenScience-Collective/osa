"""Tests for cost estimation."""

import pytest

from src.core.services.anthropic_models import BEDROCK_MODELS, HAIKU, OPENROUTER_MODEL_IDS
from src.metrics.cost import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    LONG_PROMPT_RATES,
    MODEL_PRICING,
    estimate_cost,
)


class TestEstimateCost:
    """Tests for estimate_cost()."""

    def test_known_model(self):
        """Cost for a known model uses its pricing."""
        cost = estimate_cost("openai/gpt-4o", input_tokens=1000, output_tokens=500)
        # input: 1000 * 2.50 / 1M = 0.0025, output: 500 * 10.00 / 1M = 0.005
        assert cost == 0.0075

    def test_unknown_model_uses_fallback(self):
        """Unknown model uses fallback rates."""
        cost = estimate_cost("unknown/model", input_tokens=1_000_000, output_tokens=1_000_000)
        # fallback: 1.00 input + 3.00 output = 4.00
        assert cost == 4.0

    def test_none_model_uses_fallback(self):
        """None model uses fallback rates."""
        cost = estimate_cost(None, input_tokens=1_000_000, output_tokens=0)
        assert cost == 1.0

    def test_zero_tokens(self):
        """Zero tokens should return zero cost."""
        cost = estimate_cost("openai/gpt-4o", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_rounding(self):
        """Cost should be rounded to 6 decimal places."""
        cost = estimate_cost("openai/gpt-4o-mini", input_tokens=1, output_tokens=1)
        # input: 1 * 0.15 / 1M = 0.00000015, output: 1 * 0.60 / 1M = 0.0000006
        # total: 0.00000075 -> rounds to 0.000001
        assert cost == 0.000001

    def test_all_models_have_pricing(self):
        """All models in the pricing table should have valid (input, output) tuples."""
        for model, (input_rate, output_rate) in MODEL_PRICING.items():
            assert isinstance(input_rate, (int, float)), f"{model} has invalid input rate"
            assert isinstance(output_rate, (int, float)), f"{model} has invalid output rate"
            assert input_rate >= 0, f"{model} has negative input rate"
            assert output_rate >= 0, f"{model} has negative output rate"

    def test_gpt_6_luna_cost(self):
        """GPT-6 Luna is priced, so community keys can use it (issue #514)."""
        cost = estimate_cost("openai/gpt-6-luna", input_tokens=1_000_000, output_tokens=1_000_000)
        # input: 0.10, output: 0.50, total: 0.60
        assert cost == 0.6

    def test_qwen_model_cost(self):
        """Verify cost for a Qwen model."""
        cost = estimate_cost(
            "qwen/qwen3-235b-a22b-2507",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        # input: 0.07, output: 0.10, total: 0.17
        assert cost == 0.17

    def test_expensive_model(self):
        """Verify cost for an expensive model (Claude Opus 4)."""
        cost = estimate_cost(
            "anthropic/claude-opus-4",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        # input: 15.00, output: 75.00, total: 90.00
        assert cost == 90.0


class TestCacheMultiplierValues:
    """Pin the multiplier constants themselves.

    The other tests in this module recompute expected cost from these same
    constants, so a wrong multiplier value would be undetectable there.
    These are external business facts (Anthropic's published prompt-cache
    pricing: a 5-minute cache write costs 1.25x the base input rate, a cache
    read costs 0.1x), so hardcoding the expected values here is correct.
    """

    def test_cache_write_multiplier(self):
        assert CACHE_WRITE_MULTIPLIER == 1.25

    def test_cache_read_multiplier(self):
        assert CACHE_READ_MULTIPLIER == 0.1


class TestEstimateCostCacheAware:
    """Tests for estimate_cost()'s cache_read_tokens / cache_creation_tokens pricing."""

    def test_no_cache_detail_matches_pre_cache_aware_pricing(self):
        """Omitting cache args reproduces the flat-rate pricing exactly."""
        without_cache = estimate_cost(HAIKU, input_tokens=1000, output_tokens=500)
        with_explicit_zeros = estimate_cost(
            HAIKU,
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )
        assert without_cache == with_explicit_zeros

    def test_cache_read_cheaper_than_fresh_input(self):
        """Identical token counts cost less when most input was a cache read."""
        model = HAIKU
        total_input = 100_000
        output_tokens = 1000

        all_fresh = estimate_cost(model, input_tokens=total_input, output_tokens=output_tokens)
        mostly_cache_read = estimate_cost(
            model,
            input_tokens=total_input,
            output_tokens=output_tokens,
            cache_read_tokens=90_000,
        )

        assert mostly_cache_read < all_fresh

    def test_cache_read_priced_at_read_multiplier(self):
        """A fully cache-read request costs input_rate * CACHE_READ_MULTIPLIER.

        A prompt of 100,000 tokens, the longest one in Haiku's base price tier.
        """
        rate = MODEL_PRICING[HAIKU]
        cost = estimate_cost(
            HAIKU,
            input_tokens=100_000,
            output_tokens=0,
            cache_read_tokens=100_000,
        )
        assert cost == round(100_000 * rate.input_per_1m * CACHE_READ_MULTIPLIER / 1_000_000, 6)

    def test_cache_creation_priced_at_write_multiplier(self):
        """A fully cache-write request costs input_rate * CACHE_WRITE_MULTIPLIER."""
        rate = MODEL_PRICING[HAIKU]
        cost = estimate_cost(
            HAIKU,
            input_tokens=100_000,
            output_tokens=0,
            cache_creation_tokens=100_000,
        )
        assert cost == round(100_000 * rate.input_per_1m * CACHE_WRITE_MULTIPLIER / 1_000_000, 6)

    def test_cache_write_costs_more_than_fresh_input(self):
        """A cache write premium costs more than the same tokens as fresh input."""
        model = HAIKU
        all_fresh = estimate_cost(model, input_tokens=100_000, output_tokens=0)
        all_cache_write = estimate_cost(
            model, input_tokens=100_000, output_tokens=0, cache_creation_tokens=100_000
        )
        assert all_cache_write > all_fresh

    def test_mixed_cache_and_fresh_input(self):
        """Ordinary, cache-read, and cache-write portions are priced independently."""
        rate = MODEL_PRICING[HAIKU]
        cost = estimate_cost(
            HAIKU,
            input_tokens=1000,
            output_tokens=0,
            cache_read_tokens=300,
            cache_creation_tokens=200,
        )
        # ordinary: 1000 - 300 - 200 = 500
        expected = (
            500 * rate.input_per_1m
            + 200 * rate.input_per_1m * CACHE_WRITE_MULTIPLIER
            + 300 * rate.input_per_1m * CACHE_READ_MULTIPLIER
        ) / 1_000_000
        assert cost == round(expected, 6)


#: The price tier the Bedrock-served models are held to, USD per 1M tokens (input, output):
#: what Claude Haiku 4.5 cost. Claude Haiku 5.5 replaced it as the Claude default at about
#: a tenth of that price, so it is no longer the ceiling; this one is written out so a model
#: that costs more than the cheap tier cannot be added by accident.
CHEAP_TIER_CEILING = (1.00, 5.00)


class TestBedrockModelsStayInTheCheapTier:
    """The Bedrock-served models exist to be a cheap option for small deployments.

    A price change or a newly added model that cost more than the cheap tier would defeat
    the reason they are offered. Queried from the registry, not listed.
    """

    def test_every_bedrock_model_is_priced(self):
        missing = set(BEDROCK_MODELS) - set(MODEL_PRICING)
        assert not missing, f"Bedrock models missing from MODEL_PRICING: {missing}"

    def test_no_bedrock_model_costs_more_than_the_cheap_tier(self):
        max_input, max_output = CHEAP_TIER_CEILING
        for model_id in BEDROCK_MODELS:
            rate = MODEL_PRICING[model_id]
            assert rate.input_per_1m <= max_input, model_id
            assert rate.output_per_1m <= max_output, model_id

    def test_automatic_caching_uses_the_platform_cache_multipliers(self):
        """Luna bills cache writes at 1.25x and reads at 0.1x its input rate.

        The Bedrock model card lists $0.1375 to write and $0.011 to read, against
        $0.11 for fresh input, which are the multipliers estimate_cost applies.
        """
        rate = MODEL_PRICING["openai.gpt-6-luna"]
        assert rate.input_per_1m * CACHE_WRITE_MULTIPLIER == pytest.approx(0.1375)
        assert rate.input_per_1m * CACHE_READ_MULTIPLIER == pytest.approx(0.011)


class TestLongPromptPricing:
    """Claude Haiku 5.5 is priced by prompt length: the whole request moves to the higher
    rates once its prompt is over 100,000 tokens. The prices are Anthropic's published
    ones ($0.10 / $0.50, and $0.50 / $2.50 above), so they are written out here."""

    def test_the_base_tier_is_the_models_pricing_entry(self):
        rate = MODEL_PRICING[HAIKU]
        assert (rate.input_per_1m, rate.output_per_1m) == (0.10, 0.50)

    def test_a_prompt_at_the_line_is_in_the_base_tier(self):
        cost = estimate_cost(HAIKU, input_tokens=100_000, output_tokens=1_000_000)
        assert cost == round(100_000 * 0.10 / 1_000_000 + 1_000_000 * 0.50 / 1_000_000, 6)

    def test_a_prompt_one_token_over_the_line_is_billed_whole_at_the_long_rates(self):
        cost = estimate_cost(HAIKU, input_tokens=100_001, output_tokens=1_000_000)
        assert cost == round(100_001 * 0.50 / 1_000_000 + 1_000_000 * 2.50 / 1_000_000, 6)

    def test_cache_multipliers_apply_to_the_long_input_rate(self):
        cost = estimate_cost(
            HAIKU,
            input_tokens=200_000,
            output_tokens=0,
            cache_read_tokens=150_000,
            cache_creation_tokens=20_000,
        )
        expected = (
            30_000 * 0.50
            + 20_000 * 0.50 * CACHE_WRITE_MULTIPLIER
            + 150_000 * 0.50 * CACHE_READ_MULTIPLIER
        ) / 1_000_000
        assert cost == round(expected, 6)

    def test_the_openrouter_slug_is_priced_the_same(self):
        slug = OPENROUTER_MODEL_IDS[HAIKU]
        assert MODEL_PRICING[slug] == MODEL_PRICING[HAIKU]
        assert LONG_PROMPT_RATES[slug] == LONG_PROMPT_RATES[HAIKU]
        assert estimate_cost(slug, 300_000, 1_000) == estimate_cost(HAIKU, 300_000, 1_000)

    def test_every_long_prompt_rate_is_for_a_priced_model_and_costs_more(self):
        for model, tier in LONG_PROMPT_RATES.items():
            base = MODEL_PRICING[model]
            assert tier.rate.input_per_1m > base.input_per_1m, model
            assert tier.rate.output_per_1m > base.output_per_1m, model

    def test_the_retired_generation_stays_priced_for_old_request_logs(self):
        """Logs written before Haiku 5.5 name claude-haiku-4-5; the dashboard prices them."""
        old = MODEL_PRICING["claude-haiku-4-5"]
        assert (old.input_per_1m, old.output_per_1m) == (1.00, 5.00)
