"""Budget manager — owns account state, reservations and usage reconciliation.

Design notes
------------
* Monthly budget and per-minute rate limits are strictly separate. ``accounts``
  stores the last observed ``X-RateLimit-*`` values for diagnostics only; they
  never influence the monthly reservation math.
* Remaining budget is a LOCAL ESTIMATE:
  ``allowance - initial_usage - completed_usage - reserved``.
* Reservations are atomic. The write is a single conditional ``UPDATE`` inside
  an ``IMMEDIATE`` transaction, so two concurrent requests can never reserve
  the same tokens and estimated remaining can never go negative because of a
  race.
* State survives restarts: completed usage, reservations and ledger rows are
  read back from SQLite instead of being reset.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence
from typing import Any

import aiosqlite

from app.budget.accounts import AccountDefinition, AccountSnapshot, Reservation, TokenUsage
from app.budget.errors import InsufficientBudgetError
from app.db.database import Database
from app.upstream.headers import RateLimitState

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_SETTLED = "settled"
STATUS_RELEASED = "released"

_ACCOUNT_COLUMNS = (
    "account_id, name, api_key_env, has_api_key, enabled, monthly_allowance, "
    "initial_usage_tokens, completed_tokens, reserved_tokens, excluded_until, "
    "exclusion_reason, rl_request_limit, rl_request_remaining, rl_token_limit, "
    "rl_token_remaining, rl_updated_at, models_refreshed_at"
)

# Guard used by the atomic reservation UPDATE. Accounts with an unknown
# allowance (NULL) are always reservable — we cannot verify their budget.
# Kept parenthesised: `AND` binds tighter than `OR`, so an unparenthesised
# guard would match rows belonging to other accounts.
_RESERVE_GUARD = (
    "(monthly_allowance IS NULL OR "
    "monthly_allowance - initial_usage_tokens - completed_tokens - reserved_tokens >= ?)"
)


class BudgetManager:
    """Single owner of local token accounting state."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._fingerprint: tuple[Any, ...] | None = None

    @property
    def database(self) -> Database:
        return self._db

    # ------------------------------------------------------------------
    # configuration sync
    # ------------------------------------------------------------------

    async def sync_accounts(
        self, definitions: Sequence[AccountDefinition], *, force: bool = False
    ) -> None:
        """Reconcile configured accounts into the database.

        Idempotent and safe to call per request: it runs only when the effective
        configuration fingerprint changes. Completed usage and in-flight
        reservations are never reset.
        """
        fingerprint = tuple(
            (
                d.account_id,
                d.name,
                d.api_key_env,
                d.enabled,
                d.monthly_allowance,
                d.initial_usage_tokens,
                d.has_api_key,
            )
            for d in definitions
        )
        if not force and fingerprint == self._fingerprint:
            return
        now = time.time()
        async with self._db.transaction() as conn:
            for definition in definitions:
                await conn.execute(
                    """
                    INSERT INTO accounts (
                        account_id, name, api_key_env, has_api_key, enabled,
                        monthly_allowance, initial_usage_tokens, completed_tokens,
                        reserved_tokens, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        name = excluded.name,
                        api_key_env = excluded.api_key_env,
                        has_api_key = excluded.has_api_key,
                        enabled = excluded.enabled,
                        monthly_allowance = excluded.monthly_allowance,
                        updated_at = excluded.updated_at
                    """,
                    (
                        definition.account_id,
                        definition.name,
                        definition.api_key_env,
                        int(definition.has_api_key),
                        int(definition.enabled),
                        definition.monthly_allowance,
                        definition.initial_usage_tokens,
                        now,
                        now,
                    ),
                )
        self._fingerprint = fingerprint

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def snapshots(self) -> list[AccountSnapshot]:
        """All accounts currently present in the ledger, in account_id order."""
        rows = await self._db.fetch_all(
            f"SELECT {_ACCOUNT_COLUMNS} FROM accounts ORDER BY account_id"
        )
        if not rows:
            return []
        model_map = await self._load_models([row["account_id"] for row in rows])
        return [self._to_snapshot(row, model_map.get(row["account_id"])) for row in rows]

    async def snapshot(self, account_id: str) -> AccountSnapshot | None:
        row = await self._db.fetch_one(
            f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE account_id = ?", (account_id,)
        )
        if row is None:
            return None
        models = await self._load_models([account_id])
        return self._to_snapshot(row, models.get(account_id))

    async def _load_models(self, account_ids: Sequence[str]) -> dict[str, frozenset[str] | None]:
        """Model ids per account. Missing/never-refreshed -> None (unknown)."""
        if not account_ids:
            return {}
        placeholders = ",".join("?" for _ in account_ids)
        rows = await self._db.fetch_all(
            f"SELECT account_id, model_id FROM account_models WHERE account_id IN ({placeholders})",
            tuple(account_ids),
        )
        collected: dict[str, set[str]] = {}
        for row in rows:
            collected.setdefault(row["account_id"], set()).add(row["model_id"])
        return {
            account_id: frozenset(collected.get(account_id, set()))
            if account_id in collected
            else None
            for account_id in account_ids
        }

    def _to_snapshot(self, row: aiosqlite.Row, model_ids: frozenset[str] | None) -> AccountSnapshot:
        rate_limit = None
        if row["rl_updated_at"] is not None:
            rate_limit = RateLimitState(
                request_limit=row["rl_request_limit"],
                request_remaining=row["rl_request_remaining"],
                token_limit=row["rl_token_limit"],
                token_remaining=row["rl_token_remaining"],
            )
        refreshed = row["models_refreshed_at"]
        return AccountSnapshot(
            account_id=row["account_id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            has_api_key=bool(row["has_api_key"]),
            monthly_allowance=row["monthly_allowance"],
            initial_usage_tokens=row["initial_usage_tokens"],
            completed_tokens=row["completed_tokens"],
            reserved_tokens=row["reserved_tokens"],
            excluded_until=row["excluded_until"],
            exclusion_reason=row["exclusion_reason"],
            rate_limit=rate_limit,
            model_ids=model_ids,
            models_refreshed_at=refreshed,
        )

    # ------------------------------------------------------------------
    # reservations
    # ------------------------------------------------------------------

    async def reserve(
        self,
        account_id: str,
        tokens: int,
        *,
        request_id: str,
        model: str | None = None,
        reservation_id: str | None = None,
    ) -> Reservation:
        """Atomically claim ``tokens`` from an account's estimated budget.

        Raises ``InsufficientBudgetError`` when the estimated remaining budget
        cannot cover the claim. The check and the write happen in one
        transaction with a conditional UPDATE, so concurrent callers cannot
        double-spend.
        """
        if tokens < 0:
            raise ValueError("tokens must be >= 0")
        reservation_id = reservation_id or f"rsv_{uuid.uuid4().hex[:16]}"
        now = time.time()

        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE accounts SET reserved_tokens = reserved_tokens + ?, updated_at = ? "
                f"WHERE account_id = ? AND {_RESERVE_GUARD}",
                (tokens, now, account_id, tokens),
            )
            claimed = cursor.rowcount
            await cursor.close()
            if claimed != 1:
                row = await conn.execute(
                    "SELECT monthly_allowance, initial_usage_tokens, completed_tokens, "
                    "reserved_tokens FROM accounts WHERE account_id = ?",
                    (account_id,),
                )
                state = await row.fetchone()
                await row.close()
                available: int | None = None
                if state is not None and state["monthly_allowance"] is not None:
                    available = (
                        state["monthly_allowance"]
                        - state["initial_usage_tokens"]
                        - state["completed_tokens"]
                        - state["reserved_tokens"]
                    )
                if state is None:
                    raise InsufficientBudgetError(account_id, tokens, None) from None
                raise InsufficientBudgetError(account_id, tokens, available)
            await conn.execute(
                """
                INSERT INTO reservations (
                    reservation_id, account_id, request_id, model, reserved_tokens,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (reservation_id, account_id, request_id, model, tokens, STATUS_ACTIVE, now),
            )
        return Reservation(
            reservation_id=reservation_id,
            account_id=account_id,
            request_id=request_id,
            model=model,
            reserved_tokens=tokens,
            created_at=now,
        )

    async def reconcile(
        self,
        reservation: Reservation,
        usage: TokenUsage | None = None,
    ) -> AccountSnapshot | None:
        """Settle a reservation against actual usage.

        Actual upstream usage replaces the estimate; the unused part of the
        reservation is released. When upstream reported no usage, the estimate
        is charged and marked as such (``usage_source='estimate'``).
        """
        charged = usage or TokenUsage(
            prompt_tokens=reservation.reserved_tokens,
            completion_tokens=0,
            total_tokens=reservation.reserved_tokens,
            source="estimate",
        )
        return await self._settle(reservation, charged, STATUS_SETTLED)

    async def release(self, reservation: Reservation) -> AccountSnapshot | None:
        """Release a full reservation without charging (failure path)."""
        return await self._settle(
            reservation,
            TokenUsage(source="none"),
            STATUS_RELEASED,
        )

    async def _settle(
        self,
        reservation: Reservation,
        usage: TokenUsage,
        status: str,
    ) -> AccountSnapshot | None:
        now = time.time()
        charge = usage.total_tokens if status == STATUS_SETTLED else 0
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE accounts SET "
                "  reserved_tokens = MAX(reserved_tokens - ?, 0), "
                "  completed_tokens = completed_tokens + ?, "
                "  completed_prompt_tokens = completed_prompt_tokens + ?, "
                "  completed_completion_tokens = completed_completion_tokens + ?, "
                "  request_count = request_count + 1, "
                "  updated_at = ? "
                "WHERE account_id = ?",
                (
                    reservation.reserved_tokens,
                    charge,
                    usage.prompt_tokens if charge else 0,
                    usage.completion_tokens if charge else 0,
                    now,
                    reservation.account_id,
                ),
            )
            await cursor.close()
            await conn.execute(
                """
                UPDATE reservations
                   SET status = ?, actual_tokens = ?, settled_at = ?
                 WHERE reservation_id = ? AND status = ?
                """,
                (status, charge, now, reservation.reservation_id, STATUS_ACTIVE),
            )
            if charge:
                await conn.execute(
                    """
                    INSERT INTO usage_ledger (
                        request_id, account_id, model, reservation_id,
                        prompt_tokens, completion_tokens, total_tokens,
                        usage_source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reservation.request_id,
                        reservation.account_id,
                        reservation.model,
                        reservation.reservation_id,
                        usage.prompt_tokens,
                        usage.completion_tokens,
                        charge,
                        usage.source,
                        now,
                    ),
                )
        return await self.snapshot(reservation.account_id)

    async def active_reservation_tokens(self, account_id: str) -> int:
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(reserved_tokens), 0) AS total FROM reservations "
            "WHERE account_id = ? AND status = ?",
            (account_id, STATUS_ACTIVE),
        )
        return int(row["total"]) if row is not None else 0

    # ------------------------------------------------------------------
    # failure state
    # ------------------------------------------------------------------

    async def exclude_account(self, account_id: str, *, reason: str, seconds: float) -> None:
        """Temporarily exclude an account after an auth/rate/budget failure."""
        until = time.time() + max(float(seconds), 0.0)
        await self._db.execute(
            "UPDATE accounts SET excluded_until = ?, exclusion_reason = ?, updated_at = ? "
            "WHERE account_id = ?",
            (until, reason, time.time(), account_id),
        )

    async def clear_exclusion(self, account_id: str) -> None:
        await self._db.execute(
            "UPDATE accounts SET excluded_until = NULL, exclusion_reason = NULL, "
            "updated_at = ? WHERE account_id = ?",
            (time.time(), account_id),
        )

    # ------------------------------------------------------------------
    # per-minute rate-limit observations (diagnostics only)
    # ------------------------------------------------------------------

    async def record_rate_limit(self, account_id: str, state: RateLimitState) -> None:
        await self._db.execute(
            """
            UPDATE accounts
               SET rl_request_limit = ?, rl_request_remaining = ?,
                   rl_token_limit = ?, rl_token_remaining = ?, rl_updated_at = ?
             WHERE account_id = ?
            """,
            (
                state.request_limit,
                state.request_remaining,
                state.token_limit,
                state.token_remaining,
                time.time(),
                account_id,
            ),
        )

    # ------------------------------------------------------------------
    # per-account model availability
    # ------------------------------------------------------------------

    async def replace_models(self, account_id: str, model_ids: Sequence[str]) -> None:
        """Replace the discovered model set for an account."""
        now = time.time()
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM account_models WHERE account_id = ?", (account_id,))
            for model_id in model_ids:
                await conn.execute(
                    "INSERT OR REPLACE INTO account_models "
                    "(account_id, model_id, last_seen_at) VALUES (?, ?, ?)",
                    (account_id, model_id, now),
                )
            await conn.execute(
                "UPDATE accounts SET models_refreshed_at = ?, updated_at = ? WHERE account_id = ?",
                (now, now, account_id),
            )

    async def mark_models_unknown(self, account_id: str) -> None:
        """Record that discovery ran but produced no usable model list."""
        now = time.time()
        await self._db.execute(
            "UPDATE accounts SET models_refreshed_at = ?, updated_at = ? WHERE account_id = ?",
            (now, now, account_id),
        )

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------

    async def ledger(
        self, *, account_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if account_id is None:
            rows = await self._db.fetch_all(
                "SELECT request_id, account_id, model, prompt_tokens, completion_tokens, "
                "total_tokens, usage_source, created_at FROM usage_ledger "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT request_id, account_id, model, prompt_tokens, completion_tokens, "
                "total_tokens, usage_source, created_at FROM usage_ledger "
                "WHERE account_id = ? ORDER BY id DESC LIMIT ?",
                (account_id, limit),
            )
        return [dict(row) for row in rows]
