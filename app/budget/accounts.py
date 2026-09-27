"""Account state value objects — the read model used by budget and routing.

The API key is deliberately NOT part of any object in this module. Keys are
resolved by the upstream client factory only, so account state, snapshots and
ledger rows can never leak a secret.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.upstream.headers import RateLimitState


@dataclass(frozen=True)
class AccountDefinition:
    """Static, configured facts about one upstream account.

    Produced from configuration. Contains no secret material — only the name
    of the environment variable that holds the key.
    """

    account_id: str
    name: str
    api_key_env: str
    enabled: bool = True
    monthly_allowance: int | None = None
    initial_usage_tokens: int = 0
    has_api_key: bool = False


@dataclass(frozen=True)
class TokenUsage:
    """Token counts for one completed (or estimated) request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    source: str = "upstream"

    @classmethod
    def from_upstream(cls, usage: Any) -> TokenUsage | None:
        """Parse an OpenAI-style ``usage`` object. Returns None when unusable."""
        if not isinstance(usage, dict):
            return None

        def _int(*keys: str) -> int | None:
            for key in keys:
                value = usage.get(key)
                if value is None or isinstance(value, bool):
                    continue
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed >= 0:
                    return parsed
            return None

        prompt = _int("prompt_tokens", "input_tokens")
        completion = _int("completion_tokens", "output_tokens")
        total = _int("total_tokens")
        if prompt is None and completion is None and total is None:
            return None
        if total is None:
            total = (prompt or 0) + (completion or 0)
        return cls(
            prompt_tokens=prompt or 0,
            completion_tokens=completion or 0,
            total_tokens=total,
            source="upstream",
        )

    def to_log(self) -> dict[str, Any]:
        return {
            "input_tokens": self.prompt_tokens,
            "output_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class Reservation:
    """An atomic claim on estimated tokens for one in-flight request."""

    reservation_id: str
    account_id: str
    request_id: str
    model: str | None
    reserved_tokens: int
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class AccountSnapshot:
    """Point-in-time local view of one account's budget and state.

    ``estimated_remaining`` is a LOCAL ESTIMATE derived from user-declared
    configuration plus locally recorded usage. It is never presented as
    authoritative Clean APIs billing data.
    """

    account_id: str
    name: str
    enabled: bool
    has_api_key: bool
    monthly_allowance: int | None
    initial_usage_tokens: int
    completed_tokens: int
    reserved_tokens: int
    excluded_until: float | None = None
    exclusion_reason: str | None = None
    rate_limit: RateLimitState | None = None
    model_ids: frozenset[str] | None = None
    models_refreshed_at: float | None = None

    @property
    def usage_tokens(self) -> int:
        """initial usage + usage completed since TokenRouter started tracking."""
        return self.initial_usage_tokens + self.completed_tokens

    @property
    def estimated_remaining(self) -> int | None:
        """allowance - initial usage - completed usage. None when unknown."""
        if self.monthly_allowance is None:
            return None
        return self.monthly_allowance - self.usage_tokens

    @property
    def available_tokens(self) -> int | None:
        """Estimated remaining minus tokens currently reserved in flight."""
        remaining = self.estimated_remaining
        if remaining is None:
            return None
        return remaining - self.reserved_tokens

    def is_excluded(self, now: float | None = None) -> bool:
        if self.excluded_until is None:
            return False
        return self.excluded_until > (now if now is not None else time.time())

    def serves_model(self, model: str | None) -> bool:
        """True when this account is known to serve ``model``.

        ``model_ids is None`` means discovery has not run for this account, so
        availability is unknown and the account is not filtered out.
        """
        if model is None or self.model_ids is None:
            return True
        return model in self.model_ids
