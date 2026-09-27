"""Request ID generation."""

from __future__ import annotations

import uuid


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"
