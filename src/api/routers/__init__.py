"""API routers for Open Science Assistant."""

from src.api.routers.community import create_community_router
from src.api.routers.sync import router as sync_router

__all__ = ["create_community_router", "sync_router"]
