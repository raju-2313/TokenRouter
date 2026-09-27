"""Safe structured logging.

Only allow-listed metadata fields are emitted. Anything else is replaced with
``[redacted]`` so API keys, Authorization headers, prompt contents and completion
contents can never reach the log stream, even by accident.
"""

from __future__ import annotations

import json
import time
from typing import Any

REDACTED = "[redacted]"

SAFE_FIELDS = frozenset(
    {
        "account_id",
        "attempt",
        "attempts",
        "available",
        "completed_tokens",
        "duration_ms",
        "eligible",
        "error_code",
        "estimated_completion_tokens",
        "estimated_prompt_tokens",
        "estimated_remaining",
        "estimate_total",
        "http_status",
        "input_tokens",
        "latency_ms",
        "model",
        "models_considered",
        "outcome",
        "output_tokens",
        "reason",
        "request_id",
        "reserved_after",
        "reserved_tokens",
        "retry_after",
        "result",
        "routing_strategy",
        "stream",
        "total_tokens",
        "upstream_status",
        "usage_source",
    }
)


def safe_payload(event: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Build a log payload keeping only allow-listed fields."""
    payload: dict[str, Any] = {"event": event, "ts": round(time.time(), 3)}
    for key, value in fields.items():
        if key in SAFE_FIELDS:
            payload[key] = value
        else:
            payload[key] = REDACTED
    return payload


def log_event(event: str, **fields: Any) -> None:
    """Emit one JSON metadata line on stdout."""
    print(json.dumps(safe_payload(event, fields), default=str))
