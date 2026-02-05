"""Tests for cost estimation."""

from src.metrics.cost import MODEL_PRICING, estimate_cost


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

    def test_qwen_model_cost(self):
        """Verify cost for a Qwen model."""
        cost = estimate_cost(
            "qwen/qwen3-235b-a22b-2507",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        # input: 0.14, output: 0.34, total: 0.48
        assert cost == 0.48

    def test_expensive_model(self):
        """Verify cost for an expensive model (Claude Opus 4)."""
        cost = estimate_cost(
            "anthropic/claude-opus-4",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        # input: 15.00, output: 75.00, total: 90.00
        assert cost == 90.0
