"""Configuration loading — YAML + environment variables.

Handles:
  - server: host/port/upstream_base_url/request_timeout_seconds
  - database: local SQLite path for token accounting
  - routing: strategy, bounded fallback attempts, discovery TTL, exclusions
  - accounts: list with account_id + api_key_env + monthly_allowance + initial usage

API keys are only ever referenced by the *name* of an environment variable and
are resolved on demand by the upstream client factory. Keys are never placed in
config values, database rows, logs or responses.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from app.budget.accounts import AccountDefinition

# Environment variable names such as CLEAN_API_KEY_1 / CLEAN_API_KEY_2 are
# discovered by pattern. Account numbers are never hardcoded in application
# logic — the list of accounts is whatever the configuration and environment
# provide.
API_KEY_ENV_PATTERN = re.compile(r"^CLEAN_API_KEY_(\d+)$")
DEFAULT_API_KEY_ENV_PREFIX = "CLEAN_API_KEY"


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    upstream_base_url: str = "https://cleanapis.com/v1"
    request_timeout_seconds: int = 600


class DatabaseConfig(BaseModel):
    path: str = "data/tokenrouter.db"


class TokenEstimateSettings(BaseModel):
    chars_per_token: int = 4
    message_overhead_tokens: int = 8
    default_max_completion_tokens: int = 1024
    min_reservation_tokens: int = 32


class RoutingConfig(BaseModel):
    strategy: str = "max_remaining"
    # Total number of upstream attempts for one request (1 disables fallback).
    max_fallback_attempts: int = 2
    # How long a per-account model list is trusted before re-discovery.
    models_cache_ttl_seconds: int = 300
    # Default exclusion window after an account-level failure.
    account_exclusion_seconds: int = 900


class AccountConfig(BaseModel):
    name: str
    account_id: str | None = None
    api_key_env: str = "CLEAN_API_KEY_1"
    enabled: bool = True
    monthly_allowance: int | None = None
    initial_usage_tokens: int = 0

    @field_validator("monthly_allowance")
    @classmethod
    def validate_allowance(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("monthly_allowance must be >= 0 or null")
        return v

    @field_validator("initial_usage_tokens")
    @classmethod
    def validate_initial(cls, v: int) -> int:
        if v < 0:
            raise ValueError("initial_usage_tokens must be >= 0")
        return v

    def resolve_api_key(self) -> str | None:
        """Resolve key from environment — never logs the value."""
        return os.getenv(self.api_key_env)

    def has_api_key(self) -> bool:
        return bool(self.resolve_api_key())

    @property
    def id(self) -> str:
        """Stable identifier used everywhere (DB rows, logs, routing)."""
        return self.account_id or self.name

    def to_definition(self) -> AccountDefinition:
        return AccountDefinition(
            account_id=self.id,
            name=self.name,
            api_key_env=self.api_key_env,
            enabled=self.enabled,
            monthly_allowance=self.monthly_allowance,
            initial_usage_tokens=self.initial_usage_tokens,
            # Only the fact that a key is present is recorded — never the value.
            has_api_key=self.has_api_key(),
        )


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    token_estimate: TokenEstimateSettings = Field(default_factory=TokenEstimateSettings)
    accounts: list[AccountConfig] = Field(default_factory=list)

    def account_by_id(self, account_id: str) -> AccountConfig | None:
        for account in self.accounts:
            if account.id == account_id:
                return account
        return None

    def primary_account(self) -> AccountConfig | None:
        for a in self.accounts:
            if a.enabled:
                return a
        return None

    def get_api_key(self) -> str | None:
        """Resolve primary account key (M1 compatibility helper)."""
        acct = self.primary_account()
        if acct is None:
            return None
        return acct.resolve_api_key()

    def definitions(self) -> list[AccountDefinition]:
        return [account.to_definition() for account in self.accounts]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a mapping")
    return data


def discover_env_accounts(exclude: set[str]) -> list[AccountConfig]:
    """Build accounts from ``CLEAN_API_KEY_<n>`` environment variables.

    Sorting is numeric on the ``<n>`` suffix so key 2 precedes key 10. No
    specific account number is assumed anywhere in application logic.
    """
    found: list[tuple[int, str]] = []
    for name, value in os.environ.items():
        if not value or name in exclude:
            continue
        match = API_KEY_ENV_PATTERN.match(name)
        if match is None:
            continue
        found.append((int(match.group(1)), name))
    return [
        AccountConfig(
            name=name.lower(),
            account_id=name.lower(),
            api_key_env=name,
            enabled=True,
            monthly_allowance=None,
            initial_usage_tokens=0,
        )
        for _, name in sorted(found)
    ]


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load config from YAML + env.

    Resolution order for path:
      1. explicit config_path arg
      2. TOKENROUTER_CONFIG env var
      3. config.yaml in cwd
      4. config.example.yaml fallback (for dev without config.yaml)
      5. defaults only

    Environment overrides for server (optional):
      TOKENROUTER_HOST / TOKENROUTER_PORT / TOKENROUTER_UPSTREAM_BASE_URL
    Environment overrides for M2:
      TOKENROUTER_DB_PATH / TOKENROUTER_ROUTING_STRATEGY /
      TOKENROUTER_MAX_FALLBACK_ATTEMPTS
    """
    # Load .env if present — lightweight, no extra dependency
    _load_dotenv()

    path: Path | None = None
    if config_path is not None:
        path = Path(config_path)
    elif os.getenv("TOKENROUTER_CONFIG"):
        path = Path(os.getenv("TOKENROUTER_CONFIG", ""))
    elif Path("config.yaml").exists():
        path = Path("config.yaml")
    elif Path("config.example.yaml").exists():
        # Use example as fallback so tests/dev have something
        path = Path("config.example.yaml")

    raw: dict[str, Any] = {}
    if path is not None and path.exists():
        raw = _load_yaml(path)

    cfg = AppConfig.model_validate(raw)

    # Env overrides for server
    if os.getenv("TOKENROUTER_HOST"):
        cfg.server.host = os.getenv("TOKENROUTER_HOST", cfg.server.host)
    if os.getenv("TOKENROUTER_PORT"):
        try:
            cfg.server.port = int(os.getenv("TOKENROUTER_PORT", str(cfg.server.port)))
        except ValueError:
            pass
    if os.getenv("TOKENROUTER_UPSTREAM_BASE_URL"):
        cfg.server.upstream_base_url = os.getenv(
            "TOKENROUTER_UPSTREAM_BASE_URL", cfg.server.upstream_base_url
        )
    if os.getenv("TOKENROUTER_DB_PATH"):
        cfg.database.path = os.getenv("TOKENROUTER_DB_PATH", cfg.database.path)
    if os.getenv("TOKENROUTER_ROUTING_STRATEGY"):
        cfg.routing.strategy = os.getenv("TOKENROUTER_ROUTING_STRATEGY", cfg.routing.strategy)
    if os.getenv("TOKENROUTER_MAX_FALLBACK_ATTEMPTS"):
        try:
            cfg.routing.max_fallback_attempts = int(
                os.getenv("TOKENROUTER_MAX_FALLBACK_ATTEMPTS", "")
                or cfg.routing.max_fallback_attempts
            )
        except ValueError:
            pass

    # Ensure at least one account if none configured
    if not cfg.accounts:
        cfg.accounts = [
            AccountConfig(
                name="primary",
                api_key_env="CLEAN_API_KEY_1",
                enabled=True,
                monthly_allowance=None,
                initial_usage_tokens=0,
            )
        ]

    # Additional accounts from CLEAN_API_KEY_<n> that config did not declare.
    declared_env = {account.api_key_env for account in cfg.accounts}
    for discovered in discover_env_accounts(declared_env):
        cfg.accounts.append(discovered)

    return cfg


def _load_dotenv(dotenv_path: str | Path = ".env") -> None:
    p = Path(dotenv_path)
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        # Never fail startup due to .env parse
        pass
