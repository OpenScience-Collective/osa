"""A community's `reasoning_effort` reaches the model each provider path builds (#545).

Goes through the real `create_community_assistant` on the real `hed` community, with the
level set on its loaded config, and reads the level off the model that comes back: the
Converse fields on the Bedrock model, the Messages payload of the Anthropic one, and the
`reasoning` body field of the LiteLLM one. Each path is checked at two different levels so
a hard-coded value cannot pass, and once unset so the model's own default is seen.
Nothing calls a provider: constructing a client does not.
"""

import pytest
from langchain_core.messages import HumanMessage

from src.api.config import get_settings
from src.api.routers.community import create_community_assistant
from src.api.security import ByokCredential
from src.assistants import discover_assistants, registry
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    DEFAULT_REASONING_EFFORT,
    THINKING_BUDGET_TOKENS,
    effective_reasoning_effort,
)

SONNET = "claude-sonnet-5-5"
LUNA = "openai.gpt-6-luna"
GPT_OSS = "openai.gpt-oss-120b"
HAIKU = "claude-haiku-4-5"


@pytest.fixture(autouse=True, scope="module")
def _load_communities():
    discover_assistants()


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def hed(monkeypatch):
    """The real hed community, on platform keys for Anthropic and Bedrock."""
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "platform-anthropic-key")
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    monkeypatch.setattr(settings, "bedrock_api_key", "bedrock-key")
    # A Haiku thinking budget is lowered to fit under max_tokens, which get_settings reads
    # from the environment: pin it so a machine's own value cannot decide these tests.
    monkeypatch.setattr(settings, "anthropic_max_output_tokens", 8000)
    info = registry.get("hed")
    assert info is not None
    monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", None)
    monkeypatch.setattr(info.community_config, "openrouter_api_key_env_var", None)
    _without_mcp_servers(monkeypatch, info)
    return info


def _without_mcp_servers(monkeypatch, info) -> None:
    """Building an assistant discovers its MCP servers' tools over the network (NEMAR's is
    mcp.nemar.org, waited on for up to 25 s). Which tools a community has is not what these
    tests are about, and an offline suite must not depend on a live service."""
    if info.community_config.extensions:
        monkeypatch.setattr(info.community_config.extensions, "mcp_servers", [])


def _set_level(monkeypatch, info, level: str | None) -> None:
    monkeypatch.setattr(info.community_config, "reasoning_effort", level)


def _origin(info) -> str:
    return next(o for o in info.community_config.cors_origins if "*" not in o)


def _bedrock_fields(info, model: str) -> dict:
    awm = create_community_assistant(
        "hed", origin=_origin(info), requested_model=model, preload_docs=False
    )
    assert type(awm.assistant.model).__name__ == "TaggedCitationChatBedrock"
    return awm.assistant.model.additional_model_request_fields or {}


def _anthropic_payload(model: str) -> dict:
    awm = create_community_assistant(
        "hed",
        byok=ByokCredential(key="sk-ant-" + "x" * 40, provider="anthropic"),
        requested_model=model,
        preload_docs=False,
    )
    return awm.assistant.model._get_request_payload([HumanMessage(content="hi")])


def _openrouter_kwargs(model: str) -> dict:
    awm = create_community_assistant(
        "hed",
        byok=ByokCredential(key="sk-or-fake-test-key", provider="openrouter"),
        requested_model=model,
        preload_docs=False,
    )
    assert type(awm.assistant.model).__name__ == "TaggedCitationChatLiteLLM"
    return awm.assistant.model.model_kwargs


@pytest.mark.usefixtures("platform")
class TestTheShippedCommunities:
    """The real YAML, loaded and validated as shipped, not a config set by the test."""

    @pytest.fixture
    def platform(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "anthropic_api_key", "platform-anthropic-key")
        monkeypatch.setattr(settings, "openrouter_api_key", None)
        monkeypatch.setattr(settings, "bedrock_api_key", "bedrock-key")
        monkeypatch.setattr(settings, "anthropic_max_output_tokens", 8000)

    def _with_platform_keys_only(self, monkeypatch, info) -> None:
        monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", None)
        monkeypatch.setattr(info.community_config, "openrouter_api_key_env_var", None)
        _without_mcp_servers(monkeypatch, info)

    @pytest.mark.parametrize("community_id", ["nwb", "hed", "eeglab", "bids"])
    def test_a_luna_community_runs_luna_at_the_level_its_yaml_sets(self, monkeypatch, community_id):
        info = registry.get(community_id)
        assert info is not None
        assert info.community_config.default_model == LUNA
        assert info.community_config.reasoning_effort == "high"
        self._with_platform_keys_only(monkeypatch, info)
        awm = create_community_assistant(community_id, origin=_origin(info), preload_docs=False)
        assert awm.model == LUNA
        assert awm.assistant.model.additional_model_request_fields == {
            "reasoning": {"effort": "high"}
        }

    def test_every_community_that_sets_a_level_hands_it_to_the_model_it_runs(self, monkeypatch):
        """Dynamic: whatever communities set a level, on whatever default model they run."""
        with_a_level = [
            info
            for info in registry.list_all()
            if info.community_config and info.community_config.reasoning_effort
        ]
        assert with_a_level, "no community sets reasoning_effort: nothing to check"
        for info in with_a_level:
            self._with_platform_keys_only(monkeypatch, info)
            config = info.community_config
            awm = create_community_assistant(info.id, origin=_origin(info), preload_docs=False)
            expected = effective_reasoning_effort(awm.model, config.reasoning_effort, "bedrock")
            fields = getattr(awm.assistant.model, "additional_model_request_fields", None) or {}
            if awm.model in BEDROCK_MODELS:
                assert fields == BEDROCK_MODELS[awm.model].reasoning_request_fields(expected), (
                    info.id
                )
            else:
                payload = awm.assistant.model._get_request_payload([HumanMessage(content="hi")])
                asked = effective_reasoning_effort(awm.model, config.reasoning_effort, "anthropic")
                sent = (payload.get("output_config") or {}).get("effort")
                # `none` has no level of its own on Claude: it is sent as the lowest, low.
                assert sent == {"none": "low"}.get(asked, asked), info.id


class TestBedrockPath:
    @pytest.mark.parametrize(("level", "sent"), [("low", "low"), ("xhigh", "xhigh")])
    def test_luna_gets_the_communitys_level(self, monkeypatch, hed, level, sent):
        _set_level(monkeypatch, hed, level)
        assert _bedrock_fields(hed, LUNA) == {"reasoning": {"effort": sent}}

    def test_gpt_oss_gets_it_clamped_to_what_it_accepts(self, monkeypatch, hed):
        _set_level(monkeypatch, hed, "max")
        assert _bedrock_fields(hed, GPT_OSS) == {"reasoning_effort": "high"}

    def test_unset_is_the_models_own_default(self, monkeypatch, hed):
        _set_level(monkeypatch, hed, None)
        assert _bedrock_fields(hed, LUNA) == {"reasoning": {"effort": DEFAULT_REASONING_EFFORT}}


class TestAnthropicPath:
    @pytest.mark.parametrize("level", ["low", "medium"])
    def test_sonnet_gets_the_communitys_level(self, monkeypatch, hed, level):
        _set_level(monkeypatch, hed, level)
        assert _anthropic_payload(SONNET)["output_config"] == {"effort": level}

    @pytest.mark.parametrize("asked", ["xhigh", "max"])
    def test_sonnet_is_never_sent_more_than_high(self, monkeypatch, hed, asked):
        _set_level(monkeypatch, hed, asked)
        assert _anthropic_payload(SONNET)["output_config"] == {"effort": "high"}

    def test_unset_is_high_sent_explicitly(self, monkeypatch, hed):
        _set_level(monkeypatch, hed, None)
        assert _anthropic_payload(SONNET)["output_config"] == {"effort": "high"}

    @pytest.mark.parametrize(
        ("level", "budget"), [("low", 1024), ("medium", 2048), ("high", 4096), ("max", 4096)]
    )
    def test_haiku_gets_the_communitys_level_as_a_thinking_budget(
        self, monkeypatch, hed, level, budget
    ):
        _set_level(monkeypatch, hed, level)
        payload = _anthropic_payload(HAIKU)
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": budget}
        assert "output_config" not in payload

    def test_haiku_with_no_thinking_when_the_level_is_none(self, monkeypatch, hed):
        _set_level(monkeypatch, hed, "none")
        assert "thinking" not in _anthropic_payload(HAIKU)

    def test_haiku_unset_is_high(self, monkeypatch, hed):
        """Whatever the process environment says: the retired budget setting is not read."""
        monkeypatch.setenv("ANTHROPIC_THINKING_BUDGET_TOKENS", "1500")
        _set_level(monkeypatch, hed, None)
        assert _anthropic_payload(HAIKU)["thinking"] == {
            "type": "enabled",
            "budget_tokens": THINKING_BUDGET_TOKENS[HAIKU][DEFAULT_REASONING_EFFORT],
        }


class TestOpenRouterPath:
    @pytest.mark.parametrize("level", ["low", "max"])
    def test_luna_gets_the_communitys_level(self, monkeypatch, hed, level):
        _set_level(monkeypatch, hed, level)
        assert _openrouter_kwargs(LUNA)["reasoning"] == {"effort": level}

    def test_sonnet_is_capped_here_too(self, monkeypatch, hed):
        _set_level(monkeypatch, hed, "max")
        assert _openrouter_kwargs(SONNET)["reasoning"] == {"effort": "high"}

    def test_unset_is_the_models_own_default(self, monkeypatch, hed):
        _set_level(monkeypatch, hed, None)
        assert _openrouter_kwargs(LUNA)["reasoning"] == {"effort": DEFAULT_REASONING_EFFORT}
