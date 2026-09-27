"""Multi-account routing over the public API (M2 items 1, 4, 5, 6, 7, 8, 11).

All upstream traffic is mocked; no real Clean APIs credentials are used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_budget_manager, get_registry
from app.main import app
from tests.m2_helpers import (
    KEY_A,
    KEY_B,
    KEY_C,
    AccountBehavior,
    AccountSpec,
    FakeUpstream,
    patch_account_clients,
    write_config,
)

MILLION = 1_000_000

CHAT_BODY: dict[str, Any] = {
    "model": "claude-opus-4.8",
    "messages": [{"role": "user", "content": "hi"}],
}


def _post(client: TestClient, **overrides: Any):
    payload = {**CHAT_BODY, **overrides}
    return client.post("/v1/chat/completions", json=payload)


@pytest.fixture
def two_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.setenv("CLEAN_API_KEY_2", KEY_B)
    monkeypatch.delenv("CLEAN_API_KEY_3", raising=False)


@pytest.fixture
def three_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)
    monkeypatch.setenv("CLEAN_API_KEY_2", KEY_B)
    monkeypatch.setenv("CLEAN_API_KEY_3", KEY_C)


# ------------------------------------------------------------------ test A


async def test_single_account_request_succeeds_and_records_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_A)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=50 * MILLION)],
    )
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "Hello"
    assert upstream.chat_keys() == [KEY_A]

    budget = await get_budget_manager()
    snapshot = await budget.snapshot("a")
    assert snapshot is not None
    assert snapshot.completed_tokens == 15
    assert snapshot.reserved_tokens == 0
    assert snapshot.estimated_remaining == 50 * MILLION - 15
    ledger = await budget.ledger()
    assert [entry["total_tokens"] for entry in ledger] == [15]


# ------------------------------------------------------------------ test B


async def test_highest_remaining_account_is_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    # A: 50M allowance, 38M used  -> ~12M remaining
    # B: 50M allowance, 19M used  -> ~31M remaining
    upstream = FakeUpstream([AccountBehavior(key=KEY_A), AccountBehavior(key=KEY_B)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(
                name="a",
                api_key_env="CLEAN_API_KEY_1",
                allowance=50 * MILLION,
                initial_usage=38 * MILLION,
            ),
            AccountSpec(
                name="b",
                api_key_env="CLEAN_API_KEY_2",
                allowance=50 * MILLION,
                initial_usage=19 * MILLION,
            ),
        ],
    )

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 200
    assert upstream.chat_keys() == [KEY_B]

    budget = await get_budget_manager()
    a = await budget.snapshot("a")
    b = await budget.snapshot("b")
    assert a is not None and b is not None
    assert b.completed_tokens == 15
    assert a.completed_tokens == 0


# ------------------------------------------------------------------ test C


async def test_exhausted_account_is_never_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_A), AccountBehavior(key=KEY_B)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            # A is fully consumed
            AccountSpec(
                name="a",
                api_key_env="CLEAN_API_KEY_1",
                allowance=1_000,
                initial_usage=1_000,
            ),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=5 * MILLION),
        ],
    )

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 200
    assert upstream.chat_keys() == [KEY_B]

    budget = await get_budget_manager()
    a = await budget.snapshot("a")
    assert a is not None
    assert a.estimated_remaining == 0
    assert a.completed_tokens == 0


async def test_disabled_account_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_A), AccountBehavior(key=KEY_B)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(
                name="a",
                api_key_env="CLEAN_API_KEY_1",
                allowance=90 * MILLION,
                enabled=False,
            ),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=5 * MILLION),
        ],
    )

    client = TestClient(app)
    assert _post(client).status_code == 200
    assert upstream.chat_keys() == [KEY_B]


# ------------------------------------------------------------------ test G


async def test_requested_model_is_preserved_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(key=KEY_A, models=["claude-opus-4.8"]),
            AccountBehavior(key=KEY_B, models=["claude-opus-4.8"]),
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

    client = TestClient(app)
    resp = _post(client, model="claude-opus-4.8")

    assert resp.status_code == 200
    forwarded = upstream.last_chat_body()
    assert forwarded["model"] == "claude-opus-4.8"
    # account selection never rewrites the model
    assert forwarded["messages"] == CHAT_BODY["messages"]


async def test_model_is_preserved_across_account_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                models=["claude-opus-4.8"],
                chat_status=500,
                chat_body={"error": {"message": "boom"}},
            ),
            AccountBehavior(key=KEY_B, models=["claude-opus-4.8"]),
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

    client = TestClient(app)
    resp = _post(client, model="claude-opus-4.8")

    assert resp.status_code == 200
    assert upstream.chat_keys() == [KEY_A, KEY_B]
    for body in (r["body"] for r in upstream.chat_requests()):
        assert body["model"] == "claude-opus-4.8"


# ------------------------------------------------------------------ test H


async def test_model_unavailable_for_selected_account_returns_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(key=KEY_A, models=["gpt-4o"]),
            AccountBehavior(key=KEY_B, models=["gpt-4o"]),
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
    # Catalogue says the model exists upstream in general (advertised elsewhere),
    # but neither configured account serves it.
    get_registry().upsert_raw({"id": "claude-opus-4.8", "object": "model"})

    client = TestClient(app)
    resp = _post(client, model="claude-opus-4.8")

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MODEL_NOT_AVAILABLE"
    assert upstream.chat_keys() == []


async def test_request_routes_to_account_that_serves_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(key=KEY_A, models=["gpt-4o"]),
            AccountBehavior(key=KEY_B, models=["claude-opus-4.8"]),
        ]
    )
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            # A has more remaining budget but cannot serve the requested model
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=90 * MILLION),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=5 * MILLION),
        ],
    )
    get_registry().upsert_raw({"id": "claude-opus-4.8", "object": "model"})

    client = TestClient(app)
    resp = _post(client, model="claude-opus-4.8")

    assert resp.status_code == 200
    assert upstream.chat_keys() == [KEY_B]


# ------------------------------------------------------------------ test I


async def test_upstream_429_does_not_deduct_monthly_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_status=429,
                chat_headers={"Retry-After": "120"},
                chat_body={"error": {"message": "rate limited"}},
            )
        ]
    )
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=10 * MILLION)],
    )
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "UPSTREAM_RATE_LIMITED"
    assert resp.json()["error"]["retry_after"] == 120

    budget = await get_budget_manager()
    snapshot = await budget.snapshot("a")
    assert snapshot is not None
    # per-minute rate limiting must not be booked against the monthly estimate
    assert snapshot.completed_tokens == 0
    assert snapshot.estimated_remaining == 10 * MILLION
    assert snapshot.reserved_tokens == 0
    assert await budget.ledger() == []


async def test_429_marks_a_cooldown_on_the_rate_limited_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_status=429,
                chat_headers={"Retry-After": "30"},
                chat_body={"error": {"message": "rate limited"}},
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

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 200
    assert upstream.chat_keys() == [KEY_A, KEY_B]

    budget = await get_budget_manager()
    a = await budget.snapshot("a")
    assert a is not None
    assert a.is_excluded() is True
    assert a.exclusion_reason == "UPSTREAM_RATE_LIMITED"
    assert a.estimated_remaining == 90 * MILLION
    await budget.database.close()


# ------------------------------------------------------------------ test J


async def test_upstream_401_excludes_account_and_hides_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_status=401,
                chat_body={"error": {"message": "invalid api key"}},
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

    client = TestClient(app)
    resp = _post(client)

    assert upstream.chat_keys() == [KEY_A, KEY_B]
    assert resp.status_code == 200
    for key in (KEY_A, KEY_B):
        assert key not in resp.text

    budget = await get_budget_manager()
    a = await budget.snapshot("a")
    assert a is not None
    assert a.exclusion_reason == "UPSTREAM_AUTH_ERROR"
    assert a.is_excluded() is True
    # a rejected credential is not a budget event
    assert a.completed_tokens == 0
    assert a.estimated_remaining == 90 * MILLION
    await budget.database.close()


async def test_only_account_failing_401_is_excluded_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_status=401,
                chat_body={"error": {"message": "invalid api key"}},
            )
        ]
    )
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=10 * MILLION)],
    )
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UPSTREAM_AUTH_ERROR"
    assert KEY_A not in resp.text

    # The excluded account is no longer eligible.
    second = _post(client)
    assert second.status_code == 503
    assert second.json()["error"]["code"] == "NO_ELIGIBLE_ACCOUNT"
    assert len(upstream.chat_keys()) == 1
    await budget_close()


async def budget_close() -> None:
    budget = await get_budget_manager()
    await budget.database.close()


# ------------------------------------------------------------------ test K


async def test_no_eligible_account_returns_normalized_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_A)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        # everything already consumed this month
        [
            AccountSpec(
                name="a",
                api_key_env="CLEAN_API_KEY_1",
                allowance=1_000,
                initial_usage=1_000,
            )
        ],
    )
    monkeypatch.setenv("CLEAN_API_KEY_1", KEY_A)

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "NO_ELIGIBLE_ACCOUNT"
    assert "eligible" in body["error"]["message"].lower()
    assert body["error"]["request_id"].startswith("req_")
    assert upstream.chat_keys() == []


# ------------------------------------------------------------------ test 1


# ------------------------------------------------------------------ pinning


async def test_explicitly_pinned_account_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_A), AccountBehavior(key=KEY_B)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=90 * MILLION),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=5 * MILLION),
        ],
    )

    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json=CHAT_BODY,
        headers={"x-tokenrouter-account": "b"},
    )

    # A has more remaining budget, but the caller pinned b
    assert resp.status_code == 200
    assert upstream.chat_keys() == [KEY_B]


async def test_pinned_account_is_never_silently_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(key=KEY_A),
            AccountBehavior(key=KEY_B),
        ]
    )
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=90 * MILLION),
            # b has no budget for this request, a would be a fine substitute
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=10),
        ],
    )

    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json=CHAT_BODY,
        headers={"x-tokenrouter-account": "b"},
    )

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "NO_ELIGIBLE_ACCOUNT"
    assert "b" in resp.json()["error"]["message"]
    assert upstream.chat_keys() == []


async def test_unknown_pinned_account_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_A), AccountBehavior(key=KEY_B)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=90 * MILLION),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=5 * MILLION),
        ],
    )

    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json=CHAT_BODY,
        headers={"x-tokenrouter-account": "does-not-exist"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"
    assert upstream.chat_keys() == []


async def test_three_clean_api_keys_are_configured_and_routed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, three_keys: None
) -> None:
    """CLEAN_API_KEY_1/2/3 all become accounts; the best funded one is used."""
    upstream = FakeUpstream(
        [
            AccountBehavior(key=KEY_A),
            AccountBehavior(key=KEY_B),
            AccountBehavior(key=KEY_C),
        ]
    )
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(
                name="a",
                api_key_env="CLEAN_API_KEY_1",
                allowance=10 * MILLION,
                initial_usage=9 * MILLION,
            ),
            AccountSpec(
                name="b",
                api_key_env="CLEAN_API_KEY_2",
                allowance=10 * MILLION,
                initial_usage=1 * MILLION,
            ),
            AccountSpec(
                name="c",
                api_key_env="CLEAN_API_KEY_3",
                allowance=10 * MILLION,
                initial_usage=5 * MILLION,
            ),
        ],
    )

    client = TestClient(app)
    assert _post(client).status_code == 200
    assert upstream.chat_keys() == [KEY_B]  # ~9M remaining beats ~5M and ~1M

    budget = await get_budget_manager()
    snapshots = {s.account_id: s for s in await budget.snapshots()}
    assert set(snapshots) == {"a", "b", "c"}
    assert snapshots["a"].estimated_remaining == 1 * MILLION
    assert snapshots["b"].estimated_remaining == 9 * MILLION - 15
    assert snapshots["c"].estimated_remaining == 5 * MILLION
    await budget.database.close()


async def test_account_without_api_key_is_not_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_B)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            # CLEAN_API_KEY_MISSING is declared but never set in the environment
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_MISSING", allowance=90 * MILLION),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=5 * MILLION),
        ],
    )
    monkeypatch.delenv("CLEAN_API_KEY_MISSING", raising=False)
    monkeypatch.setenv("CLEAN_API_KEY_2", KEY_B)
    monkeypatch.setenv("CLEAN_API_KEY_3", KEY_C)

    client = TestClient(app)
    resp = _post(client)

    # the account without a usable credential is skipped for the one that has it
    assert resp.status_code == 200
    assert upstream.chat_keys() == [KEY_B]

    budget = await get_budget_manager()
    a = await budget.snapshot("a")
    assert a is not None
    assert a.has_api_key is False
    assert a.completed_tokens == 0
    await budget.database.close()


# ------------------------------------------------------------------ test 8


async def test_malformed_request_is_not_retried_on_another_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_status=400,
                chat_body={"error": {"message": "bad request"}},
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

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"
    # a malformed request must not be replayed against another account
    assert upstream.chat_keys() == [KEY_A]


async def test_fallback_is_bounded_by_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_status=500,
                chat_body={"error": {"message": "boom"}},
            ),
            AccountBehavior(
                key=KEY_B,
                chat_status=500,
                chat_body={"error": {"message": "boom"}},
            ),
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
        max_fallback_attempts=2,
    )

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert len(upstream.chat_keys()) == 2
    budget = await get_budget_manager()
    assert await budget.ledger() == []
    a = await budget.snapshot("a")
    b = await budget.snapshot("b")
    assert a is not None and b is not None
    assert a.reserved_tokens == 0 and b.reserved_tokens == 0
    await budget.database.close()


async def test_fallback_can_be_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_status=500,
                chat_body={"error": {"message": "boom"}},
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
        max_fallback_attempts=1,
    )

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 502
    assert upstream.chat_keys() == [KEY_A]


async def test_original_failure_is_returned_when_fallback_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_status=402,
                chat_body={"error": {"message": "upstream budget exhausted"}},
            ),
            AccountBehavior(
                key=KEY_B,
                chat_status=401,
                chat_body={"error": {"message": "invalid api key"}},
            ),
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

    client = TestClient(app)
    resp = _post(client)

    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "UPSTREAM_PAYMENT_REQUIRED"
    assert upstream.chat_keys() == [KEY_A, KEY_B]


# ------------------------------------------------------------------ test 12


async def test_rate_limit_headers_do_not_alter_budget_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream(
        [
            AccountBehavior(
                key=KEY_A,
                chat_headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "100"},
            ),
            AccountBehavior(key=KEY_B),
        ]
    )
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            # A has the larger monthly budget, B has far less remaining
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=5 * MILLION),
            AccountSpec(
                name="b",
                api_key_env="CLEAN_API_KEY_2",
                allowance=5 * MILLION,
                initial_usage=4 * MILLION,
            ),
        ],
    )

    client = TestClient(app)
    assert _post(client).status_code == 200
    # an exhausted per-minute window does not change monthly account ranking
    assert upstream.chat_keys() == [KEY_A]

    budget = await get_budget_manager()
    a = await budget.snapshot("a")
    assert a is not None
    assert a.rate_limit is not None
    assert a.rate_limit.request_remaining == 0
    # monthly estimate untouched by per-minute headers
    assert a.estimated_remaining == 5 * MILLION - 15
    b = await budget.snapshot("b")
    assert b is not None
    assert b.completed_tokens == 0
    assert b.rate_limit is None
    await budget.database.close()


async def test_models_endpoint_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_A, models=["claude-opus-4.8", "gpt-4o"])])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=50 * MILLION),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=50 * MILLION),
        ],
    )

    client = TestClient(app)
    resp = client.get("/v1/models")

    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()["data"]}
    assert ids == {"claude-opus-4.8", "gpt-4o"}


async def test_health_is_unaffected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_keys: None
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_A)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=50 * MILLION)],
    )
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ------------------------------------------------------------------ observability


async def test_structured_log_contains_routing_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    two_keys: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    upstream = FakeUpstream([AccountBehavior(key=KEY_A), AccountBehavior(key=KEY_B)])
    patch_account_clients(monkeypatch, upstream.transport())
    write_config(
        tmp_path,
        monkeypatch,
        [
            AccountSpec(name="a", api_key_env="CLEAN_API_KEY_1", allowance=1 * MILLION),
            AccountSpec(name="b", api_key_env="CLEAN_API_KEY_2", allowance=50 * MILLION),
        ],
    )

    client = TestClient(app)
    assert _post(client).status_code == 200

    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.strip().splitlines() if line.startswith("{")]
    completion = next(
        line for line in lines if line.get("event") == "chat_completion" and "total_tokens" in line
    )
    assert completion["request_id"].startswith("req_")
    assert completion["account_id"] == "b"
    assert completion["model"] == "claude-opus-4.8"
    assert completion["routing_strategy"] == "max_remaining"
    assert completion["reserved_tokens"] > 0
    assert completion["total_tokens"] == 15
    assert completion["upstream_status"] == 200
    assert completion["estimated_remaining"] == 50 * MILLION - 15
    for key in (KEY_A, KEY_B):
        assert key not in out
    assert "hi" not in completion.get("model", "")  # prompt content is never logged
