# TokenRouter

Local OpenAI-compatible gateway that routes requests to **Clean APIs** with local token accounting.

> **Status:** Milestone 2 — multi-account, token-aware routing with atomic reservations.

## What it does (M2)

- Exposes `GET /v1/models`, `POST /v1/chat/completions` and `GET /health` locally.
- Routes each request to one of your configured Clean APIs accounts using the
  **`max_remaining`** strategy: the eligible account with the highest estimated
  remaining monthly token budget.
- Tracks token usage locally in SQLite (WAL) and persists it across restarts.
- Reserves estimated tokens atomically before the upstream call, then reconciles
  against actual upstream usage.
- Falls back to another account on account-level failures, keeping the exact
  model you requested.

M2 does not implement streaming, model substitution, or additional routing
strategies (`balanced`, `cheapest`, `quality` are explicitly not available).

## Why it exists

Clean APIs is the single upstream provider. TokenRouter gives you a local,
single-user gateway with auditable usage accounting and routing controls, without
bypassing provider limits or terms.

## Important limitation

`monthly_allowance` and `initial_usage_tokens` are **user-declared** values from
`config.yaml`. They are not fetched from Clean APIs automatically. If
`monthly_allowance` is `null`, TokenRouter does not pretend to know your
remaining monthly allowance and ranks that account after accounts with a known
budget.

Estimated remaining is a **local estimate**:

```
estimated_remaining = monthly_allowance - initial_usage_tokens - completed_usage
available           = estimated_remaining - reserved_tokens (in flight)
```

Per-minute `X-RateLimit-*` header information is recorded separately for
diagnostics and is never mixed into the monthly estimate. Authoritative billing
remains with Clean APIs.

**You are responsible for complying with Clean APIs Terms of Service, rate limits,
and billing.** Use only API keys you legitimately hold.

## Requirements

- Python 3.11+
- Clean APIs API key(s) via environment variables

## Installation

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

## Configuration

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

Edit `.env` — one variable per account:

```env
CLEAN_API_KEY_1=sk-clean-...
CLEAN_API_KEY_2=sk-clean-...
CLEAN_API_KEY_3=sk-clean-...
```

Edit `config.yaml`:

```yaml
database:
  path: data/tokenrouter.db

routing:
  strategy: max_remaining
  max_fallback_attempts: 2

accounts:
  - name: account-a
    account_id: primary-clean
    api_key_env: CLEAN_API_KEY_1
    enabled: true
    monthly_allowance: 50000000
    initial_usage_tokens: 38000000   # ~12M remaining
  - name: account-b
    account_id: secondary-clean
    api_key_env: CLEAN_API_KEY_2
    enabled: true
    monthly_allowance: 50000000
    initial_usage_tokens: 19000000   # ~31M remaining
```

With that configuration a normal request goes to `account-b` (~31M remaining)
rather than `account-a` (~12M remaining). See `docs/configuration.md`.

## Starting the server

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8787
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/v1/models
```

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-opus-4.8","messages":[{"role":"user","content":"Hello"}]}'
```

Explicit model behavior: if you request `model: "claude-opus-4.8"` the router
forwards that exact model upstream; account selection never rewrites it. Unknown
models return `MODEL_NOT_AVAILABLE` (HTTP 400). When no account is eligible you
get `NO_ELIGIBLE_ACCOUNT` (HTTP 503).

The client never needs to know which Clean APIs account was selected. To force a
specific account, send `x-tokenrouter-account: <account_id>`; a pinned request
is never silently moved to another account.

Connect a coding agent:

- Base URL: `http://127.0.0.1:8787/v1`
- API key: any placeholder (local gateway does not require an upstream key from
  the client)

## Architecture

```
app/api/routes.py        # OpenAI-compatible endpoints (no routing logic)
app/service/chat.py      # estimate -> route -> reserve -> dispatch -> reconcile
app/routing/             # RoutingEngine + strategies (no HTTP, no SQL)
app/budget/              # account state, reservations, usage ledger
app/db/                  # SQLite (WAL)
app/upstream/            # Clean APIs client + key resolution
```

See `docs/architecture.md` and `docs/routing.md`.

## Development

```bash
pytest
ruff check .
ruff format --check .
```

See `CONTRIBUTING.md` and `docs/development.md`.

## Security

- API keys are loaded from env, never stored in the database, logs, or responses.
- Structured logging emits metadata only (request id, account id, model,
  reservation, usage, remaining budget, strategy, upstream status). Prompt and
  completion contents are never logged.
- Report vulnerabilities privately — see `SECURITY.md`.

## License

MIT — see `LICENSE`.
