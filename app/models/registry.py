"""Model registry — internal representation + in-memory store (M1).

No routing decisions. No httpx here. Only discovery populates it via upstream client.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.models.capabilities import infer_capabilities


@dataclass(frozen=True)
class ModelMeta:
    id: str
    capabilities: dict[str, bool | None] = field(default_factory=dict)
    context_length: int | None = None
    pricing: dict[str, Any] | None = None
    discovered_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    raw: dict[str, Any] = field(default_factory=dict)


def normalize_model(raw: dict[str, Any], now: float | None = None) -> ModelMeta:
    """Normalize a single raw model dict from Clean APIs into ModelMeta.

    Never fabricates context_length/pricing/capabilities — keeps them None if absent.
    """
    t = now if now is not None else time.time()
    model_id = str(raw.get("id", "")).strip()
    if not model_id:
        raise ValueError("model missing id")

    # Preserve useful metadata but normalize
    context_length: int | None = None
    for key in ("context_length", "context_window", "max_context_length", "contextLength"):
        if key in raw and isinstance(raw[key], int):
            context_length = raw[key]
            break

    pricing: dict[str, Any] | None = None
    if "pricing" in raw and isinstance(raw["pricing"], dict):
        pricing = raw["pricing"]
    elif "price" in raw and isinstance(raw["price"], dict):
        pricing = raw["price"]

    caps = infer_capabilities(raw)

    return ModelMeta(
        id=model_id,
        capabilities=caps,
        context_length=context_length,
        pricing=pricing,
        discovered_at=t,
        last_seen_at=t,
        raw=raw,
    )


class ModelRegistry:
    """In-memory registry. Caching-ready; API layer does not need to change."""

    def __init__(self) -> None:
        self._models: dict[str, ModelMeta] = {}

    # ---- public API required by spec ----

    def list_models(self) -> list[ModelMeta]:
        return list(self._models.values())

    def get_model(self, model_id: str) -> ModelMeta | None:
        return self._models.get(model_id)

    def is_available(self, model_id: str) -> bool:
        return model_id in self._models

    def refresh(self, raw_models: list[dict[str, Any]]) -> None:
        """Replace/merge with freshly discovered raw models.

        Keeps discovered_at for existing models, updates last_seen_at.
        Removes models not seen anymore? No — we keep them but could mark unavailable.
        For M1, we add/update and drop models not returned (they disappeared).
        """
        now = time.time()
        new: dict[str, ModelMeta] = {}
        for raw in raw_models:
            try:
                meta = normalize_model(raw, now=now)
            except ValueError:
                continue
            # Preserve discovered_at if already known
            existing = self._models.get(meta.id)
            if existing is not None:
                meta = ModelMeta(
                    id=meta.id,
                    capabilities=meta.capabilities,
                    context_length=meta.context_length,
                    pricing=meta.pricing,
                    discovered_at=existing.discovered_at,
                    last_seen_at=now,
                    raw=meta.raw,
                )
            new[meta.id] = meta
        self._models = new

    def upsert_raw(self, raw: dict[str, Any]) -> ModelMeta:
        """Helper for single-model upserts (used in discovery)."""
        meta = normalize_model(raw)
        existing = self._models.get(meta.id)
        if existing:
            meta = ModelMeta(
                id=meta.id,
                capabilities=meta.capabilities,
                context_length=meta.context_length,
                pricing=meta.pricing,
                discovered_at=existing.discovered_at,
                last_seen_at=meta.last_seen_at,
                raw=meta.raw,
            )
        self._models[meta.id] = meta
        return meta

    def to_openai_list(self) -> dict[str, Any]:
        """Return OpenAI-compatible /v1/models response."""
        data = []
        for m in self._models.values():
            raw = m.raw
            # Preserve upstream fields if present, else synthesize minimal object
            entry: dict[str, Any] = {
                "id": m.id,
                "object": raw.get("object", "model"),
                "created": raw.get("created"),
                "owned_by": raw.get("owned_by", "cleanapis"),
            }
            # Remove None created for cleanliness — but keep if provided
            if entry["created"] is None:
                entry.pop("created")
            data.append(entry)
        return {"object": "list", "data": data}
