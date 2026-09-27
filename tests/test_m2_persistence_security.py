"""Restart persistence, concurrency and secret-handling guarantees (M2 items 3, 9, 12, 13)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.api.deps import get_budget_manager, get_chat_service, reset_runtime
from app.core.obs import safe_payload
from tests.m2_helpers import (
    KEY_A,
    KEY_B,
    AccountBehavior,
    AccountSpec,
    FakeUpstream,
    patch_account_clients,
    write_config,
)

MILLION = 1_000_000

CHAT: dict[str, Any] = {
    "model": "claude-opus-4.8",
    "messages": [{"role": "user", "content": "hi"}],
}


async def test_accounting_survives_application_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.delenv("CLEAN_API_KEY_2", raising=False)
    upstream = FakeUpstream([AccountBehavior(key=KEY_A)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=10 * MILLION)],
    )

    service = await get_chat_service()
    first = await service.handle_chat(request_id="req_first", payload=CHAT, model="claude-opus-4.8")
    assert first.status_code == 200

    budget = await get_budget_manager()
    before = await budget.snapshot("a")
    assert before is not None
    assert before.completed_tokens == 15
    await budget.database.close()

    # Simulated restart: drop every cached runtime object, keep the SQLite file.
    await reset_runtime()
    capsys.readouterr()

    service = await get_chat_service()
    second = await service.handle_chat(
        request_id="req_second", payload=CHAT, model="claude-opus-4.8"
    )
    assert second.status_code == 200

    after = await (await get_budget_manager()).snapshot("a")
    assert after is not None
    # usage was not reset by the restart, it accumulated
    assert after.completed_tokens == 30
    assert after.estimated_remaining == 10 * MILLION - 30

    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.strip().splitlines()
        if line.startswith("{")
    ]
    completion = next(
        line
        for line in lines
        if line.get("event") == "chat_completion" and line.get("account_id") == "a"
    )
    assert completion["estimated_remaining"] == 10 * MILLION - 30
    await (await get_budget_manager()).database.close()


async def test_concurrent_requests_cannot_double_spend_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.delenv("CLEAN_API_KEY_2", raising=False)
    upstream = FakeUpstream([AccountBehavior(key=KEY_A)])
    patch_account_clients(monkeypatch, upstream.transport())
    # Enough for one reservation (~1,033 estimated tokens) but not two.
    write_config(
        tmp_path,
        monkeypatch,
        [AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=1_500)],
    )

    service = await get_chat_service()
    outcomes = await asyncio.gather(
        service.handle_chat(request_id="req_1", payload=CHAT, model="claude-opus-4.8"),
        service.handle_chat(request_id="req_2", payload=CHAT, model="claude-opus-4.8"),
    )
    statuses = sorted(outcome.status_code for outcome in outcomes)
    assert statuses == [200, 503]
    denied = next(o for o in outcomes if o.status_code == 503)
    assert denied.body["error"]["code"] == "NO_ELIGIBLE_ACCOUNT"

    budget = await get_budget_manager()
    snapshot = await budget.snapshot("a")
    assert snapshot is not None
    # only the served request was charged, and nothing stays reserved
    assert snapshot.completed_tokens == 15
    assert snapshot.reserved_tokens == 0
    assert snapshot.estimated_remaining == 1_500 - 15
    assert len(await budget.ledger()) == 1
    assert len(upstream.chat_keys()) == 1
    await budget.database.close()


async def test_concurrent_requests_both_succeed_when_budget_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.setenv("CLEAN_API_KEY_2", KEY_B)
    upstream = FakeUpstream(
        [
            AccountBehavior(key=KEY_A, chat_status=500, chat_body={"error": {"message": "x"}}),
            AccountBehavior(key=KEY_B),
        ]
    )
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=10 * MILLION),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=10 * MILLION),
        ],
    )

    service = await get_chat_service()
    outcomes = await asyncio.gather(
        *(
            service.handle_chat(request_id=f"req_{i}", payload=CHAT, model="claude-opus-4.8")
            for i in range(4)
        )
    )
    assert all(outcome.status_code == 200 for outcome in outcomes)

    budget = await get_budget_manager()
    snapshots = {s.account_id: s for s in await budget.snapshots()}
    assert snapshots["a"].completed_tokens == 0
    assert snapshots["b"].completed_tokens == 60
    assert snapshots["b"].reserved_tokens == 0
    assert snapshots["b"].estimated_remaining == 10 * MILLION - 60
    await budget.database.close()


# ------------------------------------------------------------------ test M


def test_structured_logging_redacts_non_metadata_fields() -> None:
    payload = safe_payload(
        "chat_completion",
        {
            "request_id": "req_1",
            "account_id": "a",
            "api_key": "clean_secret_value",
            "authorization": "Bearer clean_secret_value",
            "messages": [{"role": "user", "content": "secret prompt"}],
            "content": "secret completion",
            "prompt": "secret prompt",
        },
    )
    assert payload["request_id"] == "req_1"
    assert payload["account_id"] == "a"
    for field in ("api_key", "authorization", "messages", "content", "prompt"):
        assert payload[field] == "[redacted]"
    assert "clean_secret_value" not in json.dumps(payload)
    assert "secret prompt" not in json.dumps(payload)


async def test_secrets_never_appear_in_logs_or_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.setenv("CLEAN_API_KEY_2", KEY_B)
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_status=401,
                chat_body={"error": {"message": f"invalid key {KEY_A}"}},
            ),
            AccountBehavior(key=KEY_B),
        ]
    )
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=90 * MILLION),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=5 * MILLION),
        ],
    )

    service = await get_chat_service()
    outcome = await service.handle_chat(
        request_id="req_1",
        payload={**CHAT, "messages": [{"role": "user", "content": "SECRET-PROMPT-MARKER"}]},
        model="claude-opus-4.8",
    )
    out = capsys.readouterr().out

    assert outcome.status_code == 200
    for secret in (KEY_A, KEY_B):
        assert secret not in out
    assert "SECRET-PROMPT-MARKER" not in out
    assert "SECRET-PROMPT-MARKER" not in json.dumps(outcome.body)


async def test_database_never_stores_api_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    upstream = FakeUpstream([AccountBehavior(key=KEY_A)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=10 * MILLION)],
    )

    service = await get_chat_service()
    await service.handle_chat(request_id="req_1", payload=CHAT, model="claude-opus-4.8")
    budget = await get_budget_manager()
    await budget.database.close()

    database_file = Path(os.environ["TOKENROUTER_DB_PATH"])
    assert database_file.exists()
    raw = database_file.read_bytes()
    assert KEY_A.encode() not in raw
    # the environment variable *name* is stored, never its value
    assert b"CLEAN_API_KEY_1" in raw
