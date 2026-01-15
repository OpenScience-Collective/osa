"""API routers for Open Science Assistant."""

from src.api.routers.hed import router as hed_router
from src.api.routers.sync import router as sync_router

__all__ = ["hed_router", "sync_router"]
