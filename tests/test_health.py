"""Milestone 0 — bootstrap smoke tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_root_returns_name() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["name"] == "TokenRouter"
