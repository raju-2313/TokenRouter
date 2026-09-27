"""Chat completions — non-streaming, items 4-10 (part 1)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import patch_upstream


def _chat_transport(
    *,
    models: list[dict] | None = None,
    chat_status: int = 200,
    chat_headers: dict[str, str] | None = None,
    chat_body: dict | None = None,
) -> httpx.MockTransport:
    if models is None:
        models = [{"id": "claude-opus-4.8", "object": "model", "owned_by": "cleanapis"}]
    if chat_body is None:
        chat_body = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(
                status_code=200, json={"object": "list", "data": models}, request=request
            )
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            hdrs = chat_headers or {}
            return httpx.Response(
                status_code=chat_status, headers=hdrs, json=chat_body, request=request
            )
        return httpx.Response(status_code=404, json={"error": "not found"}, request=request)

    return httpx.MockTransport(handler)


def test_successful_non_streaming_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _chat_transport()
    patch_upstream(monkeypatch, transport)
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-opus-4.8", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "Hello"
    assert resp.headers.get("x-request-id", "").startswith("req_")


def test_requested_model_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
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
                        {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
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


def test_tools_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    tools = [{"type": "function", "function": {"name": "get_weather", "description": "x"}}]

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
        json={
            "model": "claude-opus-4.8",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": tools,
        },
    )
    assert resp.status_code == 200
    assert captured["body"]["tools"] == tools


def test_tool_choice_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
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
                json={"id": "x", "object": "chat.completion", "choices": []},
                request=request,
            )
        return httpx.Response(status_code=404, json={}, request=request)

    patch_upstream(monkeypatch, httpx.MockTransport(handler))
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "claude-opus-4.8",
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        },
    )
    assert resp.status_code == 200
    assert captured["body"]["tool_choice"] == {
        "type": "function",
        "function": {"name": "get_weather"},
    }
