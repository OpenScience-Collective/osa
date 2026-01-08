"""API routers for Open Science Assistant."""

# Keep chat_router for backwards compatibility during transition
from src.api.routers.chat import router as chat_router
from src.api.routers.hed import router as hed_router

__all__ = ["chat_router", "hed_router"]
