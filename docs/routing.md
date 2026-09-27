# Routing

> Milestone 2 — `MAX_REMAINING` across eligible accounts, with atomic reservations.

## Layering

```
API route (app/api/routes.py)
    ↓
service layer (app/service/chat.py)
    ↓
routing engine (app/routing/*)
    ↓
budget manager (app/budget/*)
    ↓
upstream Clean APIs client (app/upstream/*)
```

Rules enforced by this structure:

- The routing engine performs **no** HTTP requests and does **not** touch SQLite.
  It ranks read-only account snapshots.
- The upstream client never decides which account to use and never computes
  budgets. It only knows the key it was built with.
- The budget manager owns account state, reservations, usage reconciliation and
  remaining-budget calculations.
- The API route contains no routing logic and no budget arithmetic.

## Eligibility

An account is eligible only when **all** of the following hold:

1. `enabled` in configuration.
2. Its `api_key_env` variable is actually set (a configured key exists).
3. No active exclusion window from a previous auth/payment/rate failure.
4. It is known to serve the requested model (see *Model availability*), or its
   model list is not known yet.
5. Its estimated available budget covers the request estimate
   (`available >= required`). Accounts with an unknown allowance skip this check
   because the budget cannot be verified.

An account explicitly requested by the client is never silently replaced. When
no account is eligible, the router returns
`503 NO_ELIGIBLE_ACCOUNT` with the estimated token requirement.

## Pinning an account

A client may pin one configured account with the optional request header:

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-tokenrouter-account: secondary-clean" \
  -d '{"model":"claude-opus-4.8","messages":[{"role":"user","content":"Hello"}]}'
```

- A pinned request uses **only** that account; no other account is substituted
  even when it would be a better fit.
- If the pinned account is unknown → `400 INVALID_REQUEST`.
- If it exists but is not eligible (disabled, no key, excluded, out of budget,
  or does not serve the model) → `503 NO_ELIGIBLE_ACCOUNT` naming the account.
- Omitting the header keeps normal `max_remaining` routing.

## MAX_REMAINING

Among eligible accounts the strategy picks the one with the highest
`estimated_remaining` (`allowance - initial_usage - completed_usage`).

- Accounts with an unknown allowance cannot be compared numerically, so they are
  ranked after accounts with a known budget. A bounded budget is never drained
  by an account of unknown capacity.
- Ties are broken by `account_id`, so selection is deterministic.
- Reservations in flight do not change the ranking; they only affect
  eligibility, so two concurrent requests cannot both pick a drained account.

Adding a strategy later means implementing `RoutingStrategy.order()` and
registering it in `app/routing/engine.py`. `balanced`, `cheapest` and `quality`
are deliberately **not** implemented in M2.

## Reservations

Before dispatch, the service estimates the maximum token requirement
(`prompt estimate + max_tokens or the configured default`) and asks the budget
manager to reserve it:

1. Estimate tokens.
2. Rank eligible accounts.
3. `BudgetManager.reserve()` — one conditional `UPDATE` inside a
   `BEGIN IMMEDIATE` transaction. Concurrent requests cannot claim the same
   tokens; if the budget does not cover the claim the account is skipped.
4. Send the upstream request with the **exact** model the client asked for.
5. Reconcile:
   - success with upstream usage → charge actual usage, release the unused part;
   - success without usage → charge the estimate (`usage_source='estimate'`);
   - failure → release the full reservation, charge nothing.
6. Record the last observed per-minute rate-limit headers (diagnostics only).

## Failure handling and fallback

| upstream result | monthly budget effect | account state | fallback |
| --- | --- | --- | --- |
| 2xx | charge actual usage | — | — |
| 400 / 404 (malformed, model) | none | unchanged | never retried |
| 401 | none | excluded (`UPSTREAM_AUTH_ERROR`) | yes |
| 402 | none | excluded (`UPSTREAM_PAYMENT_REQUIRED`) | yes |
| 429 | **none** (per-minute limit, not monthly) | cooldown from `Retry-After` | yes |
| 5xx / timeout | none | unchanged | yes |

- Fallback happens at the **account** level only, and the requested model is
  preserved for every attempt.
- Attempts are bounded by `routing.max_fallback_attempts` (default 2 total).
- A 429 is never booked against the monthly estimate.
- The **first** failure is returned to the client, so the original error is not
  hidden by a later fallback failure.
- Each request gets at most one upstream attempt per account.

## Model availability

- The requested model is forwarded unchanged; the router never substitutes a
  different model.
- Availability is tracked **per account**, because each account may expose a
  different model set. If the model is not served by the ranked accounts and the
  answer is known for all of them, the request fails with
  `MODEL_NOT_AVAILABLE` before any dispatch.
- Per-account lists are refreshed lazily and cached for
  `routing.models_cache_ttl_seconds`. Discovery failures leave availability
  unknown rather than failing the request.
- The global registry remains a dynamically discovered catalogue — there is no
  hardcoded model catalog.

## Observability

Each request logs structured metadata only: `request_id`, `account_id`,
`model`, `routing_strategy`, `reserved_tokens`, actual usage,
`estimated_remaining`, `upstream_status`, `duration_ms`, `error_code`.
Non-metadata fields are replaced with `[redacted]`; API keys, Authorization
headers and prompt/completion contents are never logged.
