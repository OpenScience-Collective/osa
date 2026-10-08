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
from src.core.services.anthropic_llm import create_anthropic_llm
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    DEFAULT_REASONING_EFFORT,
    HAIKU,
    LUNA,
    MODEL_ALIASES,
    OFFERED_MODELS,
    REASONING_LEVELS,
    REASONING_SCALE,
    SONNET,
    THINKING_OFF,
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

#: The offered Claude models, which think adaptively at an effort level.
CLAUDE_MODELS = sorted(THINKING_OFF)
GPT_OSS = "openai.gpt-oss-120b"
QWEN = "qwen.qwen3-next-80b-a3b"


# --------------------------------------------------------------------------- Bedrock


@pytest.fixture(autouse=True)
def _no_deployment_token_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings(_env_file=None)` still reads the process environment: a developer's
    exported ANTHROPIC_MAX_OUTPUT_TOKENS must not decide what these tests see."""
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


class TestAnthropicPayload:
    @pytest.mark.parametrize("model", CLAUDE_MODELS)
    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_a_claude_model_gets_adaptive_thinking_and_the_level_as_output_config(
        self, model: str, level: str
    ) -> None:
        payload = _anthropic_payload(model, reasoning_effort=level)
        assert payload["output_config"] == {"effort": level}
        assert payload["thinking"] == {"type": "adaptive"}

    @pytest.mark.parametrize("model", CLAUDE_MODELS)
    @pytest.mark.parametrize("asked", ["xhigh", "max"])
    def test_a_claude_model_is_never_sent_more_than_high(self, model: str, asked: str) -> None:
        """The maintainer's rule, read off the request itself."""
        payload = _anthropic_payload(model, reasoning_effort=asked)
        assert payload["output_config"] == {"effort": "high"}

    @pytest.mark.parametrize("model", CLAUDE_MODELS)
    def test_none_is_no_upfront_thinking_at_the_lowest_effort(self, model: str) -> None:
        """No level is lower than `low`, and the model's own off value (Sonnet 5.5's
        `between_tools`, Haiku 5.5's `disabled`; each accepted only at effort `high` or
        below) is what turns thinking down."""
        payload = _anthropic_payload(model, reasoning_effort="none")
        assert payload["thinking"] == THINKING_OFF[model]
        assert payload["output_config"] == {"effort": "low"}

    @pytest.mark.parametrize("model", CLAUDE_MODELS)
    def test_a_community_that_sets_nothing_gets_high_sent_explicitly(self, model: str) -> None:
        """Sonnet's API default is high too, so nothing changes there; Haiku 5.5's own
        default is medium, so high is what a community that sets nothing gets only because
        it is sent. Either way the level is OSA's, not the API's, to change."""
        payload = _anthropic_payload(model)
        assert (
            payload["output_config"] == {"effort": DEFAULT_REASONING_EFFORT} == {"effort": "high"}
        )
        assert payload["thinking"] == {"type": "adaptive"}

    @pytest.mark.parametrize("model", CLAUDE_MODELS)
    def test_an_explicit_thinking_setting_wins_over_the_one_a_level_implies(
        self, model: str
    ) -> None:
        payload = _anthropic_payload(model, reasoning_effort="high", thinking=None)
        assert payload["thinking"] == THINKING_OFF[model]
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

    @pytest.mark.parametrize("model", CLAUDE_MODELS)
    def test_the_pairing_the_api_refuses_is_stopped_here(
        self, model: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The off value is a 400 above `high`. The table never produces it; if it were
        ever widened, this fails at construction, not as a 400 from the endpoint."""
        monkeypatch.setitem(REASONING_LEVELS, model, (*REASONING_LEVELS[model], "xhigh", "max"))
        with pytest.raises(ValueError, match=THINKING_OFF[model]["type"]):
            _anthropic_payload(model, reasoning_effort="max", thinking=None)

    def test_the_wrong_models_off_value_is_refused(self) -> None:
        """Sonnet 5.5 400s on `disabled` and Haiku 5.5 has no `between_tools`."""
        with pytest.raises(ValueError, match="between_tools"):
            _anthropic_payload(SONNET, thinking={"type": "disabled"})
        with pytest.raises(ValueError, match="disabled"):
            _anthropic_payload(HAIKU, thinking={"type": "between_tools"})


# ------------------------------------------------------------------------ OpenRouter


@pytest.fixture
def openrouter(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOpenRouter]:
    with FakeOpenRouter() as server:
        monkeypatch.setenv("OPENROUTER_API_BASE", server.base_url)
        yield server


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
        ("asked", "sent"),
        [
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            ("xhigh", "high"),
            ("max", "high"),
        ],
    )
    def test_haiku_is_sent_an_effort_like_any_other_adaptive_model(
        self, openrouter: FakeOpenRouter, asked: str, sent: str
    ) -> None:
        """Never above high, as on every other path."""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[HAIKU], reasoning_effort=asked)
        assert body["reasoning"] == {"effort": sent}

    def test_haiku_keeps_none_on_openrouter_where_its_reasoning_is_not_mandatory(
        self, openrouter: FakeOpenRouter
    ) -> None:
        """OpenRouter's metadata for Haiku 5.5 says its reasoning is not mandatory, so
        `none` is sent as it is (Sonnet's is raised to `low`, see above)."""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[HAIKU], reasoning_effort="none")
        assert body["reasoning"] == {"effort": "none"}

    @pytest.mark.parametrize("model", [SONNET, HAIKU, LUNA, GPT_OSS])
    def test_the_other_models_keep_their_temperature_beside_reasoning(
        self, openrouter: FakeOpenRouter, model: str
    ) -> None:
        """Unchanged behavior: the temperature is not held back because a model reasons."""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[model], reasoning_effort="high")
        assert body["temperature"] == pytest.approx(0.1)
        assert "reasoning" in body

    def test_a_slug_osa_knows_nothing_about_is_sent_nothing(
        self, openrouter: FakeOpenRouter
    ) -> None:
        """A caller's own choice: it may not reason at all, and OSA does not guess."""
        body = _openrouter_body(openrouter, "some-lab/unknown-model", reasoning_effort="high")
        assert "reasoning" not in body

    @pytest.mark.parametrize("model", [SONNET, HAIKU, LUNA, GPT_OSS])
    def test_a_community_that_sets_nothing_gets_high(
        self, openrouter: FakeOpenRouter, model: str
    ) -> None:
        """The same default a request gets on every other provider, so a model behaves the
        same whichever key paid for it (OpenRouter's own default for Luna is `medium`)."""
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[model])
        assert body["reasoning"] == {"effort": "high"}

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
        providers), :floor (cheapest), :exacto (best at tool calls) and the deprecated
        :online (web search) as routing variants, accepted on any model."""
        assert OPENROUTER_ROUTING_VARIANTS == ("nitro", "floor", "exacto", "online")

    @pytest.mark.parametrize("variant", OPENROUTER_ROUTING_VARIANTS)
    @pytest.mark.parametrize(("model", "slug"), sorted(OPENROUTER_MODEL_IDS.items()))
    def test_a_routing_variant_of_an_offered_slug_is_that_offered_model(
        self, model: str, slug: str, variant: str
    ) -> None:
        """`:nitro`, `:floor` and `:exacto` only change which providers serve the request."""
        assert openrouter_model_id(f"{slug}:{variant}") == model

    @pytest.mark.parametrize("variant", ["free", "batch", "thinking", "extended", "x"])
    def test_any_other_variant_is_a_different_catalog_entry_and_is_not_looked_through(
        self, variant: str
    ) -> None:
        assert openrouter_model_id(f"{OPENROUTER_MODEL_IDS[LUNA]}:{variant}") is None

    @pytest.mark.parametrize(
        ("suffix", "looked_through"),
        [
            (":nitro:exacto", True),
            (":exacto:nitro", True),
            (":nitro:floor:exacto", True),
            (":free:nitro", False),
            (":nitro:free", False),
            (":nitro:free:exacto", False),
        ],
    )
    def test_stacked_variants_are_looked_through_in_any_order_unless_a_catalog_one_remains(
        self, suffix: str, looked_through: bool
    ) -> None:
        """OpenRouter lets suffixes be stacked: any number of routing variants, at most one
        catalog variant. `:free` stripped would resolve to the paid entry, so it stays."""
        slug = OPENROUTER_MODEL_IDS[GPT_OSS] + suffix
        assert openrouter_model_id(slug) == (GPT_OSS if looked_through else None)

    def test_a_stacked_variant_slug_runs_at_the_plain_slugs_level(
        self, openrouter: FakeOpenRouter
    ) -> None:
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[GPT_OSS] + ":nitro:exacto")
        assert body["model"] == OPENROUTER_MODEL_IDS[GPT_OSS] + ":nitro:exacto"
        assert body["reasoning"] == {"effort": "high"}

    def test_a_variant_of_an_alias_slug_is_not_the_offered_model_either(self) -> None:
        """`anthropic/claude-sonnet-4.5` is an alias of the offered Sonnet on the Claude path
        but a different, older model on OpenRouter, with or without a variant."""
        assert openrouter_model_id("anthropic/claude-sonnet-4.5:nitro") is None

    def test_a_variant_of_an_unknown_slug_is_still_unknown(self) -> None:
        assert openrouter_model_id("some-lab/unknown-model:nitro") is None

    @pytest.mark.parametrize("variant", OPENROUTER_ROUTING_VARIANTS)
    @pytest.mark.parametrize("model", [SONNET, HAIKU, LUNA, GPT_OSS])
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
    def test_an_anthropic_slug_with_a_variant_is_not_pinned_to_a_provider(
        self, openrouter: FakeOpenRouter, variant: str
    ) -> None:
        """The caller chose how it is routed (:floor for the cheapest, say): forcing
        provider.order = [Anthropic] first would override that."""
        body = _openrouter_body(
            openrouter, f"{OPENROUTER_MODEL_IDS[HAIKU]}:{variant}", provider=None
        )
        assert "provider" not in body

    def test_a_plain_anthropic_slug_is_still_pinned_to_the_anthropic_provider(
        self, openrouter: FakeOpenRouter
    ) -> None:
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[HAIKU], provider=None)
        assert body["provider"] == {"order": ["Anthropic"]}

    def test_a_free_variant_is_sent_no_reasoning_field(self, openrouter: FakeOpenRouter) -> None:
        body = _openrouter_body(openrouter, f"{OPENROUTER_MODEL_IDS[GPT_OSS]}:free")
        assert "reasoning" not in body


# ---------------------------------------------------------------- one rule, three paths


class TestEveryModelWithLevelsCanBeSentThem:
    @pytest.mark.parametrize("model", sorted(REASONING_LEVELS))
    def test_a_model_with_levels_has_a_path_that_sends_them(self, model: str) -> None:
        """Bedrock models name a request field; Claude models must be in the set the
        Anthropic factory sends `output_config` for (THINKING_OFF), or their level is
        silently dropped."""
        if model in BEDROCK_MODELS:
            assert BEDROCK_MODELS[model].reasoning_field is not None
        else:
            assert model in THINKING_OFF


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
    @pytest.mark.parametrize("model", CLAUDE_MODELS)
    def test_a_claude_model_is_capped_at_high_on_every_platform(
        self, model: str, provider: str, asked: str
    ) -> None:
        assert effective_reasoning_effort(model, asked, provider) == "high"  # type: ignore[arg-type]
