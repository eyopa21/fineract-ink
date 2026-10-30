"""Part K: the four outcomes, reversal rules, and the explicit idempotent repair."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.services.reconciliation import ErpSide, FineractSide, Item, compare, summarise
from tests.conftest import FakeErp, FakeFineract
from tests.test_loans_api import disburse, make_client, make_loan, repay


def fin(ref: str, amount: str, kind: str = "REPAYMENT", reversed_: bool = False) -> FineractSide:
    return FineractSide(ref, "LN-1", kind, Decimal(amount), date(2026, 2, 15), reversed_, 7)


def erp(ref: str, amount: str, kind: str | None = "REPAYMENT") -> ErpSide:
    return ErpSide(ref, "LN-1", kind, Decimal(amount), 1, "2026-02-15")


def statuses(items: list[Item]) -> list[tuple[str, str]]:
    return [(i.external_reference, i.status) for i in items]


# ---- pure comparison ------------------------------------------------------------------------


def test_four_outcomes_are_keyed_by_external_reference() -> None:
    items = compare(
        [fin("R1", "100"), fin("R2", "200"), fin("R3", "300")],
        [erp("R1", "100"), erp("R3", "333"), erp("E9", "50")],
    )
    assert dict(statuses(items)) == {
        "R1": "MATCHED",
        "R2": "MISSING_IN_ERP",
        "R3": "AMOUNT_MISMATCH",
        "E9": "MISSING_IN_FINERACT",
    }
    assert summarise(items) == {
        "MATCHED": 1,
        "MISSING_IN_ERP": 1,
        "MISSING_IN_FINERACT": 1,
        "AMOUNT_MISMATCH": 1,
    }
    assert next(i for i in items if i.external_reference == "R3").note == "fineract 300 vs erp 333"


def test_type_difference_is_a_mismatch_even_with_equal_amounts() -> None:
    items = compare([fin("D1", "100", "DISBURSEMENT")], [erp("D1", "100", "REPAYMENT")])
    assert items[0].status == "AMOUNT_MISMATCH" and items[0].note == "type differs"


def test_reversal_expects_a_rev_entry() -> None:
    both = compare([fin("R1", "100", reversed_=True)], [erp("R1", "100"), erp("R1-REV", "100")])
    assert statuses(both) == [("R1", "MATCHED"), ("R1-REV", "MATCHED")]
    missing = compare([fin("R1", "100", reversed_=True)], [erp("R1", "100")])
    assert statuses(missing) == [("R1", "MATCHED"), ("R1-REV", "MISSING_IN_ERP")]
    ghost = compare([fin("R1", "100")], [erp("R1", "100"), erp("R1-REV", "100")])
    assert statuses(ghost) == [("R1", "MATCHED"), ("R1-REV", "MISSING_IN_FINERACT")]
    assert "without a reversed" in (ghost[1].note or "")


# ---- endpoint with seeded outcomes ----------------------------------------------------------


def test_report_seeds_all_four_outcomes_and_repair_is_explicit_and_idempotent(
    client: TestClient, fineract: FakeFineract, erp_fake: FakeErp
) -> None:
    make_client(client)
    loan = make_loan(client)
    assert disburse(client, loan).status_code == 200  # type: ignore[attr-defined]
    assert repay(client, loan, "REP-OK", "100000").status_code == 200  # type: ignore[attr-defined]

    # MISSING_IN_ERP: crash after Fineract success leaves FINERACT_DONE and no ERP entry
    client.put("/admin/faults", json={"mode": "crash_after_fineract"})
    assert repay(client, loan, "REP-CRASH", "100000").status_code == 500  # type: ignore[attr-defined]
    client.put("/admin/faults", json={"mode": "none"})

    # AMOUNT_MISMATCH: Fineract holds REP-MIS for 100000; the ERP got a different figure
    fineract.loan_transaction_by_external_id(
        loan,
        "repayment",
        {
            "externalId": "REP-MIS",
            "transactionAmount": 100000,
            "transactionDate": "15 February 2026",
        },
        "seed",
    )
    erp_fake.seed_entry("REP-MIS", loan, "99000")

    # MISSING_IN_FINERACT: an entry only the ERP knows about
    erp_fake.seed_entry("REP-GHOST", loan, "5000")

    r = client.post("/api/reconciliation", json={"loan_references": [loan]})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["repair"] is False
    assert report["summary"] == {
        "MATCHED": 2,
        "MISSING_IN_ERP": 1,
        "MISSING_IN_FINERACT": 1,
        "AMOUNT_MISMATCH": 1,
    }
    by_ref = {i["external_reference"]: i for i in report["items"]}
    assert by_ref["REP-CRASH"]["status"] == "MISSING_IN_ERP"
    assert by_ref["REP-CRASH"]["integration_state"] == "FINERACT_DONE"
    assert by_ref["REP-MIS"]["status"] == "AMOUNT_MISMATCH"
    assert by_ref["REP-MIS"]["integration_state"] is None
    assert by_ref["REP-GHOST"]["status"] == "MISSING_IN_FINERACT"
    assert by_ref["DISB-001"]["status"] == "MATCHED"
    assert by_ref["DISB-001"]["fineract"]["type"] == "DISBURSEMENT"
    assert "REP-CRASH" not in erp_fake.entries, "read-only by default"

    # explicit repair re-posts the missing entry from the stored Fineract breakdown
    fixed = client.post(
        "/api/reconciliation", json={"loan_references": [loan], "repair": True}
    ).json()
    assert fixed["summary"]["MISSING_IN_ERP"] == 0
    assert "REP-CRASH" in erp_fake.entries
    assert client.get("/api/transactions/REP-CRASH").json()["state"] == "COMPLETED"
    repaired = next(i for i in fixed["items"] if i["external_reference"] == "REP-CRASH")
    assert repaired["status"] == "MATCHED" and repaired["repair"]["outcome"] == "posted"

    # repair again: nothing changes, nothing is duplicated
    entries_before = len(erp_fake.entries)
    again = client.post(
        "/api/reconciliation", json={"loan_references": [loan], "repair": True}
    ).json()
    assert again["summary"] == fixed["summary"] and len(erp_fake.entries) == entries_before


def test_scope_by_external_references_and_date_range(client: TestClient) -> None:
    make_client(client)
    loan = make_loan(client)
    assert disburse(client, loan).status_code == 200  # type: ignore[attr-defined]
    assert repay(client, loan, "REP-A", "100000").status_code == 200  # type: ignore[attr-defined]
    only = client.post("/api/reconciliation", json={"external_references": ["REP-A"]}).json()
    assert [i["external_reference"] for i in only["items"]] == ["REP-A"]
    ranged = client.post(
        "/api/reconciliation", json={"date_from": "2026-02-01", "date_to": "2026-02-28"}
    ).json()
    assert [i["external_reference"] for i in ranged["items"]] == ["REP-A"]
    assert client.post("/api/reconciliation", json={}).status_code == 422
