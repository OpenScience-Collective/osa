"""Tests for the dependency-free model tables.

``normalize_model`` and the tables themselves are exercised through
tests/test_core/test_anthropic_llm.py, which imports them from their
re-export site. What is tested here is what the split adds: the sampling
question a config validator has to answer, and the guarantee that the two
modules describe one set of models rather than two that can drift.
"""

import typing

import pytest

from src.core.services import anthropic_llm, anthropic_models
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    IMAGE_MEDIA_TYPES,
    MODEL_ALIASES,
    OFFERED_MODELS,
    REASONING_DEFAULTS,
    SAMPLING_MODELS,
    accepts_temperature,
    is_bedrock_model,
    normalize_model,
)


class TestAcceptsTemperature:
    """Which models honor a configured temperature."""

    def test_haiku_accepts_temperature(self) -> None:
        assert accepts_temperature("claude-haiku-4-5") is True

    def test_sonnet_5_5_does_not(self) -> None:
        """Claude 5-generation models 400 on any non-default temperature."""
        assert accepts_temperature("claude-sonnet-5-5") is False

    @pytest.mark.parametrize("alias", sorted(MODEL_ALIASES))
    def test_aliases_answer_for_the_model_they_resolve_to(self, alias: str) -> None:
        """A legacy id must get the same answer as its first-party id.

        A community config still naming "anthropic/claude-sonnet-4.5" bills
        claude-sonnet-5-5, so it has to be judged as claude-sonnet-5-5.
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


class TestImageMediaTypes:
    """The accepted image media types are a hand-copy; hold them to their source.

    ``anthropic_models`` stays free of third-party imports so a CLI-only
    install can reach it, which is why the set is written out rather than read
    from the anthropic SDK. Here, where the server extra is installed, the copy
    is compared with the SDK's own declaration, in the same spirit as
    ``TestReExportsAreTheSameObjects`` above: two statements of one fact, with
    something that fails when they stop agreeing.

    Why the set is declared at all, rather than left implicit: nothing in the
    client stack validates a media type, so a producer that emits an unaccepted
    one learns about it as a 400 from the endpoint. See
    tests/test_core/test_tool_result_image_transport.py, which measures that.
    """

    def test_matches_the_anthropic_sdk(self) -> None:
        from anthropic.types import Base64ImageSourceParam

        declared = typing.get_type_hints(Base64ImageSourceParam)["media_type"]

        assert set(typing.get_args(declared)) == set(IMAGE_MEDIA_TYPES)


class TestSonnetAliases:
    """Sonnet 5.5 replaced Sonnet 5, so every older Sonnet id must land on it."""

    @pytest.mark.parametrize(
        "legacy",
        [
            "claude-sonnet-5",
            "claude-sonnet-5.5",
            "anthropic/claude-sonnet-5",
            "anthropic/claude-sonnet-5.5",
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-sonnet-4.5",
            "claude-sonnet-4.5",
        ],
    )
    def test_earlier_sonnet_ids_resolve_to_sonnet_5_5(self, legacy: str) -> None:
        assert normalize_model(legacy) == "claude-sonnet-5-5"

    def test_sonnet_5_is_no_longer_offered_but_still_resolves(self) -> None:
        """Saved widget settings and community configs may still name it."""
        assert "claude-sonnet-5" not in OFFERED_MODELS
        assert normalize_model("claude-sonnet-5") in OFFERED_MODELS

    def test_every_alias_resolves_to_an_offered_model(self) -> None:
        """An alias to a model nobody can select would fail at request time."""
        assert set(MODEL_ALIASES.values()) <= set(OFFERED_MODELS)


class TestBedrockModels:
    """The models served from Amazon Bedrock are offered like any other."""

    def test_every_bedrock_model_is_offered_with_its_label(self) -> None:
        for model_id, spec in BEDROCK_MODELS.items():
            assert OFFERED_MODELS[model_id] == spec.label

    def test_no_bedrock_id_collides_with_a_claude_id(self) -> None:
        assert all(not model_id.startswith("claude-") for model_id in BEDROCK_MODELS)

    @pytest.mark.parametrize("model_id", sorted(BEDROCK_MODELS))
    def test_a_bedrock_model_normalizes_to_itself(self, model_id: str) -> None:
        assert normalize_model(model_id) == model_id
        assert is_bedrock_model(model_id) is True

    def test_claude_models_are_not_bedrock_models(self) -> None:
        assert is_bedrock_model("claude-haiku-4-5") is False
        assert is_bedrock_model("claude-sonnet-5") is False
        assert is_bedrock_model(None) is False

    def test_an_unoffered_model_is_not_a_bedrock_model(self) -> None:
        assert is_bedrock_model("openai/gpt-5") is False

    def test_bedrocks_own_ids_resolve(self) -> None:
        """A console-copied inference profile or versioned id names the same model."""
        assert normalize_model("us.openai.gpt-6-luna") == "openai.gpt-6-luna"
        assert normalize_model("openai.gpt-oss-120b-1:0") == "openai.gpt-oss-120b"

    def test_every_invoke_id_is_distinct(self) -> None:
        invoke_ids = [spec.invoke_id for spec in BEDROCK_MODELS.values()]
        assert len(set(invoke_ids)) == len(invoke_ids)

    def test_luna_runs_at_high_effort_in_the_us_profile(self) -> None:
        """Ohio calls GPT-6 Luna through the us. profile; global. is denied by SCP."""
        luna = BEDROCK_MODELS["openai.gpt-6-luna"]
        assert luna.invoke_id.startswith("us.")
        assert luna.reasoning_field == "nested"
        assert luna.reasoning_request_fields("high") == {"reasoning": {"effort": "high"}}
        assert REASONING_DEFAULTS["openai.gpt-6-luna"] == "high"

    def test_only_automatic_caching_is_claimed_for_luna(self) -> None:
        """Bedrock rejects cache points for all three, so only Luna's own caching exists."""
        assert BEDROCK_MODELS["openai.gpt-6-luna"].caching == "automatic"
        others = {m for m, spec in BEDROCK_MODELS.items() if spec.caching != "automatic"}
        assert others == set(BEDROCK_MODELS) - {"openai.gpt-6-luna"}

    def test_luna_takes_no_temperature_and_the_others_do(self) -> None:
        assert accepts_temperature("openai.gpt-6-luna") is False
        assert accepts_temperature("openai.gpt-oss-120b") is True
        assert accepts_temperature("qwen.qwen3-next-80b-a3b") is True
