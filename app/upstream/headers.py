"""Rate-limit header parsing — short-term limits, NOT monthly allowance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitState:
    request_limit: int | None = None
    request_remaining: int | None = None
    request_reset: int | None = None
    token_limit: int | None = None
    token_remaining: int | None = None
    token_reset: int | None = None
    retry_after: int | None = None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        # Some providers send float strings for reset timestamps
        return int(float(value.strip()))
    except (ValueError, AttributeError):
        return None


def parse_rate_limit_headers(headers: dict[str, str] | object) -> RateLimitState:
    """Parse rate-limit headers case-insensitively.

    Accepts httpx.Headers, dict, or any object with .get and case-insensitive keys.
    We normalize keys to lower-case.
    """
    # Normalize to lower-case dict
    normalized: dict[str, str] = {}
    if headers is None:
        headers = {}
    # httpx.Headers is iterable of tuples and has .get; handle both
    try:
        # Try dict-like items()
        if hasattr(headers, "items"):
            for k, v in headers.items():  # type: ignore[union-attr]
                normalized[str(k).lower()] = str(v)
        else:
            for k, v in headers:  # type: ignore[misc]
                normalized[str(k).lower()] = str(v)
    except Exception:
        normalized = {}

    def get(*keys: str) -> str | None:
        for k in keys:
            v = normalized.get(k.lower())
            if v is not None:
                return v
        return None

    return RateLimitState(
        request_limit=_parse_int(
            get("x-ratelimit-limit", "x-ratelimit-limit-requests", "ratelimit-limit")
        ),
        request_remaining=_parse_int(
            get(
                "x-ratelimit-remaining",
                "x-ratelimit-remaining-requests",
                "ratelimit-remaining",
            )
        ),
        request_reset=_parse_int(
            get(
                "x-ratelimit-reset",
                "x-ratelimit-reset-requests",
                "ratelimit-reset",
            )
        ),
        token_limit=_parse_int(
            get("x-ratelimit-limit-tokens", "x-ratelimit-tokens-limit", "ratelimit-limit-tokens")
        ),
        token_remaining=_parse_int(
            get(
                "x-ratelimit-remaining-tokens",
                "x-ratelimit-tokens-remaining",
                "ratelimit-remaining-tokens",
            )
        ),
        token_reset=_parse_int(
            get(
                "x-ratelimit-reset-tokens",
                "x-ratelimit-tokens-reset",
                "ratelimit-reset-tokens",
            )
        ),
        retry_after=_parse_int(get("retry-after")),
    )
