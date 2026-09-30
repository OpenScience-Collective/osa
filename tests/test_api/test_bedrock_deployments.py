"""What a community's Bedrock default means on each kind of deployment.

Three places have to agree on it: the model a request runs (`_route_request`), the
default the config endpoint tells the widget to show, and the startup log that warns an
operator. The real shipped communities, the real routers and the real registry, on a
deployment whose platform keys the test sets (see tests/helpers/deployment.py).
"""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.config import get_settings
from src.api.main import create_app, lifespan
from src.api.routers.community import (
    _claude_fallback,
    _route_request,
    create_community_assistant,
    create_community_router,
    log_unserved_bedrock_defaults,
)
from src.assistants import discover_assistants, registry
from src.core.services.anthropic_models import BEDROCK_MODELS, is_bedrock_model, normalize_model
from src.core.services.litellm_llm import OPENROUTER_MODEL_IDS
from tests.helpers.deployment import DEPLOYMENTS, set_platform_keys, without_mcp_servers


@pytest.fixture(autouse=True, scope="module")
def _load_communities():
    discover_assistants()


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _bedrock_communities():
    """The registered communities whose default is a Bedrock model, as shipped."""
    found = [
        info
        for info in registry.list_all()
        if info.community_config and is_bedrock_model(info.community_config.default_model)
    ]
    assert found, "no shipped community defaults to a Bedrock model"
    return found


def _origin(info) -> str:
    return next(o for o in info.community_config.cors_origins if "*" not in o)


def _config(community_id: str) -> dict:
    app = FastAPI()
    app.include_router(create_community_router(community_id))
    response = TestClient(app).get(f"/{community_id}/")
    assert response.status_code == 200
    return response.json()


def _offered_ids(data: dict) -> set[str]:
    return {entry["id"] for entry in data["offered_models"]}


class TestTheWidgetDefaultIsWhatTheServerRuns:
    """The config endpoint and routing agree, on every kind of deployment."""

    @pytest.mark.parametrize("deployment", sorted(DEPLOYMENTS))
    def test_every_community_reports_the_model_a_platform_request_runs(
        self, monkeypatch, deployment
    ):
        anthropic, openrouter, bedrock = DEPLOYMENTS[deployment]
        set_platform_keys(monkeypatch, anthropic=anthropic, openrouter=openrouter, bedrock=bedrock)

        for info in registry.list_all():
            data = _config(info.id)
            try:
                route = _route_request(info, info.id, None, _origin(info), None, log_fallback=False)
            except HTTPException as err:
                # No platform key: nothing runs. What is reported is still a model the
                # menu offers, never a Bedrock model the server has no way to run.
                assert err.status_code == 500, (deployment, info.id)
                assert data["default_model"] in _offered_ids(data), (deployment, info.id)
                assert data["default_model"] not in BEDROCK_MODELS, (deployment, info.id)
                continue
            assert data["default_model"] == route.offered_model_id, (deployment, info.id)

    def test_a_served_bedrock_default_is_reported_and_offered(self, monkeypatch):
        set_platform_keys(monkeypatch, anthropic="a", openrouter=None, bedrock="b")
        for info in _bedrock_communities():
            data = _config(info.id)
            assert data["default_model"] == normalize_model(info.community_config.default_model)
            assert data["default_model"] in _offered_ids(data)

    @pytest.mark.parametrize(
        "deployment", ["keyless", "bedrock_only", "anthropic_only", "anthropic_and_openrouter"]
    )
    def test_a_bedrock_default_nothing_can_run_is_reported_as_the_claude_fallback(
        self, monkeypatch, deployment
    ):
        """Keyless, Bedrock key only, and Anthropic key without a Bedrock key."""
        anthropic, openrouter, bedrock = DEPLOYMENTS[deployment]
        set_platform_keys(monkeypatch, anthropic=anthropic, openrouter=openrouter, bedrock=bedrock)
        settings = get_settings()
        expected = _claude_fallback(settings)
        assert expected not in BEDROCK_MODELS

        for info in _bedrock_communities():
            data = _config(info.id)
            assert data["default_model"] == expected, info.id
            assert data["default_model"] in _offered_ids(data), info.id
            # OpenRouter's upstream-host hint is for the model the platform default names,
            # not for a Claude model: a bare Claude id has none (`_select_model`).
            assert data["default_model_provider"] is None, info.id

    @pytest.mark.parametrize("deployment", ["openrouter_only", "bedrock_and_openrouter"])
    def test_on_openrouter_the_community_default_really_runs_and_is_reported(
        self, monkeypatch, deployment
    ):
        """No platform Anthropic key sends requests to OpenRouter, which runs the Bedrock
        model under its slug: reporting the Claude fallback there would be the mistake."""
        anthropic, openrouter, bedrock = DEPLOYMENTS[deployment]
        set_platform_keys(monkeypatch, anthropic=anthropic, openrouter=openrouter, bedrock=bedrock)

        for info in _bedrock_communities():
            default = normalize_model(info.community_config.default_model)
            route = _route_request(info, info.id, None, _origin(info), None, log_fallback=False)
            assert route.choice.provider == "openrouter"
            assert route.model == OPENROUTER_MODEL_IDS[default]
            assert _config(info.id)["default_model"] == default

    def test_a_community_with_a_claude_default_is_untouched(self, monkeypatch):
        set_platform_keys(monkeypatch, anthropic="a", openrouter=None, bedrock=None)
        claude = [
            info
            for info in registry.list_all()
            if info.community_config
            and info.community_config.default_model
            and not is_bedrock_model(info.community_config.default_model)
        ]
        assert claude, "every shipped community defaults to a Bedrock model"
        for info in claude:
            assert _config(info.id)["default_model"] == normalize_model(
                info.community_config.default_model
            )


class TestAnOpenRouterOnlyPlatform:
    """A deployment with only OPENROUTER_API_KEY runs every community on its slug.

    No platform Anthropic key sends platform-funded requests to OpenRouter (a fallback
    `_platform_choice` warns about), and OpenRouter runs each shipped default under the
    slug OSA maps it to, through the real graph and model construction.
    """

    @pytest.fixture(autouse=True)
    def _platform(self, monkeypatch):
        set_platform_keys(monkeypatch, anthropic=None, openrouter="platform-or-key", bedrock=None)
        for info in registry.list_all():
            without_mcp_servers(monkeypatch, info)

    def test_every_community_runs_its_default_under_its_openrouter_slug(self):
        for info in registry.list_all():
            default = normalize_model(
                info.community_config.default_model or get_settings().default_model
            )
            awm = create_community_assistant(info.id, origin=_origin(info), preload_docs=False)
            assert awm.model == OPENROUTER_MODEL_IDS[default], info.id
            assert awm.key_source == "platform", info.id
            assert type(awm.assistant.model).__name__ == "TaggedCitationChatLiteLLM", info.id

    @pytest.mark.parametrize(
        ("community_id", "slug"),
        [
            ("hed", "openai/gpt-6-luna"),
            ("nwb", "openai/gpt-6-luna"),
            ("nemar", "anthropic/claude-sonnet-5.5"),
        ],
    )
    def test_the_shipped_defaults_resolve_to_these_slugs(self, community_id, slug):
        info = registry.get(community_id)
        awm = create_community_assistant(community_id, origin=_origin(info), preload_docs=False)
        assert awm.model == slug

    def test_the_default_model_notes_reach_the_prompt_on_openrouter(self):
        """The Bedrock default's anti-search-loop note follows it onto OpenRouter."""
        info = registry.get("hed")
        default = normalize_model(info.community_config.default_model)
        awm = create_community_assistant("hed", origin=_origin(info), preload_docs=False)
        assert BEDROCK_MODELS[default].prompt_addendum in awm.assistant.get_system_prompt()


class TestTheStartupCheck:
    """One record per community whose Bedrock default this deployment cannot serve."""

    @staticmethod
    def _records(caplog) -> list[logging.LogRecord]:
        return [r for r in caplog.records if hasattr(r, "outcome")]

    def test_a_deployment_that_serves_bedrock_logs_nothing(self, monkeypatch, caplog):
        set_platform_keys(monkeypatch, anthropic="a", openrouter=None, bedrock="b")
        caplog.set_level(logging.WARNING)

        assert log_unserved_bedrock_defaults(get_settings()) == []
        assert self._records(caplog) == []

    @pytest.mark.parametrize("deployment", ["anthropic_only", "anthropic_and_openrouter"])
    def test_running_claude_instead_is_an_error_naming_each_community_and_both_keys(
        self, monkeypatch, caplog, deployment
    ):
        anthropic, openrouter, bedrock = DEPLOYMENTS[deployment]
        set_platform_keys(monkeypatch, anthropic=anthropic, openrouter=openrouter, bedrock=bedrock)
        caplog.set_level(logging.WARNING)
        expected_ids = [info.id for info in _bedrock_communities()]

        logged = log_unserved_bedrock_defaults(get_settings())

        assert logged == expected_ids
        records = self._records(caplog)
        assert [r.community_id for r in records] == expected_ids
        for record in records:
            assert record.levelno == logging.ERROR
            assert record.outcome == "claude_fallback"
            message = record.getMessage()
            assert record.community_id in message
            assert _claude_fallback(get_settings()) in message
            assert "AWS_BEARER_TOKEN_BEDROCK" in message
            assert "ANTHROPIC_API_KEY" in message

    @pytest.mark.parametrize("deployment", ["keyless", "bedrock_only"])
    def test_a_deployment_that_cannot_answer_at_all_is_an_error_too(
        self, monkeypatch, caplog, deployment
    ):
        anthropic, openrouter, bedrock = DEPLOYMENTS[deployment]
        set_platform_keys(monkeypatch, anthropic=anthropic, openrouter=openrouter, bedrock=bedrock)
        caplog.set_level(logging.WARNING)

        logged = log_unserved_bedrock_defaults(get_settings())

        assert logged == [info.id for info in _bedrock_communities()]
        for record in self._records(caplog):
            assert (record.levelno, record.outcome) == (logging.ERROR, "unavailable")
            assert "HTTP 500" in record.getMessage()
            assert "AWS_BEARER_TOKEN_BEDROCK" in record.getMessage()
            assert "ANTHROPIC_API_KEY" in record.getMessage()

    @pytest.mark.parametrize("deployment", ["openrouter_only", "bedrock_and_openrouter"])
    def test_running_the_model_on_openrouter_is_a_warning_naming_the_slug(
        self, monkeypatch, caplog, deployment
    ):
        """It works (the model runs, as its OpenRouter slug), so it is not an error."""
        anthropic, openrouter, bedrock = DEPLOYMENTS[deployment]
        set_platform_keys(monkeypatch, anthropic=anthropic, openrouter=openrouter, bedrock=bedrock)
        caplog.set_level(logging.WARNING)

        log_unserved_bedrock_defaults(get_settings())

        records = self._records(caplog)
        assert records
        for record in records:
            info = registry.get(record.community_id)
            default = normalize_model(info.community_config.default_model)
            assert (record.levelno, record.outcome) == (logging.WARNING, "openrouter")
            assert OPENROUTER_MODEL_IDS[default] in record.getMessage()
            assert "AWS_BEARER_TOKEN_BEDROCK" in record.getMessage()
            assert "ANTHROPIC_API_KEY" in record.getMessage()

    async def test_the_app_runs_the_check_when_it_starts(self, monkeypatch, caplog, tmp_path):
        set_platform_keys(monkeypatch, anthropic="a", openrouter=None, bedrock=None)
        monkeypatch.setattr(get_settings(), "sync_enabled", False)
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        caplog.set_level(logging.WARNING)

        async with lifespan(create_app()):
            pass

        ids = [r.community_id for r in self._records(caplog)]
        assert ids == [info.id for info in _bedrock_communities()]
