"""Config loading — M1 requirement 1, 8, never expose keys."""

from __future__ import annotations

import os

from app.config.settings import AccountConfig, AppConfig, ServerConfig, load_config
from app.core.masking import mask_api_key


def test_clean_api_key_loaded_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CLEAN_API_KEY_1", "clean_abc123456789")
    cfg = AppConfig(
        server=ServerConfig(),
        accounts=[AccountConfig(name="primary", api_key_env="CLEAN_API_KEY_1")],
    )
    assert cfg.get_api_key() == "clean_abc123456789"
    # Masked form must not reveal full key
    assert mask_api_key(cfg.get_api_key()) == "clean_a...6789"
    assert "clean_abc123456789" not in mask_api_key(cfg.get_api_key())


def test_monthly_allowance_null_means_unknown() -> None:
    cfg = AccountConfig(name="primary", monthly_allowance=None)
    assert cfg.monthly_allowance is None


def test_initial_usage_tokens_default() -> None:
    cfg = AccountConfig(name="primary")
    assert cfg.initial_usage_tokens == 0


def test_load_config_defaults(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Ensure no config file interferes
    monkeypatch.chdir(tmp_path)
    for k in list(os.environ):
        if k.startswith("CLEAN_API_KEY"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLEAN_API_KEY_1", "sk-test-1234567890")
    cfg = load_config(config_path=tmp_path / "nonexistent.yaml")
    assert cfg.server.upstream_base_url == "https://cleanapis.com/v1"
    assert cfg.server.request_timeout_seconds == 600
    assert cfg.get_api_key() == "sk-test-1234567890"


def test_config_yaml_overrides(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLEAN_API_KEY_1", "sk-test-1234567890")
    p = tmp_path / "cfg.yaml"
    p.write_text(
        """
server:
  host: 0.0.0.0
  port: 9999
  upstream_base_url: https://example.com/v1
  request_timeout_seconds: 30
accounts:
  - name: primary
    api_key_env: CLEAN_API_KEY_1
    enabled: true
    monthly_allowance: 10000
    initial_usage_tokens: 123
""",
        encoding="utf-8",
    )
    cfg = load_config(config_path=p)
    assert cfg.server.port == 9999
    assert cfg.accounts[0].monthly_allowance == 10000
    assert cfg.accounts[0].initial_usage_tokens == 123
