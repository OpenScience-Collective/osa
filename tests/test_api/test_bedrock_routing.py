"""Tests for routing requests to the Bedrock models.

Uses the real registered communities and real Settings, like test_authorization.py:
the cached Settings instance is overridden directly where a test needs the Bedrock
key present or absent, because pydantic-settings also reads a developer's .env.
"""

import pytest
from fastapi import HTTPException

from src.api.config import get_settings
from src.api.routers.community import (
    ProviderChoice,
    _bedrock_choice,
    _check_model_cost,
    _route_request,
)
from src.api.security import ByokCredential
from src.assistants import discover_assistants, registry
from src.assistants.registry import AssistantInfo
from src.core.config.community import CommunityConfig
from src.core.services.anthropic_models import BEDROCK_MODELS
from src.core.services.litellm_llm import OPENROUTER_MODEL_IDS


@pytest.fixture(autouse=True, scope="module")
def _load_communities():
    discover_assistants()


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _hed():
    info = registry.get("hed")
    assert info is not None
    return info


def _origin(info) -> str:
    for origin in info.community_config.cors_origins:
        if "*" not in origin:
            return origin
    pytest.fail("no exact CORS origin")


def _platform(monkeypatch, *, bedrock: str | None = "bedrock-key") -> None:
    """Platform keys as a deployment with Anthropic and (optionally) Bedrock has them."""
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "platform-anthropic-key")
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    monkeypatch.setattr(settings, "bedrock_api_key", bedrock)
    info = _hed()
    monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", None)
    monkeypatch.setattr(info.community_config, "openrouter_api_key_env_var", None)


class TestProviderChoiceForBedrock:
    def test_a_platform_bedrock_choice_carries_no_key(self):
        choice = ProviderChoice(provider="bedrock", api_key=None, key_source="platform")
        assert choice.api_key is None

    @pytest.mark.parametrize("key_source", ["byok", "community"])
    def test_a_keyless_bedrock_choice_must_be_platform_funded(self, key_source):
        with pytest.raises(ValueError, match="api_key=None is only valid"):
            ProviderChoice(provider="bedrock", api_key=None, key_source=key_source)

    def test_citations_come_from_tags_not_native_blocks(self):
        bedrock = ProviderChoice(provider="bedrock", api_key=None, key_source="platform")
        assert bedrock.tags_citations is True
        assert bedrock.cites_sources is True
        # Images are a separate question: nothing shows Bedrock models take them.
        assert bedrock.takes_native_blocks is False

    def test_anthropic_cites_natively_and_openrouter_not_at_all(self):
        anthropic = ProviderChoice(provider="anthropic", api_key=None, key_source="platform")
        assert (anthropic.takes_native_blocks, anthropic.tags_citations) == (True, False)
        assert anthropic.cites_sources is True
        openrouter = ProviderChoice(provider="openrouter", api_key="k", key_source="byok")
        assert (openrouter.takes_native_blocks, openrouter.tags_citations) == (False, False)
        assert openrouter.cites_sources is False


class TestRouteRequest:
    @pytest.mark.parametrize("model_id", sorted(BEDROCK_MODELS))
    def test_a_bedrock_model_goes_to_bedrock_on_the_platform_key(self, monkeypatch, model_id):
        _platform(monkeypatch)
        info = _hed()

        route = _route_request(info, "hed", None, _origin(info), model_id)

        assert route.choice == ProviderChoice(
            provider="bedrock", api_key=None, key_source="platform"
        )
        assert route.model == model_id
        assert route.provider_hint is None

    def test_an_alias_of_a_bedrock_model_routes_the_same_way(self, monkeypatch):
        _platform(monkeypatch)
        info = _hed()

        route = _route_request(info, "hed", None, _origin(info), "us.openai.gpt-6-luna")

        assert (route.choice.provider, route.model) == ("bedrock", "openai.gpt-6-luna")

    def test_a_claude_model_still_goes_to_anthropic(self, monkeypatch):
        _platform(monkeypatch)
        info = _hed()

        route = _route_request(info, "hed", None, _origin(info), "claude-haiku-4-5")

        assert route.choice.provider == "anthropic"

    def test_a_communitys_bedrock_default_is_used_when_nothing_is_requested(self, monkeypatch):
        _platform(monkeypatch)
        info = AssistantInfo(
            id="hed",
            name="HED",
            description="x",
            community_config=CommunityConfig(
                id="hed",
                name="HED",
                description="x",
                default_model="openai.gpt-oss-120b",
                cors_origins=[_origin(_hed())],
            ),
        )

        route = _route_request(info, "hed", None, _origin(_hed()), None)

        assert (route.choice.provider, route.model) == ("bedrock", "openai.gpt-oss-120b")

    def test_an_unauthorized_origin_cannot_reach_the_platform_bedrock_key(self, monkeypatch):
        _platform(monkeypatch)

        with pytest.raises(HTTPException) as caught:
            _route_request(_hed(), "hed", None, "https://evil.example", "openai.gpt-6-luna")

        assert caught.value.status_code == 403

    def test_a_callers_own_anthropic_key_cannot_spend_the_platforms_bedrock_key(self, monkeypatch):
        """BYOK skips the origin check because the caller pays; Bedrock is not theirs."""
        _platform(monkeypatch)
        byok = ByokCredential(key="user-anthropic-key", provider="anthropic")

        with pytest.raises(HTTPException) as caught:
            _route_request(_hed(), "hed", byok, "https://evil.example", "openai.gpt-6-luna")

        assert caught.value.status_code == 403
        assert "openai.gpt-6-luna" in caught.value.detail

    def test_a_deployment_without_a_bedrock_key_refuses_clearly(self, monkeypatch):
        _platform(monkeypatch, bedrock=None)
        info = _hed()

        with pytest.raises(HTTPException) as caught:
            _route_request(info, "hed", None, _origin(info), "openai.gpt-6-luna")

        assert caught.value.status_code == 400
        assert "not available on this server" in caught.value.detail

    def test_an_openrouter_byok_caller_gets_the_openrouter_slug(self, monkeypatch):
        """A caller who pays through OpenRouter runs the same model there."""
        _platform(monkeypatch)
        byok = ByokCredential(key="user-or-key", provider="openrouter")

        route = _route_request(_hed(), "hed", byok, None, "openai.gpt-6-luna")

        assert route.choice.provider == "openrouter"
        assert route.model == OPENROUTER_MODEL_IDS["openai.gpt-6-luna"]


class TestBedrockChoice:
    def test_a_community_funded_anthropic_request_moves_to_platform_funded_bedrock(
        self, monkeypatch
    ):
        """The community's Anthropic key does not pay for Bedrock; the platform does."""
        settings = get_settings()
        monkeypatch.setattr(settings, "bedrock_api_key", "bedrock-key")
        community = ProviderChoice(
            provider="anthropic", api_key="community-key", key_source="community"
        )

        choice = _bedrock_choice(community, "openai.gpt-6-luna", settings)

        assert choice == ProviderChoice(provider="bedrock", api_key=None, key_source="platform")


class TestBedrockModelsPassTheCostGuard:
    @pytest.mark.parametrize("model_id", sorted(BEDROCK_MODELS))
    def test_every_bedrock_model_is_allowed_on_the_platform_key(self, model_id):
        _check_model_cost(model_id, "platform")
