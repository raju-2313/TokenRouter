"""Upstream client factory.

The factory is the only place that turns an ``account_id`` into a
``CleanApisClient`` carrying that account's API key. It resolves the key from
the environment at call time, so keys never enter account state, the database,
logs or responses. The factory does not know about budgets or routing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from app.upstream.client import CleanApisClient


@runtime_checkable
class ClientFactory(Protocol):
    """Builds an upstream client for a specific configured account."""

    def client_for(self, account_id: str) -> CleanApisClient: ...

    def primary_client(self) -> CleanApisClient: ...


def build_client(
    *,
    base_url: str,
    api_key: str | None,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CleanApisClient:
    return CleanApisClient(
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )
