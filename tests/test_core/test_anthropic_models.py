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
    DEFAULT_REASONING_EFFORT,
    HAIKU,
    IMAGE_MEDIA_TYPES,
    LUNA,
    MODEL_ALIASES,
    MODEL_CLASSES,
    NO_REASONING_LEVELS,
    OFFERED_MODELS,
    OPENROUTER_MODEL_IDS,
    PREVIOUS_GENERATIONS,
    REASONING_LEVELS,
    SAMPLING_MODELS,
    SONNET,
    SUGGESTED_MODELS,
    THINKING_OFF,
    accepts_temperature,
    is_bedrock_model,
    normalize_model,
)


class TestAcceptsTemperature:
    """Which models honor a configured temperature."""

    @pytest.mark.parametrize("model", [HAIKU, "claude-sonnet-5-5"])
    def test_claude_5_models_do_not(self, model: str) -> None:
        """Claude 5-generation models 400 on any non-default temperature (Haiku 5.5 took
        over from Haiku 4.5, which accepted one)."""
        assert accepts_temperature(model) is False

    @pytest.mark.parametrize("model", ["openai.gpt-oss-120b", "qwen.qwen3-next-80b-a3b"])
    def test_models_that_still_take_sampling_parameters_do(self, model: str) -> None:
        assert accepts_temperature(model) is True

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


class TestModelClasses:
    """A class names a tier; MODEL_CLASSES says which model it is today (ADR 0016)."""

    EXPECTED_CLASSES = {"haiku", "sonnet", "opus", "fable", "luna", "terra", "sol", "astra"}

    def test_the_classes_are_the_eight_the_decision_names(self) -> None:
        assert set(MODEL_CLASSES) == self.EXPECTED_CLASSES

    def test_the_constants_are_the_classes_ids(self) -> None:
        assert (
            MODEL_CLASSES["haiku"],
            MODEL_CLASSES["sonnet"],
            MODEL_CLASSES["luna"],
        ) == (HAIKU, SONNET, LUNA)

    def test_two_classes_are_never_the_same_model(self) -> None:
        assert len(set(MODEL_CLASSES.values())) == len(MODEL_CLASSES)

    @pytest.mark.parametrize("name", sorted(MODEL_CLASSES))
    def test_a_class_name_is_an_offered_model_or_refused_as_unavailable(self, name: str) -> None:
        if MODEL_CLASSES[name] in OFFERED_MODELS:
            assert normalize_model(name) == MODEL_CLASSES[name]
        else:
            with pytest.raises(ValueError, match="not available"):
                normalize_model(name)

    def test_the_default_is_the_haiku_class(self) -> None:
        assert normalize_model(None) == normalize_model("haiku") == HAIKU

    def test_the_classes_that_are_offered_are_the_ones_the_deployment_runs(self) -> None:
        """If this changes, a community config naming a class changes with it."""
        offered = {name for name, model in MODEL_CLASSES.items() if model in OFFERED_MODELS}
        assert {"haiku", "sonnet", "luna"} <= offered

    @pytest.mark.parametrize("name", sorted(PREVIOUS_GENERATIONS))
    def test_a_class_has_only_classes_it_names_as_previous_generations(self, name: str) -> None:
        assert name in MODEL_CLASSES

    @pytest.mark.parametrize(
        ("name", "old"),
        sorted((name, old) for name, olds in PREVIOUS_GENERATIONS.items() for old in olds),
    )
    def test_a_previous_generation_resolves_to_the_classes_current_model(
        self, name: str, old: str
    ) -> None:
        assert normalize_model(old) == MODEL_CLASSES[name]
        assert old not in OFFERED_MODELS, "a previous id is not itself offered"

    def test_haiku_4_5_resolves_to_haiku_5_5(self) -> None:
        """The move the decision was made for: a saved setting that names the old Haiku."""
        for legacy in ("claude-haiku-4-5", "claude-haiku-4.5", "anthropic/claude-haiku-4.5"):
            assert normalize_model(legacy) == HAIKU

    def test_no_alias_hides_an_offered_model(self) -> None:
        """An offered id that is also an alias would make two answers to one question."""
        assert not set(MODEL_ALIASES) & set(OFFERED_MODELS)

    @pytest.mark.parametrize("model", sorted(OFFERED_MODELS))
    def test_every_offered_model_has_everything_the_deployment_needs(self, model: str) -> None:
        assert OFFERED_MODELS[model], "a label"
        assert model in OPENROUTER_MODEL_IDS, "an OpenRouter slug"
        assert model in REASONING_LEVELS or model in NO_REASONING_LEVELS, "a reasoning verdict"
        assert model in THINKING_OFF or model in BEDROCK_MODELS, "a way to be served"

    def test_the_suggested_models_are_offered_classes(self) -> None:
        assert SUGGESTED_MODELS == (HAIKU, SONNET)
        assert set(SUGGESTED_MODELS) <= set(OFFERED_MODELS)


class TestDerivedFromTheId:
    """The label and the OpenRouter slug of a Claude model follow from its id, so a new
    generation needs neither written out."""

    def test_the_label_names_the_family_and_the_dotted_version(self) -> None:
        family, major, minor = HAIKU.removeprefix("claude-").split("-")
        assert OFFERED_MODELS[HAIKU] == f"Claude {family.title()} {major}.{minor}"

    def test_the_openrouter_slug_is_the_anthropic_creator_and_the_dotted_id(self) -> None:
        for model in (HAIKU, SONNET):
            major_minor = model.rsplit("-", 2)[-2:]
            dotted = model.rsplit("-", 2)[0] + "-" + ".".join(major_minor)
            assert OPENROUTER_MODEL_IDS[model] == f"anthropic/{dotted}"

    def test_the_spellings_of_the_current_ids_resolve(self) -> None:
        for model in (HAIKU, SONNET):
            dotted = OPENROUTER_MODEL_IDS[model].removeprefix("anthropic/")
            for spelling in (dotted, f"anthropic/{model}", OPENROUTER_MODEL_IDS[model]):
                assert normalize_model(spelling) == model


class TestThinkingOff:
    """How each offered Claude model turns thinking off, which differs per generation."""

    def test_it_covers_exactly_the_offered_claude_models(self) -> None:
        claude = {model for model in OFFERED_MODELS if model not in BEDROCK_MODELS}
        assert set(THINKING_OFF) == claude

    @pytest.mark.parametrize("model", sorted(THINKING_OFF))
    def test_each_value_is_a_bare_type_and_never_the_budget_style(self, model: str) -> None:
        off = THINKING_OFF[model]
        assert set(off) == {"type"}
        assert off["type"] in {"between_tools", "disabled"}

    def test_no_claude_model_offered_takes_sampling_parameters(self) -> None:
        assert not set(THINKING_OFF) & SAMPLING_MODELS


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
        assert is_bedrock_model(HAIKU) is False
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
        assert DEFAULT_REASONING_EFFORT == "high"

    def test_only_automatic_caching_is_claimed_for_luna(self) -> None:
        """Bedrock rejects cache points for all three, so only Luna's own caching exists."""
        assert BEDROCK_MODELS["openai.gpt-6-luna"].caching == "automatic"
        others = {m for m, spec in BEDROCK_MODELS.items() if spec.caching != "automatic"}
        assert others == set(BEDROCK_MODELS) - {"openai.gpt-6-luna"}

    def test_luna_takes_no_temperature_and_the_others_do(self) -> None:
        assert accepts_temperature("openai.gpt-6-luna") is False
        assert accepts_temperature("openai.gpt-oss-120b") is True
        assert accepts_temperature("qwen.qwen3-next-80b-a3b") is True
