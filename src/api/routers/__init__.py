"""API routers for Open Science Assistant."""

from src.api.routers.community import create_community_router
from src.api.routers.metrics import router as metrics_router
from src.api.routers.metrics_public import router as metrics_public_router
from src.api.routers.sync import router as sync_router

__all__ = [
    "create_community_router",
    "metrics_public_router",
    "metrics_router",
    "sync_router",
]
