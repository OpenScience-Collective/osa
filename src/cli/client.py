"""HTTP client for communicating with the OSA API."""

import json
from collections.abc import Generator
from typing import Any

import httpx

from src.cli.config import get_user_id

DEFAULT_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=120.0,  # LLM responses can be slow
    write=10.0,
    pool=10.0,
)


class APIError(Exception):
    """Error from the OSA API."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class OSAClient:
    """HTTP client for the OSA API.

    Thin client that forwards requests to the OSA backend.
    BYOK (Bring Your Own Key): the user's OpenRouter API key is
    forwarded via the X-OpenRouter-API-Key header.
    """

    def __init__(
        self,
        api_url: str,
        openrouter_api_key: str | None = None,
        user_id: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.openrouter_api_key = openrouter_api_key
        self._user_id = user_id
        self.timeout = timeout

    @property
    def user_id(self) -> str:
        """Get user ID for cache optimization (lazy-loaded)."""
        if self._user_id is None:
            self._user_id = get_user_id()
        return self._user_id

    def _get_headers(self) -> dict[str, str]:
        """Build request headers with BYOK key and user ID."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "osa-cli",
            "X-User-ID": self.user_id,
        }
        if self.openrouter_api_key:
            headers["X-OpenRouter-API-Key"] = self.openrouter_api_key
        return headers

    def _handle_response(self, response: httpx.Response) -> None:
        """Raise APIError for HTTP 4xx/5xx responses."""
        if response.status_code >= 400:
            try:
                data = response.json()
                detail = data.get("detail", str(data))
            except (json.JSONDecodeError, ValueError):
                detail = response.text or f"HTTP {response.status_code}"
            raise APIError(
                f"API error ({response.status_code})",
                status_code=response.status_code,
                detail=detail,
            )

    def health_check(self) -> dict[str, Any]:
        """Check API health status."""
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{self.api_url}/health",
                headers=self._get_headers(),
            )
            self._handle_response(response)
            return response.json()

    def get_info(self) -> dict[str, Any]:
        """Get API information from root endpoint."""
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{self.api_url}/",
                headers=self._get_headers(),
            )
            self._handle_response(response)
            return response.json()

    def list_communities(self) -> list[dict[str, Any]]:
        """Fetch available communities from the API."""
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{self.api_url}/communities",
                headers=self._get_headers(),
            )
            self._handle_response(response)
            return response.json()

    def ask(
        self,
        community: str,
        question: str,
    ) -> dict[str, Any]:
        """Ask a single question (non-streaming).

        Returns the full response including answer and tool_calls.
        """
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.api_url}/{community}/ask",
                headers=self._get_headers(),
                json={"question": question, "stream": False},
            )
            self._handle_response(response)
            return response.json()

    def _stream_request(
        self,
        url: str,
        payload: dict[str, Any],
    ) -> Generator[tuple[str, dict[str, Any]], None, None]:
        """Send a streaming POST and yield parsed SSE events.

        Server SSE format: data: {"event": "content", "content": "text"}\\n\\n
        Yields (event_type, data_dict) tuples.
        """
        with (
            httpx.Client(timeout=self.timeout) as client,
            client.stream(
                "POST",
                url,
                headers=self._get_headers(),
                json=payload,
            ) as response,
        ):
            if response.status_code >= 400:
                response.read()
                self._handle_response(response)
                return

            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                    event_type = data.get("event", "unknown")
                    yield (event_type, data)
                except json.JSONDecodeError:
                    continue

    def ask_stream(
        self,
        community: str,
        question: str,
    ) -> Generator[tuple[str, dict[str, Any]], None, None]:
        """Ask a single question with SSE streaming.

        Yields (event_type, data_dict) tuples.
        Event types: content, tool_start, tool_end, done, error
        """
        return self._stream_request(
            f"{self.api_url}/{community}/ask",
            {"question": question, "stream": True},
        )

    def chat(
        self,
        community: str,
        message: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat message (non-streaming).

        Returns the full response including message, session_id, and tool_calls.
        """
        payload: dict[str, Any] = {
            "message": message,
            "stream": False,
        }
        if session_id:
            payload["session_id"] = session_id

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.api_url}/{community}/chat",
                headers=self._get_headers(),
                json=payload,
            )
            self._handle_response(response)
            return response.json()

    def chat_stream(
        self,
        community: str,
        message: str,
        session_id: str | None = None,
    ) -> Generator[tuple[str, dict[str, Any]], None, None]:
        """Send a chat message with SSE streaming.

        Chat emits: session (with session_id), content, tool_start, done, error
        Yields (event_type, data_dict) tuples.
        """
        payload: dict[str, Any] = {
            "message": message,
            "stream": True,
        }
        if session_id:
            payload["session_id"] = session_id

        return self._stream_request(
            f"{self.api_url}/{community}/chat",
            payload,
        )
