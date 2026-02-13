"""Public communities metadata endpoint for widget configuration."""

from typing import Any

from fastapi import APIRouter

from src.assistants import registry
from src.core.config.community import WidgetConfig

router = APIRouter(tags=["Communities"])

_DEFAULT_WIDGET = WidgetConfig()


@router.get("/communities")
def list_communities() -> list[dict[str, Any]]:
    """List available communities with widget configuration.

    Returns community metadata including widget display config
    (title, placeholder, initial message, suggested questions).
    Only returns communities with status='available'.
    """
    communities = []

    for info in registry.list_available():
        config = info.community_config
        if not config:
            continue

        widget = config.widget or _DEFAULT_WIDGET
        links = None
        if config.links:
            links = {k: v for k, v in config.links.model_dump().items() if v is not None}

        communities.append(
            {
                "id": config.id,
                "name": config.name,
                "description": config.description,
                "status": config.status,
                "widget": widget.resolve(config.name),
                "links": links,
            }
        )

    return communities
