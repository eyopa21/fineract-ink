"""POST/GET /api/accounts: idempotency, conflicts, DB-level uniqueness, balances, auth."""

from fastapi.testclient import TestClient

ACCOUNT = {
    "code": "1200",
    "name": "Loan Receivable",
    "type": "ASSET",
    "currency": "RWF",
    "external_reference": "GL-1200",
}


def test_health_and_ready_need_no_key() -> None:
    from app.main import app

    c = TestClient(app)
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/ready").status_code == 200


def test_api_key_required(client: TestClient) -> None:
    anonymous = TestClient(client.app)
    r = anonymous.get("/api/accounts")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert r.headers["X-Correlation-ID"]


def test_create_then_identical_replays_yield_one_account(client: TestClient) -> None:
    first = client.post("/api/accounts", json=ACCOUNT)
    assert first.status_code == 201
    created = first.json()
    assert created["external_reference"] == "GL-1200"

    for _ in range(3):
        again = client.post("/api/accounts", json=ACCOUNT)
        assert again.status_code == 200
        assert again.headers["X-Idempotent-Replay"] == "true"
        assert again.json() == created

    accounts = client.get("/api/accounts").json()
    assert [a["code"] for a in accounts] == ["1200"]


def test_same_reference_different_payload_is_409(client: TestClient) -> None:
    assert client.post("/api/accounts", json=ACCOUNT).status_code == 201
    r = client.post("/api/accounts", json={**ACCOUNT, "name": "Loans Receivable"})
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "CONFLICT"
    assert body["error"]["details"][0]["existing_code"] == "1200"
    assert len(client.get("/api/accounts").json()) == 1


def test_same_code_under_another_reference_is_409_from_the_database(client: TestClient) -> None:
    assert client.post("/api/accounts", json=ACCOUNT).status_code == 201
    r = client.post("/api/accounts", json={**ACCOUNT, "external_reference": "GL-OTHER"})
    assert r.status_code == 409
    assert r.json()["error"]["details"][0]["field"] == "code"


def test_validation_errors_use_the_error_envelope(client: TestClient) -> None:
    r = client.post("/api/accounts", json={**ACCOUNT, "type": "CASH", "currency": "rwf"})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    locs = {tuple(d["loc"]) for d in body["error"]["details"]}
    assert ("body", "type") in locs and ("body", "currency") in locs


def test_correlation_id_is_echoed_or_generated(client: TestClient) -> None:
    r = client.get("/api/accounts", headers={"X-Correlation-ID": "corr-123"})
    assert r.headers["X-Correlation-ID"] == "corr-123"
    r2 = client.get("/api/accounts")
    assert len(r2.headers["X-Correlation-ID"]) >= 32


def test_balance_of_seeded_account_without_entries_is_zero(client: TestClient, seeded: int) -> None:
    assert seeded == 9
    r = client.get("/api/accounts/1200/balance")
    assert r.status_code == 200
    assert r.json() == {
        "code": "1200",
        "type": "ASSET",
        "currency": "RWF",
        "debits": "0",
        "credits": "0",
        "balance": "0",
        "balance_side": "DEBIT",
        "balance_amount": "0",
        "entry_count": 0,
    }
    assert client.get("/api/accounts/9999/balance").status_code == 404


def test_seed_is_idempotent(session, seeded: int) -> None:  # type: ignore[no-untyped-def]
    from app.seed import seed_chart_of_accounts

    assert seed_chart_of_accounts(session) == 0
