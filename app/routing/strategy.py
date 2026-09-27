"""Routing abstraction.

The routing engine selects *which configured account* serves a request. It owns
no HTTP and no SQL: it ranks the read-only account snapshots handed to it by the
budget manager. Model selection is a separate concern and is never altered here.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from app.budget.accounts import AccountSnapshot

MAX_REMAINING = "max_remaining"


@dataclass(frozen=True)
class RoutingContext:
    """What a strategy needs to rank accounts."""

    required_tokens: int
    model: str | None = None
    now: float = 0.0

    def timestamp(self) -> float:
        return self.now or time.time()


class RoutingStrategy(ABC):
    """Orders eligible accounts. Additional strategies plug in here later."""

    name: str = "abstract"

    @abstractmethod
    def order(
        self, candidates: Sequence[AccountSnapshot], context: RoutingContext
    ) -> list[AccountSnapshot]:
        """Return candidates ranked best-first. Must not mutate inputs."""
        raise NotImplementedError


def is_eligible(snapshot: AccountSnapshot, context: RoutingContext) -> bool:
    """Shared eligibility rules, independent of strategy.

    An account is eligible when it is enabled, has a configured API key, is not
    inside an active exclusion window, is known to serve the requested model,
    and — when its monthly allowance is known — still has enough estimated
    remaining budget for the reservation.
    """
    if not snapshot.enabled:
        return False
    if not snapshot.has_api_key:
        return False
    if snapshot.is_excluded(context.timestamp()):
        return False
    if not snapshot.serves_model(context.model):
        return False
    available = snapshot.available_tokens
    return not (available is not None and available < context.required_tokens)


def filter_eligible(
    candidates: Sequence[AccountSnapshot], context: RoutingContext
) -> list[AccountSnapshot]:
    return [c for c in candidates if is_eligible(c, context)]


class MaxRemainingStrategy(RoutingStrategy):
    """Select the eligible account with the highest estimated remaining budget.

    Accounts with an unknown monthly allowance cannot be compared numerically;
    they are ranked after accounts with a known, larger remaining budget so that
    a bounded budget is never drained by an account of unknown capacity.
    """

    name = MAX_REMAINING

    def order(
        self, candidates: Sequence[AccountSnapshot], context: RoutingContext
    ) -> list[AccountSnapshot]:
        known: list[AccountSnapshot] = []
        unknown: list[AccountSnapshot] = []
        for snapshot in candidates:
            remaining = snapshot.estimated_remaining
            if remaining is None:
                unknown.append(snapshot)
            else:
                known.append(snapshot)
        known.sort(key=lambda s: (-(s.estimated_remaining or 0), s.account_id))
        unknown.sort(key=lambda s: s.account_id)
        return known + unknown
