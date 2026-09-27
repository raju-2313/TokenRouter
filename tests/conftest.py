"""Shared fixtures — mocked upstream, no real keys, isolated SQLite state."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.api.deps import get_registry, get_upstream_client
from app.config.settings import AccountConfig, AppConfig, ServerConfig


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests never use real config files or .env."""
    monkeypatch.setenv("CLEAN_API_KEY_1", "clean_test_1234567890")
    monkeypatch.delenv("TOKENROUTER_CONFIG", raising=False)


@pytest.fixture(autouse=True)
def clear_registry() -> None:
    from app.api.deps import reset_registry

    reset_registry()
    yield
    reset_registry()


@pytest.fixture(autouse=True)
async def isolated_accounting(tmp_path_factory, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Give every test its own SQLite database and drop cached runtime state.

    Keeps local token accounting (Milestone 2) from leaking between tests and
    guarantees a restart always starts from a clean ledger.
    """
    from app.api.deps import reset_runtime

    db_path = tmp_path_factory.mktemp("accounting") / "tokenrouter.db"
    monkeypatch.setenv("TOKENROUTER_DB_PATH", str(db_path))
    await reset_runtime()
    yield db_path
    await reset_runtime()


def make_mock_transport(
    routes: dict[tuple[str, str], tuple[int, dict[str, str], Any]],
) -> httpx.MockTransport:
    """Create MockTransport from (method, path) -> (status, headers, json_body)."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method.upper(), request.url.path)
        if key in routes:
            status, headers, body = routes[key]
            content = json.dumps(body).encode() if body is not None else b""
            return httpx.Response(
                status_code=status, headers=headers, content=content, request=request
            )
        # Default 404 for unknown routes
        return httpx.Response(status_code=404, json={"error": "not found"}, request=request)

    return httpx.MockTransport(handler)


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig(
        server=ServerConfig(
            host="127.0.0.1",
            port=8787,
            upstream_base_url="https://cleanapis.com/v1",
            request_timeout_seconds=10,
        ),
        accounts=[
            AccountConfig(
                name="primary",
                api_key_env="CLEAN_API_KEY_1",
                enabled=True,
                monthly_allowance=None,
                initial_usage_tokens=0,
            )
        ],
    )


def patch_upstream(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Patch get_upstream_client to use mocked transport and dummy key."""

    def fake_get_upstream_client(config: Any | None = None, transport_arg: Any | None = None):  # type: ignore[no-untyped-def]
        from app.upstream.client import CleanApisClient

        # Use test key that we can verify never leaks
        return CleanApisClient(
            base_url="https://cleanapis.com/v1",
            api_key="clean_test_1234567890",
            timeout_seconds=10,
            transport=transport,
        )

    import app.api.routes as routes_mod

    monkeypatch.setattr(routes_mod, "get_upstream_client", fake_get_upstream_client)
    # Also patch deps module for discovery path
    import app.api.deps as deps_mod

    monkeypatch.setattr(deps_mod, "get_upstream_client", fake_get_upstream_client)
    # Also need to patch discovery's client creation — routes does discovery via get_upstream_client
    _ = get_registry
    _ = get_upstream_client
