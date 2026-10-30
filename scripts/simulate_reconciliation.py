#!/usr/bin/env python3
"""Part K: seed all four reconciliation outcomes deliberately, show the read-only report, then the
explicit idempotent repair. Standard library only. Targets from the environment (compose defaults):
  INTEGRATION_URL=http://localhost:8100  API_KEY=partner-demo-key
  FINERACT_URL=http://localhost:8080/fineract-provider/api/v1  (admin mifos/password: seeding bypasses the service)
  ERP_URL=http://localhost:8101  ERP_API_KEY=erp-demo-key
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

INTEGRATION = os.environ.get("INTEGRATION_URL", "http://localhost:8100")
API_KEY = os.environ.get("API_KEY", "partner-demo-key")
FINERACT = os.environ.get("FINERACT_URL", "http://localhost:8080/fineract-provider/api/v1")
FINERACT_AUTH = base64.b64encode(
    f"{os.environ.get('FINERACT_ADMIN_USERNAME', 'mifos')}:{os.environ.get('FINERACT_ADMIN_PASSWORD', 'password')}".encode()
).decode()
ERP = os.environ.get("ERP_URL", "http://localhost:8101")
ERP_API_KEY = os.environ.get("ERP_API_KEY", "erp-demo-key")
RUN = str(int(time.time()))[-6:]


def http(method: str, url: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw.decode(errors="replace")


def api(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    return http(method, INTEGRATION + path, body, {"X-API-Key": API_KEY, "X-Correlation-ID": f"sim-k-{RUN}"})


def erp(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    return http(method, ERP + path, body, {"X-API-Key": ERP_API_KEY})


def fineract(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    return http(method, FINERACT + path, body, {"Authorization": f"Basic {FINERACT_AUTH}", "Fineract-Platform-TenantId": "default"})


def must(code: int, body: Any, what: str, *ok: int) -> Any:
    if code not in (ok or (200, 201)):
        print(f"!! {what}: HTTP {code} {body}")
        sys.exit(1)
    return body


def show(report: dict[str, Any]) -> None:
    print(f"   summary: {report['summary']}")
    for item in report["items"]:
        fin = item.get("fineract") or {}
        e = item.get("erp") or {}
        print(
            f"   {item['external_reference']:<28} {item['status']:<20} fineract={fin.get('type'), fin.get('amount')} "
            f"erp={e.get('entry_id'), e.get('amount')} integration={item.get('integration_state')}"
            + (f" note={item['note']}" if item.get("note") else "")
            + (f" repair={item['repair']}" if item.get("repair") else "")
        )


def main() -> int:
    print(f"== Part K reconciliation demo, run {RUN}")
    uid = f"SME-K{RUN}"
    must(*api("POST", "/api/clients", {"external_reference": f"CLI-{uid}", "client_uid": uid, "full_name": f"Recon Co {uid}", "activation_date": "2026-01-10"}), "client")
    loan = must(*api("POST", "/api/loans", {"external_reference": f"APP-{uid}", "client_uid": uid, "principal": "2000000", "submitted_on": "2026-01-12", "expected_disbursement_date": "2026-01-15"}), "loan")["loan_reference"]
    must(*api("POST", f"/api/loans/{loan}/disburse", {"external_reference": f"{loan}-DISB", "disbursed_on": "2026-01-15"}), "disburse")
    must(*api("POST", f"/api/loans/{loan}/repayments", {"external_reference": f"{loan}-R1", "amount": "150000", "paid_on": "2026-02-15"}), "repayment 1")
    print(f"\n1. Initial state: loan {loan} disbursed and repaid once through the service -> 2 MATCHED expected")

    print("\n2. Seeding the other three outcomes deliberately")
    # MISSING_IN_ERP: crash after Fineract success -> FINERACT_DONE, no ERP entry
    must(*api("PUT", "/admin/faults", {"mode": "crash_after_fineract"}), "fault")
    code, body = api("POST", f"/api/loans/{loan}/repayments", {"external_reference": f"{loan}-R2", "amount": "150000", "paid_on": "2026-03-15"})
    must(*api("PUT", "/admin/faults", {"mode": "none"}), "fault reset")
    print(f"   {loan}-R2: repayment with crash_after_fineract -> HTTP {code}; Fineract has it, the ERP does not (MISSING_IN_ERP)")
    # AMOUNT_MISMATCH: a repayment posted straight into Fineract, then a wrong figure posted straight into the ERP
    must(*fineract("POST", f"/loans/external-id/{loan}/transactions?command=repayment", {"externalId": f"{loan}-R3", "transactionAmount": 150000, "transactionDate": "15 April 2026", "locale": "en", "dateFormat": "dd MMMM yyyy", "paymentTypeId": 1}), "fineract direct repayment")
    must(*erp("POST", "/api/journal-entries", {"external_reference": f"{loan}-R3", "entry_date": "2026-04-15", "description": "seeded wrong amount", "currency": "RWF", "lines": [{"account_code": "1100", "direction": "DEBIT", "amount": "140000"}, {"account_code": "1200", "direction": "CREDIT", "amount": "140000"}], "metadata": {"loan_reference": loan, "transaction_type": "REPAYMENT"}}), "erp seeded entry", 200, 201)
    print(f"   {loan}-R3: Fineract 150000 (posted directly), ERP 140000 (posted directly) (AMOUNT_MISMATCH)")
    # MISSING_IN_FINERACT: an entry only the ERP has
    must(*erp("POST", "/api/journal-entries", {"external_reference": f"{loan}-GHOST", "entry_date": "2026-05-15", "description": "seeded ghost", "currency": "RWF", "lines": [{"account_code": "1100", "direction": "DEBIT", "amount": "5000"}, {"account_code": "1200", "direction": "CREDIT", "amount": "5000"}], "metadata": {"loan_reference": loan, "transaction_type": "REPAYMENT"}}), "erp ghost entry", 200, 201)
    print(f"   {loan}-GHOST: ERP entry with no Fineract transaction (MISSING_IN_FINERACT)")

    print(f"\n3. Request: POST /api/reconciliation {{loan_references: [{loan}]}} (read-only)")
    report = must(*api("POST", "/api/reconciliation", {"loan_references": [loan]}), "reconciliation")
    print("4. Response / 5. Fineract state / 6. ERP state / 7. Integration state, per item:")
    show(report)
    print("8. Expected: MATCHED 2, MISSING_IN_ERP 1, MISSING_IN_FINERACT 1, AMOUNT_MISMATCH 1")
    print(f"9. Actual:   {report['summary']}")
    ok = report["summary"] == {"MATCHED": 2, "MISSING_IN_ERP": 1, "MISSING_IN_FINERACT": 1, "AMOUNT_MISMATCH": 1}

    print(f"\n== Repair (explicit): POST /api/reconciliation {{loan_references: [{loan}], repair: true}}")
    repaired = must(*api("POST", "/api/reconciliation", {"loan_references": [loan], "repair": True}), "repair")
    show(repaired)
    again = must(*api("POST", "/api/reconciliation", {"loan_references": [loan], "repair": True}), "repair again")
    print(f"   repair run twice -> summary unchanged: {again['summary'] == repaired['summary']}; R2 now {again['items'][[i['external_reference'] for i in again['items']].index(f'{loan}-R2')]['integration_state']}")
    print("   AMOUNT_MISMATCH and MISSING_IN_FINERACT are never auto-repaired: the ERP figure was not produced by this service, so a human decides.")
    ok = ok and repaired["summary"]["MISSING_IN_ERP"] == 0 and again["summary"] == repaired["summary"]
    print(f"\n== {'all outcomes as expected' if ok else 'UNEXPECTED RESULT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
