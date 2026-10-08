"""What a routed request carries: its invariants, and the offered model it runs.

Real registered communities, real Settings and the real routing code, like
test_bedrock_routing.py. `create_community_assistant` builds the model and assistant
without calling a provider (constructing a client does not), so the system prompt the
request would send can be read back.
"""

import re

import pytest

from src.api.config import get_settings
from src.api.routers.community import (
    ProviderChoice,
    RequestRoute,
    _route_request,
    create_community_assistant,
)
from src.api.security import ByokCredential
from src.assistants import discover_assistants, registry
from src.core.services.anthropic_models import (
    BEDROCK_MODELS,
    HAIKU,
    OFFERED_MODELS,
    normalize_model,
)
from src.core.services.litellm_llm import OPENROUTER_MODEL_IDS
from tests.helpers.deployment import name_luna_as_the_default

NOTES_HEADING = "Working Notes For This Model"


@pytest.fixture(autouse=True, scope="module")
def _load_communities():
    discover_assistants()


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _on_a_deployment_with_every_platform_key(monkeypatch, community_id: str):
    """A real community, on a deployment with every platform key."""
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "platform-anthropic-key")
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    monkeypatch.setattr(settings, "bedrock_api_key", "bedrock-key")
    info = registry.get(community_id)
    assert info is not None
    monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", None)
    monkeypatch.setattr(info.community_config, "openrouter_api_key_env_var", None)
    # Building an assistant discovers its MCP servers' tools over the network; which
    # tools a community has is not what these tests are about.
    if info.community_config.extensions:
        monkeypatch.setattr(info.community_config.extensions, "mcp_servers", [])
    return info


@pytest.fixture
def hed(monkeypatch):
    """The real hed community on a deployment with every platform key."""
    return _on_a_deployment_with_every_platform_key(monkeypatch, "hed")


@pytest.fixture
def a_bedrock_default_community(monkeypatch):
    """A real community whose default model is one of the Bedrock models, on a deployment
    with every platform key. No shipped community names one now, so NWB is given one here."""
    info = name_luna_as_the_default(monkeypatch)
    return _on_a_deployment_with_every_platform_key(monkeypatch, info.id)


def _origin(info) -> str:
    return next(o for o in info.community_config.cors_origins if "*" not in o)


OPENROUTER_BYOK = ByokCredential(key="sk-or-fake-test-key", provider="openrouter")
ANTHROPIC_BYOK = ByokCredential(key="sk-ant-" + "x" * 40, provider="anthropic")


def _prompt_on_openrouter(requested_model: str | None, community_id: str = "hed") -> str:
    awm = create_community_assistant(
        community_id, byok=OPENROUTER_BYOK, requested_model=requested_model, preload_docs=False
    )
    assert type(awm.assistant.model).__name__ == "TaggedCitationChatLiteLLM"
    return awm.assistant.get_system_prompt()


def _notes_section(prompt: str) -> str:
    """The per-model notes of a system prompt, or an empty string when it has none."""
    match = re.search(rf"## {NOTES_HEADING}\n\n.*?(?=\n\n## |\Z)", prompt, re.DOTALL)
    return match.group(0) if match else ""


class TestRequestRouteInvariants:
    """A route whose parts contradict each other is refused at construction."""

    def test_an_openrouter_hint_belongs_to_openrouter_only(self):
        anthropic = ProviderChoice(provider="anthropic", api_key=None, key_source="platform")
        with pytest.raises(ValueError, match="routing hint"):
            RequestRoute(
                choice=anthropic,
                model=HAIKU,
                provider_hint="Cerebras",
                offered_model_id=HAIKU,
            )

    def test_a_bedrock_model_on_the_anthropic_provider_is_refused(self):
        anthropic = ProviderChoice(provider="anthropic", api_key=None, key_source="platform")
        model = next(iter(BEDROCK_MODELS))
        with pytest.raises(ValueError, match="disagree"):
            RequestRoute(choice=anthropic, model=model, provider_hint=None, offered_model_id=model)

    def test_a_claude_model_on_the_bedrock_provider_is_refused(self):
        bedrock = ProviderChoice(provider="bedrock", api_key=None, key_source="platform")
        with pytest.raises(ValueError, match="disagree"):
            RequestRoute(
                choice=bedrock,
                model=HAIKU,
                provider_hint=None,
                offered_model_id=HAIKU,
            )

    @pytest.mark.parametrize("model_id", sorted(BEDROCK_MODELS))
    def test_a_bedrock_model_on_the_bedrock_provider_is_accepted(self, model_id):
        bedrock = ProviderChoice(provider="bedrock", api_key=None, key_source="platform")
        route = RequestRoute(
            choice=bedrock, model=model_id, provider_hint=None, offered_model_id=model_id
        )
        assert route.offered_model_id == model_id

    def test_the_offered_id_must_be_the_one_the_model_stands_for(self):
        openrouter = ProviderChoice(provider="openrouter", api_key="k", key_source="byok")
        slug = OPENROUTER_MODEL_IDS["openai.gpt-6-luna"]
        with pytest.raises(ValueError, match="offered_model_id"):
            RequestRoute(
                choice=openrouter,
                model=slug,
                provider_hint=None,
                # The provider's own id, which is what the notes used to be looked up by.
                offered_model_id=slug,
            )

    def test_an_unknown_slug_has_no_offered_model(self):
        openrouter = ProviderChoice(provider="openrouter", api_key="k", key_source="byok")
        route = RequestRoute(
            choice=openrouter,
            model="some-lab/their-own-model",
            provider_hint="Cerebras",
            offered_model_id=None,
        )
        assert route.offered_model_id is None


class TestTheRoutesOfferedModel:
    @pytest.mark.parametrize("model_id", sorted(OFFERED_MODELS))
    def test_the_platform_runs_every_offered_model_under_its_own_id(self, hed, model_id):
        route = _route_request(hed, "hed", None, _origin(hed), model_id)
        assert route.model == route.offered_model_id == model_id

    @pytest.mark.parametrize("model_id", sorted(OPENROUTER_MODEL_IDS))
    def test_an_openrouter_caller_gets_the_slug_and_the_offered_id_behind_it(self, hed, model_id):
        route = _route_request(hed, "hed", OPENROUTER_BYOK, None, model_id)
        assert route.choice.provider == "openrouter"
        assert route.model == OPENROUTER_MODEL_IDS[model_id]
        assert route.offered_model_id == model_id

    def test_the_communitys_default_on_openrouter_is_found_by_its_offered_id(self, hed):
        route = _route_request(hed, "hed", OPENROUTER_BYOK, None, None)
        default = normalize_model(hed.community_config.default_model)
        assert route.model == OPENROUTER_MODEL_IDS[default]
        assert route.offered_model_id == default

    def test_a_custom_slug_is_nobodys_offered_model(self, hed):
        route = _route_request(hed, "hed", OPENROUTER_BYOK, None, "some-lab/their-own-model")
        assert route.model == "some-lab/their-own-model"
        assert route.offered_model_id is None

    def test_the_fallback_model_is_the_offered_one(self, a_bedrock_default_community, monkeypatch):
        """A Bedrock default that cannot be served runs Claude, and is that model's id."""
        community = a_bedrock_default_community
        assert normalize_model(community.community_config.default_model) in BEDROCK_MODELS, (
            "nothing to fall back from"
        )
        monkeypatch.setattr(get_settings(), "bedrock_api_key", None)
        route = _route_request(community, community.id, ANTHROPIC_BYOK, None, None)
        assert route.choice.provider == "anthropic"
        assert route.offered_model_id == route.model
        assert route.model not in BEDROCK_MODELS


class TestPerModelNotesFollowTheModelOnEveryProvider:
    """The notes are keyed by offered id, which an OpenRouter slug is not (#562 review)."""

    def test_the_default_model_gets_its_note_on_openrouter(self, a_bedrock_default_community):
        community = a_bedrock_default_community
        default = normalize_model(community.community_config.default_model)
        assert BEDROCK_MODELS[default].prompt_addendum, "the shipped default has no note"
        prompt = _prompt_on_openrouter(None, community.id)
        assert NOTES_HEADING in prompt
        assert BEDROCK_MODELS[default].prompt_addendum in prompt

    @pytest.mark.usefixtures("hed")
    def test_a_named_model_gets_its_note_on_openrouter(self):
        prompt = _prompt_on_openrouter("openai.gpt-oss-120b")
        assert NOTES_HEADING in prompt
        assert BEDROCK_MODELS["openai.gpt-oss-120b"].prompt_addendum in prompt

    @pytest.mark.parametrize("model_id", sorted(OPENROUTER_MODEL_IDS))
    def test_every_offered_model_gets_the_same_notes_on_openrouter_as_on_the_platform(
        self, hed, monkeypatch, model_id
    ):
        # A community instruction for every offered model, so a Claude model has notes too.
        monkeypatch.setattr(
            hed.community_config,
            "model_instructions",
            {offered: f"Community note for {offered}." for offered in OFFERED_MODELS},
        )
        platform = create_community_assistant(
            "hed", origin=_origin(hed), requested_model=model_id, preload_docs=False
        ).assistant.get_system_prompt()
        on_openrouter = _prompt_on_openrouter(model_id)

        expected = _notes_section(platform)
        assert f"Community note for {model_id}." in expected
        assert _notes_section(on_openrouter) == expected

    def test_a_community_instruction_for_a_claude_model_applies_on_openrouter(
        self, hed, monkeypatch
    ):
        monkeypatch.setattr(hed.community_config, "model_instructions", {HAIKU: "Be brief."})
        assert "Be brief." in _prompt_on_openrouter(HAIKU)
        # and only on that model
        assert "Be brief." not in _prompt_on_openrouter("claude-sonnet-5-5")

    def test_a_community_instruction_for_a_bedrock_model_applies_on_openrouter(
        self, hed, monkeypatch
    ):
        monkeypatch.setattr(
            hed.community_config, "model_instructions", {"openai.gpt-oss-120b": "Answer in French."}
        )
        prompt = _prompt_on_openrouter("openai.gpt-oss-120b")
        builtin = BEDROCK_MODELS["openai.gpt-oss-120b"].prompt_addendum
        assert prompt.index(builtin) < prompt.index("Answer in French.")

    def test_a_slug_osa_does_not_know_gets_no_notes(self, hed, monkeypatch):
        monkeypatch.setattr(hed.community_config, "model_instructions", {HAIKU: "Be brief."})
        assert NOTES_HEADING not in _prompt_on_openrouter("some-lab/their-own-model")

    def test_the_anthropic_path_still_finds_a_claude_instruction(self, hed, monkeypatch):
        monkeypatch.setattr(hed.community_config, "model_instructions", {HAIKU: "Be brief."})
        awm = create_community_assistant(
            "hed", byok=ANTHROPIC_BYOK, requested_model=HAIKU, preload_docs=False
        )
        assert "Be brief." in awm.assistant.get_system_prompt()
