"""Routing engine — picks an account, performs no I/O."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.budget.accounts import AccountSnapshot
from app.routing.strategy import (
    MAX_REMAINING,
    MaxRemainingStrategy,
    RoutingContext,
    RoutingStrategy,
    filter_eligible,
)


class RoutingEngine:
    """Applies eligibility rules then delegates ranking to a strategy.

    The engine receives snapshots (no SQL, no HTTP) so it stays a pure selection
    component. Reserving tokens remains the budget manager's job.
    """

    def __init__(self, strategy: RoutingStrategy | None = None) -> None:
        self._strategy = strategy or MaxRemainingStrategy()

    @property
    def strategy(self) -> RoutingStrategy:
        return self._strategy

    @property
    def strategy_name(self) -> str:
        return self._strategy.name

    def order(
        self, candidates: Sequence[AccountSnapshot], context: RoutingContext
    ) -> list[AccountSnapshot]:
        """Eligible candidates, best first."""
        return self._strategy.order(filter_eligible(candidates, context), context)

    def select(
        self, candidates: Sequence[AccountSnapshot], context: RoutingContext
    ) -> AccountSnapshot | None:
        ranked = self.order(candidates, context)
        return ranked[0] if ranked else None


_STRATEGIES: dict[str, type[RoutingStrategy]] = {
    MAX_REMAINING: MaxRemainingStrategy,
}


def get_strategy(name: str | None) -> RoutingStrategy:
    """Resolve a configured strategy name.

    Only ``max_remaining`` exists in M2. ``balanced`` / ``cheapest`` / ``quality``
    are intentionally not implemented yet.
    """
    key = (name or MAX_REMAINING).strip().lower()
    factory = _STRATEGIES.get(key)
    if factory is None:
        supported: Any = ", ".join(sorted(_STRATEGIES))
        raise ValueError(f"Unknown routing strategy '{name}'. Supported: {supported}")
    return factory()


def available_strategies() -> list[str]:
    return sorted(_STRATEGIES)
