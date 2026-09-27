"""Shared helpers for Milestone 2 tests — mocked Clean APIs, no real keys."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.upstream.client import CleanApisClient

KEY_A = "clean_test_key_a_0001"
KEY_B = "clean_test_key_b_0002"
KEY_C = "clean_test_key_c_0003"

DEFAULT_CHAT_BODY: dict[str, Any] = {
    "id": "chatcmpl-m2",
    "object": "chat.completion",
    "choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


@dataclass
class AccountBehavior:
    """What the fake upstream should answer for one account's key."""

    key: str
    models: list[str] = field(default_factory=lambda: ["claude-opus-4.8", "gpt-4o"])
    models_status: int = 200
    chat_status: int = 200
    chat_body: dict[str, Any] | None = None
    chat_headers: dict[str, str] = field(default_factory=dict)


class FakeUpstream:
    """MockTransport that answers per API key and records every request."""

    def __init__(self, accounts: list[AccountBehavior]) -> None:
        self.accounts = {a.key: a for a in accounts}
        self.requests: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            authorization = request.headers.get("authorization", "")
            key = authorization.removeprefix("Bearer ")
            record: dict[str, Any] = {
                "method": request.method.upper(),
                "path": request.url.path,
                "key": key,
            }
            if request.method.upper() == "POST" and request.content:
                record["body"] = json.loads(request.content.decode())
            self.requests.append(record)

            behavior = self.accounts.get(key)
            if behavior is None:
                return httpx.Response(
                    status_code=401,
                    json={"error": {"message": "invalid key"}},
                    request=request,
                )
            if request.method.upper() == "GET" and request.url.path.endswith("/models"):
                if behavior.models_status != 200:
                    return httpx.Response(
                        status_code=behavior.models_status,
                        json={"error": {"message": "models unavailable"}},
                        request=request,
                    )
                return httpx.Response(
                    status_code=200,
                    json={
                        "object": "list",
                        "data": [
                            {"id": model_id, "object": "model", "owned_by": "cleanapis"}
                            for model_id in behavior.models
                        ],
                    },
                    request=request,
                )
            if request.method.upper() == "POST" and request.url.path.endswith("/chat/completions"):
                body = behavior.chat_body if behavior.chat_body is not None else DEFAULT_CHAT_BODY
                return httpx.Response(
                    status_code=behavior.chat_status,
                    headers=dict(behavior.chat_headers),
                    json=body,
                    request=request,
                )
            return httpx.Response(status_code=404, json={"error": "not found"}, request=request)

        return httpx.MockTransport(handler)

    # -- assertions helpers --

    def chat_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"].endswith("/chat/completions")]

    def chat_keys(self) -> list[str]:
        return [r["key"] for r in self.chat_requests()]

    def last_chat_body(self) -> dict[str, Any]:
        return self.chat_requests()[-1]["body"]


def patch_account_clients(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Patch the upstream client factory so every account uses the mock.

    Keeps the ``(config, transport_arg, *, account)`` shape of the real factory
    so account-aware resolution still happens (and keys still come from env).
    """

    def fake_get_upstream_client(  # type: ignore[no-untyped-def]
        config: Any | None = None,
        transport_arg: Any | None = None,
        *,
        account: Any | None = None,
    ) -> CleanApisClient:
        import app.api.deps as deps_mod

        cfg = config if config is not None else deps_mod.get_config()
        api_key = account.resolve_api_key() if account is not None else cfg.get_api_key()
        return CleanApisClient(
            base_url=cfg.server.upstream_base_url,
            api_key=api_key,
            timeout_seconds=10,
            transport=transport,
        )

    import app.api.deps as deps_mod
    import app.api.routes as routes_mod

    monkeypatch.setattr(deps_mod, "get_upstream_client", fake_get_upstream_client)
    monkeypatch.setattr(routes_mod, "get_upstream_client", fake_get_upstream_client)


@dataclass
class AccountSpec:
    """Config entry for one test account."""

    name: str
    api_key_env: str
    allowance: int | None
    initial_usage: int = 0
    enabled: bool = True
    account_id: str | None = None

    def to_yaml(self) -> str:
        allowance = "null" if self.allowance is None else str(self.allowance)
        account_id = self.account_id or self.name
        return (
            f"  - name: {self.name}\n"
            f"    account_id: {account_id}\n"
            f"    api_key_env: {self.api_key_env}\n"
            f"    enabled: {'true' if self.enabled else 'false'}\n"
            f"    monthly_allowance: {allowance}\n"
            f"    initial_usage_tokens: {self.initial_usage}\n"
        )


def write_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    accounts: list[AccountSpec],
    *,
    routing: str = "max_remaining",
    max_fallback_attempts: int = 2,
    extra: str = "",
) -> Path:
    """Write a temporary config.yaml and point TOKENROUTER_CONFIG at it."""
    body = (
        "server:\n"
        "  host: 127.0.0.1\n"
        "  port: 8787\n"
        "  upstream_base_url: https://cleanapis.com/v1\n"
        "  request_timeout_seconds: 10\n"
        "routing:\n"
        f"  strategy: {routing}\n"
        f"  max_fallback_attempts: {max_fallback_attempts}\n"
        "  models_cache_ttl_seconds: 0\n"
        "  account_exclusion_seconds: 900\n"
        "accounts:\n" + "".join(spec.to_yaml() for spec in accounts) + extra
    )
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("TOKENROUTER_CONFIG", str(path))
    return path
