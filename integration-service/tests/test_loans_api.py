"""Parts C, E, F, I, J through the API with fake Fineract and ERP."""

from fastapi.testclient import TestClient

from tests.conftest import FakeErp, FakeFineract

CLIENT = {
    "external_reference": "CLI-001",
    "client_uid": "SME-000001",
    "full_name": "Kigali Tools Ltd",
    "activation_date": "2026-01-10",
}


def make_client(client: TestClient) -> None:
    assert client.post("/api/clients", json=CLIENT).status_code == 201, "client"


def make_loan(client: TestClient, ref: str = "APP-001") -> str:
    r = client.post(
        "/api/loans",
        json={
            "external_reference": ref,
            "client_uid": "SME-000001",
            "principal": "5000000",
            "submitted_on": "2026-01-12",
            "expected_disbursement_date": "2026-01-15",
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["loan_reference"])


def disburse(
    client: TestClient, loan: str, ref: str = "DISB-001", amount: str | None = None
) -> object:
    body: dict[str, str] = {"external_reference": ref, "disbursed_on": "2026-01-15"}
    if amount is not None:
        body["amount"] = amount
    return client.post(f"/api/loans/{loan}/disburse", json=body)


def repay(client: TestClient, loan: str, ref: str, amount: str) -> object:
    return client.post(
        f"/api/loans/{loan}/repayments",
        json={"external_reference": ref, "amount": amount, "paid_on": "2026-02-15"},
    )


# ---- Part C -------------------------------------------------------------------------------


def test_client_create_find_validate_and_no_fineract_ids_leak(
    client: TestClient, fineract: FakeFineract
) -> None:
    r = client.post("/api/clients", json=CLIENT)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body == {
        "client_uid": "SME-000001",
        "full_name": "Kigali Tools Ltd",
        "status": "ACTIVE",
        "active": True,
        "activation_date": "2026-01-10",
        "office": "Kigali Main Office",
    }
    assert "id" not in body and "500" not in r.text
    assert fineract.clients["SME-000001"]["externalId"] == "SME-000001"

    found = client.get("/api/clients/SME-000001")
    assert found.status_code == 200 and found.json()["active"] is True
    assert client.get("/api/clients/SME-999999").status_code == 404


def test_client_idempotency_same_reference_and_uid_uniqueness(
    client: TestClient, fineract: FakeFineract
) -> None:
    make_client(client)
    replay = client.post("/api/clients", json=CLIENT)
    assert replay.status_code == 201 and replay.headers["X-Idempotent-Replay"] == "true"
    assert len(fineract.clients) == 1

    conflict = client.post("/api/clients", json={**CLIENT, "full_name": "Other"})
    assert conflict.status_code == 409

    reuse = client.post("/api/clients", json={**CLIENT, "external_reference": "CLI-002"})
    assert reuse.status_code == 409, "same client_uid under a different reference"
    assert reuse.json()["error"]["details"][0]["field"] == "client_uid"
    assert len(fineract.clients) == 1


# ---- Parts D/E ----------------------------------------------------------------------------


def test_loan_application_issues_reference_and_approves(
    client: TestClient, fineract: FakeFineract
) -> None:
    make_client(client)
    ref = make_loan(client)
    assert ref.startswith("LN-20") and ref.endswith("-000001")
    assert fineract.loans[ref]["status"] == "APPROVED"
    replay = client.post(
        "/api/loans",
        json={
            "external_reference": "APP-001",
            "client_uid": "SME-000001",
            "principal": "5000000",
            "submitted_on": "2026-01-12",
            "expected_disbursement_date": "2026-01-15",
        },
    )
    assert replay.status_code == 201 and replay.json()["loan_reference"] == ref
    assert len(fineract.loans) == 1

    view = client.get(f"/api/loans/{ref}").json()
    assert view["status"]["value"] == "Approved" and view["transactions"] == []
    assert "fineract" not in str(view).lower() or "fineract_" not in view


def test_disburse_then_repay_to_closed_with_erp_entries_from_fineract_breakdown(
    client: TestClient, fineract: FakeFineract, erp: FakeErp
) -> None:
    make_client(client)
    loan = make_loan(client)
    d = disburse(client, loan)
    assert d.status_code == 200, d.text  # type: ignore[attr-defined]
    body = d.json()  # type: ignore[attr-defined]
    assert body["state"] == "COMPLETED" and body["erp_entry_id"] == 1
    assert erp.posts[0]["lines"] == [
        {"account_code": "1200", "direction": "DEBIT", "amount": "5000000"},
        {"account_code": "1100", "direction": "CREDIT", "amount": "5000000"},
    ]

    r1 = repay(client, loan, "REP-001", "1000000")
    assert r1.status_code == 200, r1.text  # type: ignore[attr-defined]
    assert r1.json()["fineract_breakdown"]["principal"] == "900000"  # type: ignore[attr-defined]
    assert erp.posts[1]["lines"] == [
        {"account_code": "1100", "direction": "DEBIT", "amount": "1000000"},
        {"account_code": "1200", "direction": "CREDIT", "amount": "900000"},
        {"account_code": "4000", "direction": "CREDIT", "amount": "100000"},
    ]

    repay(client, loan, "REP-002", "2000000")
    repay(client, loan, "REP-003", "2000000")
    final = repay(
        client, loan, "REP-004", "600000"
    )  # closes: outstanding 5000000-900000-1800000-1800000-540000 < 0
    assert final.status_code == 200  # type: ignore[attr-defined]
    view = client.get(f"/api/loans/{loan}").json()
    assert view["status"]["closed_obligations_met"] is True
    assert len(view["transactions"]) == 5
    tx = client.get("/api/transactions/REP-001").json()
    assert tx["state"] == "COMPLETED" and tx["fineract_transaction_id"] == 101


# ---- Part F / I ---------------------------------------------------------------------------


def test_same_disbursement_four_times_is_one_everywhere(
    client: TestClient, fineract: FakeFineract, erp: FakeErp
) -> None:
    make_client(client)
    loan = make_loan(client)
    statuses = [disburse(client, loan).status_code for _ in range(4)]  # type: ignore[attr-defined]
    assert statuses == [200, 200, 200, 200]
    assert (
        len([t for t in fineract.transactions.values() if t["type"]["value"] == "Disbursement"])
        == 1
    )
    assert len(erp.entries) == 1
    assert client.get("/api/transactions/DISB-001").json()["state"] == "COMPLETED"


def test_second_disbursement_with_a_different_reference_is_409_from_the_database(
    client: TestClient, fineract: FakeFineract
) -> None:
    make_client(client)
    loan = make_loan(client)
    assert disburse(client, loan).status_code == 200  # type: ignore[attr-defined]
    r = disburse(client, loan, ref="DISB-002")
    assert r.status_code == 409  # type: ignore[attr-defined]
    assert "already has a disbursement" in r.json()["error"]["message"]  # type: ignore[attr-defined]
    assert len(fineract.transactions) == 1


def test_different_payload_same_reference_is_409(client: TestClient) -> None:
    make_client(client)
    loan = make_loan(client)
    assert disburse(client, loan).status_code == 200  # type: ignore[attr-defined]
    assert disburse(client, loan, amount="5000000").status_code == 409  # type: ignore[attr-defined]


def test_disbursement_amount_defaults_to_and_must_match_the_approved_principal(
    client: TestClient, fineract: FakeFineract
) -> None:
    make_client(client)
    loan = make_loan(client)
    wrong = disburse(client, loan, ref="DISB-WRONG", amount="4999999")
    assert wrong.status_code == 422  # type: ignore[attr-defined]
    assert wrong.json()["error"]["details"][0]["approved_principal"] == "5000000.00"  # type: ignore[attr-defined]
    assert fineract.transactions == {}, "rejected before Fineract"
    explicit = disburse(client, loan, ref="DISB-OK", amount="5000000")
    assert explicit.status_code == 200  # type: ignore[attr-defined]
    from decimal import Decimal

    assert Decimal(explicit.json()["amount"]) == Decimal("5000000")  # type: ignore[attr-defined]


# ---- Part J -------------------------------------------------------------------------------


def test_fineract_down_leaves_failed_and_a_retry_succeeds(
    client: TestClient, fineract: FakeFineract
) -> None:
    make_client(client)
    loan = make_loan(client)
    fineract.tx_script = ["down"]
    r = disburse(client, loan)
    assert r.status_code == 503  # type: ignore[attr-defined]
    assert client.get("/api/transactions/DISB-001").json()["state"] == "FAILED"
    assert disburse(client, loan).status_code == 200, "same reference re-claimed after FAILED"  # type: ignore[attr-defined]
    assert len(fineract.transactions) == 1


def test_fineract_timeout_after_send_is_resolved_by_lookup(
    client: TestClient, fineract: FakeFineract, erp: FakeErp
) -> None:
    make_client(client)
    loan = make_loan(client)
    fineract.tx_script = ["timeout_after_send"]
    r = disburse(client, loan)
    assert r.status_code == 200, r.text  # type: ignore[attr-defined]
    assert r.json()["state"] == "COMPLETED"  # type: ignore[attr-defined]
    assert len(fineract.transactions) == 1 and len(erp.entries) == 1


def test_erp_down_after_fineract_success_is_202_then_recovered(
    client: TestClient, fineract: FakeFineract, erp: FakeErp
) -> None:
    make_client(client)
    loan = make_loan(client)
    erp.script = ["down"]
    r = disburse(client, loan)
    assert r.status_code == 202, r.text  # type: ignore[attr-defined]
    assert r.json()["state"] == "ERP_PENDING"  # type: ignore[attr-defined]
    assert len(fineract.transactions) == 1 and erp.entries == {}
    # a duplicate while pending is 202 with the current state, never a second execution
    again = disburse(client, loan)
    assert again.status_code == 202 and again.json()["status"] == "IN_PROGRESS"  # type: ignore[attr-defined]

    rec = client.post("/admin/recover").json()
    assert rec["completed"] == ["DISB-001"]
    assert client.get("/api/transactions/DISB-001").json()["state"] == "COMPLETED"
    assert len(erp.entries) == 1
    # now the original reference replays the final result
    done = disburse(client, loan)
    assert done.status_code == 200 and done.json()["state"] == "COMPLETED"  # type: ignore[attr-defined]


def test_crash_between_fineract_and_erp_is_recovered_on_sweep(
    client: TestClient, fineract: FakeFineract, erp: FakeErp
) -> None:
    make_client(client)
    loan = make_loan(client)
    client.put("/admin/faults", json={"mode": "crash_after_fineract"})
    r = disburse(client, loan)
    assert r.status_code == 500  # type: ignore[attr-defined]
    client.put("/admin/faults", json={"mode": "none"})
    assert client.get("/api/transactions/DISB-001").json()["state"] == "FINERACT_DONE"
    assert len(fineract.transactions) == 1 and erp.entries == {}

    rec = client.post("/admin/recover").json()
    assert rec["completed"] == ["DISB-001"]
    assert client.get("/api/transactions/DISB-001").json()["state"] == "COMPLETED"
    assert len(erp.entries) == 1, "exactly one ERP entry after recovery"


# ---- graceful upstream failures on read paths ---------------------------------------------


def test_reads_answer_503_in_the_envelope_when_fineract_is_down(
    client: TestClient, fineract: FakeFineract
) -> None:
    make_client(client)
    loan = make_loan(client)

    def down(*_: object, **__: object) -> None:
        from app.clients.fineract import FineractUnavailableError

        raise FineractUnavailableError("[Errno 111] Connection refused")

    fineract.get_client_by_external_id = down  # type: ignore[method-assign]
    fineract.get_loan_by_external_id = down  # type: ignore[method-assign]
    for path in ("/api/clients/SME-000001", f"/api/loans/{loan}"):
        r = client.get(path)
        assert r.status_code == 503, path
        body = r.json()
        assert body["error"]["code"] == "UPSTREAM_UNAVAILABLE"
        assert body["error"]["details"] == [{"system": "fineract"}]
        assert body["correlation_id"] == r.headers["X-Correlation-ID"]


def test_fineract_validation_rejection_is_422_and_the_reference_can_be_retried_corrected(
    client: TestClient, fineract: FakeFineract
) -> None:
    make_client(client)
    bad = {
        "external_reference": "APP-X",
        "client_uid": "SME-000001",
        "principal": "1000",
        "submitted_on": "2026-01-12",
        "expected_disbursement_date": "2026-01-15",
    }
    r = client.post("/api/loans", json=bad)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"][0]["parameter"] == "principal"
    assert "100000" in body["error"]["details"][0]["message"]
    assert fineract.loans == {}, "nothing created in Fineract"

    fixed = client.post("/api/loans", json={**bad, "principal": "500000"})
    assert fixed.status_code == 201, fixed.text
    assert len(fineract.loans) == 1
    assert "X-Idempotent-Replay" not in fixed.headers


def test_loan_submitted_without_approval_is_approved_by_the_approve_endpoint(
    client: TestClient, fineract: FakeFineract
) -> None:
    make_client(client)
    r = client.post(
        "/api/loans",
        json={
            "external_reference": "APP-NA",
            "client_uid": "SME-000001",
            "principal": "5000000",
            "submitted_on": "2026-01-12",
            "expected_disbursement_date": "2026-01-15",
            "approve": False,
        },
    )
    assert r.status_code == 201 and r.json()["status"] == "SUBMITTED"
    loan = r.json()["loan_reference"]
    assert fineract.loans[loan]["status"] == "SUBMITTED"
    assert disburse(client, loan).status_code == 422, "cannot disburse an unapproved loan"  # type: ignore[attr-defined]

    body = {"external_reference": "APR-1", "approved_on": "2026-01-13"}
    first = client.post(f"/api/loans/{loan}/approve", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "APPROVED" and fineract.loans[loan]["status"] == "APPROVED"
    again = client.post(f"/api/loans/{loan}/approve", json=body)
    assert again.status_code == 200 and again.headers["X-Idempotent-Replay"] == "true"
    other = client.post(f"/api/loans/{loan}/approve", json={**body, "external_reference": "APR-2"})
    assert other.status_code == 409 and "not pending approval" in other.json()["error"]["message"]
    assert disburse(client, loan).status_code == 200  # type: ignore[attr-defined]
