"""Chat completions — items 16-20 + security + reasoning (part 2b)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_mock_transport, patch_upstream
from tests.test_chat_completions import _chat_transport


def test_rate_limit_headers_parsed_direct() -> None:
    from app.upstream.headers import parse_rate_limit_headers

    h = {"X-RateLimit-Remaining": "5", "X-RateLimit-Limit": "100"}
    state = parse_rate_limit_headers(h)
    assert state.request_remaining == 5
    assert state.request_limit == 100


def test_retry_after_parsed_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _chat_transport(
        chat_status=429, chat_headers={"Retry-After": "120"}, chat_body={"error": "rate limited"}
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-opus-4.8", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "UPSTREAM_RATE_LIMITED"
    assert resp.json()["error"]["retry_after"] == 120


def test_api_key_never_appears_in_logs_or_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "clean_test_1234567890"
    transport = make_mock_transport(
        {
            ("GET", "/v1/models"): (
                200,
                {},
                {"object": "list", "data": [{"id": "claude-opus-4.8", "object": "model"}]},
            ),
            ("POST", "/v1/chat/completions"): (401, {}, {"error": "bad key"}),
        }
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-opus-4.8", "messages": [{"role": "user", "content": "hi"}]},
    )
    body_str = resp.text
    assert secret not in body_str
    assert secret not in capsys.readouterr().out


def test_unknown_requested_model_returns_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = make_mock_transport(
        {
            ("GET", "/v1/models"): (
                200,
                {},
                {"object": "list", "data": [{"id": "claude-opus-4.8", "object": "model"}]},
            )
        }
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "nonexistent-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MODEL_NOT_AVAILABLE"


def test_upstream_response_does_not_expose_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "clean_test_1234567890"
    transport = _chat_transport(
        chat_body={
            "id": "x",
            "object": "chat.completion",
            "choices": [
                {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-opus-4.8", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert secret not in resp.text
    assert secret not in str(resp.json())


def test_reasoning_and_vision_fields_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(
                status_code=200,
                json={"object": "list", "data": [{"id": "claude-opus-4.8", "object": "model"}]},
                request=request,
            )
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(
                status_code=200,
                json={
                    "id": "x",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "hi",
                                "reasoning": "thought",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {},
                },
                request=request,
            )
        return httpx.Response(status_code=404, json={}, request=request)

    patch_upstream(monkeypatch, httpx.MockTransport(handler))
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "claude-opus-4.8",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "https://x"}}],
                }
            ],
            "reasoning": {"effort": "high"},
        },
    )
    assert resp.status_code == 200
    assert captured["body"]["reasoning"] == {"effort": "high"}
    assert captured["body"]["messages"][0]["content"][0]["type"] == "image_url"
    assert resp.json()["choices"][0]["message"]["reasoning"] == "thought"


def test_claude_opus_48_regression_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit regression: claude-opus-4.8 must be sent unchanged."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(
                status_code=200,
                json={"object": "list", "data": [{"id": "claude-opus-4.8", "object": "model"}]},
                request=request,
            )
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(
                status_code=200,
                json={"id": "x", "object": "chat.completion", "choices": [], "usage": {}},
                request=request,
            )
        return httpx.Response(status_code=404, json={}, request=request)

    patch_upstream(monkeypatch, httpx.MockTransport(handler))
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-opus-4.8", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert captured["body"]["model"] == "claude-opus-4.8"
