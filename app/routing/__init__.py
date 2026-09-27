"""Routing package — account selection strategies (Milestone 2)."""

from __future__ import annotations

from app.routing.engine import RoutingEngine, available_strategies, get_strategy
from app.routing.strategy import (
    MAX_REMAINING,
    MaxRemainingStrategy,
    RoutingContext,
    RoutingStrategy,
    is_eligible,
)

__all__ = [
    "MAX_REMAINING",
    "MaxRemainingStrategy",
    "RoutingContext",
    "RoutingEngine",
    "RoutingStrategy",
    "available_strategies",
    "get_strategy",
    "is_eligible",
]
