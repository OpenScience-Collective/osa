"""FastAPI application entry point for Open Science Assistant."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.api.config import get_settings
from src.api.routers import chat_router


class HealthResponse(BaseModel):
    """Health check response model."""

    status: str
    version: str
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
    app.state.settings = settings
    app.state.start_time = datetime.now(UTC)

    yield

    # Shutdown (cleanup resources here)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="An extensible AI assistant platform for open science projects",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    register_routes(app)

    return app


def register_routes(app: FastAPI) -> None:
    """Register all application routes."""
    # Include routers
    app.include_router(chat_router)

    @app.get("/health", response_model=HealthResponse, tags=["System"])
    async def health_check() -> HealthResponse:
        """Check API health status.

        Returns basic health information including version and uptime.
        """
        settings = get_settings()
        return HealthResponse(
            status="healthy",
            version=settings.app_version,
            timestamp=datetime.now(UTC).isoformat(),
            environment="development" if settings.debug else "production",
        )

    @app.get("/", tags=["System"])
    async def root() -> dict[str, Any]:
        """Root endpoint with API information."""
        settings = get_settings()
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs" if settings.debug else "Disabled in production",
        }


# Create the application instance
app = create_app()
