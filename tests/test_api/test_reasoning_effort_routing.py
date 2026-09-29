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
from src.core.services.anthropic_models import REASONING_DEFAULTS

SONNET = "claude-sonnet-5-5"
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
    info = registry.get("hed")
    assert info is not None
    monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", None)
    monkeypatch.setattr(info.community_config, "openrouter_api_key_env_var", None)
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
        assert _bedrock_fields(hed, LUNA) == {"reasoning": {"effort": REASONING_DEFAULTS[LUNA]}}


class TestAnthropicPath:
    @pytest.mark.parametrize("level", ["low", "medium"])
    def test_sonnet_gets_the_communitys_level(self, monkeypatch, hed, level):
        _set_level(monkeypatch, hed, level)
        assert _anthropic_payload(SONNET)["output_config"] == {"effort": level}

    @pytest.mark.parametrize("asked", ["xhigh", "max"])
    def test_sonnet_is_never_sent_more_than_high(self, monkeypatch, hed, asked):
        _set_level(monkeypatch, hed, asked)
        assert _anthropic_payload(SONNET)["output_config"] == {"effort": "high"}

    def test_unset_sends_nothing_so_the_api_default_applies(self, monkeypatch, hed):
        _set_level(monkeypatch, hed, None)
        assert "output_config" not in _anthropic_payload(SONNET)


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
        assert _openrouter_kwargs(LUNA)["reasoning"] == {"effort": REASONING_DEFAULTS[LUNA]}
