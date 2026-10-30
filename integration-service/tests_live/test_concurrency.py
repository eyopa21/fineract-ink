"""Part I: duplicates and concurrency, proven against the live stack with real parallel HTTP.

Layers that make "exactly one disbursement" hold, from first to last:
  1. API idempotency key: same reference -> one execution, replays/202 for the rest
     (unique (operation_type, external_reference) in idempotency_keys).
  2. Integration DB constraint: one non-FAILED DISBURSEMENT row per loan_reference (partial unique
     index) -> different references on the same loan collide at INSERT, before Fineract is called.
  3. Fineract transaction externalId: unique in m_loan_transaction -> a re-sent reference is refused.
  4. Fineract loan state: an active loan cannot be disbursed again -> refused even with a fresh
     reference and an empty integration database.
Why one layer is not enough: (1) only sees identical references; (2) lives in our database and
disappears if it is wiped or bypassed; (3) only sees identical references. The last line of
defence is (4): Fineract's own loan state machine, which holds independently of everything we built.

Each test prints an evidence block: initial state, request, responses, Fineract state, ERP state,
integration state, expected, actual.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from functools import partial

import httpx

from tests_live.conftest import Stack

PARALLEL = 12


def _fire(calls: list[Callable[[], httpx.Response]]) -> list[httpx.Response]:
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        return list(pool.map(lambda fn: fn(), calls))


def _codes(responses: list[httpx.Response]) -> dict[int, int]:
    out: dict[int, int] = {}
    for r in responses:
        out[r.status_code] = out.get(r.status_code, 0) + 1
    return dict(sorted(out.items()))


def _print_block(title: str, **sections: object) -> None:
    print(f"\n==== {title}")
    for name, value in sections.items():
        print(f"  {name.replace('_', ' ')}: {value}")


# ---- I1 sequential duplicates --------------------------------------------------------------


def test_i1_same_disbursement_four_times_sequentially(stack: Stack) -> None:
    uid = stack.new_client("I1")
    loan = stack.new_loan(uid, "I1")
    ref = f"{loan}-DISB"
    before = len(stack.fineract_transactions(loan, "Disbursement"))

    responses = [stack.disburse(loan, ref) for _ in range(4)]

    fineract_disb = stack.fineract_transactions(loan, "Disbursement")
    erp = stack.erp_entry(ref)
    tx = stack.integration_tx(ref)
    _print_block(
        "I1 sequential duplicates",
        initial_state=f"loan {loan} Approved, Fineract disbursements={before}",
        request=f"POST /api/loans/{loan}/disburse external_reference={ref} x4 (sequential)",
        responses=_codes(responses),
        fineract_state=f"disbursements={len(fineract_disb)} ids={[t['id'] for t in fineract_disb]}",
        erp_state=f"journal entries for {ref}: {1 if erp else 0} (entry_id={erp['entry_id'] if erp else None})",
        integration_state=f"transactions for {ref}: {1 if tx else 0} state={tx['state'] if tx else None}",
        expected="requests 4, Fineract disbursements 1, ERP journal entries 1, integration transactions 1",
        actual=f"requests {len(responses)}, Fineract disbursements {len(fineract_disb)}, ERP entries {1 if erp else 0}, integration transactions {1 if tx else 0}",
    )
    assert _codes(responses) == {200: 4}
    assert len(fineract_disb) == 1
    assert erp is not None and tx is not None and tx["state"] == "COMPLETED"
    assert all(
        r.json()["fineract_transaction_id"] == tx["fineract_transaction_id"] for r in responses
    )
    assert sum(1 for r in responses if r.headers.get("X-Idempotent-Replay") == "true") == 3


# ---- I2 concurrent disbursements -----------------------------------------------------------


def test_i2a_ten_concurrent_disbursements_same_reference(stack: Stack) -> None:
    uid = stack.new_client("I2A")
    loan = stack.new_loan(uid, "I2A")
    ref = f"{loan}-DISB"

    responses = _fire([partial(stack.disburse, loan, ref) for _ in range(PARALLEL)])
    # any 202 (in progress at the moment of the race) settles on a follow-up
    settled = [stack.disburse(loan, ref) if r.status_code == 202 else r for r in responses]

    fineract_disb = stack.fineract_transactions(loan, "Disbursement")
    erp = stack.erp_entry(ref)
    tx = stack.integration_tx(ref)
    _print_block(
        "I2a concurrent disbursements, same reference",
        initial_state=f"loan {loan} Approved, no disbursement",
        request=f"{PARALLEL} simultaneous POST .../disburse external_reference={ref}",
        responses=f"first wave {_codes(responses)}, after settling 202s {_codes(settled)}",
        fineract_state=f"disbursements={len(fineract_disb)}",
        erp_state=f"entries for {ref}: {1 if erp else 0}",
        integration_state=f"transactions for {ref}: {1 if tx else 0} state={tx['state'] if tx else None}",
        expected="exactly one Fineract disbursement, one ERP entry, one integration transaction",
        actual=f"{len(fineract_disb)} / {1 if erp else 0} / {1 if tx else 0}",
    )
    assert set(_codes(responses)) <= {200, 202}
    assert _codes(settled) == {200: PARALLEL}
    assert (
        len(fineract_disb) == 1
        and erp is not None
        and tx is not None
        and tx["state"] == "COMPLETED"
    )


def test_i2b_ten_concurrent_disbursements_different_references(stack: Stack) -> None:
    uid = stack.new_client("I2B")
    loan = stack.new_loan(uid, "I2B")
    refs = [f"{loan}-DISB-{i:02d}" for i in range(PARALLEL)]

    responses = _fire([partial(stack.disburse, loan, r) for r in refs])

    fineract_disb = stack.fineract_transactions(loan, "Disbursement")
    erp_entries = [r for r in refs if stack.erp_entry(r)]
    tx_states = {r: (stack.integration_tx(r) or {}).get("state") for r in refs}
    winners = [r for r, s in tx_states.items() if s == "COMPLETED"]
    losers_409 = [r for r in responses if r.status_code == 409]
    layer = {"db_index": 0, "fineract": 0}
    for r in losers_409:
        msg = r.json()["error"]["message"]
        layer["db_index" if "already has a disbursement" in msg else "fineract"] += 1
    _print_block(
        "I2b concurrent disbursements, different references",
        initial_state=f"loan {loan} Approved, no disbursement",
        request=f"{PARALLEL} simultaneous POST .../disburse with references {refs[0]}..{refs[-1]}",
        responses=f"{_codes(responses)}; 409s refused by: {layer}",
        fineract_state=f"disbursements={len(fineract_disb)}",
        erp_state=f"entries: {len(erp_entries)} {erp_entries}",
        integration_state=f"COMPLETED={winners}, other rows={ {k: v for k, v in tx_states.items() if v and v != 'COMPLETED'} }",
        expected="exactly one disbursement in Fineract, one ERP entry, one COMPLETED transaction; the rest 409",
        actual=f"{len(fineract_disb)} / {len(erp_entries)} / {len(winners)}; {len(losers_409)} x 409",
    )
    assert len(fineract_disb) == 1
    assert len(erp_entries) == 1 and len(winners) == 1
    assert _codes(responses) == {200: 1, 409: PARALLEL - 1}


# ---- I3 concurrent repayments --------------------------------------------------------------


def test_i3_concurrent_repayments_apply_exactly_once(stack: Stack) -> None:
    uid = stack.new_client("I3")
    loan = stack.new_loan(uid, "I3")
    assert stack.disburse(loan, f"{loan}-DISB").status_code == 200
    initial = Decimal(str(stack.fineract_loan(loan)["summary"]["totalOutstanding"]))

    n_unique = 10
    refs = [f"{loan}-R{i:02d}" for i in range(n_unique)]
    amounts = {r: 100000 + i * 1000 for i, r in enumerate(refs)}
    duplicated = refs[0]
    calls: list[Callable[[], httpx.Response]] = [
        partial(stack.repay, loan, r, amounts[r]) for r in refs
    ]
    # duplicates of one reference, fired in the same wave
    calls += [partial(stack.repay, loan, duplicated, amounts[duplicated]) for _ in range(3)]

    responses = _fire(calls)
    # Fineract serialises writes on one loan with optimistic locking: a loser gets 503 "safe to
    # retry" with nothing recorded (FAILED row). Settling = re-sending every reference once; the
    # idempotency layer turns that into a replay (200), a 202 while pending, or a real retry.
    retried = 0
    for ref in refs:
        again = stack.repay(loan, ref, amounts[ref])
        assert again.status_code in (200, 202), again.text
        if again.headers.get("X-Idempotent-Replay") != "true":
            retried += 1
    for _ in range(3):  # settle any ERP_PENDING 202s via the recovery endpoint
        stack.api.post("/admin/recover")

    fineract_repayments = stack.fineract_transactions(loan, "Repayment")
    by_ref = {t.get("externalId"): t for t in fineract_repayments}
    recorded = {r: Decimal(str(by_ref[r]["amount"])) for r in refs if r in by_ref}
    final = Decimal(str(stack.fineract_loan(loan)["summary"]["totalOutstanding"]))
    erp_entries = [r for r in refs if stack.erp_entry(r)]
    _print_block(
        "I3 concurrent repayments, different references + duplicates of one",
        initial_state=f"loan {loan} Active, Fineract totalOutstanding={initial}",
        request=f"{n_unique} simultaneous repayments ({refs[0]}..{refs[-1]}, amounts {min(amounts.values())}..{max(amounts.values())}) + 3 duplicates of {duplicated}",
        responses=f"first wave {_codes(responses)}; settling pass re-executed {retried} (the rest were replays)",
        fineract_state=f"repayments={len(fineract_repayments)}, sum of recorded amounts={sum(recorded.values())}, totalOutstanding={final}",
        erp_state=f"entries: {len(erp_entries)}",
        integration_state={r: (stack.integration_tx(r) or {}).get("state") for r in refs},
        expected=f"each reference applied once: {n_unique} Fineract repayments; final = initial - sum(Fineract amounts) = {initial - sum(recorded.values())}",
        actual=f"{len(fineract_repayments)} repayments; final {final}",
    )
    assert len(fineract_repayments) == n_unique, (
        "each reference exactly once, duplicates applied once"
    )
    assert set(recorded) == set(refs)
    assert final == initial - sum(recorded.values()), (
        "balance moved by what Fineract recorded, no more, no less"
    )
    assert len(erp_entries) == n_unique
    assert all((stack.integration_tx(r) or {}).get("state") == "COMPLETED" for r in refs)
