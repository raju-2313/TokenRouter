"""Rate-limit header parsing — M1 requirement 7 + 16/17."""

from __future__ import annotations

from app.upstream.headers import parse_rate_limit_headers


def test_parse_x_ratelimit_headers() -> None:
    h = {
        "X-RateLimit-Limit": "100",
        "X-RateLimit-Remaining": "42",
        "X-RateLimit-Reset": "1710000000",
    }
    state = parse_rate_limit_headers(h)
    assert state.request_limit == 100
    assert state.request_remaining == 42
    assert state.request_reset == 1710000000
    assert state.retry_after is None


def test_parse_token_headers() -> None:
    h = {
        "X-RateLimit-Limit-Tokens": "5000",
        "X-RateLimit-Remaining-Tokens": "1234",
        "X-RateLimit-Reset-Tokens": "99",
    }
    state = parse_rate_limit_headers(h)
    assert state.token_limit == 5000
    assert state.token_remaining == 1234
    assert state.token_reset == 99


def test_parse_retry_after() -> None:
    h = {"Retry-After": "60"}
    state = parse_rate_limit_headers(h)
    assert state.retry_after == 60


def test_parse_retry_after_case_insensitive() -> None:
    h = {"retry-after": "120"}
    state = parse_rate_limit_headers(h)
    assert state.retry_after == 120


def test_parse_empty_headers() -> None:
    state = parse_rate_limit_headers({})
    assert state.request_limit is None
    assert state.retry_after is None


def test_parse_invalid_int_returns_none() -> None:
    h = {"X-RateLimit-Limit": "not-a-number", "Retry-After": "abc"}
    state = parse_rate_limit_headers(h)
    assert state.request_limit is None
    assert state.retry_after is None


def test_token_headers_not_confused_with_monthly_allowance() -> None:
    """RateLimitState is short-term, distinct from monthly_allowance."""
    h = {
        "X-RateLimit-Remaining-Tokens": "10",
        "X-RateLimit-Remaining": "50",
    }
    state = parse_rate_limit_headers(h)
    # These are per-minute/window limits, not monthly budget
    assert state.token_remaining == 10
    assert state.request_remaining == 50
