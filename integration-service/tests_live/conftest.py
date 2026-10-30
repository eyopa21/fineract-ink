"""Live tests: run against a real stack (Integration Service, Fineract, Mock ERP) over HTTP.

Not part of `make test-python` (pytest testpaths excludes this directory). Run with
`make test-python-live` or `make simulate-concurrency`. Targets come from the environment:
  INTEGRATION_URL (default http://localhost:8100)   API_KEY (partner-demo-key)
  FINERACT_URL    (default http://localhost:8080/fineract-provider/api/v1)  admin mifos/password
  ERP_URL         (default http://localhost:8101)   ERP_API_KEY (erp-demo-key)
"""

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

INTEGRATION_URL = os.environ.get("INTEGRATION_URL", "http://localhost:8100")
API_KEY = os.environ.get("API_KEY", "partner-demo-key")
FINERACT_URL = os.environ.get("FINERACT_URL", "http://localhost:8080/fineract-provider/api/v1")
FINERACT_ADMIN = (
    os.environ.get("FINERACT_ADMIN_USERNAME", "mifos"),
    os.environ.get("FINERACT_ADMIN_PASSWORD", "password"),
)
ERP_URL = os.environ.get("ERP_URL", "http://localhost:8101")
ERP_API_KEY = os.environ.get("ERP_API_KEY", "erp-demo-key")


@dataclass
class Stack:
    api: httpx.Client
    fineract: httpx.Client
    erp: httpx.Client
    run_id: str

    # ---- helpers that read the three systems of record -----------------------------------
    def fineract_loan(self, loan_reference: str) -> dict[str, Any]:
        r = self.fineract.get(
            f"/loans/external-id/{loan_reference}", params={"associations": "transactions"}
        )
        r.raise_for_status()
        result: dict[str, Any] = r.json()
        return result

    def fineract_transactions(
        self, loan_reference: str, kind: str | None = None
    ) -> list[dict[str, Any]]:
        txs = [
            t
            for t in self.fineract_loan(loan_reference).get("transactions", [])
            if not t.get("manuallyReversed")
        ]
        if kind:
            txs = [t for t in txs if (t.get("type") or {}).get("value") == kind]
        return txs

    def erp_entry(self, reference: str) -> dict[str, Any] | None:
        r = self.erp.get(f"/api/journal-entries/{reference}")
        return r.json() if r.status_code == 200 else None

    def integration_tx(self, reference: str) -> dict[str, Any] | None:
        r = self.api.get(f"/api/transactions/{reference}")
        return r.json() if r.status_code == 200 else None

    # ---- setup helpers ---------------------------------------------------------------------
    def new_client(self, suffix: str) -> str:
        uid = f"SME-{self.run_id}{suffix}"[:50]
        r = self.api.post(
            "/api/clients",
            json={
                "external_reference": f"CLI-{uid}",
                "client_uid": uid,
                "full_name": f"Concurrency Co {uid}",
                "activation_date": "2026-01-10",
            },
        )
        assert r.status_code in (200, 201), r.text
        return uid

    def new_loan(self, client_uid: str, suffix: str, principal: int = 5000000) -> str:
        r = self.api.post(
            "/api/loans",
            json={
                "external_reference": f"APP-{client_uid}-{suffix}",
                "client_uid": client_uid,
                "principal": str(principal),
                "submitted_on": "2026-01-12",
                "expected_disbursement_date": "2026-01-15",
            },
        )
        assert r.status_code in (200, 201), r.text
        return str(r.json()["loan_reference"])

    def disburse(self, loan_reference: str, reference: str) -> httpx.Response:
        return self.api.post(
            f"/api/loans/{loan_reference}/disburse",
            json={"external_reference": reference, "disbursed_on": "2026-01-15"},
        )

    def repay(
        self, loan_reference: str, reference: str, amount: int, when: str = "2026-02-15"
    ) -> httpx.Response:
        return self.api.post(
            f"/api/loans/{loan_reference}/repayments",
            json={"external_reference": reference, "amount": str(amount), "paid_on": when},
        )


@pytest.fixture(scope="session")
def stack() -> Stack:
    api = httpx.Client(base_url=INTEGRATION_URL, headers={"X-API-Key": API_KEY}, timeout=60)
    fineract = httpx.Client(
        base_url=FINERACT_URL,
        auth=FINERACT_ADMIN,
        headers={"Fineract-Platform-TenantId": "default"},
        timeout=60,
    )
    erp = httpx.Client(base_url=ERP_URL, headers={"X-API-Key": ERP_API_KEY}, timeout=30)
    for name, client, path in (("integration", api, "/ready"), ("erp", erp, "/ready")):
        r = client.get(path)
        if r.status_code != 200:
            pytest.skip(f"{name} not reachable at {client.base_url}{path}: {r.status_code}")
    if fineract.get("/offices").status_code != 200:
        pytest.skip(f"fineract not reachable at {FINERACT_URL}")
    return Stack(api=api, fineract=fineract, erp=erp, run_id=str(int(time.time()))[-6:])
