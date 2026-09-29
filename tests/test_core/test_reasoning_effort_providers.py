"""What each provider actually sends for a reasoning level (issue #545).

A level has three request shapes, and a wrong one is often a silent no-op, so these tests
read the request each provider's client would put on the wire, not the arguments handed to
a factory: the Converse body for Bedrock (captured by botocore's own `before-send` hook,
as tests/test_core/test_bedrock_llm.py does), the Messages payload the real
`ChatAnthropic` builds for Anthropic, and the request body LiteLLM posts to a local fake of
OpenRouter's chat-completions endpoint (tests/helpers/openrouter.py). Nothing here is
called live: there is no Anthropic or OpenRouter key on the machines that run it. What the
live services do with these fields was measured for Bedrock (2026-09-29, see the tables in
src/core/services/anthropic_models.py) and is documented, not measured, for the others.

Ranges over `BEDROCK_MODELS`, `OFFERED_MODELS` and `REASONING_SCALE` rather than naming
models, so a model added later is covered; the pinned examples are the maintainer's rules
and the traps found while measuring (Qwen must be sent nothing; the nested field is a 200
no-op for gpt-oss, and the flat one a 400 for Luna).
"""

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from src.api.config import Settings
from src.core.services import anthropic_models
from src.core.services.anthropic_llm import (
    _ADAPTIVE_THINKING_MODELS,
    MIN_THINKING_BUDGET_TOKENS,
    create_anthropic_llm,
    default_thinking,
)
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    DEFAULT_REASONING_EFFORT,
    MODEL_ALIASES,
    OFFERED_MODELS,
    REASONING_LEVELS,
    REASONING_SCALE,
    THINKING_BUDGET_TOKENS,
    effective_reasoning_effort,
)
from src.core.services.bedrock_llm import _bedrock_client, create_bedrock_llm
from src.core.services.litellm_llm import (
    OPENROUTER_MODEL_IDS,
    OPENROUTER_ROUTING_VARIANTS,
    create_openrouter_llm,
    openrouter_model_id,
)
from tests.helpers.openrouter import FakeOpenRouter, stream_of
from tests.test_core.test_bedrock_llm import _converse_reply, _settings, _Wire

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-4-5"
LUNA = "openai.gpt-6-luna"
GPT_OSS = "openai.gpt-oss-120b"
QWEN = "qwen.qwen3-next-80b-a3b"


# --------------------------------------------------------------------------- Bedrock


@pytest.fixture(autouse=True)
def _no_deployment_token_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings(_env_file=None)` still reads the process environment: a developer's
    exported ANTHROPIC_MAX_OUTPUT_TOKENS must not decide what these tests see (a Haiku
    budget is lowered to fit under it)."""
    monkeypatch.delenv("ANTHROPIC_MAX_OUTPUT_TOKENS", raising=False)


@pytest.fixture(autouse=True)
def _fresh_clients() -> Iterator[None]:
    """Bedrock clients are cached and a test hooks the one it gets: start each from one
    nobody has hooked, or an earlier test's hook answers for it."""
    _bedrock_client.cache_clear()
    yield
    _bedrock_client.cache_clear()


def _bedrock_body(model: str, **kwargs: Any) -> dict[str, Any]:
    llm = create_bedrock_llm(model, settings=_settings(), **kwargs)
    llm.streaming = False
    wire = _Wire(llm, _converse_reply("OK"), "application/json")
    llm.invoke([HumanMessage(content="hi")])
    return wire.body


class TestBedrockWire:
    @pytest.mark.parametrize("level", REASONING_SCALE)
    def test_luna_is_sent_the_nested_field_at_the_level_asked(self, level: str) -> None:
        body = _bedrock_body(LUNA, reasoning_effort=level)
        assert body["additionalModelRequestFields"] == {"reasoning": {"effort": level}}

    @pytest.mark.parametrize(
        ("asked", "sent"),
        [("none", "low"), ("low", "low"), ("medium", "medium"), ("high", "high"),
         ("xhigh", "high"), ("max", "high")],
    )  # fmt: skip
    def test_gpt_oss_is_sent_the_flat_field_at_a_level_it_accepts(
        self, asked: str, sent: str
    ) -> None:
        """Bedrock rejects `max` (400) and `none` (400: Harmony does not support it)."""
        body = _bedrock_body(GPT_OSS, reasoning_effort=asked)
        assert body["additionalModelRequestFields"] == {"reasoning_effort": sent}

    @pytest.mark.parametrize("level", [None, *REASONING_SCALE])
    def test_qwen_is_never_sent_anything(self, level: str | None) -> None:
        """Measured: a flat `high` hangs Qwen3 Next until the read timeout."""
        body = _bedrock_body(QWEN, reasoning_effort=level)
        assert "additionalModelRequestFields" not in body

    @pytest.mark.parametrize("model", sorted(BEDROCK_MODELS))
    def test_a_community_that_sets_nothing_gets_the_default_level(self, model: str) -> None:
        body = _bedrock_body(model)
        spec = BEDROCK_MODELS[model]
        expected = spec.reasoning_request_fields(DEFAULT_REASONING_EFFORT)
        assert body.get("additionalModelRequestFields", {}) == expected

    def test_the_defaults_are_high_for_luna_and_gpt_oss(self) -> None:
        assert _bedrock_body(LUNA)["additionalModelRequestFields"] == {
            "reasoning": {"effort": "high"}
        }
        assert _bedrock_body(GPT_OSS)["additionalModelRequestFields"] == {
            "reasoning_effort": "high"
        }

    @pytest.mark.parametrize("level", REASONING_SCALE)
    def test_each_model_gets_only_its_own_shape(self, level: str) -> None:
        """The wrong shape is a 400 for Luna and a silent no-op for gpt-oss."""
        luna = _bedrock_body(LUNA, reasoning_effort=level)["additionalModelRequestFields"]
        oss = _bedrock_body(GPT_OSS, reasoning_effort=level)["additionalModelRequestFields"]
        assert "reasoning_effort" not in luna
        assert "reasoning" not in oss

    def test_a_level_off_the_scale_is_an_error_not_a_guess(self) -> None:
        with pytest.raises(ValueError, match="minimal"):
            create_bedrock_llm(LUNA, settings=_settings(), reasoning_effort="minimal")


# ------------------------------------------------------------------------- Anthropic


def _anthropic_payload(
    model: str, settings_overrides: dict[str, Any] | None = None, **kwargs: Any
) -> dict[str, Any]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, anthropic_api_key="sk-ant-" + "x" * 40, **(settings_overrides or {})
    )
    llm = create_anthropic_llm(model, api_key="sk-ant-" + "x" * 40, settings=settings, **kwargs)
    return llm._get_request_payload([HumanMessage(content="hi")])  # type: ignore[attr-defined]


def _wire_temperature(payload: dict[str, Any]) -> float | None:
    """The temperature a payload carries: this client puts it in `extra_body`."""
    return payload.get("temperature", (payload.get("extra_body") or {}).get("temperature"))


class TestAnthropicPayload:
    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_sonnet_gets_adaptive_thinking_and_the_level_as_output_config(self, level: str) -> None:
        payload = _anthropic_payload(SONNET, reasoning_effort=level)
        assert payload["output_config"] == {"effort": level}
        assert payload["thinking"] == {"type": "adaptive"}

    @pytest.mark.parametrize("asked", ["xhigh", "max"])
    def test_sonnet_is_never_sent_more_than_high(self, asked: str) -> None:
        """The maintainer's rule, read off the request itself."""
        payload = _anthropic_payload(SONNET, reasoning_effort=asked)
        assert payload["output_config"] == {"effort": "high"}

    def test_none_is_no_upfront_thinking_at_the_lowest_effort(self) -> None:
        """No level is lower than `low`, and `between_tools` (Sonnet 5.5's lowest thinking
        setting, accepted only at effort `high` or below) is what turns thinking down."""
        payload = _anthropic_payload(SONNET, reasoning_effort="none")
        assert payload["thinking"] == {"type": "between_tools"}
        assert payload["output_config"] == {"effort": "low"}

    def test_a_community_that_sets_nothing_gets_high_sent_explicitly(self) -> None:
        """The API's own default is high too, so nothing changes on the Claude Platform;
        it is sent so the level is OSA's, not the API's, to change."""
        payload = _anthropic_payload(SONNET)
        assert (
            payload["output_config"] == {"effort": DEFAULT_REASONING_EFFORT} == {"effort": "high"}
        )
        assert payload["thinking"] == {"type": "adaptive"}

    @pytest.mark.parametrize("level", [None, *REASONING_SCALE])
    def test_haiku_is_never_sent_an_effort_field(self, level: str | None) -> None:
        """Haiku 4.5 thinks with a token budget; the effort field is not on its list."""
        payload = _anthropic_payload(HAIKU, reasoning_effort=level)
        assert "output_config" not in payload

    def test_an_explicit_thinking_setting_wins_over_the_one_a_level_implies(self) -> None:
        payload = _anthropic_payload(SONNET, reasoning_effort="high", thinking=None)
        assert payload["thinking"] == {"type": "between_tools"}
        assert payload["output_config"] == {"effort": "high"}

    def test_the_effort_is_sent_without_the_caching_layer_too(self) -> None:
        payload = _anthropic_payload(SONNET, reasoning_effort="medium", enable_caching=False)
        assert payload["output_config"] == {"effort": "medium"}
        assert "cache_control" not in str(payload)

    def test_the_effort_survives_the_caching_layer(self) -> None:
        """CachingChatAnthropic rewrites the payload for cache breakpoints."""
        payload = _anthropic_payload(SONNET, reasoning_effort="medium", enable_caching=True)
        assert payload["output_config"] == {"effort": "medium"}
        assert "cache_control" in payload

    def test_the_pairing_the_api_refuses_is_stopped_here(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`between_tools` is a 400 above `high`. The table never produces it; if it were
        ever widened, this fails at construction, not as a 400 from the endpoint."""
        monkeypatch.setitem(REASONING_LEVELS, SONNET, (*REASONING_LEVELS[SONNET], "xhigh", "max"))
        with pytest.raises(ValueError, match="between_tools"):
            _anthropic_payload(SONNET, reasoning_effort="max", thinking=None)


class TestHaikuThinkingBudget:
    """Haiku has no effort levels, so a level is a thinking budget (issue #548)."""

    def test_the_table_is_written_out(self) -> None:
        assert THINKING_BUDGET_TOKENS[HAIKU] == {"low": 1024, "medium": 2048, "high": 4096}

    def test_every_level_but_none_has_a_budget_the_api_accepts_in_increasing_order(self) -> None:
        budgets = THINKING_BUDGET_TOKENS[HAIKU]
        assert set(budgets) == set(REASONING_LEVELS[HAIKU]) - {"none"}
        ordered = [budgets[level] for level in REASONING_LEVELS[HAIKU] if level != "none"]
        assert ordered == sorted(set(ordered))
        assert min(ordered) >= MIN_THINKING_BUDGET_TOKENS

    def test_the_default_level_budget_is_what_default_thinking_gives(self) -> None:
        assert default_thinking(HAIKU) == {
            "type": "enabled",
            "budget_tokens": THINKING_BUDGET_TOKENS[HAIKU][DEFAULT_REASONING_EFFORT],
        }

    @pytest.mark.parametrize(
        ("level", "budget"),
        [("low", 1024), ("medium", 2048), ("high", 4096), ("xhigh", 4096), ("max", 4096)],
    )
    def test_a_communitys_level_is_the_budget_capped_at_high(self, level: str, budget: int) -> None:
        payload = _anthropic_payload(HAIKU, reasoning_effort=level)
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": budget}

    def test_none_is_no_thinking(self) -> None:
        payload = _anthropic_payload(HAIKU, reasoning_effort="none")
        assert "thinking" not in payload

    def test_unset_is_high(self) -> None:
        payload = _anthropic_payload(HAIKU)
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 4096}

    def test_an_explicit_thinking_setting_wins(self) -> None:
        custom = {"type": "enabled", "budget_tokens": 1500}
        payload = _anthropic_payload(HAIKU, reasoning_effort="high", thinking=custom)
        assert payload["thinking"] == custom
        assert "thinking" not in _anthropic_payload(HAIKU, reasoning_effort="high", thinking=None)

    @pytest.mark.parametrize("level", [None, "high"])
    @pytest.mark.parametrize(
        ("max_tokens", "budget"),
        [
            (8000, 4096),  # fits: not lowered
            (5121, 4096),  # room is 4097, above the budget
            (5120, 4096),  # room equals the budget
            (5119, 4095),  # one token short: lowered by one
            (3000, 1976),  # leaves the API's minimum budget's worth for the answer
            (2049, 1025),
            (2048, 1024),  # the boundary: room is exactly the minimum budget
            (1500, 1024),  # the minimum itself, valid below max_tokens though tight
            (1025, 1024),
        ],
    )
    def test_a_budget_that_would_not_fit_max_tokens_is_lowered_not_refused(
        self, level: str | None, max_tokens: int, budget: int
    ) -> None:
        """A deployment whose max output is below a level's budget still answers."""
        payload = _anthropic_payload(HAIKU, reasoning_effort=level, max_tokens=max_tokens)
        assert payload["thinking"]["budget_tokens"] == budget
        assert payload["thinking"]["budget_tokens"] < max_tokens

    @pytest.mark.parametrize("max_tokens", [1024, 1000])
    def test_a_max_tokens_that_does_not_exceed_the_minimum_budget_is_still_refused(
        self, max_tokens: int
    ) -> None:
        """No budget is valid: the API's smallest is 1024 and must be below max_tokens."""
        with pytest.raises(ValueError, match="below max_tokens"):
            _anthropic_payload(HAIKU, reasoning_effort="high", max_tokens=max_tokens)

    def test_a_budget_a_level_gives_that_is_already_below_max_tokens_is_kept(self) -> None:
        """low is 1024: nothing to lower, whatever the room."""
        payload = _anthropic_payload(HAIKU, reasoning_effort="low", max_tokens=1500)
        assert payload["thinking"]["budget_tokens"] == 1024

    def test_a_callers_own_budget_is_never_lowered_only_refused(self) -> None:
        """The fit rule is for budgets OSA chose; an explicit one that does not fit is the
        caller's error, as before."""
        explicit = {"type": "enabled", "budget_tokens": 4000}
        with pytest.raises(ValueError, match="below max_tokens"):
            _anthropic_payload(HAIKU, reasoning_effort="high", thinking=explicit, max_tokens=3000)
        fits = _anthropic_payload(HAIKU, thinking=explicit, max_tokens=8000)
        assert fits["thinking"] == explicit

    def test_temperature_is_dropped_while_thinking_and_kept_when_it_is_off(self) -> None:
        """Anthropic does not allow a temperature with thinking: `none` turns thinking off,
        so it is forwarded there and only there."""
        assert _wire_temperature(
            _anthropic_payload(HAIKU, reasoning_effort="none", temperature=0.1)
        ) == pytest.approx(0.1)
        for level in (None, "low", "high"):
            payload = _anthropic_payload(HAIKU, reasoning_effort=level, temperature=0.1)
            assert _wire_temperature(payload) is None, level


# ------------------------------------------------------------------------ OpenRouter


@pytest.fixture
def openrouter(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOpenRouter]:
    server = FakeOpenRouter()
    monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
    yield server
    server.close()


def _openrouter_body(openrouter: FakeOpenRouter, model: str, **kwargs: Any) -> dict[str, Any]:
    openrouter.reply(stream_of("ok"))
    llm = create_openrouter_llm(model=model, api_key="sk-or-test", **kwargs)
    llm.invoke([HumanMessage(content="hi")])
    return openrouter.requests[-1]


class TestOpenRouterBody:
    @pytest.mark.parametrize("level", REASONING_SCALE)
    def test_luna_is_sent_the_unified_reasoning_field(
        self, openrouter: FakeOpenRouter, level: str
    ) -> None:
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[LUNA], reasoning_effort=level)
        assert body["reasoning"] == {"effort": level}
        assert "reasoning_effort" not in body, "LiteLLM's top-level name has no max, and can raise"

    @pytest.mark.parametrize("asked", ["xhigh", "max"])
    def test_sonnet_is_never_sent_more_than_high(
        self, openrouter: FakeOpenRouter, asked: str
    ) -> None:
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[SONNET], reasoning_effort=asked)
        assert body["reasoning"] == {"effort": "high"}

    def test_sonnet_has_no_none_on_openrouter_where_its_reasoning_is_mandatory(
        self, openrouter: FakeOpenRouter
    ) -> None:
        """Raised to its lowest level, not sent: OpenRouter's docs say a mandatory model
        rejects `effort: none`. (On the Claude Platform `none` is real: between_tools.)"""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[SONNET], reasoning_effort="none")
        assert body["reasoning"] == {"effort": "low"}

    def test_gpt_oss_is_held_to_its_levels_here_too(self, openrouter: FakeOpenRouter) -> None:
        for asked, sent in (("none", "low"), ("max", "high"), ("medium", "medium")):
            body = _openrouter_body(
                openrouter, OPENROUTER_MODEL_IDS[GPT_OSS], reasoning_effort=asked
            )
            assert body["reasoning"] == {"effort": sent}, asked

    @pytest.mark.parametrize("level", REASONING_SCALE)
    def test_a_model_with_no_levels_is_sent_no_reasoning_field(
        self, openrouter: FakeOpenRouter, level: str
    ) -> None:
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[QWEN], reasoning_effort=level)
        assert "reasoning" not in body
        assert "reasoning_effort" not in body

    @pytest.mark.parametrize(
        ("asked", "budget"),
        [("low", 1024), ("medium", 2048), ("high", 4096), ("xhigh", 4096), ("max", 4096)],
    )
    def test_haiku_is_sent_its_levels_budget_not_an_effort(
        self, openrouter: FakeOpenRouter, asked: str, budget: int
    ) -> None:
        """OpenRouter would turn an effort into a share of an unset max_tokens; its
        `reasoning.max_tokens` is used as given, the budget the Claude Platform path uses."""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[HAIKU], reasoning_effort=asked)
        assert body["reasoning"] == {"max_tokens": budget}

    def test_haiku_none_is_no_reasoning_field_and_keeps_its_temperature(
        self, openrouter: FakeOpenRouter
    ) -> None:
        """Without the field it does not think, so a temperature is allowed."""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[HAIKU], reasoning_effort="none")
        assert "reasoning" not in body
        assert body["temperature"] == pytest.approx(0.1)

    @pytest.mark.parametrize("level", [None, "low", "high"])
    def test_haiku_is_sent_no_temperature_while_it_thinks(
        self, openrouter: FakeOpenRouter, level: str | None
    ) -> None:
        """Anthropic does not allow a temperature with thinking, so it is not sent, as on
        the direct path."""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[HAIKU], reasoning_effort=level)
        assert "temperature" not in body

    @pytest.mark.parametrize("model", [SONNET, LUNA, GPT_OSS])
    def test_the_other_models_keep_their_temperature_beside_reasoning(
        self, openrouter: FakeOpenRouter, model: str
    ) -> None:
        """Unchanged behavior: only a budget model is held to the no-temperature rule."""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[model], reasoning_effort="high")
        assert body["temperature"] == pytest.approx(0.1)
        assert "reasoning" in body

    def test_a_slug_osa_knows_nothing_about_is_sent_nothing(
        self, openrouter: FakeOpenRouter
    ) -> None:
        """A caller's own choice: it may not reason at all, and OSA does not guess."""
        body = _openrouter_body(openrouter, "some-lab/unknown-model", reasoning_effort="high")
        assert "reasoning" not in body

    @pytest.mark.parametrize("model", [SONNET, LUNA, GPT_OSS])
    def test_a_community_that_sets_nothing_gets_high(
        self, openrouter: FakeOpenRouter, model: str
    ) -> None:
        """The same default a request gets on every other provider, so a model behaves the
        same whichever key paid for it (OpenRouter's own default for Luna is `medium`)."""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[model])
        assert body["reasoning"] == {"effort": "high"}

    def test_haiku_unset_is_the_high_budget(self, openrouter: FakeOpenRouter) -> None:
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[HAIKU])
        assert body["reasoning"] == {"max_tokens": THINKING_BUDGET_TOKENS[HAIKU]["high"]}

    def test_qwen_is_sent_nothing_by_default_either(self, openrouter: FakeOpenRouter) -> None:
        assert "reasoning" not in _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[QWEN])

    def test_provider_routing_is_still_sent_beside_it(self, openrouter: FakeOpenRouter) -> None:
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[LUNA], reasoning_effort="low")
        assert body["provider"]["order"], "the reasoning field must not displace routing"


class TestOpenRouterSlugs:
    """Which slugs count as an offered model: exactly the ones OSA maps, no others."""

    @pytest.mark.parametrize(("model", "slug"), sorted(OPENROUTER_MODEL_IDS.items()))
    def test_a_slug_osa_maps_is_its_offered_model(self, model: str, slug: str) -> None:
        assert openrouter_model_id(slug) == model

    @pytest.mark.parametrize("slug", [None, "", "some-lab/unknown-model"])
    def test_anything_else_is_not_an_offered_model(self, slug: str | None) -> None:
        assert openrouter_model_id(slug) is None

    @pytest.mark.parametrize(
        "alias",
        sorted(a for a in MODEL_ALIASES if "/" in a and a not in OPENROUTER_MODEL_IDS.values()),
    )
    def test_an_older_claude_slug_that_is_only_an_alias_is_not_the_offered_model(
        self, alias: str
    ) -> None:
        """`normalize_model` maps anthropic/claude-sonnet-4.5 to the offered Sonnet, but
        OpenRouter runs that slug as the older model, which the offered model's levels
        say nothing about."""
        assert openrouter_model_id(alias) is None

    @pytest.mark.parametrize(
        "alias",
        sorted(a for a in MODEL_ALIASES if "/" in a and a not in OPENROUTER_MODEL_IDS.values()),
    )
    def test_an_older_claude_slug_is_sent_no_reasoning(
        self, openrouter: FakeOpenRouter, alias: str
    ) -> None:
        body = _openrouter_body(openrouter, alias, reasoning_effort="high")
        assert "reasoning" not in body

    def test_an_unknown_slug_is_not_read_as_the_default_model(
        self, openrouter: FakeOpenRouter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`normalize_model(None)` is the default model; an unmapped slug must not become
        it. Held apart by making the default a model that has levels."""
        monkeypatch.setattr(anthropic_models, "DEFAULT_MODEL", SONNET)
        body = _openrouter_body(openrouter, "openai/gpt-5", reasoning_effort="max")
        assert "reasoning" not in body

    def test_a_level_that_goes_nowhere_leaves_a_debug_line(
        self, openrouter: FakeOpenRouter, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="src.core.services.litellm_llm"):
            _openrouter_body(openrouter, "some-lab/unknown-model", reasoning_effort="high")
        assert any("some-lab/unknown-model" in r.getMessage() for r in caplog.records)

    def test_nothing_is_logged_when_no_level_was_asked_for(
        self, openrouter: FakeOpenRouter, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="src.core.services.litellm_llm"):
            _openrouter_body(openrouter, "some-lab/unknown-model")
        assert not any("reasoning_effort" in r.getMessage() for r in caplog.records)

    def test_the_routing_variants_are_the_ones_openrouter_documents(self) -> None:
        """Written out, not derived: OpenRouter's "Model variants" page lists :nitro (fastest
        providers), :floor (cheapest) and :exacto (best at tool calls) as routing-only."""
        assert OPENROUTER_ROUTING_VARIANTS == ("nitro", "floor", "exacto")

    @pytest.mark.parametrize("variant", OPENROUTER_ROUTING_VARIANTS)
    @pytest.mark.parametrize(("model", "slug"), sorted(OPENROUTER_MODEL_IDS.items()))
    def test_a_routing_variant_of_an_offered_slug_is_that_offered_model(
        self, model: str, slug: str, variant: str
    ) -> None:
        """`:nitro`, `:floor` and `:exacto` only change which providers serve the request."""
        assert openrouter_model_id(f"{slug}:{variant}") == model

    @pytest.mark.parametrize("variant", ["free", "batch", "thinking", "extended", "online", "x"])
    def test_any_other_variant_is_a_different_catalog_entry_and_is_not_looked_through(
        self, variant: str
    ) -> None:
        assert openrouter_model_id(f"{OPENROUTER_MODEL_IDS[LUNA]}:{variant}") is None

    def test_a_variant_of_an_unknown_slug_is_still_unknown(self) -> None:
        assert openrouter_model_id("some-lab/unknown-model:nitro") is None

    @pytest.mark.parametrize("variant", OPENROUTER_ROUTING_VARIANTS)
    @pytest.mark.parametrize("model", [SONNET, LUNA, GPT_OSS])
    def test_a_routing_variant_runs_at_the_same_level_as_the_plain_slug(
        self, openrouter: FakeOpenRouter, model: str, variant: str
    ) -> None:
        """The model id keeps its suffix on the wire, and the level is the plain slug's."""
        slug = OPENROUTER_MODEL_IDS[model]
        plain = _openrouter_body(openrouter, slug, reasoning_effort="medium")
        varied = _openrouter_body(openrouter, f"{slug}:{variant}", reasoning_effort="medium")
        assert varied["model"] == f"{slug}:{variant}"
        assert varied["reasoning"] == plain["reasoning"] == {"effort": "medium"}

    @pytest.mark.parametrize("variant", OPENROUTER_ROUTING_VARIANTS)
    def test_haiku_through_a_routing_variant_gets_its_budget_and_no_temperature(
        self, openrouter: FakeOpenRouter, variant: str
    ) -> None:
        body = _openrouter_body(
            openrouter, f"{OPENROUTER_MODEL_IDS[HAIKU]}:{variant}", reasoning_effort="low"
        )
        assert body["reasoning"] == {"max_tokens": 1024}
        assert "temperature" not in body

    def test_a_free_variant_is_sent_no_reasoning_field(self, openrouter: FakeOpenRouter) -> None:
        body = _openrouter_body(openrouter, f"{OPENROUTER_MODEL_IDS[GPT_OSS]}:free")
        assert "reasoning" not in body


# ---------------------------------------------------------------- one rule, three paths


class TestEveryModelWithLevelsCanBeSentThem:
    @pytest.mark.parametrize("model", sorted(REASONING_LEVELS))
    def test_a_model_with_levels_has_a_path_that_sends_them(self, model: str) -> None:
        """Bedrock models name a request field; Claude models must be in the set the
        Anthropic factory sends `output_config` for, or have a thinking budget per level,
        or their level is silently dropped."""
        if model in BEDROCK_MODELS:
            assert BEDROCK_MODELS[model].reasoning_field is not None
        else:
            assert model in _ADAPTIVE_THINKING_MODELS or model in THINKING_BUDGET_TOKENS


class TestTheSameRuleOnEveryPath:
    @pytest.mark.parametrize("model", sorted(OFFERED_MODELS))
    @pytest.mark.parametrize("asked", REASONING_SCALE)
    def test_no_path_ever_sends_a_level_the_model_does_not_accept(
        self, model: str, asked: str
    ) -> None:
        """Whatever the platform, the level is one of the model's own, or nothing."""
        for provider in ("anthropic", "bedrock", "openrouter"):
            sent = effective_reasoning_effort(model, asked, provider)  # type: ignore[arg-type]
            if sent is not None:
                assert sent in REASONING_LEVELS.get(model, ()), (model, provider, asked)

    @pytest.mark.parametrize("provider", ["anthropic", "bedrock", "openrouter"])
    @pytest.mark.parametrize("asked", ["xhigh", "max"])
    def test_sonnet_is_capped_at_high_on_every_platform(self, provider: str, asked: str) -> None:
        assert effective_reasoning_effort(SONNET, asked, provider) == "high"  # type: ignore[arg-type]
