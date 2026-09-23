"""Tests for provider/API key resolution and model selection logic.

Uses real community configurations loaded via discover_assistants().
No mocks -- environment variables are set via monkeypatch (real Settings reads
them), and where a test needs to force a *platform* key on or off regardless
of a local .env file (pydantic-settings reads .env directly, so
monkeypatch.delenv on os.environ alone cannot clear a value that .env also
sets), it overrides the attribute directly on the cached Settings instance.
"""

import pytest
from fastapi import HTTPException

from src.api.config import get_settings
from src.api.routers.community import (
    ProviderChoice,
    _is_authorized_origin,
    _resolve_provider,
    _select_model,
)
from src.api.security import ByokCredential, resolve_byok
from src.assistants import discover_assistants, registry
from src.assistants.registry import AssistantInfo
from src.core.config.community import CommunityConfig
from src.core.services.anthropic_llm import OFFERED_MODELS, normalize_model
from src.core.services.litellm_llm import DEFAULT_MODEL as OPENROUTER_DEFAULT_MODEL
from src.core.services.litellm_llm import DEFAULT_PROVIDER as OPENROUTER_DEFAULT_PROVIDER
from src.core.services.litellm_llm import OPENROUTER_MODEL_IDS


@pytest.fixture(autouse=True, scope="module")
def _load_communities():
    """Load real community configurations for all tests in this module."""
    discover_assistants()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Clear the lru_cache on get_settings so each test gets fresh Settings."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _get_config(community_id):
    """Get a community's config from the registry."""
    info = registry.get(community_id)
    assert info is not None, f"Community '{community_id}' not found in registry"
    return info


def _get_exact_origin(community_id):
    """Get the first non-wildcard CORS origin for a community."""
    info = _get_config(community_id)
    for origin in info.community_config.cors_origins:
        if "*" not in origin:
            return origin
    pytest.fail(f"No exact CORS origin found for '{community_id}'")


def _get_wildcard_origin(community_id):
    """Get a wildcard CORS pattern for a community, returns (pattern, constructed_origin)."""
    info = _get_config(community_id)
    for origin in info.community_config.cors_origins:
        if "*" in origin:
            # Replace * with a test subdomain
            return origin, origin.replace("*", "test-subdomain")
    pytest.skip(f"No wildcard CORS origin for '{community_id}'")


def _community_without_configured_keys(monkeypatch):
    """Configure a real registered community with neither key env var set.

    Previously scanned the registry for a community that happened to
    qualify, and skipped (taking six tests with it) the moment every
    shipped community had a key env var configured -- an even easier way
    to lose coverage than the single-community helper beside it, since
    this one needs *all* communities configured to break, not just one.
    Hardened the same way: monkeypatch a real, registered CommunityConfig
    directly instead of searching for one that qualifies.
    """
    info = _get_config("hed")
    monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", None)
    monkeypatch.setattr(info.community_config, "openrouter_api_key_env_var", None)
    return info


def _community_with_openrouter_env_var(monkeypatch):
    """Configure a real community's config with an OpenRouter-only key env var.

    No shipped community sets openrouter_api_key_env_var any more (issue
    #363: the four that used to are now platform-funded by default), but
    the field itself is still supported for a community that funds its own
    OpenRouter usage, so that code path still needs coverage. Rather than
    searching the registry for a shipped config that no longer exists,
    monkeypatch the field directly onto a real, registered CommunityConfig
    -- that exercises the real object and the real resolution logic, and
    is test configuration, not a mock.

    Also clears anthropic_api_key_env_var on the same config, since
    _resolve_provider checks it first: without clearing it, the OpenRouter
    branch under test would never actually be reached.

    Returns:
        Tuple of (AssistantInfo, env_var_name).
    """
    info = _get_config("hed")
    env_var = "OPENROUTER_API_KEY_TEST_HED"
    monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", None)
    monkeypatch.setattr(info.community_config, "openrouter_api_key_env_var", env_var)
    return info, env_var


def _force_platform_keys(monkeypatch, *, anthropic=None, openrouter=None):
    """Force the cached Settings instance's platform keys to specific values.

    Needed instead of monkeypatch.delenv/setenv for the "no platform key"
    case: pydantic-settings reads a local .env file directly, so deleting a
    var from os.environ does not stop it from being read from .env.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", anthropic)
    monkeypatch.setattr(settings, "openrouter_api_key", openrouter)


class TestIsAuthorizedOrigin:
    """Tests for _is_authorized_origin helper function (unchanged by Phase 2)."""

    def test_platform_default_origin_always_allowed(self):
        """Platform default origins should be allowed for all communities."""
        for community_id in ["hed", "bids", "eeglab"]:
            assert _is_authorized_origin("https://demo.osc.earth", community_id) is True
        # Legacy pages.dev
        assert _is_authorized_origin("https://osa-demo.pages.dev", "hed") is True

    def test_platform_wildcard_origin_always_allowed(self):
        """Platform wildcard origins should be allowed for all communities."""
        assert _is_authorized_origin("https://develop-demo.osc.earth", "hed") is True
        assert _is_authorized_origin("https://preview-123-demo.osc.earth", "bids") is True
        # Legacy pages.dev subdomains
        assert _is_authorized_origin("https://feature-branch.osa-demo.pages.dev", "eeglab") is True

    def test_exact_origin_match(self):
        """Should return True for exact origin match using real community CORS origins."""
        hed_info = _get_config("hed")
        for origin in hed_info.community_config.cors_origins:
            if "*" not in origin:
                assert _is_authorized_origin(origin, "hed") is True, (
                    f"Expected {origin} to be authorized for HED"
                )

    def test_wildcard_origin_match(self):
        """Should return True for wildcard subdomain match using real config."""
        _pattern, test_origin = _get_wildcard_origin("mne")
        assert _is_authorized_origin(test_origin, "mne") is True

    def test_wildcard_does_not_match_multiple_levels(self):
        """Wildcard should match single subdomain, not multiple levels."""
        pattern, _test_origin = _get_wildcard_origin("mne")
        # Insert extra subdomain level into the constructed origin
        multi_level = pattern.replace("*", "foo.bar")
        assert _is_authorized_origin(multi_level, "mne") is False

    def test_no_origin_returns_false(self):
        """Should return False when origin is None (CLI, mobile apps)."""
        assert _is_authorized_origin(None, "hed") is False

    def test_unauthorized_origin_returns_false(self):
        """Should return False for origin not in CORS list."""
        assert _is_authorized_origin("https://evil.com", "hed") is False
        assert _is_authorized_origin("https://example.org", "hed") is False

    def test_case_sensitive_origin_matching(self):
        """Origin matching should be case-sensitive."""
        origin = _get_exact_origin("hed")
        assert _is_authorized_origin(origin, "hed") is True
        assert _is_authorized_origin(origin.replace("https://", "HTTPS://"), "hed") is False

    def test_community_cors_origins_from_eeglab(self):
        """Verify EEGLAB CORS origins work correctly."""
        eeglab_info = _get_config("eeglab")
        for origin in eeglab_info.community_config.cors_origins:
            if "*" not in origin:
                assert _is_authorized_origin(origin, "eeglab") is True
        assert _is_authorized_origin("https://example.com", "eeglab") is False

    def test_nemar_staging_site_is_authorized_for_nemar_only(self):
        """nemar.org's staging site tests the widget against the dev worker, so it must
        reach the platform key for NEMAR. Any other nemar.org subdomain, and any other
        community, still needs a key of its own."""
        assert _is_authorized_origin("https://test.nemar.org", "nemar") is True
        assert _is_authorized_origin("https://other.nemar.org", "nemar") is False
        assert _is_authorized_origin("https://test.nemar.org", "hed") is False

    def test_unknown_community_returns_false(self):
        """Should return False for unknown community ID."""
        origin = _get_exact_origin("hed")
        assert _is_authorized_origin(origin, "nonexistent-community-xyz") is False

    def test_domain_case_sensitivity(self):
        """Domain matching is currently case-sensitive.

        Note: Per RFC 3986, scheme and host should be case-insensitive,
        but current implementation uses exact string matching.
        This test documents current behavior.
        """
        origin = _get_exact_origin("hed")
        assert _is_authorized_origin(origin, "hed") is True
        assert _is_authorized_origin(origin.upper(), "hed") is False

    def test_cross_community_origins_not_shared(self):
        """Community origins should not work for other communities."""
        hed_origin = _get_exact_origin("hed")
        bids_origin = _get_exact_origin("bids")
        assert _is_authorized_origin(hed_origin, "bids") is False
        assert _is_authorized_origin(bids_origin, "hed") is False


class TestResolveByok:
    """Tests for src.api.security.resolve_byok."""

    def test_anthropic_only(self):
        cred = resolve_byok("anthropic-key", None)
        assert cred == ByokCredential(key="anthropic-key", provider="anthropic")

    def test_openrouter_only(self):
        cred = resolve_byok(None, "openrouter-key")
        assert cred == ByokCredential(key="openrouter-key", provider="openrouter")

    def test_both_prefers_anthropic(self):
        cred = resolve_byok("anthropic-key", "openrouter-key")
        assert cred == ByokCredential(key="anthropic-key", provider="anthropic")

    def test_neither_returns_none(self):
        assert resolve_byok(None, None) is None


class TestByokCredentialInvariant:
    """ByokCredential.__post_init__ rejects an empty key on the type itself,
    not just at resolve_byok's one call site (see security.py)."""

    def test_empty_key_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            ByokCredential(key="", provider="anthropic")

    def test_nonempty_key_accepted(self):
        cred = ByokCredential(key="a-real-key", provider="anthropic")
        assert cred.key == "a-real-key"


class TestResolveProvider:
    """Tests for _resolve_provider authorization + provider selection logic."""

    def test_anthropic_byok_always_allowed(self, monkeypatch):
        """Anthropic BYOK should always be allowed regardless of origin."""
        _force_platform_keys(monkeypatch, anthropic="platform-key")
        origin = _get_exact_origin("hed")
        cred = ByokCredential(key="user-anthropic-key", provider="anthropic")

        for test_origin in (origin, "https://evil.com", None):
            choice = _resolve_provider("hed", cred, test_origin)
            assert choice == ProviderChoice(
                provider="anthropic", api_key="user-anthropic-key", key_source="byok"
            )

    def test_openrouter_byok_always_allowed(self, monkeypatch):
        """OpenRouter BYOK should always be allowed regardless of origin."""
        _force_platform_keys(monkeypatch, anthropic="platform-key")
        cred = ByokCredential(key="user-or-key", provider="openrouter")

        for test_origin in (_get_exact_origin("hed"), "https://evil.com", None):
            choice = _resolve_provider("hed", cred, test_origin)
            assert choice == ProviderChoice(
                provider="openrouter", api_key="user-or-key", key_source="byok"
            )

    def test_authorized_origin_uses_platform_anthropic_key_by_default(self, monkeypatch):
        """Authorized origin with no community key uses the platform Anthropic key."""
        _force_platform_keys(monkeypatch, anthropic="platform-anthropic-key")
        info = _community_without_configured_keys(monkeypatch)
        origin = _get_exact_origin(info.id)

        choice = _resolve_provider(info.id, None, origin)
        assert choice == ProviderChoice(provider="anthropic", api_key=None, key_source="platform")

    def test_authorized_origin_falls_back_to_openrouter_when_no_anthropic_platform_key(
        self, monkeypatch
    ):
        """No Anthropic platform key configured falls back to the OpenRouter platform key."""
        _force_platform_keys(monkeypatch, anthropic=None, openrouter="platform-or-key")
        info = _community_without_configured_keys(monkeypatch)
        origin = _get_exact_origin(info.id)

        choice = _resolve_provider(info.id, None, origin)
        assert choice == ProviderChoice(
            provider="openrouter", api_key="platform-or-key", key_source="platform"
        )

    def test_authorized_origin_uses_community_anthropic_key(self, monkeypatch):
        """A configured anthropic_api_key_env_var wins over both openrouter and platform.

        Sets BOTH env var fields (with the OpenRouter one pointing at a
        populated, distinct env var) rather than only the Anthropic one:
        hed no longer ships an openrouter_api_key_env_var, so with only the
        Anthropic field set, an implementation that checked OpenRouter
        first would find it unset, fall through to Anthropic anyway, and
        this test would still pass without actually proving precedence.
        """
        _force_platform_keys(monkeypatch, anthropic="platform-anthropic-key")
        hed_info = _get_config("hed")
        monkeypatch.setattr(
            hed_info.community_config, "anthropic_api_key_env_var", "ANTHROPIC_API_KEY_TEST_HED"
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY_TEST_HED", "community-anthropic-key")
        monkeypatch.setattr(
            hed_info.community_config, "openrouter_api_key_env_var", "OPENROUTER_API_KEY_TEST_HED"
        )
        monkeypatch.setenv("OPENROUTER_API_KEY_TEST_HED", "community-openrouter-key")

        origin = _get_exact_origin("hed")
        choice = _resolve_provider("hed", None, origin)
        assert choice == ProviderChoice(
            provider="anthropic", api_key="community-anthropic-key", key_source="community"
        )

    def test_authorized_origin_uses_community_openrouter_key(self, monkeypatch):
        """openrouter_api_key_env_var still funds a community when no anthropic one is set."""
        _force_platform_keys(monkeypatch, anthropic="platform-anthropic-key")
        info, env_var = _community_with_openrouter_env_var(monkeypatch)
        monkeypatch.setenv(env_var, "community-openrouter-key")

        origin = _get_exact_origin(info.id)
        choice = _resolve_provider(info.id, None, origin)
        assert choice == ProviderChoice(
            provider="openrouter", api_key="community-openrouter-key", key_source="community"
        )

    def test_community_anthropic_env_var_missing_falls_back_to_platform(self, monkeypatch):
        """A configured but unset anthropic env var falls back to the platform key."""
        _force_platform_keys(monkeypatch, anthropic="platform-anthropic-key")
        hed_info = _get_config("hed")
        monkeypatch.setattr(
            hed_info.community_config,
            "anthropic_api_key_env_var",
            "ANTHROPIC_API_KEY_TEST_HED_MISSING",
        )
        monkeypatch.delenv("ANTHROPIC_API_KEY_TEST_HED_MISSING", raising=False)

        origin = _get_exact_origin("hed")
        choice = _resolve_provider("hed", None, origin)
        assert choice == ProviderChoice(provider="anthropic", api_key=None, key_source="platform")

    def test_community_openrouter_env_var_missing_falls_back_to_platform(self, monkeypatch):
        """A configured but unset openrouter env var falls back to the platform key."""
        _force_platform_keys(monkeypatch, anthropic="platform-anthropic-key")
        info, env_var = _community_with_openrouter_env_var(monkeypatch)
        monkeypatch.delenv(env_var, raising=False)

        origin = _get_exact_origin(info.id)
        choice = _resolve_provider(info.id, None, origin)
        assert choice == ProviderChoice(provider="anthropic", api_key=None, key_source="platform")

    def test_unauthorized_origin_requires_byok(self, monkeypatch):
        """Unauthorized origin without BYOK should raise 403 naming both BYOK headers."""
        _force_platform_keys(monkeypatch, anthropic="platform-key")

        with pytest.raises(HTTPException) as exc_info:
            _resolve_provider("hed", None, "https://evil.com")

        assert exc_info.value.status_code == 403
        assert "X-Anthropic-API-Key" in exc_info.value.detail
        assert "X-OpenRouter-Key" in exc_info.value.detail

    def test_cli_without_byok_requires_key(self, monkeypatch):
        """CLI (no origin) without BYOK should raise 403."""
        _force_platform_keys(monkeypatch, anthropic="platform-key")

        with pytest.raises(HTTPException) as exc_info:
            _resolve_provider("hed", None, None)

        assert exc_info.value.status_code == 403
        assert "API key required" in exc_info.value.detail

    def test_no_platform_key_configured_raises_500(self, monkeypatch):
        """No platform key configured (either provider) should raise 500."""
        _force_platform_keys(monkeypatch, anthropic=None, openrouter=None)
        info = _community_without_configured_keys(monkeypatch)
        origin = _get_exact_origin(info.id)

        with pytest.raises(HTTPException) as exc_info:
            _resolve_provider(info.id, None, origin)

        assert exc_info.value.status_code == 500
        assert "No API key configured" in exc_info.value.detail


class TestProviderChoiceInvariant:
    """ProviderChoice.__post_init__ enforces the invariant every real
    construction site (_platform_choice, _resolve_provider) already
    follows: api_key=None only for Anthropic server mode, and every other
    combination carries a non-empty key."""

    def test_none_key_valid_for_anthropic_platform(self):
        choice = ProviderChoice(provider="anthropic", api_key=None, key_source="platform")
        assert choice.api_key is None

    def test_none_key_invalid_for_openrouter(self):
        with pytest.raises(ValueError, match="api_key=None is only valid"):
            ProviderChoice(provider="openrouter", api_key=None, key_source="platform")

    def test_none_key_invalid_for_byok(self):
        with pytest.raises(ValueError, match="api_key=None is only valid"):
            ProviderChoice(provider="anthropic", api_key=None, key_source="byok")

    def test_none_key_invalid_for_community(self):
        with pytest.raises(ValueError, match="api_key=None is only valid"):
            ProviderChoice(provider="anthropic", api_key=None, key_source="community")

    def test_empty_string_key_rejected(self):
        with pytest.raises(ValueError, match="must not be an empty string"):
            ProviderChoice(provider="anthropic", api_key="", key_source="byok")

    def test_nonempty_key_accepted_for_every_source(self):
        for key_source in ("byok", "community", "platform"):
            choice = ProviderChoice(
                provider="openrouter", api_key="a-real-key", key_source=key_source
            )
            assert choice.api_key == "a-real-key"


class TestSelectModelAnthropic:
    """Tests for _select_model on the Anthropic path."""

    def test_any_offered_model_allowed(self):
        """Any offered model may be requested without BYOK on the Anthropic path."""
        community_info = _get_config("hed")
        for offered_model in OFFERED_MODELS:
            model, provider = _select_model(
                community_info, offered_model, provider="anthropic", has_byok=False
            )
            assert model == offered_model
            assert provider is None

    def test_uses_community_default_when_no_request(self):
        """Falls back to the community's normalized default_model."""
        community_info = _get_config("hed")
        expected = normalize_model(community_info.community_config.default_model)

        model, provider = _select_model(community_info, None, provider="anthropic", has_byok=False)

        assert model == expected
        assert provider is None

    def test_uses_platform_default_when_no_community_model(self, monkeypatch):
        """Falls back to the platform default when community has no default_model."""
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "claude-sonnet-5")
        community_info = AssistantInfo(
            id="test-no-model",
            name="Test Community",
            description="Community without a default model",
            community_config=None,
        )

        model, provider = _select_model(community_info, None, provider="anthropic", has_byok=False)

        assert model == "claude-sonnet-5"
        assert provider is None

    def test_unoffered_model_rejected_with_400_naming_offered_models(self):
        """An unoffered model is a 400 naming the offered models, even without BYOK."""
        community_info = _get_config("hed")

        with pytest.raises(HTTPException) as exc_info:
            _select_model(community_info, "gpt-4o", provider="anthropic", has_byok=False)

        assert exc_info.value.status_code == 400
        for offered_model in OFFERED_MODELS:
            assert offered_model in exc_info.value.detail
        assert "OpenRouter" in exc_info.value.detail

    def test_unoffered_model_rejected_even_with_byok_flag(self):
        """has_byok is irrelevant on the Anthropic path: only the offered list matters."""
        community_info = _get_config("hed")

        with pytest.raises(HTTPException) as exc_info:
            _select_model(community_info, "gpt-4o", provider="anthropic", has_byok=True)

        assert exc_info.value.status_code == 400

    def test_legacy_openrouter_style_alias_resolves(self):
        """A legacy OpenRouter-style id in a community config still normalizes."""
        info = AssistantInfo(
            id="legacy-test",
            name="Legacy Test",
            description="Community with an unmigrated legacy default_model",
            community_config=CommunityConfig(
                id="legacy-test",
                name="Legacy Test",
                description="x",
                default_model="anthropic/claude-haiku-4.5",
            ),
        )

        model, provider = _select_model(info, None, provider="anthropic", has_byok=False)

        assert model == "claude-haiku-4-5"
        assert provider is None

    def test_default_model_provider_ignored(self):
        """default_model_provider (OpenRouter-only routing) is ignored on this path,
        and CommunityConfig itself warns about that at load time (a bare
        default_model discards the provider hint on every path -- see
        validate_default_model_provider_has_effect)."""
        with pytest.warns(UserWarning, match="default_model_provider.*is ignored"):
            community_config = CommunityConfig(
                id="legacy-test-2",
                name="Legacy Test 2",
                description="x",
                default_model="claude-sonnet-5",
                default_model_provider="Cerebras",
            )
        info = AssistantInfo(
            id="legacy-test-2",
            name="Legacy Test 2",
            description="x",
            community_config=community_config,
        )

        model, provider = _select_model(info, None, provider="anthropic", has_byok=False)

        assert model == "claude-sonnet-5"
        assert provider is None


class TestSelectModelOpenRouter:
    """Tests for _select_model on the OpenRouter path (unchanged by Phase 2)."""

    def test_bare_anthropic_default_maps_to_the_same_model_on_openrouter(self, monkeypatch):
        """A bare first-party default resolves to the same model's OpenRouter slug.

        Phase 2 made every community and platform default_model a bare id
        like "claude-haiku-4-5", which is not a valid OpenRouter slug. The
        request must still run the model the community chose: OpenRouter
        serves the same Claude models under creator/model slugs, so it maps
        across. Falling back to OpenRouter's own default here would silently
        change model family based on which key funded the request.
        """
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "claude-haiku-4-5")
        monkeypatch.setattr(settings, "default_model_provider", None)

        community_info = AssistantInfo(
            id="test-bare-default",
            name="Test",
            description="x",
            community_config=None,
        )

        model, provider = _select_model(community_info, None, provider="openrouter", has_byok=True)

        assert model == OPENROUTER_MODEL_IDS["claude-haiku-4-5"]
        # Routing is left to OpenRouter, which auto-selects the Anthropic
        # provider for anthropic/* models.
        assert provider is None

    def test_unmappable_bare_default_falls_back_to_openrouter_default(self, monkeypatch):
        """A misconfigured bare default still serves, via the factory default.

        An id that is neither an offered Anthropic model nor an OpenRouter
        slug cannot be routed. The request stays serviceable rather than
        failing, and the code logs an error so the misconfiguration is
        visible instead of silent.
        """
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "not-a-real-model")
        monkeypatch.setattr(settings, "default_model_provider", None)

        community_info = AssistantInfo(
            id="test-unmappable-default",
            name="Test",
            description="x",
            community_config=None,
        )

        model, provider = _select_model(community_info, None, provider="openrouter", has_byok=True)

        assert model == OPENROUTER_DEFAULT_MODEL
        assert provider == OPENROUTER_DEFAULT_PROVIDER

    def test_offered_model_requested_by_first_party_id_maps_across(self, monkeypatch):
        """Naming an offered model by first-party id works on an OpenRouter key."""
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "claude-haiku-4-5")
        monkeypatch.setattr(settings, "default_model_provider", None)

        community_info = AssistantInfo(
            id="test-requested-first-party",
            name="Test",
            description="x",
            community_config=None,
        )

        model, _provider = _select_model(
            community_info, "claude-sonnet-5", provider="openrouter", has_byok=True
        )

        assert model == OPENROUTER_MODEL_IDS["claude-sonnet-5"]

    def test_uses_community_default_model(self, monkeypatch):
        """Should use community default_model when configured."""
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "openai/gpt-oss-120b")
        monkeypatch.setattr(settings, "default_model_provider", "Cerebras")

        info = AssistantInfo(
            id="or-test",
            name="OR Test",
            description="x",
            community_config=CommunityConfig(
                id="or-test",
                name="OR Test",
                description="x",
                default_model="anthropic/claude-haiku-4.5",
                default_model_provider="Anthropic",
            ),
        )

        model, provider = _select_model(info, None, provider="openrouter", has_byok=False)

        assert model == "anthropic/claude-haiku-4.5"
        assert provider == "Anthropic"

    def test_uses_platform_default_when_no_community_model(self, monkeypatch):
        """Should use platform default when community has no default_model."""
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "openai/gpt-oss-120b")
        monkeypatch.setattr(settings, "default_model_provider", "Cerebras")

        community_info = AssistantInfo(
            id="test-no-model",
            name="Test Community",
            description="Community without a default model",
            community_config=None,
        )
        model, provider = _select_model(community_info, None, provider="openrouter", has_byok=False)

        assert model == "openai/gpt-oss-120b"
        assert provider == "Cerebras"

    def test_custom_model_with_byok_allowed(self, monkeypatch):
        """Custom model should be allowed when user has BYOK."""
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "openai/gpt-oss-120b")

        community_info = _get_config("hed")
        model, provider = _select_model(
            community_info, "anthropic/claude-opus-4", provider="openrouter", has_byok=True
        )

        assert model == "anthropic/claude-opus-4"
        assert provider is None  # Custom models use default routing

    def test_custom_model_without_byok_rejected(self, monkeypatch):
        """Custom model without BYOK should raise 403."""
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "openai/gpt-oss-120b")

        community_info = _get_config("hed")

        with pytest.raises(HTTPException) as exc_info:
            _select_model(
                community_info, "anthropic/claude-opus-4", provider="openrouter", has_byok=False
            )

        assert exc_info.value.status_code == 403
        assert "Custom model" in exc_info.value.detail
        assert "anthropic/claude-opus-4" in exc_info.value.detail
        assert "requires your own API key" in exc_info.value.detail

    def test_legacy_alias_default_maps_to_claude_slug_not_emergency_default(self, monkeypatch):
        """A legacy alias community default must map to its Claude slug.

        Regression for item 2: before canonicalizing through normalize_model,
        to_openrouter_model("claude-sonnet-4.5") returned None (it only knows
        the two canonical ids), so this fell through to the OpenRouter
        emergency default -- a model-family substitution, not the community's
        chosen model.
        """
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "claude-sonnet-4.5")
        monkeypatch.setattr(settings, "default_model_provider", None)

        community_info = AssistantInfo(
            id="test-legacy-alias-default",
            name="Test",
            description="x",
            community_config=None,
        )

        model, provider = _select_model(community_info, None, provider="openrouter", has_byok=True)

        assert model == OPENROUTER_MODEL_IDS["claude-sonnet-5"]
        assert model != OPENROUTER_DEFAULT_MODEL
        assert provider is None

    def test_legacy_alias_requested_model_maps_to_claude_slug_not_emergency_default(
        self, monkeypatch
    ):
        """A legacy alias requested directly (with BYOK) also maps correctly."""
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "openai/gpt-oss-120b")
        monkeypatch.setattr(settings, "default_model_provider", "Cerebras")

        community_info = AssistantInfo(
            id="test-legacy-alias-requested",
            name="Test",
            description="x",
            community_config=None,
        )

        model, provider = _select_model(
            community_info, "claude-haiku-4.5", provider="openrouter", has_byok=True
        )

        assert model == OPENROUTER_MODEL_IDS["claude-haiku-4-5"]
        assert provider is None

    def test_requesting_default_model_explicitly_allowed(self, monkeypatch):
        """Explicitly requesting the community default model should not require BYOK."""
        settings = get_settings()
        monkeypatch.setattr(settings, "default_model", "openai/gpt-oss-120b")

        info = AssistantInfo(
            id="or-test-2",
            name="OR Test 2",
            description="x",
            community_config=CommunityConfig(
                id="or-test-2",
                name="OR Test 2",
                description="x",
                default_model="anthropic/claude-haiku-4.5",
            ),
        )

        model, provider = _select_model(
            info, "anthropic/claude-haiku-4.5", provider="openrouter", has_byok=False
        )

        assert model == "anthropic/claude-haiku-4.5"
        assert provider is None


class TestProviderAndModelSelectionCombined:
    """Combined _resolve_provider + _select_model scenarios (unit-level, no HTTP).

    Renamed from "TestIntegration": these call the two functions directly in
    the same test, not through the HTTP layer, so the name should not imply
    HTTP/integration coverage it does not have. See tests/test_api/
    test_community_router.py and the TestClient-driven BYOK tests in
    tests/test_api/test_security.py for actual HTTP-level coverage.
    """

    def test_widget_user_gets_anthropic_platform_default(self, monkeypatch):
        """Widget user on an authorized site defaults to the Anthropic platform key."""
        _force_platform_keys(monkeypatch, anthropic="platform-anthropic-key")
        info = _community_without_configured_keys(monkeypatch)
        origin = _get_exact_origin(info.id)

        choice = _resolve_provider(info.id, None, origin)
        assert choice.provider == "anthropic"
        assert choice.key_source == "platform"

        model, provider = _select_model(info, None, provider=choice.provider, has_byok=False)
        assert model == normalize_model(info.community_config.default_model)
        assert provider is None

    def test_widget_user_can_request_any_offered_model(self, monkeypatch):
        """Widget user on the Anthropic path may request either offered model."""
        _force_platform_keys(monkeypatch, anthropic="platform-anthropic-key")
        info = _community_without_configured_keys(monkeypatch)
        origin = _get_exact_origin(info.id)

        choice = _resolve_provider(info.id, None, origin)
        for offered_model in OFFERED_MODELS:
            model, _provider = _select_model(
                info, offered_model, provider=choice.provider, has_byok=False
            )
            assert model == offered_model

    def test_widget_user_unoffered_model_rejected(self, monkeypatch):
        """Widget user requesting an unoffered model on the Anthropic path gets 400."""
        _force_platform_keys(monkeypatch, anthropic="platform-anthropic-key")
        info = _community_without_configured_keys(monkeypatch)
        origin = _get_exact_origin(info.id)

        choice = _resolve_provider(info.id, None, origin)
        with pytest.raises(HTTPException) as exc_info:
            _select_model(info, "gpt-4o", provider=choice.provider, has_byok=False)
        assert exc_info.value.status_code == 400

    def test_cli_user_with_openrouter_byok_and_custom_model(self):
        """CLI user with OpenRouter BYOK can use an arbitrary custom model."""
        cred = ByokCredential(key="user-key", provider="openrouter")
        choice = _resolve_provider("hed", cred, None)
        assert choice.key_source == "byok"
        assert choice.provider == "openrouter"

        community_info = _get_config("hed")
        model, _provider = _select_model(
            community_info, "anthropic/claude-opus-4", provider=choice.provider, has_byok=True
        )
        assert model == "anthropic/claude-opus-4"

    def test_cli_user_without_byok_rejected(self, monkeypatch):
        """CLI user without BYOK should be rejected."""
        _force_platform_keys(monkeypatch, anthropic="platform-key")

        with pytest.raises(HTTPException) as exc_info:
            _resolve_provider("hed", None, None)
        assert exc_info.value.status_code == 403
