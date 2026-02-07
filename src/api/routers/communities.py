"""Public communities metadata endpoint for widget configuration."""

import logging
from typing import Any

from fastapi import APIRouter

from src.assistants import registry

router = APIRouter(tags=["Communities"])
logger = logging.getLogger(__name__)


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

        try:
            widget = config.widget
            widget_data: dict[str, Any] = {
                "title": (widget.title if widget else None) or config.name,
                "initial_message": widget.initial_message if widget else None,
                "placeholder": (widget.placeholder if widget else None) or "Ask a question...",
                "suggested_questions": widget.suggested_questions if widget else [],
            }

            communities.append(
                {
                    "id": config.id,
                    "name": config.name,
                    "description": config.description,
                    "status": config.status,
                    "widget": widget_data,
                }
            )
        except Exception:
            logger.exception("Failed to build widget data for community %s", info.id)

    return communities
