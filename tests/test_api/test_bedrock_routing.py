"""Tests for routing requests to the Bedrock models.

Uses the real registered communities and real Settings, like test_authorization.py:
the cached Settings instance is overridden directly where a test needs the Bedrock
key present or absent, because pydantic-settings also reads a developer's .env.
"""

import logging
import os

import pytest
from fastapi import HTTPException

from src.api.config import RETIRED_ENV_VARS, get_settings
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
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    HAIKU,
    is_bedrock_model,
    normalize_model,
)
from src.core.services.litellm_llm import OPENROUTER_MODEL_IDS
from tests.helpers.deployment import set_platform_keys


@pytest.fixture(autouse=True, scope="module")
def _load_communities():
    discover_assistants()


@pytest.fixture(autouse=True)
def _no_retired_env_vars(monkeypatch):
    """Run without the retired variables a developer's or a server's shell may export.

    ``get_settings`` logs a warning for each one that is set (``RETIRED_ENV_VARS``), and the
    tests below assert exactly what a request logs, so one exported name would fail them
    on that machine alone. The warning reads the environment case-insensitively, so every
    spelling present is removed. A test about the warning sets the name itself.
    """
    for name in [name for name in os.environ if name.upper() in RETIRED_ENV_VARS]:
        monkeypatch.delenv(name)


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
    set_platform_keys(monkeypatch, bedrock=bedrock)


class TestProviderChoiceForBedrock:
    def test_a_platform_bedrock_choice_carries_no_key(self):
        choice = ProviderChoice(provider="bedrock", api_key=None, key_source="platform")
        assert choice.api_key is None

    @pytest.mark.parametrize("key_source", ["byok", "community"])
    def test_a_keyless_bedrock_choice_must_be_platform_funded(self, key_source):
        with pytest.raises(ValueError, match="api_key=None is only valid"):
            ProviderChoice(provider="bedrock", api_key=None, key_source=key_source)

    @pytest.mark.parametrize(
        ("api_key", "key_source"),
        [("sk-user", "byok"), ("sk-user", "community"), ("sk-user", "platform")],
    )
    def test_a_bedrock_choice_cannot_carry_a_key_or_another_funder(self, api_key, key_source):
        """A `byok` Bedrock choice would spend the platform's token with cost checks off."""
        with pytest.raises(ValueError, match="always platform-funded"):
            ProviderChoice(provider="bedrock", api_key=api_key, key_source=key_source)

    def test_citations_come_from_tags_not_native_blocks(self):
        bedrock = ProviderChoice(provider="bedrock", api_key=None, key_source="platform")
        assert bedrock.tags_citations is True
        assert bedrock.cites_sources is True
        # Images are a separate question: nothing shows Bedrock models take them.
        assert bedrock.takes_native_blocks is False

    def test_anthropic_cites_natively_and_the_others_by_tags(self):
        anthropic = ProviderChoice(provider="anthropic", api_key=None, key_source="platform")
        assert (anthropic.takes_native_blocks, anthropic.tags_citations) == (True, False)
        assert anthropic.cites_sources is True
        openrouter = ProviderChoice(provider="openrouter", api_key="k", key_source="byok")
        assert (openrouter.takes_native_blocks, openrouter.tags_citations) == (False, True)
        assert openrouter.cites_sources is True

    @pytest.mark.parametrize("provider", ["anthropic", "bedrock", "openrouter"])
    def test_every_provider_cites_its_sources(self, provider):
        """No provider path is left on the markdown-link fallback prompt."""
        api_key = None if provider != "openrouter" else "k"
        key_source = "platform" if provider != "openrouter" else "byok"
        choice = ProviderChoice(provider=provider, api_key=api_key, key_source=key_source)
        assert choice.cites_sources is True


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

        route = _route_request(info, "hed", None, _origin(info), HAIKU)

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


def _luna_default_community() -> AssistantInfo:
    """The #514 case: a community whose default model is a Bedrock model."""
    return AssistantInfo(
        id="hed",
        name="HED",
        description="x",
        community_config=CommunityConfig(
            id="hed",
            name="HED",
            description="x",
            default_model="openai.gpt-6-luna",
            cors_origins=[_origin(_hed())],
        ),
    )


class TestABedrockDefaultThatCannotBeServed:
    """Nobody asked for the community's default, so refusing would take the community down."""

    def test_a_caller_with_their_own_key_and_no_model_runs_claude(self, monkeypatch, caplog):
        """The CLI never sends a model and can only use its own key."""
        _platform(monkeypatch)
        byok = ByokCredential(key="user-anthropic-key", provider="anthropic")

        route = _route_request(_luna_default_community(), "hed", byok, None, None)

        assert (route.choice.provider, route.choice.key_source) == ("anthropic", "byok")
        assert route.model == HAIKU
        assert "openai.gpt-6-luna" in caplog.text

    def test_a_deployment_without_a_bedrock_key_runs_claude(self, monkeypatch):
        _platform(monkeypatch, bedrock=None)
        info = _luna_default_community()

        route = _route_request(info, "hed", None, _origin(_hed()), None)

        assert (route.choice.provider, route.model) == ("anthropic", HAIKU)

    def test_the_deployments_own_claude_default_is_the_fallback(self, monkeypatch):
        _platform(monkeypatch, bedrock=None)
        monkeypatch.setattr(get_settings(), "default_model", "claude-sonnet-5-5")

        route = _route_request(_luna_default_community(), "hed", None, _origin(_hed()), None)

        assert route.model == "claude-sonnet-5-5"

    def test_a_bedrock_deployment_default_is_never_the_fallback(self, monkeypatch):
        _platform(monkeypatch, bedrock=None)
        monkeypatch.setattr(get_settings(), "default_model", "openai.gpt-oss-120b")

        route = _route_request(_luna_default_community(), "hed", None, _origin(_hed()), None)

        assert route.model == HAIKU

    def test_naming_the_model_still_gets_the_refusal(self, monkeypatch):
        """A caller who asked for Luna with their own key is told why not."""
        _platform(monkeypatch)
        byok = ByokCredential(key="user-anthropic-key", provider="anthropic")

        with pytest.raises(HTTPException) as caught:
            _route_request(_luna_default_community(), "hed", byok, None, "openai.gpt-6-luna")

        assert caught.value.status_code == 403

    def test_naming_the_model_on_a_deployment_without_the_key_is_still_a_400(self, monkeypatch):
        _platform(monkeypatch, bedrock=None)
        info = _luna_default_community()

        with pytest.raises(HTTPException) as caught:
            _route_request(info, "hed", None, _origin(_hed()), "openai.gpt-6-luna")

        assert caught.value.status_code == 400

    def test_the_default_is_still_served_where_it_can_be(self, monkeypatch):
        _platform(monkeypatch)

        route = _route_request(_luna_default_community(), "hed", None, _origin(_hed()), None)

        assert (route.choice.provider, route.model) == ("bedrock", "openai.gpt-6-luna")


def _fallback_records(caplog) -> list[logging.LogRecord]:
    """The records that say a Bedrock default was replaced (they carry a `fallback`)."""
    return [r for r in caplog.records if hasattr(r, "fallback")]


class TestTheFallbackLog:
    """ERROR when the deployment cannot serve the model, WARNING for a caller's own key."""

    def test_a_deployment_without_the_key_logs_an_error(self, monkeypatch, caplog):
        _platform(monkeypatch, bedrock=None)
        caplog.set_level(logging.WARNING)

        _route_request(_luna_default_community(), "hed", None, _origin(_hed()), None)

        (record,) = _fallback_records(caplog)
        assert record.levelno == logging.ERROR
        assert record.community_id == "hed"
        assert record.model == "openai.gpt-6-luna"
        assert record.fallback == HAIKU
        assert (record.key_source, record.cause) == ("platform", "no_bedrock_key")
        assert "AWS_BEARER_TOKEN_BEDROCK" in record.getMessage()

    def test_a_callers_own_key_with_no_model_logs_a_warning(self, monkeypatch, caplog):
        """Every CLI request is this: expected, so it must not read as a misconfiguration."""
        _platform(monkeypatch)
        caplog.set_level(logging.WARNING)
        byok = ByokCredential(key="user-anthropic-key", provider="anthropic")

        _route_request(_luna_default_community(), "hed", byok, None, None)

        (record,) = _fallback_records(caplog)
        assert record.levelno == logging.WARNING
        assert record.community_id == "hed"
        assert record.model == "openai.gpt-6-luna"
        assert record.fallback == HAIKU
        assert (record.key_source, record.cause) == ("byok", "callers_own_key")

    def test_a_callers_own_key_on_a_deployment_without_the_key_is_still_an_error(
        self, monkeypatch, caplog
    ):
        """The deployment cannot serve the model either way, which is what needs fixing."""
        _platform(monkeypatch, bedrock=None)
        caplog.set_level(logging.WARNING)
        byok = ByokCredential(key="user-anthropic-key", provider="anthropic")

        _route_request(_luna_default_community(), "hed", byok, None, None)

        (record,) = _fallback_records(caplog)
        assert (record.levelno, record.cause) == (logging.ERROR, "no_bedrock_key")

    def test_a_probe_routes_the_same_and_logs_nothing(self, monkeypatch, caplog):
        """`/chat/resume` routes twice per request; only the real one logs."""
        _platform(monkeypatch, bedrock=None)
        caplog.set_level(logging.WARNING)
        info = _luna_default_community()

        quiet = _route_request(info, "hed", None, _origin(_hed()), None, log=False)
        assert _fallback_records(caplog) == []
        loud = _route_request(info, "hed", None, _origin(_hed()), None)

        assert (quiet.choice, quiet.model) == (loud.choice, loud.model)
        assert len(_fallback_records(caplog)) == 1

    @pytest.mark.parametrize(
        "situation",
        ["openrouter_fallback", "no_platform_key", "community_key_unset", "bad_default"],
    )
    def test_a_probe_is_silent_about_everything_routing_finds(self, monkeypatch, caplog, situation):
        """Not just the Bedrock fallback: the platform falling back to OpenRouter, having no
        key at all, a community key that is missing, and a default nothing can serve are
        each logged by routing too, and `/chat/resume` would log each of them twice."""
        info = _hed()
        config = info.community_config
        if situation == "openrouter_fallback":
            set_platform_keys(monkeypatch, anthropic=None, openrouter="or-key", bedrock=None)
        elif situation == "no_platform_key":
            set_platform_keys(monkeypatch, anthropic=None, openrouter=None, bedrock=None)
        elif situation == "community_key_unset":
            set_platform_keys(monkeypatch)
            monkeypatch.setattr(config, "anthropic_api_key_env_var", "OSA_TEST_UNSET_KEY")
            monkeypatch.delenv("OSA_TEST_UNSET_KEY", raising=False)
        else:
            set_platform_keys(monkeypatch, anthropic=None, openrouter="or-key", bedrock=None)
            monkeypatch.setattr(config, "default_model", "not-an-offered-model")
            monkeypatch.setattr(config, "default_model_provider", None)
        caplog.set_level(logging.INFO)

        def route(**kwargs):
            try:
                return _route_request(info, "hed", None, _origin(info), None, **kwargs)
            except HTTPException as err:
                return err.status_code

        quiet = route(log=False)
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
        loud = route()

        assert [r for r in caplog.records if r.levelno >= logging.WARNING], "it does log"
        if situation == "bad_default":
            assert any("neither an offered model" in r.getMessage() for r in caplog.records)
        assert quiet == loud or (quiet.choice, quiet.model) == (loud.choice, loud.model)

    def test_a_community_key_in_use_is_noted_once_and_not_by_a_probe(self, monkeypatch, caplog):
        set_platform_keys(monkeypatch)
        info = _hed()
        monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", "OSA_TEST_KEY")
        monkeypatch.setenv("OSA_TEST_KEY", "community-key")
        caplog.set_level(logging.INFO)

        _route_request(info, "hed", None, _origin(info), None, log=False)
        assert [r for r in caplog.records if hasattr(r, "env_var")] == []
        _route_request(info, "hed", None, _origin(info), None)

        (record,) = [r for r in caplog.records if hasattr(r, "env_var")]
        assert record.env_var == "OSA_TEST_KEY" and record.key_source == "community"

    def test_nothing_is_logged_where_the_default_is_served(self, monkeypatch, caplog):
        _platform(monkeypatch)
        caplog.set_level(logging.WARNING)

        route = _route_request(_luna_default_community(), "hed", None, _origin(_hed()), None)

        assert route.choice.provider == "bedrock"
        assert _fallback_records(caplog) == []

    def test_every_shipped_bedrock_default_fails_loudly_on_a_deployment_without_the_key(
        self, monkeypatch, caplog
    ):
        """Dynamic: each community whose default is a Bedrock model, as shipped."""
        set_platform_keys(monkeypatch, bedrock=None)
        caplog.set_level(logging.WARNING)
        bedrock_communities = [
            info
            for info in registry.list_all()
            if info.community_config and is_bedrock_model(info.community_config.default_model)
        ]
        assert bedrock_communities, "no shipped community defaults to a Bedrock model"

        for info in bedrock_communities:
            caplog.clear()
            route = _route_request(info, info.id, None, _origin(info), None)

            assert route.choice.provider == "anthropic", info.id
            assert route.model not in BEDROCK_MODELS, info.id
            (record,) = _fallback_records(caplog)
            assert record.levelno == logging.ERROR, info.id
            assert record.community_id == info.id
            assert info.id in record.getMessage()
            assert record.model == normalize_model(info.community_config.default_model)
            assert record.fallback == route.model


class TestNoPlatformKey:
    """A request no key can serve fails with a 500, and the log says why."""

    def test_a_bedrock_key_alone_serves_nothing_and_the_error_log_says_so(
        self, monkeypatch, caplog
    ):
        set_platform_keys(monkeypatch, anthropic=None, openrouter=None, bedrock="bedrock-key")
        caplog.set_level(logging.ERROR)
        info = _hed()

        with pytest.raises(HTTPException) as caught:
            _route_request(info, "hed", None, _origin(info), None)

        assert caught.value.status_code == 500
        (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert record.community_id == "hed"
        assert record.bedrock_key_configured is True
        assert "ANTHROPIC_API_KEY" in record.getMessage()
        assert "AWS_BEARER_TOKEN_BEDROCK" in record.getMessage()

    def test_a_keyless_deployment_logs_it_too(self, monkeypatch, caplog):
        set_platform_keys(monkeypatch, anthropic=None, openrouter=None, bedrock=None)
        caplog.set_level(logging.ERROR)
        info = _hed()

        with pytest.raises(HTTPException) as caught:
            _route_request(info, "hed", None, _origin(info), None)

        assert caught.value.status_code == 500
        (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert record.community_id == "hed"
        assert record.bedrock_key_configured is False
        assert "AWS_BEARER_TOKEN_BEDROCK" not in record.getMessage()


class TestBedrockAndOpenRouterWithoutAnthropic:
    """No platform Anthropic key sends platform requests to OpenRouter, Bedrock key or not.

    That is the long-standing fallback for a deployment that has not configured an
    Anthropic key (`_platform_choice` warns on every such request), and it is why
    `_serves_bedrock_models` needs both keys: Bedrock models are reached only from the
    Anthropic provider. A community whose default is a Bedrock model runs it on OpenRouter
    under its slug there, which OpenRouter prices no higher than Bedrock does.
    """

    def test_the_default_runs_on_openrouter_and_the_fallback_is_warned_about(
        self, monkeypatch, caplog
    ):
        set_platform_keys(
            monkeypatch, anthropic=None, openrouter="platform-or-key", bedrock="b-key"
        )
        caplog.set_level(logging.WARNING)
        info = _hed()

        route = _route_request(info, "hed", None, _origin(info), None)

        default = normalize_model(info.community_config.default_model)
        assert route.choice.provider == "openrouter"
        assert route.choice.key_source == "platform"
        assert route.model == OPENROUTER_MODEL_IDS[default]
        assert route.offered_model_id == default
        (record,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert "ANTHROPIC_API_KEY is not configured" in record.getMessage()


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
