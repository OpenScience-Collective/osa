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

        widget_data: dict[str, Any] = {}
        if config.widget:
            widget_data = {
                "title": config.widget.title or config.name,
                "initial_message": config.widget.initial_message,
                "placeholder": config.widget.placeholder or "Ask a question...",
                "suggested_questions": config.widget.suggested_questions,
            }
        else:
            widget_data = {
                "title": config.name,
                "initial_message": None,
                "placeholder": "Ask a question...",
                "suggested_questions": [],
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

    return communities
