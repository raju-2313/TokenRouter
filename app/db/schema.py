"""SQLite schema — accounts, usage ledger, reservations, per-account models.

Milestone 2 extends the M0/M1 skeleton. The database holds only locally derived
accounting state. API keys are never stored: accounts are keyed by ``account_id``
and the secret stays in the environment variable named by configuration.
"""

from __future__ import annotations

SCHEMA_VERSION = 2

ACCOUNTS_DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id              TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    api_key_env             TEXT NOT NULL,
    has_api_key             INTEGER NOT NULL DEFAULT 0,
    enabled                 INTEGER NOT NULL DEFAULT 1,
    monthly_allowance       INTEGER,
    initial_usage_tokens    INTEGER NOT NULL DEFAULT 0,
    completed_tokens        INTEGER NOT NULL DEFAULT 0,
    completed_prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completed_completion_tokens INTEGER NOT NULL DEFAULT 0,
    reserved_tokens         INTEGER NOT NULL DEFAULT 0,
    excluded_until          REAL,
    exclusion_reason        TEXT,
    request_count           INTEGER NOT NULL DEFAULT 0,
    rl_request_limit        INTEGER,
    rl_request_remaining    INTEGER,
    rl_token_limit          INTEGER,
    rl_token_remaining      INTEGER,
    rl_updated_at           REAL,
    models_refreshed_at     REAL,
    created_at              REAL NOT NULL,
    updated_at              REAL NOT NULL,
    CHECK (monthly_allowance IS NULL OR monthly_allowance >= 0),
    CHECK (initial_usage_tokens >= 0),
    CHECK (completed_tokens >= 0),
    CHECK (reserved_tokens >= 0)
)
"""

RESERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS reservations (
    reservation_id TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL,
    request_id     TEXT NOT NULL,
    model          TEXT,
    reserved_tokens INTEGER NOT NULL,
    status         TEXT NOT NULL,
    actual_tokens  INTEGER,
    created_at     REAL NOT NULL,
    settled_at     REAL,
    FOREIGN KEY (account_id) REFERENCES accounts (account_id)
)
"""

USAGE_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS usage_ledger (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        TEXT NOT NULL,
    account_id        TEXT NOT NULL,
    model             TEXT,
    reservation_id    TEXT,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    usage_source      TEXT NOT NULL DEFAULT 'upstream',
    created_at        REAL NOT NULL
)
"""

ACCOUNT_MODELS_DDL = """
CREATE TABLE IF NOT EXISTS account_models (
    account_id   TEXT NOT NULL,
    model_id     TEXT NOT NULL,
    last_seen_at REAL NOT NULL,
    PRIMARY KEY (account_id, model_id)
)
"""

INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_reservations_account ON reservations (account_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_usage_ledger_account ON usage_ledger (account_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_usage_ledger_request ON usage_ledger (request_id)",
    "CREATE INDEX IF NOT EXISTS idx_account_models_account ON account_models (account_id)",
)

ALL_DDL = (
    ACCOUNTS_DDL,
    RESERVATIONS_DDL,
    USAGE_LEDGER_DDL,
    ACCOUNT_MODELS_DDL,
    *INDEX_DDL,
)
