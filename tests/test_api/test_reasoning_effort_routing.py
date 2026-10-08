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
from src.api.routers.community import _route_request, create_community_assistant
from src.api.security import ByokCredential
from src.assistants import discover_assistants, registry
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    DEFAULT_REASONING_EFFORT,
    HAIKU,
    SONNET,
    THINKING_OFF,
    effective_reasoning_effort,
    is_bedrock_model,
    normalize_model,
)
from tests.helpers.deployment import without_mcp_servers

LUNA = "openai.gpt-6-luna"
GPT_OSS = "openai.gpt-oss-120b"


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
    # max_tokens comes from the environment through get_settings: pin it so a machine's
    # own value cannot decide these tests.
    monkeypatch.setattr(settings, "anthropic_max_output_tokens", 8000)
    info = registry.get("hed")
    assert info is not None
    monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", None)
    monkeypatch.setattr(info.community_config, "openrouter_api_key_env_var", None)
    without_mcp_servers(monkeypatch, info)
    return info


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
        without_mcp_servers(monkeypatch, info)

    def test_a_luna_community_runs_luna_at_the_level_its_yaml_sets(self, monkeypatch):
        """A community that names GPT-6 Luna runs it at the level its YAML sets.

        No shipped community names Luna now, so each one that sets high is given Luna
        as its default here, the way its config.yaml would name it.
        """
        with_high = [
            info
            for info in registry.list_all()
            if info.community_config and info.community_config.reasoning_effort == "high"
        ]
        assert with_high, "no community sets reasoning_effort: high, nothing to check"
        for info in with_high:
            monkeypatch.setattr(info.community_config, "default_model", LUNA)
            self._with_platform_keys_only(monkeypatch, info)
            awm = create_community_assistant(info.id, origin=_origin(info), preload_docs=False)
            assert awm.model == LUNA, info.id
            assert awm.assistant.model.additional_model_request_fields == {
                "reasoning": {"effort": "high"}
            }, info.id

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
            if awm.model in BEDROCK_MODELS:
                expected = effective_reasoning_effort(awm.model, config.reasoning_effort, "bedrock")
                fields = awm.assistant.model.additional_model_request_fields or {}
                assert fields == BEDROCK_MODELS[awm.model].reasoning_request_fields(expected), (
                    info.id
                )
                continue
            _assert_claude_level(awm, config.reasoning_effort, info.id)

    def test_a_community_on_haiku_hands_its_level_over_as_an_effort(self, monkeypatch):
        """Haiku 5.5 takes the same effort field as Sonnet: the key is sent as written."""
        on_haiku = [
            info
            for info in registry.list_all()
            if info.community_config
            and normalize_model(info.community_config.default_model or get_settings().default_model)
            == HAIKU
        ]
        assert on_haiku, "no shipped community defaults to Claude Haiku"
        for level in ("low", "medium"):
            for info in on_haiku:
                self._with_platform_keys_only(monkeypatch, info)
                monkeypatch.setattr(info.community_config, "reasoning_effort", level)
                awm = create_community_assistant(info.id, origin=_origin(info), preload_docs=False)
                payload = awm.assistant.model._get_request_payload([HumanMessage(content="hi")])
                assert awm.model == HAIKU, info.id
                assert payload["thinking"] == {"type": "adaptive"}, info.id
                assert payload["output_config"] == {"effort": level}, info.id
                _assert_claude_level(awm, level, info.id)

    def test_nemar_is_pinned_to_sonnet_on_the_claude_platform_at_high(self, monkeypatch):
        """NEMAR's tools return figures, which only a Claude model can see (#522, #530)."""
        info = registry.get("nemar")
        assert info is not None
        self._with_platform_keys_only(monkeypatch, info)

        route = _route_request(info, "nemar", None, _origin(info), None)

        assert route.choice.provider == "anthropic"
        assert route.model == SONNET
        assert route.choice.takes_native_blocks
        awm = create_community_assistant("nemar", origin=_origin(info), preload_docs=False)
        assert awm.model == SONNET
        payload = awm.assistant.model._get_request_payload([HumanMessage(content="hi")])
        assert payload["output_config"] == {"effort": "high"}

    def test_a_community_with_client_tools_or_mcp_servers_does_not_default_to_a_bedrock_model(
        self,
    ):
        """Dynamic. Their tools return image blocks, and no Bedrock model takes images."""
        with_tools = [
            info
            for info in registry.list_all()
            if info.community_config
            and info.community_config.extensions
            and (
                info.community_config.extensions.client_tools
                or info.community_config.extensions.mcp_servers
            )
        ]
        assert with_tools, "no shipped community has client tools or MCP servers"
        for info in with_tools:
            assert not is_bedrock_model(info.community_config.default_model), info.id


def _assert_claude_level(awm, requested: str | None, community_id: str) -> None:
    """A Claude model is handed the level as its provider names it: `output_config.effort`
    (`none` is sent as `low`, the lowest level there is, with the model's own thinking-off
    value)."""
    payload = awm.assistant.model._get_request_payload([HumanMessage(content="hi")])
    asked = effective_reasoning_effort(awm.model, requested, "anthropic")
    sent = (payload.get("output_config") or {}).get("effort")
    assert sent == {"none": "low"}.get(asked, asked), community_id
    if asked == "none":
        assert payload["thinking"] == THINKING_OFF[normalize_model(awm.model)], community_id


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

    @pytest.mark.parametrize("level", ["low", "medium"])
    def test_haiku_gets_the_communitys_level(self, monkeypatch, hed, level):
        _set_level(monkeypatch, hed, level)
        payload = _anthropic_payload(HAIKU)
        assert payload["output_config"] == {"effort": level}
        assert payload["thinking"] == {"type": "adaptive"}

    @pytest.mark.parametrize("asked", ["xhigh", "max"])
    def test_haiku_is_never_sent_more_than_high(self, monkeypatch, hed, asked):
        _set_level(monkeypatch, hed, asked)
        assert _anthropic_payload(HAIKU)["output_config"] == {"effort": "high"}

    def test_haiku_with_thinking_off_when_the_level_is_none(self, monkeypatch, hed):
        _set_level(monkeypatch, hed, "none")
        payload = _anthropic_payload(HAIKU)
        assert payload["thinking"] == THINKING_OFF[HAIKU]
        assert payload["output_config"] == {"effort": "low"}

    def test_haiku_unset_is_high_sent_explicitly(self, monkeypatch, hed):
        """Its own default is medium, so high is what a community that sets nothing gets only
        because it is sent. Whatever the process environment says: the retired budget setting
        is not read."""
        monkeypatch.setenv("ANTHROPIC_THINKING_BUDGET_TOKENS", "1500")
        _set_level(monkeypatch, hed, None)
        payload = _anthropic_payload(HAIKU)
        assert payload["thinking"] == {"type": "adaptive"}
        assert payload["output_config"] == {"effort": DEFAULT_REASONING_EFFORT}


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
