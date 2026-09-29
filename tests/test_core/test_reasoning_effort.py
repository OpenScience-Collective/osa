"""Reasoning effort (issue #545): the neutral scale, each model's levels, the clamp,
and the community setting that carries a level to every provider.

The tables are read, not restated: every test that ranges over models or levels ranges
over ``REASONING_LEVELS`` and ``OFFERED_MODELS``, so a model added later is covered
without touching this file (and one that is not classified fails the partition test).
The two policy rules the maintainer set are pinned by name, in the one place where
deriving them from the table would let a wrong table pass its own test: Claude Sonnet
is never run above ``high``, and a level a model does not accept is never sent.
"""

import warnings

import pytest
from pydantic import ValidationError

from src.core.config.community import CommunityConfig
from src.core.services.anthropic_models import (
    MODEL_ALIASES,
    NO_REASONING_LEVELS,
    OFFERED_MODELS,
    REASONING_LEVELS,
    REASONING_SCALE,
    reasoning_levels,
    resolve_reasoning_effort,
)

SONNET = "claude-sonnet-5-5"


def _rank(level: str) -> int:
    return REASONING_SCALE.index(level)


class TestTheTables:
    def test_the_scale_runs_from_none_to_max(self) -> None:
        assert REASONING_SCALE[0] == "none"
        assert REASONING_SCALE[-1] == "max"
        assert len(set(REASONING_SCALE)) == len(REASONING_SCALE)

    def test_every_offered_model_is_classified_exactly_once(self) -> None:
        """A newly offered model must be put on one side: it has levels, or it has none."""
        with_levels = set(REASONING_LEVELS)
        assert with_levels.isdisjoint(NO_REASONING_LEVELS)
        assert with_levels | NO_REASONING_LEVELS == set(OFFERED_MODELS)

    @pytest.mark.parametrize("model", sorted(REASONING_LEVELS))
    def test_a_models_levels_are_on_the_scale_in_order(self, model: str) -> None:
        levels = REASONING_LEVELS[model]
        assert levels, model
        assert all(level in REASONING_SCALE for level in levels)
        assert [_rank(level) for level in levels] == sorted({_rank(level) for level in levels})

    def test_claude_sonnet_is_never_run_above_high(self) -> None:
        """The maintainer's rule: not xhigh, not max, whatever a community asks."""
        assert "high" in reasoning_levels(SONNET)
        assert "xhigh" not in reasoning_levels(SONNET)
        assert "max" not in reasoning_levels(SONNET)
        for asked in ("xhigh", "max"):
            assert resolve_reasoning_effort(SONNET, asked) == "high"

    def test_every_name_sonnet_answers_to_is_capped_the_same(self) -> None:
        """A saved widget setting, a CLI config or an OpenRouter slug reaches the same cap."""
        names = [alias for alias, target in MODEL_ALIASES.items() if target == SONNET]
        assert names, "the aliases of Claude Sonnet are what this test ranges over"
        for name in names:
            assert resolve_reasoning_effort(name, "max") == "high", name
            assert resolve_reasoning_effort(name, "xhigh") == "high", name

    def test_luna_and_gpt_oss_keep_the_levels_bedrock_was_measured_to_accept(self) -> None:
        """Measured against the live service on 2026-09-29 (see the table's comment)."""
        assert reasoning_levels("openai.gpt-6-luna") == (
            "none", "low", "medium", "high", "xhigh", "max",
        )  # fmt: skip
        assert "minimal" not in REASONING_SCALE
        assert reasoning_levels("openai.gpt-oss-120b") == ("low", "medium", "high")


class TestTheClamp:
    @pytest.mark.parametrize("model", sorted(REASONING_LEVELS))
    @pytest.mark.parametrize("asked", REASONING_SCALE)
    def test_a_model_only_ever_gets_a_level_it_accepts(self, model: str, asked: str) -> None:
        got = resolve_reasoning_effort(model, asked)
        assert got in REASONING_LEVELS[model]

    @pytest.mark.parametrize("model", sorted(REASONING_LEVELS))
    @pytest.mark.parametrize("asked", REASONING_SCALE)
    def test_it_is_the_nearest_level_at_or_below_the_ask_else_the_lowest(
        self, model: str, asked: str
    ) -> None:
        levels = REASONING_LEVELS[model]
        at_or_below = [level for level in levels if _rank(level) <= _rank(asked)]
        expected = at_or_below[-1] if at_or_below else levels[0]
        assert resolve_reasoning_effort(model, asked) == expected

    @pytest.mark.parametrize("model", sorted(REASONING_LEVELS))
    def test_a_level_a_model_accepts_is_used_as_asked(self, model: str) -> None:
        for level in REASONING_LEVELS[model]:
            assert resolve_reasoning_effort(model, level) == level

    def test_gpt_oss_cannot_go_below_low(self) -> None:
        assert resolve_reasoning_effort("openai.gpt-oss-120b", "none") == "low"

    def test_gpt_oss_cannot_go_above_high(self) -> None:
        for asked in ("xhigh", "max"):
            assert resolve_reasoning_effort("openai.gpt-oss-120b", asked) == "high"

    @pytest.mark.parametrize("model", sorted(NO_REASONING_LEVELS))
    @pytest.mark.parametrize("asked", REASONING_SCALE)
    def test_a_model_with_no_levels_is_sent_nothing(self, model: str, asked: str) -> None:
        assert resolve_reasoning_effort(model, asked) is None
        assert reasoning_levels(model) == ()

    def test_nothing_asked_sends_nothing(self) -> None:
        for model in OFFERED_MODELS:
            assert resolve_reasoning_effort(model, None) is None

    def test_an_id_that_is_not_offered_is_sent_nothing(self) -> None:
        assert resolve_reasoning_effort("some-lab/unknown-model", "high") is None
        assert reasoning_levels("some-lab/unknown-model") == ()

    def test_a_level_off_the_scale_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="minimal"):
            resolve_reasoning_effort("openai.gpt-6-luna", "minimal")


def _community(**fields) -> CommunityConfig:
    return CommunityConfig(id="effortcheck", name="Effort", description="Effort", **fields)


class TestTheCommunitySetting:
    def test_it_is_optional_and_defaults_to_the_models_own(self) -> None:
        assert _community().reasoning_effort is None

    @pytest.mark.parametrize("level", REASONING_SCALE)
    def test_every_level_of_the_scale_loads(self, level: str) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert _community(reasoning_effort=level).reasoning_effort == level

    @pytest.mark.parametrize("typo", ["hihg", "minimal", "HIGH", "maximum", ""])
    def test_a_level_off_the_scale_is_refused_at_load(self, typo: str) -> None:
        with pytest.raises(ValidationError):
            _community(reasoning_effort=typo)

    def test_asking_more_than_the_default_model_accepts_warns_with_what_it_will_run_at(
        self,
    ) -> None:
        with pytest.warns(UserWarning, match=r"will run at 'high'"):
            _community(default_model=SONNET, reasoning_effort="max")

    def test_asking_a_model_with_no_levels_warns_that_it_is_ignored_there(self) -> None:
        with pytest.warns(UserWarning, match="is ignored for"):
            _community(default_model="claude-haiku-4-5", reasoning_effort="high")

    def _reasoning_warnings(self, **fields) -> list[str]:
        """Warnings about reasoning_effort only: a Bedrock default has its own, unrelated one."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _community(**fields)
        return [str(w.message) for w in caught if "reasoning_effort" in str(w.message)]

    def test_a_level_the_default_model_accepts_is_silent(self) -> None:
        assert (
            self._reasoning_warnings(default_model="openai.gpt-oss-120b", reasoning_effort="medium")
            == []
        )
        assert self._reasoning_warnings(default_model=SONNET, reasoning_effort="high") == []

    def test_nothing_to_check_without_a_default_model(self) -> None:
        assert self._reasoning_warnings(reasoning_effort="max") == []
