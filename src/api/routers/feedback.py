"""User feedback API endpoint.

Receives anonymous feedback from the chat widget (per-response thumbs up/down
and free-text general feedback) and persists it to the metrics database for
community admins to review on the status dashboard.

This is a top-level (non community-prefixed) route because the widget posts to
the Cloudflare Worker's global ``POST /feedback`` proxy, which forwards here;
the community is carried in the request body, not the path.
"""

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from src.api.routers.community import _is_authorized_origin
from src.assistants import registry
from src.metrics.db import FeedbackEntry, now_iso, write_feedback

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Feedback"])

_MAX_COMMENT_LEN = 5000


class FeedbackRequest(BaseModel):
    """Body for ``POST /feedback`` submitted by the widget.

    Two shapes share this model:

    * ``feedback_type="response"`` -- a thumbs up/down on one reply. ``sentiment``
      is required; ``request_id`` / ``message_index`` link it to the answer.
    * ``feedback_type="general"`` -- free-text feedback. ``comment`` is required;
      ``sentiment`` is ignored.
    """

    community_id: str = Field(..., min_length=1, max_length=64)
    feedback_type: Literal["response", "general"] = "response"
    sentiment: Literal["up", "down"] | None = None
    request_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    message_index: int | None = Field(default=None, ge=0)
    comment: str | None = Field(default=None, max_length=_MAX_COMMENT_LEN)
    page_url: str | None = Field(default=None, max_length=2048)

    @field_validator("page_url")
    @classmethod
    def _validate_page_url_scheme(cls, url: str | None) -> str | None:
        """Only accept http(s) URLs.

        This endpoint is anonymous, and page_url is later rendered as a clickable
        link in the admin dashboard. Rejecting non-http(s) schemes prevents a
        stored 'javascript:'/'data:' link from becoming XSS against an admin.
        """
        if url is None:
            return url
        if not url.startswith(("http://", "https://")):
            raise ValueError("page_url must start with http:// or https://")
        return url

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        """Normalize the raw input before field validation.

        Runs on the raw dict so the after-validator can be a pure guard: drop
        any sentiment on general feedback (it is meaningless there) and collapse
        a whitespace-only comment to None so the DB stays clean.
        """
        if not isinstance(data, dict):
            return data
        if data.get("feedback_type") == "general":
            data = {**data, "sentiment": None}
        comment = data.get("comment")
        if isinstance(comment, str) and not comment.strip():
            data = {**data, "comment": None}
        return data

    @model_validator(mode="after")
    def _check_shape(self) -> "FeedbackRequest":
        """Enforce the per-type required fields (pure guard, no mutation)."""
        if self.feedback_type == "response" and self.sentiment is None:
            raise ValueError("response feedback requires a sentiment ('up' or 'down')")
        if self.feedback_type == "general" and not (self.comment and self.comment.strip()):
            raise ValueError("general feedback requires a non-empty comment")
        return self


class FeedbackResponse(BaseModel):
    """Acknowledgement returned to the widget."""

    status: Literal["ok"] = "ok"
    feedback_id: str


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    body: FeedbackRequest,
    origin: Annotated[str | None, Header()] = None,
) -> FeedbackResponse:
    """Record a piece of user feedback.

    Anonymous by design: widget users carry no API key. The community must be a
    registered one. The Origin header is checked softly -- an unrecognized or
    absent origin is logged (CLI, mobile, proxies strip it) but does not reject
    the submission, mirroring how the chat endpoints degrade gracefully.

    Abuse/DoS defense: the public path to this endpoint is the Cloudflare Worker
    (`POST /feedback` -> handleFeedback -> rateLimitOrReject), which rate-limits
    per client before proxying here. There is intentionally no in-process limiter;
    if this endpoint is ever exposed without the Worker in front, add one.
    """
    info = registry.get(body.community_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Unknown community: {body.community_id}")

    if not _is_authorized_origin(origin, body.community_id):
        logger.info(
            "Feedback from unrecognized origin %r for community %s (accepted)",
            origin,
            body.community_id,
        )

    try:
        entry = FeedbackEntry(
            feedback_id=str(uuid.uuid4()),
            timestamp=now_iso(),
            community_id=body.community_id,
            feedback_type=body.feedback_type,
            sentiment=body.sentiment,
            request_id=body.request_id,
            session_id=body.session_id,
            message_index=body.message_index,
            comment=body.comment,
            page_url=body.page_url,
        )
    except ValueError as e:
        # FeedbackRequest already validates these invariants; this guards against
        # the two validators drifting apart so a bad shape returns 422, not 500.
        raise HTTPException(status_code=422, detail=str(e)) from e

    # write_feedback is best-effort: it logs and swallows storage errors (and
    # escalates after repeated failures) rather than failing the user's request,
    # so the widget always receives a clean acknowledgement.
    write_feedback(entry)
    return FeedbackResponse(feedback_id=entry.feedback_id)
