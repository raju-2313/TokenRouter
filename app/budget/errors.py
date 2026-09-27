"""Normalized error codes for local routing/budget failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NO_ELIGIBLE_ACCOUNT = "NO_ELIGIBLE_ACCOUNT"
INSUFFICIENT_BUDGET = "INSUFFICIENT_BUDGET"
ROUTING_ERROR = "ROUTING_ERROR"


@dataclass(frozen=True)
class BudgetError(Exception):
    code: str
    message: str
    status_code: int = 503
    account_id: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.account_id is not None:
            error["account_id"] = self.account_id
        if self.details:
            error.update(self.details)
        return {"error": error}


class InsufficientBudgetError(BudgetError):
    """Raised when an account cannot cover the estimated token requirement."""

    def __init__(
        self,
        account_id: str,
        required_tokens: int,
        available_tokens: int | None,
    ) -> None:
        detail = "unknown" if available_tokens is None else str(available_tokens)
        super().__init__(
            code=INSUFFICIENT_BUDGET,
            message=(
                f"Account '{account_id}' has insufficient estimated remaining "
                f"monthly budget: need ~{required_tokens} tokens, {detail} available"
            ),
            status_code=503,
            account_id=account_id,
            details={
                "required_tokens": required_tokens,
                "available_tokens": available_tokens,
            },
        )


def no_eligible_account(
    required_tokens: int | None = None,
    model: str | None = None,
    reason: str = "No configured account is eligible for this request",
) -> BudgetError:
    details: dict[str, Any] = {}
    if required_tokens is not None:
        details["required_tokens"] = required_tokens
    if model is not None:
        details["model"] = model
    return BudgetError(
        code=NO_ELIGIBLE_ACCOUNT,
        message=reason,
        status_code=503,
        details=details or None,
    )
