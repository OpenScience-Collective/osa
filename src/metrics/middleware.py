"""Request timing and metrics middleware.

Captures request-scoped data (endpoint, duration, status_code, timestamp).
For agent requests, the handler sets metrics on request.state which the
middleware reads after the response completes.

Streaming caveat: For streaming responses, the middleware fires before
streaming completes. Streaming handlers log metrics directly at the end
of the generator instead.
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from src.metrics.db import RequestLogEntry, log_request, now_iso

logger = logging.getLogger(__name__)

# Path segments that indicate a community route
_COMMUNITY_ENDPOINTS = {"/ask", "/chat"}


def _extract_community_id(path: str) -> str | None:
    """Extract community_id from URL path like /{community_id}/ask."""
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and f"/{parts[1]}" in _COMMUNITY_ENDPOINTS:
        return parts[0]
    return None


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware that logs request timing and metrics.

    Sets request.state.request_id and request.state.start_time for
    downstream handlers to use. After the response, logs a basic
    request entry unless the handler has set request.state.metrics_logged
    (indicating the handler logged its own detailed entry, e.g. streaming).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.start_time = time.monotonic()
        request.state.metrics_logged = False

        response = await call_next(request)

        try:
            # If handler already logged metrics (streaming), skip
            if getattr(request.state, "metrics_logged", False):
                return response

            duration_ms = (time.monotonic() - request.state.start_time) * 1000
            community_id = _extract_community_id(request.url.path)

            # Check if handler set agent metrics on request.state
            agent_data = getattr(request.state, "metrics_agent_data", None)

            agent_kwargs = {}
            if agent_data and isinstance(agent_data, dict):
                agent_kwargs = {
                    "model": agent_data.get("model"),
                    "input_tokens": agent_data.get("input_tokens"),
                    "output_tokens": agent_data.get("output_tokens"),
                    "total_tokens": agent_data.get("total_tokens"),
                    "estimated_cost": agent_data.get("estimated_cost"),
                    "tools_called": agent_data.get("tools_called", []),
                    "key_source": agent_data.get("key_source"),
                    "stream": agent_data.get("stream", False),
                    "tool_call_count": agent_data.get("tool_call_count", 0),
                    "error_message": agent_data.get("error_message"),
                    "langfuse_trace_id": agent_data.get("langfuse_trace_id"),
                }

            entry = RequestLogEntry(
                request_id=request_id,
                timestamp=now_iso(),
                endpoint=request.url.path,
                method=request.method,
                community_id=community_id,
                duration_ms=round(duration_ms, 1),
                status_code=response.status_code,
                **agent_kwargs,
            )

            log_request(entry)
        except Exception:
            logger.exception("Metrics middleware failed for request %s", request_id)

        return response
