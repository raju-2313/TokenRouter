"""Multi-account configuration (M2 items 1, 10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import (
    AccountConfig,
    AppConfig,
    discover_env_accounts,
    load_config,
)
from tests.m2_helpers import KEY_A, KEY_B, KEY_C


def test_multiple_accounts_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKENROUTER_DB_PATH", raising=False)
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.setenv("CLEAN_API_KEY_2", KEY_B)
    path = tmp_path / "config.yaml"
    path.write_text(
        """
server:
  upstream_base_url: https://cleanapis.com/v1
database:
  path: data/tokenrouter.db
routing:
  strategy: max_remaining
  max_fallback_attempts: 3
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
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path=path)

    assert [a.id for a in cfg.accounts] == ["primary-clean", "secondary-clean"]
    assert cfg.accounts[0].monthly_allowance == 50_000_000
    assert cfg.accounts[0].initial_usage_tokens == 38_000_000
    assert cfg.routing.strategy == "max_remaining"
    assert cfg.routing.max_fallback_attempts == 3
    assert cfg.database.path == "data/tokenrouter.db"
    assert cfg.account_by_id("secondary-clean") is not None
    assert cfg.account_by_id("missing") is None

    definitions = cfg.definitions()
    assert [d.account_id for d in definitions] == ["primary-clean", "secondary-clean"]
    assert all(d.has_api_key for d in definitions)


def test_account_id_defaults_to_name() -> None:
    assert AccountConfig(name="primary").id == "primary"
    assert AccountConfig(name="primary", account_id="fixed").id == "fixed"


def test_additional_keys_discovered_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.setenv("CLEAN_API_KEY_2", KEY_B)
    monkeypatch.setenv("CLEAN_API_KEY_10", KEY_C)

    discovered = discover_env_accounts(exclude=set())

    # numeric ordering, not lexicographic: 1, 2, 10
    assert [a.api_key_env for a in discovered] == [
        "CLEAN_API_KEY_1",
        "CLEAN_API_KEY_2",
        "CLEAN_API_KEY_10",
    ]
    assert all(a.has_api_key() for a in discovered)
    # unknown allowance — TokenRouter must not invent one
    assert all(a.monthly_allowance is None for a in discovered)


def test_declared_accounts_are_not_duplicated_by_env_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.setenv("CLEAN_API_KEY_2", KEY_B)
    path = tmp_path / "config.yaml"
    path.write_text(
        """
accounts:
  - name: account-a
    account_id: primary-clean
    api_key_env: CLEAN_API_KEY_1
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path=path)

    assert [a.id for a in cfg.accounts] == ["primary-clean", "clean_api_key_2"]


def test_missing_key_marks_account_as_without_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLEAN_API_KEY_9", raising=False)
    cfg = AppConfig(
        accounts=[AccountConfig(name="a", api_key_env="CLEAN_API_KEY_9")],
    )
    assert cfg.accounts[0].has_api_key() is False
    assert cfg.definitions()[0].has_api_key is False


def test_negative_allowance_is_rejected() -> None:
    with pytest.raises(ValueError):
        AccountConfig(name="a", monthly_allowance=-1)
    with pytest.raises(ValueError):
        AccountConfig(name="a", initial_usage_tokens=-1)


def test_unknown_account_id_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.setenv("CLEAN_API_TOKEN", "not-a-key-env-name")
    monkeypatch.setenv("CLEAN_API_KEY_", "malformed")
    monkeypatch.setenv("CLEAN_API_KEY_AB", "malformed")

    discovered = discover_env_accounts(exclude=set())

    assert [a.api_key_env for a in discovered] == ["CLEAN_API_KEY_1"]


def test_env_overrides_for_m2_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKENROUTER_DB_PATH", str(tmp_path / "custom.db"))
    monkeypatch.setenv("TOKENROUTER_ROUTING_STRATEGY", "max_remaining")
    monkeypatch.setenv("TOKENROUTER_MAX_FALLBACK_ATTEMPTS", "4")
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  port: 9999\n", encoding="utf-8")

    cfg = load_config(config_path=path)

    assert cfg.database.path == str(tmp_path / "custom.db")
    assert cfg.routing.max_fallback_attempts == 4
    assert cfg.server.port == 9999
