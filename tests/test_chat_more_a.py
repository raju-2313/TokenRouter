"""Chat completions — items 8-14 (more)."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import patch_upstream
from tests.test_chat_completions import _chat_transport


def test_usage_is_extracted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = _chat_transport(
        chat_body={
            "id": "x",
            "object": "chat.completion",
            "choices": [
                {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-opus-4.8", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    out = capsys.readouterr().out
    assert '"total_tokens": 15' in out


def test_missing_usage_is_handled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = _chat_transport(
        chat_body={
            "id": "x",
            "object": "chat.completion",
            "choices": [
                {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
            ],
        }
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-opus-4.8", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    out = capsys.readouterr().out
    assert '"input_tokens": null' in out


def _error_case(monkeypatch: pytest.MonkeyPatch, status: int, expected_code: str) -> None:
    transport = _chat_transport(
        chat_status=status, chat_body={"error": {"message": "upstream says no"}}
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-opus-4.8", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.json()["error"]["code"] == expected_code


def test_400_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _error_case(monkeypatch, 400, "INVALID_REQUEST")


def test_401_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _error_case(monkeypatch, 401, "UPSTREAM_AUTH_ERROR")


def test_402_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _error_case(monkeypatch, 402, "UPSTREAM_PAYMENT_REQUIRED")


def test_429_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _error_case(monkeypatch, 429, "UPSTREAM_RATE_LIMITED")


def test_500_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _error_case(monkeypatch, 500, "UPSTREAM_UNAVAILABLE")


def test_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(
                status_code=200,
                json={"object": "list", "data": [{"id": "claude-opus-4.8", "object": "model"}]},
                request=request,
            )
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(status_code=404, json={}, request=request)

    patch_upstream(monkeypatch, httpx.MockTransport(handler))
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-opus-4.8", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "UPSTREAM_TIMEOUT"
