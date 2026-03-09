"""Tests for model cost protection.

Verifies that expensive models are blocked when using platform/community keys,
but allowed when users provide their own API key (BYOK).
"""

import pytest
from fastapi import HTTPException

from src.api.routers.community import _check_model_cost
from src.metrics.cost import COST_BLOCK_THRESHOLD, COST_WARN_THRESHOLD, MODEL_PRICING


def _models_by_cost(min_rate: float = 0.0, max_rate: float = float("inf")) -> list[str]:
    """Return model names with input rates in [min_rate, max_rate)."""
    return [m for m, (inp, _) in MODEL_PRICING.items() if min_rate <= inp < max_rate]


class TestCheckModelCost:
    """Tests for _check_model_cost() pre-invocation cost guard."""

    def test_cheap_model_on_platform_key_allowed(self) -> None:
        """Cheap models should be allowed on platform keys without error."""
        cheap_models = _models_by_cost(max_rate=COST_WARN_THRESHOLD)
        assert cheap_models, "Test requires at least one cheap model in MODEL_PRICING"

        _check_model_cost(cheap_models[0], "platform")
        _check_model_cost(cheap_models[0], "community")

    def test_expensive_model_blocked_on_platform_key(self) -> None:
        """Models above block threshold should be rejected with 403 on platform keys."""
        expensive_models = _models_by_cost(min_rate=COST_BLOCK_THRESHOLD)
        assert expensive_models, "Test requires at least one expensive model in MODEL_PRICING"

        with pytest.raises(HTTPException) as exc_info:
            _check_model_cost(expensive_models[0], "platform")
        assert exc_info.value.status_code == 403
        assert "exceeds the platform limit" in exc_info.value.detail
        assert "openrouter.ai/keys" in exc_info.value.detail

    def test_expensive_model_blocked_on_community_key(self) -> None:
        """Models above block threshold should also be rejected on community keys."""
        expensive_models = _models_by_cost(min_rate=COST_BLOCK_THRESHOLD)
        assert expensive_models, "Test requires at least one expensive model in MODEL_PRICING"

        with pytest.raises(HTTPException) as exc_info:
            _check_model_cost(expensive_models[0], "community")
        assert exc_info.value.status_code == 403

    def test_expensive_model_allowed_with_byok(self) -> None:
        """BYOK users should be able to use any model, even expensive ones."""
        expensive_models = _models_by_cost(min_rate=COST_BLOCK_THRESHOLD)
        assert expensive_models, "Test requires at least one expensive model in MODEL_PRICING"

        _check_model_cost(expensive_models[0], "byok")

    def test_unknown_model_blocked_on_platform_key(self) -> None:
        """Unknown models (not in pricing table) should be blocked on platform keys."""
        with pytest.raises(HTTPException) as exc_info:
            _check_model_cost("unknown/made-up-model-xyz", "platform")
        assert exc_info.value.status_code == 403
        assert "not in the approved pricing list" in exc_info.value.detail

    def test_unknown_model_allowed_with_byok(self) -> None:
        """BYOK users with unknown models should also be allowed."""
        _check_model_cost("unknown/made-up-model-xyz", "byok")

    def test_warn_threshold_model_not_blocked(self) -> None:
        """Models between warn and block thresholds should be allowed (just warned)."""
        warn_only_models = _models_by_cost(
            min_rate=COST_WARN_THRESHOLD, max_rate=COST_BLOCK_THRESHOLD
        )
        if not warn_only_models:
            pytest.skip("No models between warn and block thresholds in current pricing")

        _check_model_cost(warn_only_models[0], "platform")

    def test_model_at_exact_block_threshold_is_blocked(self) -> None:
        """A model priced exactly at the block threshold should be blocked."""
        exact_models = [m for m, (inp, _) in MODEL_PRICING.items() if inp == COST_BLOCK_THRESHOLD]
        if not exact_models:
            pytest.skip("No model priced exactly at block threshold")

        with pytest.raises(HTTPException) as exc_info:
            _check_model_cost(exact_models[0], "platform")
        assert exc_info.value.status_code == 403

    def test_thresholds_are_sane(self) -> None:
        """Sanity check: warn threshold should be lower than block threshold."""
        assert COST_WARN_THRESHOLD < COST_BLOCK_THRESHOLD
        assert COST_WARN_THRESHOLD > 0
        assert COST_BLOCK_THRESHOLD > 0
