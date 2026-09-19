"""Tests for src.api.config.Settings validation.

Real Settings construction throughout -- no mocks, since the whole point is
to verify pydantic's own validation behavior.
"""

import pytest
from pydantic import ValidationError

from src.api.config import Settings


class TestWorkspaceIdWithBaseUrl:
    """ANTHROPIC_BASE_URL requires ANTHROPIC_WORKSPACE_ID (see
    validate_workspace_id_with_base_url): the Claude Platform on AWS
    endpoint rejects requests without the anthropic-workspace-id header, so
    this is caught at Settings construction instead of on the first request
    that falls through to the platform key.
    """

    def test_base_url_without_workspace_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ANTHROPIC_WORKSPACE_ID"):
            Settings(
                anthropic_base_url="https://aws-anthropic.example.test",
                anthropic_workspace_id=None,
            )

    def test_base_url_with_workspace_id_accepted(self) -> None:
        settings = Settings(
            anthropic_base_url="https://aws-anthropic.example.test",
            anthropic_workspace_id="wrkspc_test123",
        )
        assert settings.anthropic_base_url == "https://aws-anthropic.example.test"
        assert settings.anthropic_workspace_id == "wrkspc_test123"

    def test_no_base_url_no_workspace_id_accepted(self) -> None:
        """Both unset is the first-party/BYOK-only case, not an error."""
        settings = Settings(anthropic_base_url=None, anthropic_workspace_id=None)
        assert settings.anthropic_base_url is None
        assert settings.anthropic_workspace_id is None

    def test_workspace_id_without_base_url_accepted(self) -> None:
        """A workspace id with no base_url still constructs; base_url has
        its own default elsewhere (see create_anthropic_llm)."""
        settings = Settings(anthropic_base_url=None, anthropic_workspace_id="wrkspc_test123")
        assert settings.anthropic_workspace_id == "wrkspc_test123"
