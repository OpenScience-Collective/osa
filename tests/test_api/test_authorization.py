"""Tests for API key authorization and model selection logic.

Uses real community configurations loaded via discover_assistants().
No mocks -- environment variables are set via monkeypatch (real Settings reads them).
"""

import pytest
from fastapi import HTTPException

from src.api.config import get_settings
from src.api.routers.community import _is_authorized_origin, _select_api_key, _select_model
from src.assistants import discover_assistants, registry
from src.assistants.registry import AssistantInfo


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


class TestIsAuthorizedOrigin:
    """Tests for _is_authorized_origin helper function."""

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


class TestSelectApiKey:
    """Tests for _select_api_key authorization logic."""

    def test_byok_always_allowed(self, monkeypatch):
        """BYOK should always be allowed regardless of origin."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "platform-key")
        origin = _get_exact_origin("hed")

        # BYOK with authorized origin
        key, source = _select_api_key("hed", "user-key", origin)
        assert key == "user-key"
        assert source == "byok"

        # BYOK with unauthorized origin
        key, source = _select_api_key("hed", "user-key", "https://evil.com")
        assert key == "user-key"
        assert source == "byok"

        # BYOK with no origin (CLI)
        key, source = _select_api_key("hed", "user-key", None)
        assert key == "user-key"
        assert source == "byok"

    def test_authorized_origin_uses_platform_key(self, monkeypatch):
        """Authorized origin without community key should use platform key."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "platform-key")
        origin = _get_exact_origin("mne")
        key, source = _select_api_key("mne", None, origin)
        assert key == "platform-key"
        assert source == "platform"

    def test_authorized_origin_uses_community_key(self, monkeypatch):
        """Authorized origin should prefer community key over platform key."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "platform-key")
        hed_info = _get_config("hed")
        env_var = hed_info.community_config.openrouter_api_key_env_var
        assert env_var, "HED should have openrouter_api_key_env_var configured"
        monkeypatch.setenv(env_var, "community-key")

        origin = _get_exact_origin("hed")
        key, source = _select_api_key("hed", None, origin)
        assert key == "community-key"
        assert source == "community"

    def test_authorized_origin_falls_back_to_platform_when_community_key_missing(self, monkeypatch):
        """When community env var is configured but not set, fall back to platform key."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "platform-key")
        hed_info = _get_config("hed")
        env_var = hed_info.community_config.openrouter_api_key_env_var
        monkeypatch.delenv(env_var, raising=False)

        origin = _get_exact_origin("hed")
        key, source = _select_api_key("hed", None, origin)
        assert key == "platform-key"
        assert source == "platform"

    def test_unauthorized_origin_requires_byok(self, monkeypatch):
        """Unauthorized origin without BYOK should raise 403."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "platform-key")

        with pytest.raises(HTTPException) as exc_info:
            _select_api_key("hed", None, "https://evil.com")

        assert exc_info.value.status_code == 403
        assert "API key required" in exc_info.value.detail
        assert "openrouter.ai/keys" in exc_info.value.detail

    def test_cli_without_byok_requires_key(self, monkeypatch):
        """CLI (no origin) without BYOK should raise 403."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "platform-key")

        with pytest.raises(HTTPException) as exc_info:
            _select_api_key("hed", None, None)

        assert exc_info.value.status_code == 403
        assert "API key required" in exc_info.value.detail

    def test_no_platform_key_configured_raises_500(self, monkeypatch):
        """No platform key configured should raise 500 for authorized origins."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        hed_info = _get_config("hed")
        env_var = hed_info.community_config.openrouter_api_key_env_var
        if env_var:
            monkeypatch.delenv(env_var, raising=False)

        origin = _get_exact_origin("hed")
        with pytest.raises(HTTPException) as exc_info:
            _select_api_key("hed", None, origin)

        assert exc_info.value.status_code == 500
        assert "No API key configured" in exc_info.value.detail


class TestSelectModel:
    """Tests for _select_model logic."""

    def test_uses_community_default_model(self, monkeypatch):
        """Should use community default_model when configured."""
        monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-oss-120b")
        monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "Cerebras")

        community_info = _get_config("hed")
        expected_model = community_info.community_config.default_model
        expected_provider = community_info.community_config.default_model_provider
        assert expected_model, "HED should have a default_model configured"

        model, provider = _select_model(community_info, None, has_byok=False)

        assert model == expected_model
        assert provider == expected_provider

    def test_uses_platform_default_when_no_community_model(self, monkeypatch):
        """Should use platform default when community has no default_model."""
        monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-oss-120b")
        monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "Cerebras")

        # Real dataclass instance with no community config (not a mock)
        community_info = AssistantInfo(
            id="test-no-model",
            name="Test Community",
            description="Community without a default model",
            community_config=None,
        )
        model, provider = _select_model(community_info, None, has_byok=False)

        assert model == "openai/gpt-oss-120b"
        assert provider == "Cerebras"

    def test_custom_model_with_byok_allowed(self, monkeypatch):
        """Custom model should be allowed when user has BYOK."""
        monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-oss-120b")
        monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "Cerebras")

        community_info = _get_config("hed")
        model, provider = _select_model(community_info, "anthropic/claude-opus-4", has_byok=True)

        assert model == "anthropic/claude-opus-4"
        assert provider is None  # Custom models use default routing

    def test_custom_model_without_byok_rejected(self, monkeypatch):
        """Custom model without BYOK should raise 403."""
        monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-oss-120b")
        monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "Cerebras")

        community_info = _get_config("hed")

        with pytest.raises(HTTPException) as exc_info:
            _select_model(community_info, "anthropic/claude-opus-4", has_byok=False)

        assert exc_info.value.status_code == 403
        assert "Custom model" in exc_info.value.detail
        assert "anthropic/claude-opus-4" in exc_info.value.detail
        assert "requires your own API key" in exc_info.value.detail

    def test_requesting_default_model_explicitly_allowed(self, monkeypatch):
        """Explicitly requesting the community default model should not require BYOK."""
        monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-oss-120b")
        monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "Cerebras")

        community_info = _get_config("hed")
        default_model = community_info.community_config.default_model
        default_provider = community_info.community_config.default_model_provider

        model, provider = _select_model(community_info, default_model, has_byok=False)

        assert model == default_model
        assert provider == default_provider

    def test_requesting_platform_default_model_allowed(self, monkeypatch):
        """Explicitly requesting the platform default should not require BYOK."""
        monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-oss-120b")
        monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "Cerebras")

        # Community with no default_model so platform default is the effective default
        community_info = AssistantInfo(
            id="test-no-model",
            name="Test Community",
            description="Community without a default model",
            community_config=None,
        )
        model, provider = _select_model(community_info, "openai/gpt-oss-120b", has_byok=False)

        assert model == "openai/gpt-oss-120b"
        assert provider == "Cerebras"


class TestIntegration:
    """Integration tests for combined authorization + model selection."""

    def test_widget_user_default_model(self, monkeypatch):
        """Widget user on authorized site with default model."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "platform-key")
        monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-oss-120b")
        monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "Cerebras")
        hed_info = _get_config("hed")
        env_var = hed_info.community_config.openrouter_api_key_env_var
        if env_var:
            monkeypatch.delenv(env_var, raising=False)

        origin = _get_exact_origin("hed")
        api_key, key_source = _select_api_key("hed", None, origin)
        assert key_source in ["platform", "community"]

        model, provider = _select_model(hed_info, None, has_byok=False)
        assert model == hed_info.community_config.default_model

    def test_widget_user_custom_model_rejected(self, monkeypatch):
        """Widget user trying to use custom model should be rejected."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "platform-key")
        monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-oss-120b")
        hed_info = _get_config("hed")
        env_var = hed_info.community_config.openrouter_api_key_env_var
        if env_var:
            monkeypatch.delenv(env_var, raising=False)

        origin = _get_exact_origin("hed")
        api_key, key_source = _select_api_key("hed", None, origin)
        assert key_source in ["platform", "community"]

        with pytest.raises(HTTPException) as exc_info:
            _select_model(hed_info, "anthropic/claude-opus-4", has_byok=False)
        assert exc_info.value.status_code == 403

    def test_cli_user_with_byok_and_custom_model(self, monkeypatch):
        """CLI user with BYOK can use custom model."""
        monkeypatch.setenv("DEFAULT_MODEL", "openai/gpt-oss-120b")

        api_key, key_source = _select_api_key("hed", "user-key", None)
        assert api_key == "user-key"
        assert key_source == "byok"

        community_info = _get_config("hed")
        model, provider = _select_model(community_info, "anthropic/claude-opus-4", has_byok=True)
        assert model == "anthropic/claude-opus-4"

    def test_cli_user_without_byok_rejected(self, monkeypatch):
        """CLI user without BYOK should be rejected."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "platform-key")

        with pytest.raises(HTTPException) as exc_info:
            _select_api_key("hed", None, None)
        assert exc_info.value.status_code == 403
