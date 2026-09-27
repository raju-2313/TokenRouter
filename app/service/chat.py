"""Service layer — orchestrates routing, reservation and upstream dispatch.

Dependency direction:

    API route -> ChatService -> RoutingEngine -> BudgetManager -> CleanApisClient

The route never selects an account, never touches SQLite and never calls the
upstream client directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.budget.accounts import AccountSnapshot, TokenUsage
from app.budget.errors import BudgetError, InsufficientBudgetError, no_eligible_account
from app.budget.estimator import TokenEstimator
from app.budget.manager import BudgetManager
from app.config.settings import AppConfig
from app.core.obs import log_event
from app.models.discovery import discover_models
from app.models.registry import ModelRegistry
from app.routing.engine import RoutingEngine
from app.routing.strategy import RoutingContext
from app.upstream.errors import (
    INVALID_REQUEST,
    MODEL_NOT_AVAILABLE,
    UPSTREAM_AUTH_ERROR,
    UPSTREAM_PAYMENT_REQUIRED,
    UPSTREAM_RATE_LIMITED,
    UpstreamError,
)
from app.upstream.factory import ClientFactory

# Upstream failures that are safe to retry on another account while preserving
# the exact model the client requested. Malformed requests and unknown models
# are never retried.
FALLBACK_CODES = frozenset(
    {
        UPSTREAM_AUTH_ERROR,
        UPSTREAM_PAYMENT_REQUIRED,
        UPSTREAM_RATE_LIMITED,
        "UPSTREAM_UNAVAILABLE",
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_ERROR",
    }
)
NON_FALLBACK_CODES = frozenset({INVALID_REQUEST, MODEL_NOT_AVAILABLE})


@dataclass
class ChatOutcome:
    """Result of one chat request, ready to be serialized by the route."""

    status_code: int
    body: Any
    request_id: str
    account_id: str | None = None
    error: UpstreamError | BudgetError | None = None
    latency_ms: int = 0
    usage: TokenUsage | None = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200


class ChatService:
    """Estimate -> route -> reserve -> dispatch -> reconcile."""

    def __init__(
        self,
        *,
        config: AppConfig,
        budget: BudgetManager,
        engine: RoutingEngine,
        clients: ClientFactory,
        registry: ModelRegistry,
        estimator: TokenEstimator,
    ) -> None:
        self._config = config
        self._budget = budget
        self._engine = engine
        self._clients = clients
        self._registry = registry
        self._estimator = estimator

    # ------------------------------------------------------------------
    # public entrypoint
    # ------------------------------------------------------------------

    async def handle_chat(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        model: str,
        pinned_account_id: str | None = None,
    ) -> ChatOutcome:
        started = time.perf_counter()
        try:
            return await self._handle(
                request_id=request_id,
                payload=payload,
                model=model,
                started=started,
                pinned_account_id=pinned_account_id,
            )
        except BudgetError as exc:
            self._log(
                "chat_completion",
                request_id=request_id,
                model=model,
                http_status=exc.status_code,
                duration_ms=self._elapsed(started),
                routing_strategy=self._engine.strategy_name,
                error_code=exc.code,
                outcome="budget_error",
            )
            return ChatOutcome(
                status_code=exc.status_code,
                body=exc.to_dict(),
                request_id=request_id,
                error=exc,
                latency_ms=self._elapsed(started),
            )

    async def _handle(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        model: str,
        started: float,
        pinned_account_id: str | None = None,
    ) -> ChatOutcome:
        # 1. Model availability (M1 behaviour preserved: request is never
        #    silently substituted).
        try:
            await self._ensure_catalogue()
        except UpstreamError as exc:
            return self._upstream_failure_outcome(exc, request_id=request_id, started=started)
        if not self._registry.is_available(model):
            return self._model_unavailable(request_id=request_id, model=model, started=started)

        # 2. Estimate the maximum token requirement (reservation only).
        estimate = self._estimator.estimate(payload)
        required = estimate.total_tokens

        # 3. Snapshots + per-account model awareness (no routing logic here).
        snapshots = await self._budget.snapshots()
        await self._refresh_model_availability(snapshots, model=model)
        snapshots = await self._budget.snapshots()

        # 4. Rank eligible accounts with the configured strategy.
        context = RoutingContext(required_tokens=required, model=model, now=time.time())
        if pinned_account_id is not None:
            # An explicitly requested account is never silently replaced.
            snapshots = [s for s in snapshots if s.account_id == pinned_account_id]
            if not snapshots:
                error = BudgetError(
                    code=INVALID_REQUEST,
                    message=f"Unknown account '{pinned_account_id}'",
                    status_code=400,
                    account_id=pinned_account_id,
                )
                return ChatOutcome(
                    status_code=error.status_code,
                    body=error.to_dict(),
                    request_id=request_id,
                    error=error,
                    latency_ms=self._elapsed(started),
                )
        ranked = self._engine.order(snapshots, context)
        self._log(
            "routing_decision",
            request_id=request_id,
            model=model,
            routing_strategy=self._engine.strategy_name,
            estimated_prompt_tokens=estimate.prompt_tokens,
            estimated_completion_tokens=estimate.completion_tokens,
            estimate_total=required,
            eligible=len(ranked),
            account_id=ranked[0].account_id if ranked else None,
        )
        if not ranked:
            if pinned_account_id is not None:
                error = no_eligible_account(
                    required_tokens=required,
                    model=model,
                    reason=(
                        f"Pinned account '{pinned_account_id}' is not eligible for this request"
                    ),
                )
                return ChatOutcome(
                    status_code=error.status_code,
                    body=error.to_dict(),
                    request_id=request_id,
                    error=error,
                    latency_ms=self._elapsed(started),
                )
            if self._model_known_unavailable(snapshots, model=model):
                return self._model_unavailable(request_id=request_id, model=model, started=started)
            error = no_eligible_account(required_tokens=required, model=model)
            return ChatOutcome(
                status_code=error.status_code,
                body=error.to_dict(),
                request_id=request_id,
                error=error,
                latency_ms=self._elapsed(started),
            )

        # 5. Bounded attempt loop across eligible accounts.
        max_attempts = max(int(self._config.routing.max_fallback_attempts), 1)
        first_error: UpstreamError | None = None
        attempts = 0

        for account in ranked:
            if attempts >= max_attempts:
                break
            # 5a. Atomic reservation.
            try:
                reservation = await self._budget.reserve(
                    account.account_id,
                    required,
                    request_id=request_id,
                    model=model,
                )
            except InsufficientBudgetError as exc:
                self._log(
                    "reservation_denied",
                    request_id=request_id,
                    model=model,
                    account_id=account.account_id,
                    reserved_tokens=required,
                    error_code=exc.code,
                )
                continue
            attempts += 1

            # 5b. Dispatch — the requested model is forwarded unchanged.
            try:
                client = self._clients.client_for(account.account_id)
                response = await client.chat_completion(payload)
            except UpstreamError as exc:
                await self._budget.release(reservation)
                await self._apply_failure_policy(account, exc)
                if first_error is None:
                    first_error = exc
                self._log(
                    "chat_completion",
                    request_id=request_id,
                    model=model,
                    account_id=account.account_id,
                    routing_strategy=self._engine.strategy_name,
                    reserved_tokens=required,
                    http_status=exc.status_code,
                    upstream_status=exc.upstream_status,
                    duration_ms=self._elapsed(started),
                    error_code=exc.code,
                    attempt=attempts,
                    outcome="failed",
                )
                if exc.code in NON_FALLBACK_CODES or exc.code not in FALLBACK_CODES:
                    break
                continue
            except Exception:  # noqa: BLE001 - never leak upstream internals
                await self._budget.release(reservation)
                if first_error is None:
                    first_error = UpstreamError(
                        code="UPSTREAM_ERROR",
                        message="Upstream error",
                        status_code=502,
                    )
                continue

            # 5c. Reconcile: actual usage replaces the estimate.
            usage = TokenUsage.from_upstream(
                response.body.get("usage") if isinstance(response.body, dict) else None
            )
            snapshot = await self._budget.reconcile(reservation, usage)
            await self._budget.record_rate_limit(account.account_id, response.rate_limit)
            self._log(
                "chat_completion",
                request_id=request_id,
                model=model,
                account_id=account.account_id,
                routing_strategy=self._engine.strategy_name,
                reserved_tokens=required,
                input_tokens=usage.prompt_tokens if usage is not None else None,
                output_tokens=usage.completion_tokens if usage is not None else None,
                total_tokens=usage.total_tokens if usage is not None else None,
                usage_source=usage.source if usage is not None else "unreported",
                estimated_remaining=(
                    snapshot.estimated_remaining if snapshot is not None else None
                ),
                http_status=200,
                upstream_status=response.status_code,
                duration_ms=self._elapsed(started),
                latency_ms=response.latency_ms,
                attempt=attempts,
                outcome="ok",
            )
            return ChatOutcome(
                status_code=200,
                body=response.body,
                request_id=request_id,
                account_id=account.account_id,
                latency_ms=response.latency_ms,
                usage=usage,
            )

        if first_error is not None:
            return self._upstream_failure_outcome(
                first_error,
                request_id=request_id,
                started=started,
                account_id=ranked[0].account_id,
            )

        # Every candidate account lacked budget for this request.
        error = no_eligible_account(
            required_tokens=required,
            model=model,
            reason="No configured account has sufficient estimated remaining monthly budget",
        )
        return ChatOutcome(
            status_code=error.status_code,
            body=error.to_dict(),
            request_id=request_id,
            error=error,
            latency_ms=self._elapsed(started),
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _ensure_catalogue(self) -> None:
        if self._registry.list_models():
            return
        client = self._clients.primary_client()
        await discover_models(client, self._registry)

    async def _refresh_model_availability(
        self, snapshots: list[AccountSnapshot], *, model: str
    ) -> None:
        """Learn which accounts actually serve ``model`` (lazy, TTL-cached).

        Discovery failures leave availability unknown rather than blocking the
        request — the model list is per account, never assumed identical.
        """
        ttl = max(int(self._config.routing.models_cache_ttl_seconds), 0)
        now = time.time()
        for snapshot in snapshots:
            if not snapshot.enabled or not snapshot.has_api_key:
                continue
            if snapshot.model_ids is not None and model in snapshot.model_ids:
                continue
            refreshed_at = snapshot.models_refreshed_at
            fresh = refreshed_at is not None and (now - refreshed_at) < ttl
            if fresh and snapshot.model_ids is not None:
                continue
            try:
                client = self._clients.client_for(snapshot.account_id)
                response = await client.list_models()
            except UpstreamError:
                continue
            except Exception:  # noqa: BLE001
                continue
            model_ids = self._extract_model_ids(response.body)
            if model_ids is None:
                await self._budget.mark_models_unknown(snapshot.account_id)
            else:
                await self._budget.replace_models(snapshot.account_id, model_ids)

    def _model_known_unavailable(self, snapshots: list[AccountSnapshot], *, model: str) -> bool:
        """True when every account has a known model list that excludes ``model``."""
        if not snapshots:
            return False
        return all(
            snapshot.model_ids is not None and not snapshot.serves_model(model)
            for snapshot in snapshots
        )

    def _extract_model_ids(self, body: Any) -> list[str] | None:
        if isinstance(body, dict):
            data = body.get("data")
        elif isinstance(body, list):
            data = body
        else:
            return None
        if not isinstance(data, list):
            return None
        ids: list[str] = []
        for entry in data:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                ids.append(entry["id"])
        return ids

    async def _apply_failure_policy(self, account: AccountSnapshot, exc: UpstreamError) -> None:
        """Exclusion windows are per-account and never touch monthly accounting."""
        window = max(float(self._config.routing.account_exclusion_seconds), 0.0)
        if window <= 0:
            return
        if exc.code == UPSTREAM_AUTH_ERROR:
            # The credential itself failed — no local budget is deducted.
            await self._budget.exclude_account(account.account_id, reason=exc.code, seconds=window)
        elif exc.code == UPSTREAM_PAYMENT_REQUIRED:
            await self._budget.exclude_account(account.account_id, reason=exc.code, seconds=window)
        elif exc.code == UPSTREAM_RATE_LIMITED:
            delay = float(exc.retry_after) if exc.retry_after else min(window, 60.0)
            await self._budget.exclude_account(
                account.account_id,
                reason=exc.code,
                seconds=min(delay, window) if window else delay,
            )

    def _upstream_failure_outcome(
        self,
        exc: UpstreamError,
        *,
        request_id: str,
        started: float,
        account_id: str | None = None,
    ) -> ChatOutcome:
        return ChatOutcome(
            status_code=exc.status_code,
            body=exc.to_dict(),
            request_id=request_id,
            account_id=account_id,
            error=exc,
            latency_ms=self._elapsed(started),
        )

    def _model_unavailable(self, *, request_id: str, model: str, started: float) -> ChatOutcome:
        error = UpstreamError(
            code=MODEL_NOT_AVAILABLE,
            message=f"Model '{model}' is not available",
            status_code=400,
        )
        self._log(
            "chat_completion",
            request_id=request_id,
            model=model,
            http_status=400,
            duration_ms=self._elapsed(started),
            error_code=MODEL_NOT_AVAILABLE,
            outcome="model_unavailable",
        )
        return ChatOutcome(
            status_code=400,
            body=error.to_dict(),
            request_id=request_id,
            error=error,
            latency_ms=self._elapsed(started),
        )

    @staticmethod
    def _elapsed(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _log(self, event: str, **fields: Any) -> None:
        log_event(event, **fields)
