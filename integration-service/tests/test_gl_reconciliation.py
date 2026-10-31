"""Stretch: GL master-data reconciliation."""

from fastapi.testclient import TestClient

from app.gl_mapping import ErpAccount
from app.services.gl_reconciliation import FineractGl, compare_gl, summarise_gl
from tests.conftest import FakeErp, FakeFineract


def test_compare_gl_covers_all_outcomes() -> None:
    erp = [
        ErpAccount("1100", "Bank", "ASSET", "RWF", "GL-1100"),
        ErpAccount("1200", "Loan Receivable", "ASSET", "RWF", "GL-1200"),
        ErpAccount("4000", "Interest Income", "INCOME", "RWF", "GL-4000"),
    ]
    fin = [
        FineractGl("GL-1100", "1100", "Bank", "ASSET", 10, False),
        FineractGl("GL-1200", "1200", "Loans Receivable", "ASSET", 22, False),  # name differs
        FineractGl(None, "9000", "Manual Suspense", "ASSET", 3, False),  # no ERP account
        FineractGl("GL-OLD", "9100", "Decommissioned", "EXPENSE", 4, True),
    ]
    items = compare_gl(erp, fin)
    assert {i.key: i.status for i in items} == {
        "GL-1100": "MATCHED",
        "GL-1200": "ATTRIBUTE_MISMATCH",
        "GL-4000": "MISSING_IN_FINERACT",
        "fineract:9000": "MISSING_IN_ERP",
        "GL-OLD": "MISSING_IN_ERP",
    }
    assert next(i for i in items if i.key == "GL-1200").note == "differs in name"
    assert summarise_gl(items) == {
        "MATCHED": 1,
        "MISSING_IN_ERP": 2,
        "MISSING_IN_FINERACT": 1,
        "ATTRIBUTE_MISMATCH": 1,
    }


def test_gl_reconciliation_endpoint(
    client: TestClient, fineract: FakeFineract, erp: FakeErp
) -> None:
    assert client.post("/api/accounts/sync").status_code == 200  # 3 seed accounts synced
    fineract.extra_gl.append(
        {
            "id": 77,
            "glCode": "9000",
            "name": "Manual Suspense",
            "type": {"id": 1},
            "externalId": None,
            "disabled": False,
        }
    )
    erp.accounts.append(
        ErpAccount("4100", "Fee Income", "INCOME", "RWF", "GL-4100")
    )  # not yet synced
    report = client.post("/api/reconciliation/gl-accounts").json()
    assert report["summary"] == {
        "MATCHED": 3,
        "MISSING_IN_ERP": 1,
        "MISSING_IN_FINERACT": 1,
        "ATTRIBUTE_MISMATCH": 0,
    }
    by_key = {i["key"]: i for i in report["items"]}
    assert by_key["GL-1100"]["mapping_state"] == "SYNCED"
    assert by_key["GL-4100"]["note"] == "run POST /api/accounts/sync"
    assert by_key["fineract:9000"]["status"] == "MISSING_IN_ERP"
    # the named repair: sync, then everything from the ERP is MATCHED
    assert client.post("/api/accounts/sync").status_code == 200
    assert (
        client.post("/api/reconciliation/gl-accounts").json()["summary"]["MISSING_IN_FINERACT"] == 0
    )
