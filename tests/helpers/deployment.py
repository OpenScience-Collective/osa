"""The platform's keys as a deployment configures them, for routing tests.

Routing and the community config endpoint both decide from which platform keys a
deployment has, and `get_settings()` also reads a developer's own `.env`, so a test sets
the cached Settings instance directly and leaves no community to fund itself.
"""

from src.api.config import get_settings
from src.assistants import registry
from src.core.services.anthropic_models import LUNA

#: Deployments by the platform keys they hold: (Anthropic, OpenRouter, Bedrock).
#: Every combination is here, because what a request does depends on all three.
DEPLOYMENTS: dict[str, tuple[str | None, str | None, str | None]] = {
    "keyless": (None, None, None),
    "bedrock_only": (None, None, "bedrock-key"),
    "openrouter_only": (None, "openrouter-key", None),
    "bedrock_and_openrouter": (None, "openrouter-key", "bedrock-key"),
    "anthropic_only": ("anthropic-key", None, None),
    "anthropic_and_bedrock": ("anthropic-key", None, "bedrock-key"),
    "anthropic_and_openrouter": ("anthropic-key", "openrouter-key", None),
    "all_three": ("anthropic-key", "openrouter-key", "bedrock-key"),
}


def set_platform_keys(
    monkeypatch,
    *,
    anthropic: str | None = "platform-anthropic-key",
    openrouter: str | None = None,
    bedrock: str | None = "bedrock-key",
) -> None:
    """Give the cached Settings these platform keys, and no community its own.

    Every registered community is left to the platform's keys, whatever env vars its
    config names, so a request to it resolves the way an unkeyed browser's does.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", anthropic)
    monkeypatch.setattr(settings, "openrouter_api_key", openrouter)
    monkeypatch.setattr(settings, "bedrock_api_key", bedrock)
    for info in registry.list_all():
        if info.community_config:
            monkeypatch.setattr(info.community_config, "anthropic_api_key_env_var", None)
            monkeypatch.setattr(info.community_config, "openrouter_api_key_env_var", None)


def without_mcp_servers(monkeypatch, info) -> None:
    """Building an assistant discovers its MCP servers' tools over the network (NEMAR's is
    mcp.nemar.org, waited on for up to 25 s). Which tools a community has is not what a
    routing test is about, and an offline suite must not depend on a live service."""
    if info.community_config.extensions:
        monkeypatch.setattr(info.community_config.extensions, "mcp_servers", [])


def name_luna_as_the_default(monkeypatch, community_id: str = "nwb"):
    """Name GPT-6 Luna as a shipped community's default, for one test, and return it.

    No shipped community names a Bedrock model now (they moved to Haiku), so the tests of a
    Bedrock default give NWB Luna the way its config.yaml would. monkeypatch undoes it.
    """
    info = registry.get(community_id)
    monkeypatch.setattr(info.community_config, "default_model", LUNA)
    return info
