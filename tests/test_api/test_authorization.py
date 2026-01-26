"""Tests for API key authorization and model selection logic."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routers.community import _is_authorized_origin, _select_api_key, _select_model


@pytest.fixture
def mock_registry():
    """Mock registry with test community configurations."""
    with patch("src.api.routers.community.registry") as mock_reg:
        # HED community with CORS origins
        hed_info = MagicMock()
        hed_info.id = "hed"
        hed_info.community_config = MagicMock()
        hed_info.community_config.cors_origins = [
            "https://hedtags.org",
            "https://www.hedtags.org",
            "https://*.pages.dev",
        ]
        hed_info.community_config.openrouter_api_key_env_var = "OPENROUTER_API_KEY_HED"
        hed_info.community_config.default_model = None
        hed_info.community_config.default_model_provider = None

        # BIDS community with custom model
        bids_info = MagicMock()
        bids_info.id = "bids"
        bids_info.community_config = MagicMock()
        bids_info.community_config.cors_origins = ["https://bids.neuroimaging.io"]
        bids_info.community_config.openrouter_api_key_env_var = None
        bids_info.community_config.default_model = "anthropic/claude-3.5-sonnet"
        bids_info.community_config.default_model_provider = None

        # Community without CORS origins
        no_cors_info = MagicMock()
        no_cors_info.id = "no-cors"
        no_cors_info.community_config = MagicMock()
        no_cors_info.community_config.cors_origins = []

        mock_reg.get.side_effect = lambda id: {
            "hed": hed_info,
            "bids": bids_info,
            "no-cors": no_cors_info,
        }.get(id)

        yield mock_reg


class TestIsAuthorizedOrigin:
    """Tests for _is_authorized_origin helper function."""

    def test_platform_default_origin_always_allowed(self, mock_registry):  # noqa: ARG002
        """Platform default origin (osa-demo.pages.dev) should be allowed for all communities."""
        # Exact match
        assert _is_authorized_origin("https://osa-demo.pages.dev", "hed") is True
        assert _is_authorized_origin("https://osa-demo.pages.dev", "bids") is True
        assert _is_authorized_origin("https://osa-demo.pages.dev", "no-cors") is True

    def test_platform_wildcard_origin_always_allowed(self, mock_registry):  # noqa: ARG002
        """Platform wildcard origins (*.osa-demo.pages.dev) should be allowed for all communities."""
        # Wildcard subdomains
        assert _is_authorized_origin("https://develop.osa-demo.pages.dev", "hed") is True
        assert _is_authorized_origin("https://preview-123.osa-demo.pages.dev", "bids") is True
        assert _is_authorized_origin("https://feature-branch.osa-demo.pages.dev", "no-cors") is True

    def test_exact_origin_match(self, mock_registry):  # noqa: ARG002
        """Should return True for exact origin match."""
        assert _is_authorized_origin("https://hedtags.org", "hed") is True
        assert _is_authorized_origin("https://www.hedtags.org", "hed") is True

    def test_wildcard_origin_match(self, mock_registry):  # noqa: ARG002
        """Should return True for wildcard subdomain match."""
        assert _is_authorized_origin("https://my-app.pages.dev", "hed") is True
        assert _is_authorized_origin("https://preview-123.pages.dev", "hed") is True

    def test_wildcard_does_not_match_multiple_levels(self, mock_registry):  # noqa: ARG002
        """Wildcard should match single subdomain, not multiple levels."""
        # *.pages.dev should NOT match foo.bar.pages.dev
        assert _is_authorized_origin("https://foo.bar.pages.dev", "hed") is False

    def test_no_origin_returns_false(self, mock_registry):  # noqa: ARG002
        """Should return False when origin is None (CLI, mobile apps)."""
        assert _is_authorized_origin(None, "hed") is False

    def test_unauthorized_origin_returns_false(self, mock_registry):  # noqa: ARG002
        """Should return False for origin not in CORS list."""
        assert _is_authorized_origin("https://evil.com", "hed") is False
        assert _is_authorized_origin("https://example.org", "hed") is False

    def test_case_sensitive_origin_matching(self, mock_registry):  # noqa: ARG002
        """Origin matching should be case-sensitive."""
        # HTTPS vs https
        assert _is_authorized_origin("https://hedtags.org", "hed") is True
        assert _is_authorized_origin("HTTPS://hedtags.org", "hed") is False

    def test_community_without_cors_origins(self, mock_registry):  # noqa: ARG002
        """Should return False for community with empty cors_origins."""
        assert _is_authorized_origin("https://example.com", "no-cors") is False

    def test_unknown_community_returns_false(self, mock_registry):  # noqa: ARG002
        """Should return False for unknown community ID."""
        assert _is_authorized_origin("https://hedtags.org", "unknown") is False


class TestSelectApiKey:
    """Tests for _select_api_key authorization logic."""

    @patch.dict("os.environ", {}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_byok_always_allowed(self, mock_settings, mock_registry):  # noqa: ARG002
        """BYOK should always be allowed regardless of origin."""
        mock_settings.return_value.openrouter_api_key = "platform-key"

        # BYOK with authorized origin
        key, source = _select_api_key("hed", "user-key", "https://hedtags.org")
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

    @patch.dict("os.environ", {}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_authorized_origin_uses_platform_key(self, mock_settings, mock_registry):  # noqa: ARG002
        """Authorized origin should use community or platform key."""
        mock_settings.return_value.openrouter_api_key = "platform-key"

        key, source = _select_api_key("hed", None, "https://hedtags.org")
        assert key == "platform-key"
        assert source == "platform"

    @patch.dict("os.environ", {"OPENROUTER_API_KEY_HED": "community-key"}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_authorized_origin_uses_community_key(self, mock_settings, mock_registry):  # noqa: ARG002
        """Authorized origin should prefer community key over platform key."""
        mock_settings.return_value.openrouter_api_key = "platform-key"

        key, source = _select_api_key("hed", None, "https://hedtags.org")
        assert key == "community-key"
        assert source == "community"

    @patch.dict("os.environ", {}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_unauthorized_origin_requires_byok(self, mock_settings, mock_registry):  # noqa: ARG002
        """Unauthorized origin without BYOK should raise 403."""
        mock_settings.return_value.openrouter_api_key = "platform-key"

        with pytest.raises(HTTPException) as exc_info:
            _select_api_key("hed", None, "https://evil.com")

        assert exc_info.value.status_code == 403
        assert "API key required" in exc_info.value.detail
        assert "openrouter.ai/keys" in exc_info.value.detail

    @patch.dict("os.environ", {}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_cli_without_byok_requires_key(self, mock_settings, mock_registry):  # noqa: ARG002
        """CLI (no origin) without BYOK should raise 403."""
        mock_settings.return_value.openrouter_api_key = "platform-key"

        with pytest.raises(HTTPException) as exc_info:
            _select_api_key("hed", None, None)

        assert exc_info.value.status_code == 403
        assert "API key required" in exc_info.value.detail

    @patch.dict("os.environ", {}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_no_platform_key_configured_raises_500(self, mock_settings, mock_registry):  # noqa: ARG002
        """No platform key configured should raise 500 for authorized origins."""
        mock_settings.return_value.openrouter_api_key = None

        with pytest.raises(HTTPException) as exc_info:
            _select_api_key("hed", None, "https://hedtags.org")

        assert exc_info.value.status_code == 500
        assert "No API key configured" in exc_info.value.detail


class TestSelectModel:
    """Tests for _select_model logic."""

    @patch("src.api.routers.community.get_settings")
    def test_uses_platform_default_when_no_community_model(self, mock_settings, mock_registry):
        """Should use platform default when community has no default_model."""
        mock_settings.return_value.default_model = "openai/gpt-oss-120b"
        mock_settings.return_value.default_model_provider = "Cerebras"

        community_info = mock_registry.get("hed")
        model, provider = _select_model(community_info, None, has_byok=False)

        assert model == "openai/gpt-oss-120b"
        assert provider == "Cerebras"

    @patch("src.api.routers.community.get_settings")
    def test_uses_community_default_model(self, mock_settings, mock_registry):
        """Should use community default_model when configured."""
        mock_settings.return_value.default_model = "openai/gpt-oss-120b"
        mock_settings.return_value.default_model_provider = "Cerebras"

        community_info = mock_registry.get("bids")
        model, provider = _select_model(community_info, None, has_byok=False)

        assert model == "anthropic/claude-3.5-sonnet"
        assert provider is None  # BIDS doesn't specify provider

    @patch("src.api.routers.community.get_settings")
    def test_custom_model_with_byok_allowed(self, mock_settings, mock_registry):
        """Custom model should be allowed when user has BYOK."""
        mock_settings.return_value.default_model = "openai/gpt-oss-120b"
        mock_settings.return_value.default_model_provider = "Cerebras"

        community_info = mock_registry.get("hed")
        model, provider = _select_model(community_info, "anthropic/claude-opus-4", has_byok=True)

        assert model == "anthropic/claude-opus-4"
        assert provider is None  # Custom models use default routing

    @patch("src.api.routers.community.get_settings")
    def test_custom_model_without_byok_rejected(self, mock_settings, mock_registry):
        """Custom model without BYOK should raise 403."""
        mock_settings.return_value.default_model = "openai/gpt-oss-120b"
        mock_settings.return_value.default_model_provider = "Cerebras"

        community_info = mock_registry.get("hed")

        with pytest.raises(HTTPException) as exc_info:
            _select_model(community_info, "anthropic/claude-opus-4", has_byok=False)

        assert exc_info.value.status_code == 403
        assert "Custom model" in exc_info.value.detail
        assert "anthropic/claude-opus-4" in exc_info.value.detail
        assert "requires your own API key" in exc_info.value.detail

    @patch("src.api.routers.community.get_settings")
    def test_requesting_default_model_explicitly_allowed(self, mock_settings, mock_registry):
        """Explicitly requesting the default model should not require BYOK."""
        mock_settings.return_value.default_model = "openai/gpt-oss-120b"
        mock_settings.return_value.default_model_provider = "Cerebras"

        community_info = mock_registry.get("hed")
        # User explicitly requests platform default - should not be treated as custom
        model, provider = _select_model(community_info, "openai/gpt-oss-120b", has_byok=False)

        assert model == "openai/gpt-oss-120b"
        assert provider == "Cerebras"

    @patch("src.api.routers.community.get_settings")
    def test_requesting_community_default_model_allowed(self, mock_settings, mock_registry):
        """Explicitly requesting the community default should not require BYOK."""
        mock_settings.return_value.default_model = "openai/gpt-oss-120b"
        mock_settings.return_value.default_model_provider = "Cerebras"

        community_info = mock_registry.get("bids")
        # User explicitly requests BIDS's default - should not be treated as custom
        model, provider = _select_model(
            community_info, "anthropic/claude-3.5-sonnet", has_byok=False
        )

        assert model == "anthropic/claude-3.5-sonnet"
        assert provider is None


class TestIntegration:
    """Integration tests for combined authorization + model selection."""

    @patch.dict("os.environ", {}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_widget_user_default_model(self, mock_settings, mock_registry):
        """Widget user on authorized site with default model."""
        mock_settings.return_value.openrouter_api_key = "platform-key"
        mock_settings.return_value.default_model = "openai/gpt-oss-120b"
        mock_settings.return_value.default_model_provider = "Cerebras"

        # Select API key
        api_key, key_source = _select_api_key("hed", None, "https://hedtags.org")
        assert key_source in ["platform", "community"]

        # Select model
        community_info = mock_registry.get("hed")
        model, provider = _select_model(community_info, None, has_byok=False)
        assert model == "openai/gpt-oss-120b"

    @patch.dict("os.environ", {}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_widget_user_custom_model_rejected(self, mock_settings, mock_registry):
        """Widget user trying to use custom model should be rejected."""
        mock_settings.return_value.openrouter_api_key = "platform-key"
        mock_settings.return_value.default_model = "openai/gpt-oss-120b"

        # API key is allowed (authorized origin)
        api_key, key_source = _select_api_key("hed", None, "https://hedtags.org")
        assert key_source in ["platform", "community"]

        # But custom model is rejected (no BYOK)
        community_info = mock_registry.get("hed")
        with pytest.raises(HTTPException) as exc_info:
            _select_model(community_info, "anthropic/claude-opus-4", has_byok=False)
        assert exc_info.value.status_code == 403

    @patch.dict("os.environ", {}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_cli_user_with_byok_and_custom_model(self, mock_settings, mock_registry):
        """CLI user with BYOK can use custom model."""
        mock_settings.return_value.default_model = "openai/gpt-oss-120b"

        # CLI provides BYOK
        api_key, key_source = _select_api_key("hed", "user-key", None)
        assert api_key == "user-key"
        assert key_source == "byok"

        # Can use custom model
        community_info = mock_registry.get("hed")
        model, provider = _select_model(community_info, "anthropic/claude-opus-4", has_byok=True)
        assert model == "anthropic/claude-opus-4"

    @patch.dict("os.environ", {}, clear=True)
    @patch("src.api.routers.community.get_settings")
    def test_cli_user_without_byok_rejected(self, mock_settings, mock_registry):  # noqa: ARG002
        """CLI user without BYOK should be rejected."""
        mock_settings.return_value.openrouter_api_key = "platform-key"

        # CLI without BYOK is rejected
        with pytest.raises(HTTPException) as exc_info:
            _select_api_key("hed", None, None)
        assert exc_info.value.status_code == 403
