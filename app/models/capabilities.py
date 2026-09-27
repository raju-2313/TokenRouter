"""Capability helpers — never fabricate, only derive from raw metadata."""

from __future__ import annotations

from typing import Any


def infer_capabilities(raw: dict[str, Any]) -> dict[str, bool | None]:
    """Infer capabilities from raw model metadata without fabrication.

    Returns dict with keys: tools, vision, reasoning, streaming.
    Values: True/False if evidence exists, None if unknown.
    """
    caps: dict[str, bool | None] = {
        "tools": None,
        "vision": None,
        "reasoning": None,
        "streaming": None,
    }

    # Common fields some OpenAI-compatible providers expose
    # We only set True if we see explicit evidence; otherwise None.
    data = raw
    # Check top-level keys
    if "capabilities" in data and isinstance(data["capabilities"], dict):
        for k in caps:
            if k in data["capabilities"]:
                v = data["capabilities"][k]
                if isinstance(v, bool):
                    caps[k] = v

    # Tool detection deliberately left as unknown unless explicit.
    # Presence of "tools"/"tool_calls" keys alone does not prove capability.

    return caps
