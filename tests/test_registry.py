"""Registry unit tests — M1 requirement 4."""

from __future__ import annotations

from app.models.registry import ModelRegistry


def test_registry_list_and_get() -> None:
    r = ModelRegistry()
    r.refresh([{"id": "model-a", "object": "model"}, {"id": "model-b"}])
    assert r.is_available("model-a")
    assert r.get_model("model-a") is not None
    assert len(r.list_models()) == 2


def test_registry_refresh_drops_missing() -> None:
    r = ModelRegistry()
    r.refresh([{"id": "a"}, {"id": "b"}])
    r.refresh([{"id": "a"}])
    assert r.is_available("a")
    assert not r.is_available("b")


def test_registry_preserves_raw() -> None:
    r = ModelRegistry()
    r.refresh(
        [
            {
                "id": "m1",
                "object": "model",
                "created": 123,
                "owned_by": "cleanapis",
                "pricing": {"input": 1},
            }
        ]
    )
    meta = r.get_model("m1")
    assert meta is not None
    assert meta.raw["pricing"] == {"input": 1}
    assert meta.pricing == {"input": 1}


def test_registry_unknown_fields_stay_null() -> None:
    r = ModelRegistry()
    r.refresh([{"id": "m1"}])
    meta = r.get_model("m1")
    assert meta is not None
    assert meta.context_length is None
    assert meta.pricing is None


def test_registry_to_openai_list() -> None:
    r = ModelRegistry()
    r.refresh(
        [{"id": "claude-opus-4.8", "object": "model", "created": 999, "owned_by": "cleanapis"}]
    )
    out = r.to_openai_list()
    assert out["object"] == "list"
    assert out["data"][0]["id"] == "claude-opus-4.8"
    assert out["data"][0]["created"] == 999
