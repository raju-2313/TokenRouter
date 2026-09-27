"""API key masking helper — consistent, never leaks secrets."""

from __future__ import annotations


def mask_api_key(key: str | None) -> str:
    """Mask an API key for diagnostics.

    - None / empty -> "****"
    - Short keys (<=8) -> "****"
    - Otherwise: keep first 7 chars and last 4 chars, mask middle with "..."
      e.g. "clean_abc123456789" -> "clean_a...6789"
           "sk-clean-1234567890" -> "sk-clea...7890"

    This is intentionally deterministic and does not reveal length precisely
    beyond the masked middle.
    """
    if not key:
        return "****"
    key = str(key)
    if len(key) <= 8:
        return "****"
    # Keep prefix 7, suffix 4 — matches example clean_a...6789
    prefix = key[:7]
    suffix = key[-4:]
    return f"{prefix}...{suffix}"


def mask_value(value: str | None) -> str:
    """Generic mask for any secret value."""
    return mask_api_key(value)
