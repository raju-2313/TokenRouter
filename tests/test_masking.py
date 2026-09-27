"""Tests for API key masking helper."""

from __future__ import annotations

from app.core.masking import mask_api_key


def test_mask_long_key_matches_spec_example() -> None:
    assert mask_api_key("clean_abc123456789") == "clean_a...6789"


def test_mask_sk_clean_key() -> None:
    assert mask_api_key("sk-clean-1234567890") == "sk-clea...7890"


def test_mask_none_returns_stars() -> None:
    assert mask_api_key(None) == "****"


def test_mask_empty_returns_stars() -> None:
    assert mask_api_key("") == "****"


def test_mask_short_key_returns_stars() -> None:
    assert mask_api_key("short") == "****"
    assert mask_api_key("12345678") == "****"


def test_mask_exactly_nine_chars() -> None:
    # 9 chars -> prefix 7 + ... + suffix 4 => masked
    assert mask_api_key("123456789") == "1234567...6789"


def test_mask_does_not_reveal_full_key() -> None:
    key = "clean_abc123456789"
    masked = mask_api_key(key)
    assert key not in masked
    assert "abc123456" not in masked
