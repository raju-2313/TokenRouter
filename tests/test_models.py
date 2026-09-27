"""Model discovery + GET /v1/models — items 1,2,3."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_mock_transport, patch_upstream


def test_successful_model_discovery(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    transport = make_mock_transport(
        {
            ("GET", "/v1/models"): (
                200,
                {},
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "claude-opus-4.8",
                            "object": "model",
                            "created": 1710000000,
                            "owned_by": "cleanapis",
                        },
                        {"id": "gpt-4o", "object": "model", "owned_by": "cleanapis"},
                    ],
                },
            )
        }
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    ids = {m["id"] for m in data["data"]}
    assert "claude-opus-4.8" in ids
    assert "gpt-4o" in ids
    # Preserve owned_by / created
    opus = next(m for m in data["data"] if m["id"] == "claude-opus-4.8")
    assert opus["owned_by"] == "cleanapis"
    assert opus["created"] == 1710000000


def test_empty_model_list(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    transport = make_mock_transport(
        {
            ("GET", "/v1/models"): (200, {}, {"object": "list", "data": []}),
        }
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_malformed_upstream_model_response(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    # Upstream returns dict without data/object — should be treated as malformed -> 502
    transport = make_mock_transport(
        {
            ("GET", "/v1/models"): (200, {}, {"weird": "shape"}),
        }
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.get("/v1/models")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "UPSTREAM_ERROR"
