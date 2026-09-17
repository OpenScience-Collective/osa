"""Tests for the dependency-free model tables.

``normalize_model`` and the tables themselves are exercised through
tests/test_core/test_anthropic_llm.py, which imports them from their
re-export site. What is tested here is what the split adds: the sampling
question a config validator has to answer, and the guarantee that the two
modules describe one set of models rather than two that can drift.
"""

import pytest

from src.core.services import anthropic_llm, anthropic_models
from src.core.services.anthropic_models import (
    MODEL_ALIASES,
    OFFERED_MODELS,
    SAMPLING_MODELS,
    accepts_temperature,
    normalize_model,
)


class TestAcceptsTemperature:
    """Which models honor a configured temperature."""

    def test_haiku_accepts_temperature(self) -> None:
        assert accepts_temperature("claude-haiku-4-5") is True

    def test_sonnet_5_does_not(self) -> None:
        """Claude 5-generation models 400 on any non-default temperature."""
        assert accepts_temperature("claude-sonnet-5") is False

    @pytest.mark.parametrize("alias", sorted(MODEL_ALIASES))
    def test_aliases_answer_for_the_model_they_resolve_to(self, alias: str) -> None:
        """A legacy id must get the same answer as its first-party id.

        A community config still naming "anthropic/claude-sonnet-4.5" bills
        claude-sonnet-5, so it has to be judged as claude-sonnet-5.
        """
        assert accepts_temperature(alias) == accepts_temperature(MODEL_ALIASES[alias])

    def test_unknown_model_is_false_rather_than_raising(self) -> None:
        """Callers ask this while reporting on a config, not while sending one.

        An unoffered model cannot be sent at all, so "does it accept a
        temperature" is moot; raising here would turn a warning about one
        field into a crash while reading a config file.
        """
        assert accepts_temperature("openai/gpt-5") is False

    def test_none_answers_for_the_default_model(self) -> None:
        assert accepts_temperature(None) == accepts_temperature(anthropic_models.DEFAULT_MODEL)

    def test_every_sampling_model_is_offered(self) -> None:
        """A sampling entry for a model nobody can select is dead weight."""
        assert set(OFFERED_MODELS) >= SAMPLING_MODELS

    def test_the_answer_covers_every_offered_model(self) -> None:
        """Dynamic, so a third offered model cannot skip this decision."""
        for model in OFFERED_MODELS:
            assert accepts_temperature(model) == (model in SAMPLING_MODELS)


class TestReExportsAreTheSameObjects:
    """anthropic_llm re-exports these; it must not carry its own copies.

    Server-side callers (routers, agents, most tests) import the tables from
    anthropic_llm, and config validation imports them from anthropic_models.
    Two dicts with the same contents today would be two dicts to update
    tomorrow, and only one of the two would be wrong.
    """

    @pytest.mark.parametrize(
        "name", ["DEFAULT_MODEL", "OFFERED_MODELS", "MODEL_ALIASES", "SAMPLING_MODELS"]
    )
    def test_table_is_shared(self, name: str) -> None:
        assert getattr(anthropic_llm, name) is getattr(anthropic_models, name)

    def test_normalize_model_is_shared(self) -> None:
        assert anthropic_llm.normalize_model is normalize_model
