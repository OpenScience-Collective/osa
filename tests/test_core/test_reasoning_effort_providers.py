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

from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from src.api.config import Settings
from src.core.services.anthropic_llm import create_anthropic_llm
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    OFFERED_MODELS,
    REASONING_DEFAULTS,
    REASONING_LEVELS,
    REASONING_SCALE,
    effective_reasoning_effort,
)
from src.core.services.bedrock_llm import _bedrock_client, create_bedrock_llm
from src.core.services.litellm_llm import OPENROUTER_MODEL_IDS, create_openrouter_llm
from tests.helpers.openrouter import FakeOpenRouter, stream_of
from tests.test_core.test_bedrock_llm import _converse_reply, _settings, _Wire

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-4-5"
LUNA = "openai.gpt-6-luna"
GPT_OSS = "openai.gpt-oss-120b"
QWEN = "qwen.qwen3-next-80b-a3b"


# --------------------------------------------------------------------------- Bedrock


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
        """Measured: a flat `high` or `xhigh` hangs Qwen3 Next until the read timeout."""
        body = _bedrock_body(QWEN, reasoning_effort=level)
        assert "additionalModelRequestFields" not in body

    @pytest.mark.parametrize("model", sorted(BEDROCK_MODELS))
    def test_a_community_that_sets_nothing_gets_the_models_own_default(self, model: str) -> None:
        body = _bedrock_body(model)
        spec = BEDROCK_MODELS[model]
        expected = spec.reasoning_request_fields(REASONING_DEFAULTS.get(model))
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


def _anthropic_payload(model: str, **kwargs: Any) -> dict[str, Any]:
    settings = Settings(_env_file=None, anthropic_api_key="sk-ant-" + "x" * 40)  # type: ignore[call-arg]
    llm = create_anthropic_llm(model, api_key="sk-ant-" + "x" * 40, settings=settings, **kwargs)
    return llm._get_request_payload([HumanMessage(content="hi")])  # type: ignore[attr-defined]


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

    def test_none_turns_thinking_off_and_sends_no_effort(self) -> None:
        """No API level means none: `between_tools` is Sonnet 5.5's thinking-off setting,
        accepted only at effort `high` or below, and the API's default effort applies."""
        payload = _anthropic_payload(SONNET, reasoning_effort="none")
        assert payload["thinking"] == {"type": "between_tools"}
        assert "output_config" not in payload

    def test_a_community_that_sets_nothing_changes_nothing(self) -> None:
        payload = _anthropic_payload(SONNET)
        assert "output_config" not in payload
        assert payload["thinking"] == {"type": "adaptive"}

    @pytest.mark.parametrize("level", [None, *REASONING_SCALE])
    def test_haiku_is_never_sent_an_effort(self, level: str | None) -> None:
        """Haiku 4.5 thinks with a token budget; the effort field is not on its list."""
        payload = _anthropic_payload(HAIKU, reasoning_effort=level)
        assert "output_config" not in payload
        assert payload["thinking"]["type"] == "enabled"
        assert payload["thinking"]["budget_tokens"] == 2048

    def test_an_explicit_thinking_setting_wins_over_the_one_a_level_implies(self) -> None:
        payload = _anthropic_payload(SONNET, reasoning_effort="high", thinking=None)
        assert payload["thinking"] == {"type": "between_tools"}
        assert payload["output_config"] == {"effort": "high"}

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

    @pytest.mark.parametrize("model", [HAIKU, QWEN])
    @pytest.mark.parametrize("level", REASONING_SCALE)
    def test_a_model_with_no_levels_is_sent_no_reasoning_field(
        self, openrouter: FakeOpenRouter, model: str, level: str
    ) -> None:
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[model], reasoning_effort=level)
        assert "reasoning" not in body
        assert "reasoning_effort" not in body

    def test_a_slug_osa_knows_nothing_about_is_sent_nothing(
        self, openrouter: FakeOpenRouter
    ) -> None:
        """A caller's own choice: it may not reason at all, and OSA does not guess."""
        body = _openrouter_body(openrouter, "some-lab/unknown-model", reasoning_effort="high")
        assert "reasoning" not in body

    def test_a_community_that_sets_nothing_gets_the_models_own_default(
        self, openrouter: FakeOpenRouter
    ) -> None:
        """The same default a request gets on Bedrock, so a model behaves the same
        whichever key paid for it (OpenRouter's own default for Luna is `medium`)."""
        luna = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[LUNA])
        assert luna["reasoning"] == {"effort": "high"}
        sonnet = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[SONNET])
        assert "reasoning" not in sonnet, "Sonnet has no default: its own applies, as before"

    def test_provider_routing_is_still_sent_beside_it(self, openrouter: FakeOpenRouter) -> None:
        body = _openrouter_body(openrouter, OPENROUTER_MODEL_IDS[LUNA], reasoning_effort="low")
        assert body["provider"]["order"], "the reasoning field must not displace routing"


# ---------------------------------------------------------------- one rule, three paths


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
