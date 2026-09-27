"""Budget manager — reservations, reconciliation, persistence (M2 items 2, 3, 9)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.budget.accounts import AccountDefinition, TokenUsage
from app.budget.errors import InsufficientBudgetError
from app.budget.manager import BudgetManager
from app.db.database import Database
from app.upstream.headers import RateLimitState

MILLION = 1_000_000


def _definitions(*specs: tuple[str, int | None, int]) -> list[AccountDefinition]:
    return [
        AccountDefinition(
            account_id=account_id,
            name=account_id,
            api_key_env=f"CLEAN_API_KEY_{index + 1}",
            enabled=True,
            monthly_allowance=allowance,
            initial_usage_tokens=initial,
        )
        for index, (account_id, allowance, initial) in enumerate(specs)
    ]


async def _manager(db_path: Path, definitions: list[AccountDefinition]) -> BudgetManager:
    manager = BudgetManager(Database(db_path))
    await manager.sync_accounts(definitions)
    return manager


# ---------------------------------------------------------------- test A/E


async def test_single_account_records_usage(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "a.db", _definitions(("a", MILLION, 0)))

    reservation = await manager.reserve("a", 500, request_id="req_1", model="m1")
    assert reservation.reserved_tokens == 500
    reserved_snapshot = await manager.snapshot("a")
    assert reserved_snapshot is not None
    assert reserved_snapshot.reserved_tokens == 500
    assert reserved_snapshot.available_tokens == MILLION - 500

    snapshot = await manager.reconcile(
        reservation, TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    )
    assert snapshot is not None
    assert snapshot.completed_tokens == 15
    assert snapshot.reserved_tokens == 0
    assert snapshot.estimated_remaining == MILLION - 15

    ledger = await manager.ledger()
    assert len(ledger) == 1
    assert ledger[0]["total_tokens"] == 15
    assert ledger[0]["usage_source"] == "upstream"
    assert ledger[0]["account_id"] == "a"
    await manager.database.close()


# ------------------------------------------------------------------ test E


async def test_reconciliation_releases_unused_reservation(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "b.db", _definitions(("a", 10_000, 0)))

    reservation = await manager.reserve("a", 4_000, request_id="req_1", model="m1")
    snapshot = await manager.reconcile(
        reservation, TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    )

    assert snapshot is not None
    # actual usage replaces the estimate; unused reserved tokens are released
    assert snapshot.completed_tokens == 150
    assert snapshot.reserved_tokens == 0
    assert snapshot.estimated_remaining == 10_000 - 150
    assert await manager.active_reservation_tokens("a") == 0
    await manager.database.close()


async def test_reconciliation_without_upstream_usage_charges_estimate(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "b2.db", _definitions(("a", 10_000, 0)))

    reservation = await manager.reserve("a", 2_000, request_id="req_1", model="m1")
    snapshot = await manager.reconcile(reservation, None)

    assert snapshot is not None
    assert snapshot.completed_tokens == 2_000
    assert snapshot.reserved_tokens == 0
    ledger = await manager.ledger()
    assert ledger[0]["usage_source"] == "estimate"
    await manager.database.close()


async def test_release_does_not_charge_tokens(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "b3.db", _definitions(("a", 10_000, 0)))

    reservation = await manager.reserve("a", 3_000, request_id="req_1", model="m1")
    snapshot = await manager.release(reservation)

    assert snapshot is not None
    assert snapshot.completed_tokens == 0
    assert snapshot.reserved_tokens == 0
    assert snapshot.estimated_remaining == 10_000
    assert await manager.ledger() == []
    await manager.database.close()


# ------------------------------------------------------------------ test D


async def test_concurrent_reservations_cannot_double_spend(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "c.db", _definitions(("a", 1_000, 0)))

    results = await asyncio.gather(
        manager.reserve("a", 800, request_id="req_1", model="m1"),
        manager.reserve("a", 800, request_id="req_2", model="m1"),
        return_exceptions=True,
    )
    granted = [r for r in results if not isinstance(r, BaseException)]
    denied = [r for r in results if isinstance(r, BaseException)]

    assert len(granted) == 1
    assert len(denied) == 1
    assert isinstance(denied[0], InsufficientBudgetError)

    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.reserved_tokens == 800
    # estimated_remaining stays truthful (allowance - usage); the reservation
    # only reduces what is available, and never below zero through a race.
    assert snapshot.estimated_remaining == 1_000
    assert snapshot.available_tokens == 200
    await manager.database.close()


async def test_concurrent_reservations_stay_within_budget(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "c2.db", _definitions(("a", 1_000, 0)))

    results = await asyncio.gather(
        *(manager.reserve("a", 100, request_id=f"req_{i}", model="m1") for i in range(15)),
        return_exceptions=True,
    )
    granted = [r for r in results if not isinstance(r, BaseException)]
    denied = [r for r in results if isinstance(r, BaseException)]

    assert len(granted) == 10
    assert len(denied) == 5
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.reserved_tokens == 1_000
    assert snapshot.available_tokens == 0
    await manager.database.close()


async def test_reservation_raises_when_budget_insufficient(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "c3.db", _definitions(("a", 100, 90)))

    with pytest.raises(InsufficientBudgetError) as excinfo:
        await manager.reserve("a", 50, request_id="req_1", model="m1")

    assert excinfo.value.code == "INSUFFICIENT_BUDGET"
    assert excinfo.value.details["available_tokens"] == 10
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.reserved_tokens == 0
    await manager.database.close()


async def test_unknown_allowance_is_always_reservable(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "c4.db", _definitions(("a", None, 0)))

    reservation = await manager.reserve("a", 10_000_000, request_id="req_1", model="m1")
    assert reservation.reserved_tokens == 10_000_000
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    # unknown allowance -> we cannot claim a remaining figure
    assert snapshot.estimated_remaining is None
    assert snapshot.available_tokens is None
    await manager.database.close()


# ------------------------------------------------------------------ test F


async def test_state_persists_across_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "persist.db"
    definitions = _definitions(("a", 100_000, 1_000))

    first = await _manager(db_path, definitions)
    reservation = await first.reserve("a", 5_000, request_id="req_1", model="m1")
    await first.reconcile(
        reservation, TokenUsage(prompt_tokens=900, completion_tokens=100, total_tokens=1_000)
    )
    await first.database.close()

    # Simulated application restart: brand new Database + BudgetManager.
    second = await _manager(db_path, definitions)
    snapshot = await second.snapshot("a")
    assert snapshot is not None
    assert snapshot.completed_tokens == 1_000
    assert snapshot.initial_usage_tokens == 1_000
    assert snapshot.estimated_remaining == 100_000 - 2_000
    ledger = await second.ledger()
    assert len(ledger) == 1
    assert ledger[0]["total_tokens"] == 1_000
    await second.database.close()


async def test_config_sync_never_resets_usage(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.db"
    definitions = _definitions(("a", 100_000, 0))

    manager = await _manager(db_path, definitions)
    reservation = await manager.reserve("a", 500, request_id="req_1", model="m1")
    await manager.reconcile(
        reservation, TokenUsage(prompt_tokens=300, completion_tokens=200, total_tokens=500)
    )
    # Re-running sync (as happens on every request) must not wipe accounting.
    await manager.sync_accounts(definitions)
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.completed_tokens == 500
    assert snapshot.estimated_remaining == 99_500
    await manager.database.close()


async def test_allowance_change_is_applied_without_resetting_usage(tmp_path: Path) -> None:
    db_path = tmp_path / "change.db"
    manager = await _manager(db_path, _definitions(("a", 100_000, 0)))
    reservation = await manager.reserve("a", 500, request_id="req_1", model="m1")
    await manager.reconcile(
        reservation, TokenUsage(prompt_tokens=300, completion_tokens=200, total_tokens=500)
    )

    await manager.sync_accounts(_definitions(("a", 200_000, 0)))
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.monthly_allowance == 200_000
    assert snapshot.completed_tokens == 500
    assert snapshot.estimated_remaining == 199_500
    await manager.database.close()


# --------------------------------------------------------- rate-limit + misc


async def test_rate_limit_state_is_stored_separately_from_budget(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "rl.db", _definitions(("a", 100_000, 0)))

    await manager.record_rate_limit(
        "a", RateLimitState(request_limit=100, request_remaining=7, token_remaining=4_242)
    )
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.rate_limit is not None
    assert snapshot.rate_limit.request_remaining == 7
    assert snapshot.rate_limit.token_remaining == 4_242
    # per-minute headers must not be mixed into the monthly estimate
    assert snapshot.estimated_remaining == 100_000
    assert snapshot.reserved_tokens == 0
    await manager.database.close()


async def test_exclusion_windows(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "ex.db", _definitions(("a", 100_000, 0)))

    await manager.exclude_account("a", reason="UPSTREAM_AUTH_ERROR", seconds=600)
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.is_excluded() is True
    assert snapshot.exclusion_reason == "UPSTREAM_AUTH_ERROR"
    assert snapshot.estimated_remaining == 100_000

    await manager.clear_exclusion("a")
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.is_excluded() is False
    await manager.database.close()


async def test_per_account_model_storage(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "models.db", _definitions(("a", None, 0)))

    assert (await manager.snapshot("a")) is not None
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.model_ids is None
    assert snapshot.serves_model("anything") is True

    await manager.replace_models("a", ["m1", "m2"])
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.model_ids == frozenset({"m1", "m2"})
    assert snapshot.serves_model("m1") is True
    assert snapshot.serves_model("m9") is False

    await manager.replace_models("a", ["m2"])
    snapshot = await manager.snapshot("a")
    assert snapshot is not None
    assert snapshot.model_ids == frozenset({"m2"})
    await manager.database.close()


async def test_reserve_for_unknown_account_is_denied(tmp_path: Path) -> None:
    manager = await _manager(tmp_path / "unknown.db", _definitions(("a", None, 0)))
    with pytest.raises(InsufficientBudgetError):
        await manager.reserve("nope", 10, request_id="req_1", model="m1")
    await manager.database.close()
