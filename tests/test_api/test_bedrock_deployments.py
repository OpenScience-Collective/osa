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
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    MODEL_CLASSES,
    is_bedrock_model,
    normalize_model,
)
from src.core.services.litellm_llm import OPENROUTER_MODEL_IDS
from tests.helpers.deployment import (
    DEPLOYMENTS,
    name_luna_as_the_default,
    set_platform_keys,
    without_mcp_servers,
)


@pytest.fixture(autouse=True, scope="module")
def _load_communities():
    discover_assistants()


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _bedrock_communities(monkeypatch):
    """The registered communities whose default is a Bedrock model, once the test names one.

    No shipped community names a Bedrock model now, so NWB is given GPT-6 Luna here.
    """
    name_luna_as_the_default(monkeypatch)
    found = [
        info
        for info in registry.list_all()
        if info.community_config and is_bedrock_model(info.community_config.default_model)
    ]
    assert found, "no community defaults to a Bedrock model"
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
                route = _route_request(info, info.id, None, _origin(info), None, log=False)
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
        for info in _bedrock_communities(monkeypatch):
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

        for info in _bedrock_communities(monkeypatch):
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

        for info in _bedrock_communities(monkeypatch):
            default = normalize_model(info.community_config.default_model)
            route = _route_request(info, info.id, None, _origin(info), None, log=False)
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


KEY_STATES = ("unnamed", "unset", "set")


def _name_community_keys(monkeypatch, info, *, anthropic: str, openrouter: str) -> None:
    """Have a community fund itself: name the env vars its keys are read from, and give
    each the state a deployment could leave it in (not named, named but unset, named and
    set)."""
    for kind, state, attr in (
        ("ANTHROPIC", anthropic, "anthropic_api_key_env_var"),
        ("OPENROUTER", openrouter, "openrouter_api_key_env_var"),
    ):
        if state == "unnamed":
            continue
        var = f"OSA_TEST_{info.id.upper()}_{kind}_KEY"
        monkeypatch.setattr(info.community_config, attr, var)
        if state == "set":
            monkeypatch.setenv(var, "community-key")
        else:
            monkeypatch.delenv(var, raising=False)


class TestACommunityThatFundsItself:
    """A community's own key decides where its requests go, as much as the platform's
    does (``_resolve_provider``), so the widget's default, the model menu and the startup
    log must read the same key. Every combination of platform keys and of the community's
    two keys, against the route a request really takes."""

    @pytest.mark.parametrize("openrouter", KEY_STATES)
    @pytest.mark.parametrize("anthropic", KEY_STATES)
    @pytest.mark.parametrize("deployment", sorted(DEPLOYMENTS))
    def test_what_is_reported_is_what_the_request_runs(
        self, monkeypatch, caplog, deployment, anthropic, openrouter
    ):
        platform_anthropic, platform_openrouter, platform_bedrock = DEPLOYMENTS[deployment]
        set_platform_keys(
            monkeypatch,
            anthropic=platform_anthropic,
            openrouter=platform_openrouter,
            bedrock=platform_bedrock,
        )
        info = _bedrock_communities(monkeypatch)[0]
        _name_community_keys(monkeypatch, info, anthropic=anthropic, openrouter=openrouter)
        caplog.set_level(logging.WARNING)
        settings = get_settings()

        data = _config(info.id)
        logged = log_unserved_bedrock_defaults(settings)
        records = [
            r
            for r in caplog.records
            if hasattr(r, "outcome") and getattr(r, "community_id", None) == info.id
        ]
        bedrock_menu = BEDROCK_MODELS.keys() & _offered_ids(data)
        try:
            route = _route_request(info, info.id, None, _origin(info), None, log=False)
        except HTTPException as err:
            assert err.status_code == 500
            assert data["default_model"] == _claude_fallback(settings)
            assert not bedrock_menu
            assert [r.outcome for r in records] == ["unavailable"]
            return

        default = normalize_model(info.community_config.default_model)
        if route.choice.provider == "bedrock":
            assert data["default_model"] == default == route.model
            assert bedrock_menu == set(BEDROCK_MODELS)
            assert records == [] and info.id not in logged
        elif route.choice.provider == "openrouter":
            # OpenRouter runs the model under its slug, and is not offered the Bedrock menu.
            assert data["default_model"] == default
            assert route.model == OPENROUTER_MODEL_IDS[default]
            assert not bedrock_menu
            assert [r.outcome for r in records] == ["openrouter"]
        else:
            assert data["default_model"] == route.model == _claude_fallback(settings)
            assert not bedrock_menu
            assert [r.outcome for r in records] == ["claude_fallback"]

    def test_its_own_anthropic_key_serves_bedrock_without_a_platform_anthropic_key(
        self, monkeypatch
    ):
        """The case the platform-only reading got wrong: a Bedrock key and nothing else on
        the platform, and a community with its own Anthropic key, runs Bedrock."""
        set_platform_keys(monkeypatch, anthropic=None, openrouter=None, bedrock="bedrock-key")
        info = _bedrock_communities(monkeypatch)[0]
        _name_community_keys(monkeypatch, info, anthropic="set", openrouter="unnamed")

        route = _route_request(info, info.id, None, _origin(info), None, log=False)

        assert route.choice.provider == "bedrock"
        assert _config(info.id)["default_model"] == normalize_model(
            info.community_config.default_model
        )
        assert info.id not in log_unserved_bedrock_defaults(get_settings())

    @pytest.mark.parametrize(
        ("anthropic", "openrouter", "outcome", "needs"),
        [
            ("set", "unnamed", "claude_fallback", "It needs AWS_BEARER_TOKEN_BEDROCK."),
            ("unnamed", "set", "openrouter", "anthropic_api_key_env_var"),
        ],
    )
    def test_the_startup_log_names_the_key_the_community_funds_itself_with(
        self, monkeypatch, caplog, anthropic, openrouter, outcome, needs
    ):
        set_platform_keys(monkeypatch, anthropic=None, openrouter=None, bedrock=None)
        info = _bedrock_communities(monkeypatch)[0]
        _name_community_keys(monkeypatch, info, anthropic=anthropic, openrouter=openrouter)
        caplog.set_level(logging.WARNING)

        log_unserved_bedrock_defaults(get_settings())

        (record,) = [
            r
            for r in caplog.records
            if hasattr(r, "outcome") and getattr(r, "community_id", None) == info.id
        ]
        message = record.getMessage()
        assert record.outcome == outcome
        assert f"OSA_TEST_{info.id.upper()}_" in message, "it names the variable that funds it"
        assert needs in message
        assert "platform-funded" not in message.split(":", 1)[1], message

    def test_a_named_anthropic_key_that_is_unset_does_not_fall_on_to_openrouters(self, monkeypatch):
        """Naming an Anthropic variable settles it (see ``_resolve_provider``): a community
        whose variable is unset goes to the platform key, not to its OpenRouter variable."""
        set_platform_keys(monkeypatch, anthropic="platform-key", openrouter=None, bedrock=None)
        info = _bedrock_communities(monkeypatch)[0]
        _name_community_keys(monkeypatch, info, anthropic="unset", openrouter="set")

        route = _route_request(info, info.id, None, _origin(info), None, log=False)

        assert route.choice.provider == "anthropic" and route.choice.key_source == "platform"
        assert _config(info.id)["default_model"] == _claude_fallback(get_settings())


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
        ("community_id", "model_class"),
        [("hed", "haiku"), ("nwb", "haiku"), ("nemar", "haiku")],
    )
    def test_the_shipped_defaults_resolve_to_their_classes_slugs(self, community_id, model_class):
        """Each community names a class, and runs under that class's OpenRouter slug."""
        info = registry.get(community_id)
        assert info.community_config.default_model == model_class
        awm = create_community_assistant(community_id, origin=_origin(info), preload_docs=False)
        assert awm.model == OPENROUTER_MODEL_IDS[MODEL_CLASSES[model_class]]

    def test_the_default_model_notes_reach_the_prompt_on_openrouter(self, monkeypatch):
        """The Bedrock default's anti-search-loop note follows it onto OpenRouter."""
        info = _bedrock_communities(monkeypatch)[0]
        default = normalize_model(info.community_config.default_model)
        assert BEDROCK_MODELS[default].prompt_addendum, "the default has no note to follow"
        awm = create_community_assistant(info.id, origin=_origin(info), preload_docs=False)
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
        expected_ids = [info.id for info in _bedrock_communities(monkeypatch)]

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

        expected_ids = [info.id for info in _bedrock_communities(monkeypatch)]

        logged = log_unserved_bedrock_defaults(get_settings())

        assert logged == expected_ids
        records = self._records(caplog)
        # Not vacuous: one record for each community, so the loop below judges every one.
        assert [r.community_id for r in records] == expected_ids
        for record in records:
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

        expected_ids = [info.id for info in _bedrock_communities(monkeypatch)]

        log_unserved_bedrock_defaults(get_settings())

        records = self._records(caplog)
        assert [r.community_id for r in records] == expected_ids
        for record in records:
            info = registry.get(record.community_id)
            default = normalize_model(info.community_config.default_model)
            assert (record.levelno, record.outcome) == (logging.WARNING, "openrouter")
            assert OPENROUTER_MODEL_IDS[default] in record.getMessage()
            assert "AWS_BEARER_TOKEN_BEDROCK" in record.getMessage()
            assert "ANTHROPIC_API_KEY" in record.getMessage()

    async def test_the_app_runs_the_check_when_it_starts(self, monkeypatch, caplog, tmp_path):
        set_platform_keys(monkeypatch, anthropic="a", openrouter=None, bedrock=None)
        expected_ids = [info.id for info in _bedrock_communities(monkeypatch)]
        monkeypatch.setattr(get_settings(), "sync_enabled", False)
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        caplog.set_level(logging.WARNING)

        async with lifespan(create_app()):
            pass

        ids = [r.community_id for r in self._records(caplog)]
        assert ids == expected_ids

    async def test_a_check_that_fails_does_not_stop_the_app_starting(
        self, monkeypatch, caplog, tmp_path
    ):
        """The check is a diagnostic. Nothing real makes it raise today, so the failure is
        injected where the app calls it: the rest of startup (the metrics database, the
        scheduler) must still run, and the failure must be logged with its traceback."""
        from src.api import main as app_main
        from src.metrics.db import metrics_connection

        def broken(_settings):
            raise RuntimeError("the registry changed under the check")

        set_platform_keys(monkeypatch, anthropic="a", openrouter=None, bedrock=None)
        monkeypatch.setattr(get_settings(), "sync_enabled", False)
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(app_main, "log_unserved_bedrock_defaults", broken)
        caplog.set_level(logging.WARNING)

        async with lifespan(create_app()):
            with metrics_connection() as conn:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }

        assert "request_log" in tables, "startup went on to the metrics database"
        (record,) = [r for r in caplog.records if "Bedrock" in r.getMessage()]
        assert record.levelno == logging.ERROR
        assert record.exc_info and record.exc_info[0] is RuntimeError
        assert "startup" in record.getMessage()
