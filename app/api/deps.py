"""Dependency wiring — config, registry, upstream client, budget, routing.

Composition happens here so that each layer keeps a single responsibility:

    routes -> ChatService -> RoutingEngine -> BudgetManager -> CleanApisClient
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import httpx

from app.budget.estimator import TokenEstimateConfig, TokenEstimator
from app.budget.manager import BudgetManager
from app.config.settings import AccountConfig, AppConfig, load_config
from app.db.database import MEMORY_PATH, Database
from app.models.registry import ModelRegistry
from app.routing.engine import RoutingEngine, get_strategy
from app.service.chat import ChatService
from app.upstream.client import CleanApisClient
from app.upstream.factory import build_client

# Singleton registry — populated via discovery
_registry = ModelRegistry()

# Runtime singletons (one per database path). Rebuilt when tests reset state.
_budget_manager: BudgetManager | None = None
_budget_db_path: str | None = None


def get_registry() -> ModelRegistry:
    return _registry


def reset_registry() -> None:
    """For tests — clear registry."""
    global _registry
    _registry = ModelRegistry()


def get_config() -> AppConfig:
    # Loaded fresh each call so test env overrides are respected
    return load_config()


def get_upstream_client(
    config: AppConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    account: AccountConfig | None = None,
) -> CleanApisClient:
    """Build a Clean APIs client.

    ``account`` selects which configured account's key to use. Without it the
    primary (first enabled) account is used, preserving M1 behaviour. The key
    is resolved from the environment here and nowhere else.
    """
    cfg = config or get_config()
    target = account or cfg.primary_account()
    api_key = target.resolve_api_key() if target is not None else None
    return build_client(
        base_url=cfg.server.upstream_base_url,
        api_key=api_key,
        timeout_seconds=float(cfg.server.request_timeout_seconds),
        transport=transport,
    )


def _accepts_account(factory: object) -> bool:
    """Whether a client factory honours the ``account`` keyword.

    Test doubles may replace ``get_upstream_client`` with a narrower signature;
    those still work through the ``config``-only path.
    """
    try:
        params = inspect.signature(factory).parameters  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if "account" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class _ConfigClientFactory:
    """Resolves an ``account_id`` to a configured account, then a client."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config

    def _cfg(self) -> AppConfig:
        return self._config or get_config()

    def client_for(self, account_id: str) -> CleanApisClient:
        cfg = self._cfg()
        account = cfg.account_by_id(account_id)
        if account is None:
            raise LookupError(f"Unknown account '{account_id}'")
        factory = get_upstream_client
        if _accepts_account(factory):
            return factory(cfg, account=account)
        return factory(cfg)

    def primary_client(self) -> CleanApisClient:
        """Client for model discovery: first enabled account that has a key."""
        cfg = self._cfg()
        account = (
            next(
                (a for a in cfg.accounts if a.enabled and a.has_api_key()),
                None,
            )
            or cfg.primary_account()
        )
        if account is None:
            return get_upstream_client(cfg)
        factory = get_upstream_client
        if _accepts_account(factory):
            return factory(cfg, account=account)
        return factory(cfg)


def get_client_factory(config: AppConfig | None = None) -> _ConfigClientFactory:
    return _ConfigClientFactory(config)


def get_token_estimator(config: AppConfig | None = None) -> TokenEstimator:
    cfg = config or get_config()
    return TokenEstimator(
        TokenEstimateConfig(
            chars_per_token=cfg.token_estimate.chars_per_token,
            message_overhead_tokens=cfg.token_estimate.message_overhead_tokens,
            default_max_completion_tokens=cfg.token_estimate.default_max_completion_tokens,
            min_reservation_tokens=cfg.token_estimate.min_reservation_tokens,
        )
    )


def get_routing_engine(config: AppConfig | None = None) -> RoutingEngine:
    cfg = config or get_config()
    return RoutingEngine(get_strategy(cfg.routing.strategy))


def get_database(config: AppConfig | None = None) -> Database:
    cfg = config or get_config()
    path = cfg.database.path or MEMORY_PATH
    if path == MEMORY_PATH:
        return Database(MEMORY_PATH)
    return Database(str(Path(path).expanduser()))


async def get_budget_manager(config: AppConfig | None = None) -> BudgetManager:
    """Budget manager bound to the configured SQLite database.

    Completed usage and in-flight reservations are read back from SQLite, so a
    restart never resets accounting.
    """
    global _budget_manager, _budget_db_path
    cfg = config or get_config()
    path = cfg.database.path or MEMORY_PATH
    if _budget_manager is None or _budget_db_path != path:
        _budget_manager = BudgetManager(get_database(cfg))
        _budget_db_path = path
    await _budget_manager.sync_accounts(cfg.definitions())
    return _budget_manager


async def get_chat_service(config: AppConfig | None = None) -> ChatService:
    cfg = config or get_config()
    budget = await get_budget_manager(cfg)
    return ChatService(
        config=cfg,
        budget=budget,
        engine=get_routing_engine(cfg),
        clients=get_client_factory(cfg),
        registry=get_registry(),
        estimator=get_token_estimator(cfg),
    )


async def reset_runtime() -> None:
    """Drop cached runtime state (used by tests)."""
    global _budget_manager, _budget_db_path
    manager, _budget_manager, _budget_db_path = _budget_manager, None, None
    reset_registry()
    if manager is not None:
        try:
            await manager.database.close()
        except Exception:  # noqa: BLE001
            pass


def get_transport_from_env() -> httpx.AsyncBaseTransport | None:
    """Hook for tests — currently unused; tests patch ``get_upstream_client``."""
    _ = os.getenv("CLEAN_APIS_MOCK_TRANSPORT")
    return None
