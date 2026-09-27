# TokenRouter

Local OpenAI-compatible gateway routing to Clean APIs with local token accounting.

> Status: Milestone 2 — multi-account, token-aware routing.

## Stack

- Python 3.11+
- FastAPI + Uvicorn
- Pydantic v2
- httpx (upstream HTTP only)
- PyYAML
- aiosqlite (SQLite, WAL) for local token accounting
- pytest + pytest-asyncio
- ruff

## Layout

```
TokenRouter/
  app/
    __init__.py
    main.py            # FastAPI entrypoint — /health, /ready, /
    config/            # YAML + env configuration (accounts, routing, database)
    core/              # request ids, masking, safe structured logging
    api/               # OpenAI-compatible routes + dependency wiring
    service/           # request orchestration (estimate/route/reserve/dispatch)
    routing/           # RoutingEngine + strategies (no HTTP, no SQL)
    budget/            # account state, token reservations, usage ledger
    models/            # model registry / discovery / capabilities
    upstream/          # Clean APIs client, headers, errors, key resolution
    db/                # SQLite schema + async connection (WAL)
  tests/
  docs/
  pyproject.toml
  config.example.yaml
  .env.example
```

## Dependency direction

```
API route -> service -> routing engine -> budget manager -> upstream client
```

- The **routing engine** selects an eligible account from read-only snapshots.
  It performs no HTTP and no SQL.
- The **budget manager** owns account state, atomic reservations, usage
  reconciliation and remaining-budget calculations.
- The **upstream client** performs HTTP only. It never picks an account, never
  computes budgets and never contains routing logic.
- API keys are resolved in exactly one place (the upstream client factory) and
  never enter account state, the database, logs or responses.

## Database

`app/db/` owns a single SQLite database in WAL mode (`data/tokenrouter.db` by
default):

| table | purpose |
| --- | --- |
| `accounts` | configured account state: allowance, initial usage, completed usage, reserved tokens, exclusion, last observed per-minute rate-limit headers |
| `reservations` | one row per in-flight reservation (`active` → `settled` / `released`) |
| `usage_ledger` | append-only record of charged usage per request |
| `account_models` | models discovered per account (availability is per account) |

Reservations are written with a single conditional `UPDATE` inside a
`BEGIN IMMEDIATE` transaction, so concurrent requests can never spend the same
budget. Accounting state is read back on startup: restarting does not reset usage.

## Principles

- Clean APIs is the only upstream provider.
- Monthly allowance and remaining budget are **local estimates** from
  user-declared configuration plus locally recorded usage. Authoritative
  billing remains with Clean APIs.
- `X-RateLimit-*` header information is per-minute and is never mixed into the
  monthly estimate.
- Provider-specific code stays under `app/upstream/`.
- Never log API keys, Authorization headers, prompts or completions.
- Never silently substitute a model the client requested.

## Error codes

`INVALID_REQUEST`, `MODEL_NOT_AVAILABLE`, `NO_ELIGIBLE_ACCOUNT`,
`INSUFFICIENT_BUDGET`, `UPSTREAM_AUTH_ERROR`, `UPSTREAM_PAYMENT_REQUIRED`,
`UPSTREAM_RATE_LIMITED`, `UPSTREAM_UNAVAILABLE`, `UPSTREAM_TIMEOUT`,
`UPSTREAM_ERROR`.

## Milestones

0. Bootstrap ✅
1. Clean APIs client, OpenAI-compatible API, model discovery ✅
2. Multi-account token-aware routing (`max_remaining`), local usage ledger,
   atomic reservations, bounded account fallback ✅
3+ Streaming, further routing strategies (`balanced`, `cheapest`, `quality`),
   profiles, dashboards — not started.
