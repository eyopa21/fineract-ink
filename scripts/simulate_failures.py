#!/usr/bin/env python3
"""Part J: five failure scenarios with state before, during and after recovery.

Standard library only. Targets from the environment (defaults match the compose stack):
  INTEGRATION_URL=http://localhost:8100  API_KEY=partner-demo-key
  FINERACT_URL=http://localhost:8080/fineract-provider/api/v1  (admin mifos/password for read-back)
  ERP_URL=http://localhost:8101  ERP_API_KEY=erp-demo-key
  RESTART_CMD  optional shell command that restarts the Integration Service (scenario 5). When unset,
               the startup sweep is exercised through POST /admin/recover, which runs the same code.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
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
RESTART_CMD = os.environ.get("RESTART_CMD")
RUN = str(int(time.time()))[-6:]


def http(method: str, url: str, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 60) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw.decode(errors="replace")
    except Exception as exc:  # connection refused, timeout
        return 0, f"{type(exc).__name__}: {exc}"


def api(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    return http(method, INTEGRATION + path, body, {"X-API-Key": API_KEY, "X-Correlation-ID": f"sim-j-{RUN}"})


def erp(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    return http(method, ERP + path, body, {"X-API-Key": ERP_API_KEY})


def fineract_tx(loan: str, ref: str) -> dict[str, Any] | None:
    code, body = http("GET", f"{FINERACT}/loans/external-id/{loan}/transactions/external-id/{ref}", headers={"Authorization": f"Basic {FINERACT_AUTH}", "Fineract-Platform-TenantId": "default"})
    return body if code == 200 and isinstance(body, dict) else None


def fineract_count(loan: str, kind: str) -> int:
    code, body = http("GET", f"{FINERACT}/loans/external-id/{loan}?associations=transactions", headers={"Authorization": f"Basic {FINERACT_AUTH}", "Fineract-Platform-TenantId": "default"})
    if code != 200:
        return -1
    return sum(1 for t in body.get("transactions", []) if (t.get("type") or {}).get("value") == kind and not t.get("manuallyReversed"))


def set_fault(mode: str) -> None:
    code, body = api("PUT", "/admin/faults", {"mode": mode})
    assert code == 200, (code, body)


def set_erp_fault(mode: str, delay: float = 30) -> None:
    code, body = erp("PUT", "/admin/faults", {"mode": mode, "delay_seconds": delay})
    assert code == 200, (code, body)


def tx_state(ref: str) -> str:
    code, body = api("GET", f"/api/transactions/{ref}")
    return body.get("state", "?") if code == 200 else f"none({code})"


def erp_entry(ref: str) -> str:
    code, body = erp("GET", f"/api/journal-entries/{ref}")
    return f"entry_id={body['entry_id']}" if code == 200 else "absent"


def new_loan(tag: str, disburse: bool = True) -> str:
    uid = f"SME-J{RUN}{tag}"
    code, body = api("POST", "/api/clients", {"external_reference": f"CLI-{uid}", "client_uid": uid, "full_name": f"Failure Co {uid}", "activation_date": "2026-01-10"})
    assert code in (200, 201), (code, body)
    code, body = api("POST", "/api/loans", {"external_reference": f"APP-{uid}", "client_uid": uid, "principal": "2000000", "submitted_on": "2026-01-12", "expected_disbursement_date": "2026-01-15"})
    assert code in (200, 201), (code, body)
    loan = body["loan_reference"]
    if disburse:
        code, body = api("POST", f"/api/loans/{loan}/disburse", {"external_reference": f"{loan}-DISB", "disbursed_on": "2026-01-15"})
        assert code == 200, (code, body)
    return loan


def repay(loan: str, ref: str, amount: str = "150000") -> tuple[int, Any]:
    return api("POST", f"/api/loans/{loan}/repayments", {"external_reference": ref, "amount": amount, "paid_on": "2026-02-15"})


def block(title: str, **sections: Any) -> None:
    print(f"\n==== {title}")
    for i, (name, value) in enumerate(sections.items(), 1):
        print(f"  {i}. {name.replace('_', ' ')}: {value}")


def state_line(loan: str, ref: str, kind: str = "Repayment") -> str:
    return f"integration={tx_state(ref)} | fineract {kind.lower()}s={fineract_count(loan, kind)} | erp {erp_entry(ref)}"


def main() -> int:
    print(f"== Part J failure simulations, run {RUN}, integration={INTEGRATION}")
    set_fault("none"); set_erp_fault("none")
    failures = 0

    # ---- 1. ERP down after Fineract success --------------------------------------------------
    loan = new_loan("S1"); ref = f"{loan}-R1"
    before = state_line(loan, ref)
    set_erp_fault("down")
    code, body = repay(loan, ref)
    during = state_line(loan, ref)
    dup_code, dup_body = repay(loan, ref)
    set_erp_fault("none")
    rcode, rbody = api("POST", "/admin/recover")
    after = state_line(loan, ref)
    replay_code, replay_body = repay(loan, ref)
    block("Scenario 1: ERP down after Fineract success",
          initial_state=before,
          request=f"POST /api/loans/{loan}/repayments {ref} 150000 with the ERP answering 503",
          response=f"HTTP {code} state={body.get('state') if isinstance(body, dict) else body}; duplicate while pending -> HTTP {dup_code} {dup_body.get('status') if isinstance(dup_body, dict) else dup_body}",
          fineract_state=f"repayment recorded once (fineract_transaction_id={body.get('fineract_transaction_id') if isinstance(body, dict) else '?'})",
          erp_state=f"during: {erp_entry(ref)} -> recover: completed={rbody.get('completed')} -> after: {after.split('erp ')[1]}",
          integration_state=f"during: {during.split('|')[0].strip()} -> after: {after.split('|')[0].strip()}; replay now HTTP {replay_code} state={replay_body.get('state') if isinstance(replay_body, dict) else replay_body}",
          expected="202 ERP_PENDING with Fineract done; duplicate 202 never re-executes; ERP catches up on recovery; exactly one journal entry; no double posting",
          actual=f"{code} {body.get('state') if isinstance(body, dict) else ''} -> {tx_state(ref)}, erp {erp_entry(ref)}, fineract repayments={fineract_count(loan, 'Repayment')}",
          explanation="The row persisted at FINERACT_DONE/ERP_PENDING carries Fineract's transaction id and breakdown. The sweep re-posts the same external_reference; the ERP is idempotent on it, so even a lost ERP response cannot double post.")
    failures += not (code == 202 and tx_state(ref) == "COMPLETED" and erp_entry(ref) != "absent" and fineract_count(loan, "Repayment") == 1)

    # ---- 2. Fineract down ----------------------------------------------------------------------
    loan = new_loan("S2"); ref = f"{loan}-R1"
    before = state_line(loan, ref)
    set_fault("fineract_down")
    code, body = repay(loan, ref)
    during = state_line(loan, ref)
    set_fault("none")
    rcode, rbody = repay(loan, ref)
    after = state_line(loan, ref)
    block("Scenario 2: Fineract down",
          initial_state=before,
          request=f"POST .../repayments {ref} with Fineract unreachable",
          response=f"HTTP {code} {body.get('error', {}).get('message') if isinstance(body, dict) else body}",
          fineract_state=f"nothing recorded (repayments={fineract_count(loan, 'Repayment')})",
          erp_state=f"{erp_entry(ref)} (nothing posted)",
          integration_state=f"during: {during.split('|')[0].strip()} (persisted, FAILED = nothing happened) -> retry same reference HTTP {rcode} state={rbody.get('state') if isinstance(rbody, dict) else rbody}",
          expected="caller gets 503 'nothing was recorded, safe to retry'; a FAILED row is persisted; the same reference retries cleanly once Fineract is back",
          actual=f"{code} -> {after}",
          explanation="A connection failure means the request never reached Fineract, so FAILED is a safe terminal state and the idempotency key is released for the retry.")
    failures += not (code == 503 and rcode == 200 and fineract_count(loan, "Repayment") == 1)

    # ---- 3. Fineract timeout after sending -----------------------------------------------------
    loan = new_loan("S3"); ref = f"{loan}-R1"
    before = state_line(loan, ref)
    set_fault("fineract_timeout_after_send")
    code, body = repay(loan, ref)
    set_fault("none")
    after = state_line(loan, ref)
    block("Scenario 3: Fineract timeout after sending",
          initial_state=before,
          request=f"POST .../repayments {ref}; Fineract processes it but the response is dropped",
          response=f"HTTP {code} state={body.get('state') if isinstance(body, dict) else body} fineract_transaction_id={body.get('fineract_transaction_id') if isinstance(body, dict) else '?'}",
          fineract_state=f"repayments={fineract_count(loan, 'Repayment')} (one, found by GET /loans/external-id/{loan}/transactions/external-id/{ref})",
          erp_state=erp_entry(ref),
          integration_state=after.split('|')[0].strip(),
          expected="no blind retry: the service looks the transaction up by its external id, finds it, and completes; exactly one Fineract repayment",
          actual=after,
          explanation="Our reference is Fineract's externalId, so 'did it happen?' is one GET. Re-sending would be refused by Fineract's unique externalId anyway, but the lookup avoids even that round trip.")
    failures += not (code == 200 and fineract_count(loan, "Repayment") == 1 and tx_state(ref) == "COMPLETED")

    # ---- 4. ERP timeout after the entry was written --------------------------------------------
    loan = new_loan("S4"); ref = f"{loan}-R1"
    before = state_line(loan, ref)
    set_erp_fault("timeout_after_write", delay=12)  # integration ERP read timeout is 10s
    t0 = time.time()
    code, body = repay(loan, ref)
    elapsed = round(time.time() - t0, 1)
    during = state_line(loan, ref)
    set_erp_fault("none")
    rcode, rbody = api("POST", "/admin/recover")
    after = state_line(loan, ref)
    block("Scenario 4: ERP timeout after the entry was written",
          initial_state=before,
          request=f"POST .../repayments {ref}; the ERP commits the entry then stalls 12s (> 10s client timeout)",
          response=f"HTTP {code} state={body.get('state') if isinstance(body, dict) else body} after {elapsed}s",
          fineract_state=f"repayments={fineract_count(loan, 'Repayment')}",
          erp_state=f"during: {during.split('erp ')[1]} (written although the caller timed out) -> recover: completed={rbody.get('completed')} -> after: {after.split('erp ')[1]}",
          integration_state=f"during: {during.split('|')[0].strip()} -> after: {after.split('|')[0].strip()}",
          expected="202 ERP_PENDING; on retry the ERP replays the existing entry (idempotent on external_reference); exactly one journal entry",
          actual=after,
          explanation="The retry re-posts the same reference; the ERP's unique constraint and stored request hash return the original entry with 200 instead of creating a second one.")
    failures += not (code == 202 and tx_state(ref) == "COMPLETED" and erp_entry(ref) != "absent")

    # ---- 5. Crash between Fineract success and the ERP call ------------------------------------
    loan = new_loan("S5"); ref = f"{loan}-R1"
    before = state_line(loan, ref)
    set_fault("crash_after_fineract")
    code, body = repay(loan, ref)
    during = state_line(loan, ref)
    set_fault("none")
    if RESTART_CMD:
        subprocess.run(RESTART_CMD, shell=True, check=False, capture_output=True)
        for _ in range(60):
            if api("GET", "/ready")[0] == 200:
                break
            time.sleep(2)
        how = f"restart via `{RESTART_CMD}` (startup sweep)"
    else:
        rcode, rbody = api("POST", "/admin/recover")
        how = f"POST /admin/recover (same code as the startup sweep): completed={rbody.get('completed')}"
    after = state_line(loan, ref)
    replay_code, replay_body = repay(loan, ref)
    block("Scenario 5: crash between Fineract success and the ERP call",
          initial_state=before,
          request=f"POST .../repayments {ref}; the process dies right after Fineract confirms",
          response=f"HTTP {code} {body.get('error', {}).get('code') if isinstance(body, dict) else body}",
          fineract_state=f"repayments={fineract_count(loan, 'Repayment')} (recorded before the crash)",
          erp_state=f"during: {during.split('erp ')[1]} -> after recovery: {after.split('erp ')[1]}",
          integration_state=f"during: {during.split('|')[0].strip()} (persisted FINERACT_DONE) -> {how} -> after: {after.split('|')[0].strip()}; replay HTTP {replay_code} state={replay_body.get('state') if isinstance(replay_body, dict) else replay_body}",
          expected="FINERACT_DONE survives the crash; recovery finds it and completes the ERP leg; one entry; the original reference then replays 200 COMPLETED",
          actual=after,
          explanation="State is written to the database before and after every external call, so a crash can only leave a row in a state the sweep knows how to finish.")
    failures += not (code == 500 and tx_state(ref) == "COMPLETED" and erp_entry(ref) != "absent" and replay_code == 200)

    set_fault("none"); set_erp_fault("none")
    print(f"\n== {5 - failures}/5 scenarios behaved as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
