"""Public communities metadata endpoint for widget configuration."""

from typing import Any

from fastapi import APIRouter

from src.api.routers.community import _find_logo_file
from src.assistants import registry
from src.core.config.community import WidgetConfig

router = APIRouter(tags=["Communities"])

_DEFAULT_WIDGET = WidgetConfig()


@router.get("/communities")
def list_communities() -> list[dict[str, Any]]:
    """List available communities with widget configuration.

    Returns community metadata including widget display config
    (title, placeholder, initial message, suggested questions, logo).
    Only returns communities with status='available'.
    """
    communities = []

    for info in registry.list_available():
        config = info.community_config
        if not config:
            continue

        widget = config.widget or _DEFAULT_WIDGET

        # Convention-based logo detection
        convention_logo = None
        if not widget.logo_url and _find_logo_file(config.id):
            convention_logo = f"/{config.id}/logo"

        communities.append(
            {
                "id": config.id,
                "name": config.name,
                "description": config.description,
                "status": config.status,
                "widget": widget.resolve(config.name, logo_url=convention_logo),
                "links": config.links.resolve() if config.links else None,
            }
        )

    return communities
