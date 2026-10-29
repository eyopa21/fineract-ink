"""POST/GET /api/journal-entries: balance rule, idempotency, conflicts, balances, faults."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


def disbursement(reference: str = "LN-2026-000001-DISB", amount: str = "5000000.00") -> dict:  # type: ignore[type-arg]
    return {
        "external_reference": reference,
        "entry_date": "2026-01-15",
        "description": "Disbursement LN-2026-000001",
        "currency": "RWF",
        "lines": [
            {"account_code": "1200", "direction": "DEBIT", "amount": amount},
            {"account_code": "1100", "direction": "CREDIT", "amount": amount},
        ],
        "metadata": {
            "loan_reference": "LN-2026-000001",
            "transaction_type": "DISBURSEMENT",
            "fineract_transaction_id": "17",
        },
    }


def repayment(reference: str, principal: str, interest: str) -> dict:  # type: ignore[type-arg]
    total = str(Decimal(principal) + Decimal(interest))
    return {
        "external_reference": reference,
        "entry_date": "2026-02-15",
        "description": "Repayment",
        "currency": "RWF",
        "lines": [
            {"account_code": "1100", "direction": "DEBIT", "amount": total},
            {"account_code": "1200", "direction": "CREDIT", "amount": principal},
            {"account_code": "4000", "direction": "CREDIT", "amount": interest},
        ],
    }


@pytest.mark.usefixtures("seeded")
def test_post_entry_then_replay_returns_original_once(client: TestClient) -> None:
    first = client.post("/api/journal-entries", json=disbursement())
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["status"] == "POSTED"
    assert body["total_debits"] == body["total_credits"] == "5000000.00"
    assert body["metadata"]["transaction_type"] == "DISBURSEMENT"
    entry_id = body["entry_id"]

    for _ in range(3):
        again = client.post("/api/journal-entries", json=disbursement())
        assert again.status_code == 200
        assert again.headers["X-Idempotent-Replay"] == "true"
        assert again.json()["entry_id"] == entry_id

    fetched = client.get("/api/journal-entries/LN-2026-000001-DISB")
    assert fetched.status_code == 200
    assert fetched.json()["entry_id"] == entry_id
    assert client.get("/api/journal-entries/NOPE").status_code == 404


@pytest.mark.usefixtures("seeded")
def test_same_reference_different_amount_is_409(client: TestClient) -> None:
    assert client.post("/api/journal-entries", json=disbursement()).status_code == 201
    r = client.post("/api/journal-entries", json=disbursement(amount="4999999.00"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


@pytest.mark.usefixtures("seeded")
def test_unbalanced_entry_is_rejected(client: TestClient) -> None:
    bad = disbursement()
    bad["lines"][1]["amount"] = "4000000.00"
    r = client.post("/api/journal-entries", json=bad)
    assert r.status_code == 422
    assert "does not balance" in r.text
    assert client.get("/api/journal-entries/LN-2026-000001-DISB").status_code == 404


@pytest.mark.usefixtures("seeded")
def test_unknown_account_and_currency_mismatch_are_rejected(client: TestClient) -> None:
    unknown = disbursement()
    unknown["lines"][0]["account_code"] = "7777"
    r = client.post("/api/journal-entries", json=unknown)
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["missing"] == ["7777"]

    wrong_currency = {**disbursement(), "currency": "USD"}
    r = client.post("/api/journal-entries", json=wrong_currency)
    assert r.status_code == 422
    assert "currency" in r.json()["error"]["message"]


@pytest.mark.usefixtures("seeded")
def test_balances_follow_the_entries(client: TestClient) -> None:
    assert client.post("/api/journal-entries", json=disbursement()).status_code == 201
    assert (
        client.post(
            "/api/journal-entries", json=repayment("R1", "381000.00", "75000.00")
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/journal-entries", json=repayment("R2", "386715.00", "69285.00")
        ).status_code
        == 201
    )

    receivable = client.get("/api/accounts/1200/balance").json()
    assert receivable["balance"] == "4232285.00"
    assert receivable["balance_side"] == "DEBIT"
    assert receivable["balance_amount"] == "4232285.00"
    assert receivable["entry_count"] == 3
    bank = client.get("/api/accounts/1100/balance").json()
    assert bank["balance"] == "-4088000.00"
    assert (bank["balance_side"], bank["balance_amount"]) == ("CREDIT", "4088000.00")
    income = client.get("/api/accounts/4000/balance").json()
    assert income["balance"] == "144285.00"


@pytest.mark.usefixtures("seeded")
def test_fault_down_blocks_api_but_not_admin_or_health(client: TestClient) -> None:
    assert client.put("/admin/faults", json={"mode": "down"}).status_code == 200
    r = client.get("/api/accounts")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert client.get("/health").status_code == 200
    assert client.get("/admin/faults").json()["mode"] == "down"
    assert client.put("/admin/faults", json={"mode": "none"}).status_code == 200
    assert client.get("/api/accounts").status_code == 200


@pytest.mark.usefixtures("seeded")
def test_timeout_after_write_persists_the_entry_before_stalling(client: TestClient) -> None:
    client.put("/admin/faults", json={"mode": "timeout_after_write", "delay_seconds": 0.2})
    r = client.post("/api/journal-entries", json=disbursement("SLOW-1"))
    assert r.status_code == 201
    client.put("/admin/faults", json={"mode": "none"})
    # a retry after the "timeout" is a replay, not a second entry
    again = client.post("/api/journal-entries", json=disbursement("SLOW-1"))
    assert again.status_code == 200
    assert again.json()["entry_id"] == r.json()["entry_id"]


def test_fault_mode_is_validated(client: TestClient) -> None:
    assert client.put("/admin/faults", json={"mode": "explode"}).status_code == 422
