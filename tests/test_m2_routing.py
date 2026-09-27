"""Routing engine + MAX_REMAINING strategy (M2 items 4, 5)."""

from __future__ import annotations

import time

import pytest

from app.budget.accounts import AccountSnapshot
from app.routing.engine import RoutingEngine, available_strategies, get_strategy
from app.routing.strategy import (
    MAX_REMAINING,
    MaxRemainingStrategy,
    RoutingContext,
    is_eligible,
)

MILLION = 1_000_000


def _account(
    account_id: str,
    *,
    allowance: int | None = 50 * MILLION,
    completed: int = 0,
    reserved: int = 0,
    enabled: bool = True,
    has_key: bool = True,
    excluded_in: float | None = None,
    model_ids: frozenset[str] | None = None,
) -> AccountSnapshot:
    return AccountSnapshot(
        account_id=account_id,
        name=account_id,
        enabled=enabled,
        has_api_key=has_key,
        monthly_allowance=allowance,
        initial_usage_tokens=0,
        completed_tokens=completed,
        reserved_tokens=reserved,
        excluded_until=excluded_in,
        model_ids=model_ids,
    )


def _context(required: int = 1_000, model: str | None = "m1") -> RoutingContext:
    return RoutingContext(required_tokens=required, model=model, now=time.time())


def test_engine_uses_max_remaining_by_default() -> None:
    engine = RoutingEngine()
    assert engine.strategy_name == MAX_REMAINING
    assert isinstance(engine.strategy, MaxRemainingStrategy)


def test_max_remaining_selects_highest_remaining() -> None:
    # Scenario from the M2 brief: A has ~12M left, B has ~31M left.
    a = _account("a", allowance=50 * MILLION, completed=38 * MILLION)
    b = _account("b", allowance=50 * MILLION, completed=19 * MILLION)
    engine = RoutingEngine()
    selected = engine.select([a, b], _context())
    assert selected is not None
    assert selected.account_id == "b"


def test_max_remaining_ignores_in_flight_reservations_for_ranking() -> None:
    a = _account("a", allowance=50 * MILLION, completed=38 * MILLION, reserved=5 * MILLION)
    b = _account("b", allowance=50 * MILLION, completed=19 * MILLION, reserved=1_000)
    engine = RoutingEngine()
    ranked = engine.order([a, b], _context())
    assert [s.account_id for s in ranked] == ["b", "a"]


def test_unknown_allowance_ranked_after_known_budget() -> None:
    known = _account("known", allowance=10_000, completed=0)
    unknown = _account("unknown", allowance=None)
    engine = RoutingEngine()
    ranked = engine.order([known, unknown], _context())
    assert [s.account_id for s in ranked] == ["known", "unknown"]


def test_unknown_allowance_is_selectable_when_alone() -> None:
    unknown = _account("unknown", allowance=None)
    engine = RoutingEngine()
    selected = engine.select([unknown], _context())
    assert selected is not None
    assert selected.account_id == "unknown"


def test_deterministic_ordering_for_equal_remaining() -> None:
    a = _account("b_account", allowance=10_000)
    b = _account("a_account", allowance=10_000)
    engine = RoutingEngine()
    ranked = engine.order([a, b], _context())
    assert [s.account_id for s in ranked] == ["a_account", "b_account"]


# ------------------------------------------------------------------ eligibility


def test_disabled_account_is_not_eligible() -> None:
    assert is_eligible(_account("a", enabled=False), _context()) is False


def test_account_without_api_key_is_not_eligible() -> None:
    assert is_eligible(_account("a", has_key=False), _context()) is False


def test_excluded_account_is_not_eligible() -> None:
    now = time.time()
    assert is_eligible(_account("a", excluded_in=now + 60), _context()) is False
    # an expired exclusion window no longer blocks the account
    assert is_eligible(_account("a", excluded_in=now - 60), _context()) is True


def test_exhausted_account_is_not_eligible() -> None:
    # allowance 50 tokens, nothing consumed — a 1,000 token request cannot fit
    exhausted = _account("a", allowance=50)
    assert is_eligible(exhausted, _context(required=1_000)) is False
    assert is_eligible(exhausted, _context(required=50)) is True


def test_exactly_enough_budget_is_eligible() -> None:
    account = _account("a", allowance=1_000)
    assert is_eligible(account, _context(required=1_000)) is True


def test_reserved_tokens_reduce_availability() -> None:
    # 1,000 allowance with 600 already reserved in flight leaves 400 usable
    account = _account("a", allowance=1_000, reserved=600)
    assert is_eligible(account, _context(required=400)) is True
    assert is_eligible(account, _context(required=401)) is False


def test_account_not_serving_model_is_not_eligible() -> None:
    account = _account("a", model_ids=frozenset({"m1"}))
    assert is_eligible(account, _context(model="m1")) is True
    assert is_eligible(account, _context(model="m2")) is False


def test_unknown_model_availability_does_not_filter() -> None:
    account = _account("a", model_ids=None)
    assert is_eligible(account, _context(model="anything")) is True


def test_engine_returns_none_when_nothing_is_eligible() -> None:
    engine = RoutingEngine()
    accounts = [
        _account("a", allowance=10),
        _account("b", enabled=False),
        _account("c", has_key=False),
    ]
    assert engine.select(accounts, _context(required=5_000)) is None


# ------------------------------------------------------------------ strategy registry


def test_only_max_remaining_is_implemented() -> None:
    assert available_strategies() == [MAX_REMAINING]


@pytest.mark.parametrize("name", ["balanced", "cheapest", "quality", "round_robin"])
def test_unimplemented_strategies_are_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        get_strategy(name)


def test_get_strategy_resolves_max_remaining_forms() -> None:
    assert get_strategy("max_remaining").name == MAX_REMAINING
    assert get_strategy("MAX_REMAINING").name == MAX_REMAINING
    assert get_strategy(None).name == MAX_REMAINING
