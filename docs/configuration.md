# Configuration

> Milestone 2 — multi-account configuration, local accounting database, routing settings.

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

`.env` holds secrets (never committed). `config.yaml` holds non-secret settings.
Keys are referenced by **environment variable name** only.

## Config resolution

1. explicit path passed to `load_config()`
2. `TOKENROUTER_CONFIG`
3. `config.yaml` in the working directory
4. `config.example.yaml` (dev/test fallback)
5. built-in defaults

A `.env` file in the working directory is loaded if present and never overrides
variables that are already set in the environment.

## Accounts

```yaml
accounts:
  - name: account-a
    account_id: primary-clean
    api_key_env: CLEAN_API_KEY_1
    enabled: true
    monthly_allowance: 50000000
    initial_usage_tokens: 38000000

  - name: account-b
    account_id: secondary-clean
    api_key_env: CLEAN_API_KEY_2
    enabled: true
    monthly_allowance: 50000000
    initial_usage_tokens: 19000000
```

| field | meaning |
| --- | --- |
| `name` | human-readable label |
| `account_id` | stable identifier (database rows, routing, logs). Defaults to `name` |
| `api_key_env` | **name** of the environment variable holding the key |
| `enabled` | disabled accounts are never selected |
| `monthly_allowance` | user-declared monthly allowance in tokens, or `null` for unknown |
| `initial_usage_tokens` | tokens already consumed before TokenRouter tracked this account |

- `monthly_allowance` and `initial_usage_tokens` are **user-declared**. They are
  never fetched from Clean APIs. With `monthly_allowance: null` TokenRouter
  cannot verify remaining budget and ranks the account after accounts with a
  known remaining budget.
- Estimated remaining is always local:
  `allowance - initial_usage - completed_usage`; tokens reserved by in-flight
  requests are additionally excluded from what is still available.

## Accounts discovered from the environment

Any `CLEAN_API_KEY_<n>` variable that is set becomes an account automatically
(numerically ordered), so `CLEAN_API_KEY_1`, `CLEAN_API_KEY_2`,
`CLEAN_API_KEY_3` work without editing `config.yaml`. Auto-discovered accounts
get an unknown allowance; declare them in `config.yaml` to set one.

An account whose `api_key_env` variable is unset is never eligible — no request
is ever sent without a credential.

## Routing

```yaml
routing:
  strategy: max_remaining
  max_fallback_attempts: 2
  models_cache_ttl_seconds: 300
  account_exclusion_seconds: 900
```

| field | meaning |
| --- | --- |
| `strategy` | `max_remaining` is the only strategy implemented in M2 |
| `max_fallback_attempts` | total upstream attempts per request; `1` disables fallback |
| `models_cache_ttl_seconds` | how long a per-account model list is trusted |
| `account_exclusion_seconds` | exclusion window after auth/payment/rate failures |

## Token estimation (reservation only)

```yaml
token_estimate:
  chars_per_token: 4
  message_overhead_tokens: 8
  default_max_completion_tokens: 1024
  min_reservation_tokens: 32
```

These values only size the reservation made **before** the upstream call. When
the request completes, actual upstream usage replaces the estimate and the
unused reservation is released.

## Database

```yaml
database:
  path: data/tokenrouter.db
```

- `TOKENROUTER_DB_PATH` overrides it.
- SQLite in WAL mode. Accounting state is read back on startup, so a restart
  never resets usage.
- No API keys are stored — only the environment variable *name* and a boolean
  flag recording whether a key was present.

## Environment variables

| variable | purpose |
| --- | --- |
| `CLEAN_API_KEY_<n>` | one Clean APIs key per legitimate account |
| `TOKENROUTER_CONFIG` | path to `config.yaml` |
| `TOKENROUTER_HOST` / `TOKENROUTER_PORT` | bind address |
| `TOKENROUTER_UPSTREAM_BASE_URL` | upstream base URL |
| `TOKENROUTER_DB_PATH` | SQLite file location |
| `TOKENROUTER_ROUTING_STRATEGY` | routing strategy name |
| `TOKENROUTER_MAX_FALLBACK_ATTEMPTS` | bounded attempt count |
