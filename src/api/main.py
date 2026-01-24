"""FastAPI application entry point for Open Science Assistant."""

import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.api.config import get_settings
from src.api.routers import create_community_router, sync_router
from src.api.scheduler import start_scheduler, stop_scheduler
from src.assistants import discover_assistants, registry

logger = logging.getLogger(__name__)

# Discover assistants at module load time to populate registry
discover_assistants()


class HealthResponse(BaseModel):
    """Health check response model."""

    status: str
    version: str
    commit_sha: str | None
    timestamp: str
    environment: str


class ErrorResponse(BaseModel):
    """Standard error response model."""

    error: str
    detail: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup and shutdown events."""
    settings = get_settings()

    # Startup
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    app.state.settings = settings
    app.state.start_time = datetime.now(UTC)

    # Start background scheduler for knowledge sync
    scheduler = start_scheduler()
    app.state.scheduler = scheduler
    if scheduler:
        logger.info("Knowledge sync scheduler started")

    yield

    # Shutdown
    logger.info("Shutting down %s", settings.app_name)
    stop_scheduler()


def _wildcard_origin_to_regex(pattern: str) -> str:
    """Convert a wildcard CORS origin pattern to a regex string.

    Converts patterns like 'https://*.pages.dev' to a regex that matches
    any subdomain (e.g., 'https://my-app.pages.dev').

    Args:
        pattern: Origin with wildcard (e.g., 'https://*.example.com').

    Returns:
        Regex string matching the pattern.
    """
    escaped = re.escape(pattern)
    # Replace escaped wildcard \* with regex for a valid subdomain label
    return escaped.replace(r"\*", r"[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?")


def _collect_cors_config() -> tuple[list[str], str | None]:
    """Collect CORS origins from settings and all registered communities.

    Aggregates exact origins and wildcard patterns from:
    1. Platform-level settings (Settings.cors_origins)
    2. Per-community config (CommunityConfig.cors_origins)
    3. Default platform wildcard (*.osa-demo.pages.dev)

    Returns:
        Tuple of (exact_origins, origin_regex_pattern).
        origin_regex_pattern is None if no wildcards are configured.
    """
    settings = get_settings()

    exact_origins: list[str] = list(settings.cors_origins)
    wildcard_patterns: list[str] = [
        "https://*.osa-demo.pages.dev",  # Default: all community demo pages
    ]

    # Collect from all registered communities
    for info in registry.list_all():
        if info.community_config and info.community_config.cors_origins:
            for origin in info.community_config.cors_origins:
                if "*" in origin:
                    if origin not in wildcard_patterns:
                        wildcard_patterns.append(origin)
                else:
                    if origin not in exact_origins:
                        exact_origins.append(origin)

    # Build combined regex from wildcard patterns
    origin_regex: str | None = None
    if wildcard_patterns:
        regex_parts = [_wildcard_origin_to_regex(p) for p in wildcard_patterns]
        origin_regex = "^(" + "|".join(regex_parts) + ")$"

    return exact_origins, origin_regex


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="An extensible AI assistant platform for open science projects",
        lifespan=lifespan,
        root_path=settings.root_path,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # CORS middleware - aggregate origins from settings and community configs
    exact_origins, origin_regex = _collect_cors_config()
    cors_kwargs: dict[str, Any] = {
        "allow_origins": exact_origins,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if origin_regex:
        cors_kwargs["allow_origin_regex"] = origin_regex
    app.add_middleware(CORSMiddleware, **cors_kwargs)

    # Register routes
    register_routes(app)

    return app


def register_routes(app: FastAPI) -> None:
    """Register all application routes.

    Auto-mounts routers for all registered communities from the registry.
    Each community gets endpoints at /{community_id}/ask, /{community_id}/chat, etc.
    """
    # Auto-mount routers for all registered communities
    registered_communities = []
    for info in registry.list_available():
        try:
            router = create_community_router(info.id)
            app.include_router(router)
            registered_communities.append(info.id)
            logger.info("Registered API routes for community: %s", info.id)
        except Exception as e:
            logger.error("Failed to register routes for %s: %s", info.id, e)

    # Sync router (not community-specific)
    app.include_router(sync_router)

    @app.get("/health", response_model=HealthResponse, tags=["System"])
    async def health_check() -> HealthResponse:
        """Check API health status.

        Returns basic health information including version and uptime.
        """
        settings = get_settings()
        return HealthResponse(
            status="healthy",
            version=settings.app_version,
            commit_sha=settings.git_commit_sha,
            timestamp=datetime.now(UTC).isoformat(),
            environment="development" if settings.debug else "production",
        )

    @app.get("/", tags=["System"])
    async def root() -> dict[str, Any]:
        """Root endpoint with API information."""
        settings = get_settings()

        # Build dynamic endpoint list based on registered communities
        endpoints: dict[str, str] = {}
        for community_id in registered_communities:
            info = registry.get(community_id)
            name = info.name if info else community_id.upper()
            endpoints[f"POST /{community_id}/ask"] = f"Ask a single question about {name}"
            endpoints[f"POST /{community_id}/chat"] = f"Multi-turn conversation about {name}"
            endpoints[f"GET /{community_id}/sessions"] = f"List active {name} sessions"
            endpoints[f"GET /{community_id}/sessions/{{session_id}}"] = "Get session info"
            endpoints[f"DELETE /{community_id}/sessions/{{session_id}}"] = "Delete a session"

        # Add non-community endpoints
        endpoints["GET /sync/status"] = "Knowledge sync status"
        endpoints["GET /sync/health"] = "Sync health check"
        endpoints["POST /sync/trigger"] = "Trigger sync (requires API key)"
        endpoints["GET /health"] = "Health check"

        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "description": "AI assistant for open science tools",
            "communities": registered_communities,
            "endpoints": endpoints,
        }


# Create the application instance
app = create_app()
