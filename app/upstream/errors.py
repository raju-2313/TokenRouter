"""Normalized upstream errors — never expose API keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Canonical error codes
INVALID_REQUEST = "INVALID_REQUEST"
MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
UPSTREAM_AUTH_ERROR = "UPSTREAM_AUTH_ERROR"
UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
UPSTREAM_PAYMENT_REQUIRED = "UPSTREAM_PAYMENT_REQUIRED"
UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
UPSTREAM_ERROR = "UPSTREAM_ERROR"


@dataclass(frozen=True)
class UpstreamError(Exception):
    code: str
    message: str
    status_code: int
    upstream_status: int | None = None
    upstream_body: Any | None = None
    retry_after: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "upstream_status": self.upstream_status,
            }
        }


def map_status_to_error(
    status: int,
    body: Any | None = None,
    retry_after: int | None = None,
) -> UpstreamError:
    """Map HTTP status to normalized error."""
    body_snippet: str | None = None
    if body is not None:
        try:
            body_snippet = str(body)[:500]
        except Exception:
            body_snippet = None

    if status == 400:
        return UpstreamError(
            code=INVALID_REQUEST,
            message="Invalid request",
            status_code=400,
            upstream_status=status,
            upstream_body=body_snippet,
        )
    if status == 401:
        return UpstreamError(
            code=UPSTREAM_AUTH_ERROR,
            message="Upstream authentication failed",
            status_code=401,
            upstream_status=status,
            upstream_body=body_snippet,
        )
    if status == 402:
        return UpstreamError(
            code=UPSTREAM_PAYMENT_REQUIRED,
            message="Upstream payment required / insufficient budget",
            status_code=402,
            upstream_status=status,
            upstream_body=body_snippet,
        )
    if status == 404:
        return UpstreamError(
            code=MODEL_NOT_AVAILABLE,
            message="Model not available",
            status_code=404,
            upstream_status=status,
            upstream_body=body_snippet,
        )
    if status == 429:
        return UpstreamError(
            code=UPSTREAM_RATE_LIMITED,
            message="Upstream rate limited",
            status_code=429,
            upstream_status=status,
            upstream_body=body_snippet,
            retry_after=retry_after,
        )
    if 500 <= status < 600:
        return UpstreamError(
            code=UPSTREAM_UNAVAILABLE,
            message="Upstream unavailable",
            status_code=502,
            upstream_status=status,
            upstream_body=body_snippet,
        )
    return UpstreamError(
        code=UPSTREAM_ERROR,
        message="Upstream error",
        status_code=502,
        upstream_status=status,
        upstream_body=body_snippet,
    )
