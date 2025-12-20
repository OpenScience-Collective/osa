"""HTTP client for communicating with the OSA API."""

from typing import Any

import httpx

from src.cli.config import CLIConfig


class OSAClient:
    """HTTP client for the OSA API."""

    def __init__(self, config: CLIConfig) -> None:
        """Initialize the client with configuration."""
        self.config = config
        self.base_url = config.api_url.rstrip("/")

    def _get_headers(self) -> dict[str, str]:
        """Build request headers including API keys."""
        headers: dict[str, str] = {"Content-Type": "application/json"}

        # Server API key
        if self.config.api_key:
            headers["X-API-Key"] = self.config.api_key

        # BYOK headers
        if self.config.openai_api_key:
            headers["X-OpenAI-API-Key"] = self.config.openai_api_key
        if self.config.anthropic_api_key:
            headers["X-Anthropic-API-Key"] = self.config.anthropic_api_key
        if self.config.openrouter_api_key:
            headers["X-OpenRouter-API-Key"] = self.config.openrouter_api_key

        return headers

    def health_check(self) -> dict[str, Any]:
        """Check API health status.

        Returns health information including version and status.
        Raises httpx.HTTPError on connection or HTTP errors.
        """
        with httpx.Client() as client:
            response = client.get(
                f"{self.base_url}/health",
                headers=self._get_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    def get_info(self) -> dict[str, Any]:
        """Get API information from root endpoint.

        Returns basic API info including name and version.
        Raises httpx.HTTPError on connection or HTTP errors.
        """
        with httpx.Client() as client:
            response = client.get(
                f"{self.base_url}/",
                headers=self._get_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
