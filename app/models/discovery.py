"""Model discovery — fetches catalogue via upstream client and populates registry."""

from __future__ import annotations

from typing import Any

from app.models.registry import ModelRegistry
from app.upstream.client import CleanApisClient


async def discover_models(client: CleanApisClient, registry: ModelRegistry) -> list[dict[str, Any]]:
    """Fetch /v1/models and populate registry. Returns raw model dicts."""
    resp = await client.list_models()
    body = resp.body

    # Expected shape: {"object":"list","data":[...] } or {"data": [...]} or [...]
    raw_models: list[dict[str, Any]] = []
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            raw_models = [m for m in data if isinstance(m, dict)]
        elif isinstance(data, dict):
            raw_models = [data]
        else:
            # If dict but no data key, treat as malformed — let caller handle
            raw_models = []
    elif isinstance(body, list):
        raw_models = [m for m in body if isinstance(m, dict)]
    else:
        raw_models = []

    if body is not None and not isinstance(body, (dict, list)):
        raise ValueError("Malformed upstream model response: expected object or list")

    # Distinguish empty list (valid) vs malformed dict without data
    _is_malformed = (
        isinstance(body, dict) and "data" not in body and raw_models == [] and bool(body)
    )
    if _is_malformed:
        assert isinstance(body, dict)
        if "error" in body:
            raise ValueError(f"Upstream error envelope: {body.get('error')}")
        if "object" not in body:
            raise ValueError(f"Malformed model response missing 'data': {body}")

    registry.refresh(raw_models)
    return raw_models
