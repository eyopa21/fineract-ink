"""Part K: reconcile Fineract loan transactions against ERP journal entries.

Keying: one item per Fineract transaction of type Disbursement or Repayment that carries an
external id, matched to the ERP journal entry with the same external_reference. Fineract-internal
transactions without an external id (accruals and similar) are out of scope: they are never posted
to the ERP by design.

Statuses:
  MATCHED              present in both, same amount and type
  MISSING_IN_ERP       in Fineract, not in the ERP
  MISSING_IN_FINERACT  in the ERP (for the loan / date range), not in Fineract
  AMOUNT_MISMATCH      both present, amounts (or types) differ

Reversals: a Fineract transaction flagged manuallyReversed is expected to have TWO entries in the
ERP, the original and "<reference>-REV" with every line flipped. The original is compared as usual;
the reversal is a second item keyed "<reference>-REV": MATCHED if present, MISSING_IN_ERP if not.
A "-REV" entry in the ERP whose Fineract transaction is not reversed is MISSING_IN_FINERACT.

Read-only by default. `repair=true` re-posts MISSING_IN_ERP items for which this service holds the
Fineract breakdown (its own transaction row); the ERP is idempotent on the reference, so repair can
run any number of times. Items without a local row are reported, never invented.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounting_mapping import FineractBreakdown, journal_entry_payload
from app.clients.erp import ErpConflictError, ErpGateway, ErpTimeoutError, ErpUnavailableError
from app.clients.fineract import FineractLoanGateway
from app.models import Loan, LoanTransaction, TxState
from app.services.fineract_dates import from_fineract

log = structlog.get_logger()
IN_SCOPE_TYPES = {"Disbursement": "DISBURSEMENT", "Repayment": "REPAYMENT"}


@dataclass(frozen=True)
class FineractSide:
    reference: str
    loan_reference: str
    type: str
    amount: Decimal
    date: date | None
    reversed: bool
    fineract_transaction_id: int


@dataclass(frozen=True)
class ErpSide:
    reference: str
    loan_reference: str | None
    type: str | None
    amount: Decimal
    entry_id: int
    date: str | None


@dataclass
class Item:
    external_reference: str
    loan_reference: str | None
    status: str
    fineract: dict[str, Any] | None = None
    erp: dict[str, Any] | None = None
    note: str | None = None
    integration_state: str | None = None
    repair: dict[str, Any] | None = None


# ---- pure comparison -------------------------------------------------------------------------


def fineract_sides(loan_reference: str, loan: dict[str, Any]) -> list[FineractSide]:
    out: list[FineractSide] = []
    for t in loan.get("transactions", []) or []:
        kind = IN_SCOPE_TYPES.get((t.get("type") or {}).get("value", ""))
        if kind is None or not t.get("externalId"):
            continue
        out.append(
            FineractSide(
                reference=str(t["externalId"]),
                loan_reference=loan_reference,
                type=kind,
                amount=Decimal(str(t.get("amount", 0))),
                date=from_fineract(t.get("date")),
                reversed=bool(t.get("manuallyReversed")),
                fineract_transaction_id=int(t["id"]),
            )
        )
    return out


def erp_sides(entries: list[dict[str, Any]]) -> list[ErpSide]:
    out: list[ErpSide] = []
    for e in entries:
        meta = e.get("metadata") or {}
        out.append(
            ErpSide(
                reference=str(e["external_reference"]),
                loan_reference=meta.get("loan_reference"),
                type=meta.get("transaction_type"),
                amount=Decimal(str(e.get("total_debits", 0))),
                entry_id=int(e["entry_id"]),
                date=e.get("entry_date"),
            )
        )
    return out


def _fin_view(f: FineractSide) -> dict[str, Any]:
    return {
        "type": f.type,
        "amount": str(f.amount),
        "date": f.date.isoformat() if f.date else None,
        "reversed": f.reversed,
        "fineract_transaction_id": f.fineract_transaction_id,
    }


def _erp_view(e: ErpSide) -> dict[str, Any]:
    return {"entry_id": e.entry_id, "type": e.type, "amount": str(e.amount), "date": e.date}


def compare(fineract: list[FineractSide], erp: list[ErpSide]) -> list[Item]:
    """Pure: no I/O. Items are ordered by reference for a stable report."""
    erp_by_ref = {e.reference: e for e in erp}
    items: list[Item] = []
    seen: set[str] = set()
    for f in sorted(fineract, key=lambda x: x.reference):
        seen.add(f.reference)
        e = erp_by_ref.get(f.reference)
        if e is None:
            items.append(
                Item(f.reference, f.loan_reference, "MISSING_IN_ERP", fineract=_fin_view(f))
            )
        elif e.amount != f.amount or (e.type is not None and e.type != f.type):
            note = (
                "type differs" if e.amount == f.amount else f"fineract {f.amount} vs erp {e.amount}"
            )
            items.append(
                Item(
                    f.reference,
                    f.loan_reference,
                    "AMOUNT_MISMATCH",
                    _fin_view(f),
                    _erp_view(e),
                    note,
                )
            )
        else:
            items.append(Item(f.reference, f.loan_reference, "MATCHED", _fin_view(f), _erp_view(e)))
        if f.reversed:
            rev_ref = f"{f.reference}-REV"
            seen.add(rev_ref)
            rev = erp_by_ref.get(rev_ref)
            view = {**_fin_view(f), "reversal_of": f.reference}
            if rev is None:
                note = "reversed in Fineract, reversal entry absent in ERP"
                items.append(
                    Item(rev_ref, f.loan_reference, "MISSING_IN_ERP", fineract=view, note=note)
                )
            elif rev.amount != f.amount:
                note = f"reversal {rev.amount} vs fineract {f.amount}"
                items.append(
                    Item(rev_ref, f.loan_reference, "AMOUNT_MISMATCH", view, _erp_view(rev), note)
                )
            else:
                items.append(Item(rev_ref, f.loan_reference, "MATCHED", view, _erp_view(rev)))
    for e in sorted(erp, key=lambda x: x.reference):
        if e.reference in seen:
            continue
        ghost_note: str | None = None
        if e.reference.endswith("-REV"):
            ghost_note = "reversal entry without a reversed Fineract transaction"
        items.append(
            Item(
                e.reference,
                e.loan_reference,
                "MISSING_IN_FINERACT",
                erp=_erp_view(e),
                note=ghost_note,
            )
        )
    return items


def summarise(items: list[Item]) -> dict[str, int]:
    counts = {"MATCHED": 0, "MISSING_IN_ERP": 0, "MISSING_IN_FINERACT": 0, "AMOUNT_MISMATCH": 0}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


# ---- orchestration ---------------------------------------------------------------------------


@dataclass
class Scope:
    loan_references: list[str] = field(default_factory=list)
    external_references: list[str] = field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None


def _in_range(d: date | None, scope: Scope) -> bool:
    if d is None:
        return True
    if scope.date_from and d < scope.date_from:
        return False
    return not (scope.date_to and d > scope.date_to)


def reconcile(
    session: Session,
    fineract: FineractLoanGateway,
    erp: ErpGateway,
    scope: Scope,
    repair: bool,
    cid: str,
) -> dict[str, Any]:
    loans = list(scope.loan_references)
    if scope.external_references:
        rows = session.scalars(
            select(LoanTransaction).where(
                LoanTransaction.external_reference.in_(scope.external_references)
            )
        ).all()
        loans += [r.loan_reference for r in rows if r.loan_reference not in loans]
    if not loans and (scope.date_from or scope.date_to):
        loans = list(session.scalars(select(Loan.loan_reference)).all())

    fin: list[FineractSide] = []
    erp_entries: list[ErpSide] = []
    for loan_reference in loans:
        remote = fineract.get_loan_by_external_id(loan_reference, cid)
        if remote is not None:
            fin += [f for f in fineract_sides(loan_reference, remote) if _in_range(f.date, scope)]
        erp_entries += erp_sides(
            erp.list_journal_entries(
                cid,
                loan_reference=loan_reference,
                date_from=scope.date_from.isoformat() if scope.date_from else None,
                date_to=scope.date_to.isoformat() if scope.date_to else None,
            )
        )
    if scope.external_references:
        wanted = set(scope.external_references)
        fin = [f for f in fin if f.reference in wanted]
        erp_entries = [
            e
            for e in erp_entries
            if e.reference in wanted or e.reference.removesuffix("-REV") in wanted
        ]

    items = compare(fin, erp_entries)
    refs = [i.external_reference for i in items] or ["-"]
    local = {
        r.external_reference: r
        for r in session.scalars(
            select(LoanTransaction).where(LoanTransaction.external_reference.in_(refs))
        ).all()
    }
    for item in items:
        row = local.get(item.external_reference)
        item.integration_state = row.state if row else None
        if repair and item.status == "MISSING_IN_ERP":
            item.repair = _repair(session, erp, row, cid)
            if item.repair.get("outcome") in ("posted", "replayed"):
                # the entry now exists with the recorded Fineract figures: report the repaired state
                item.status = "MATCHED"
                item.erp = {"entry_id": item.repair["erp_entry_id"], "repaired": True}
                item.integration_state = row.state if row else None

    summary = summarise(items)
    report = {
        "scope": {
            "loans": loans,
            "external_references": scope.external_references,
            "date_from": scope.date_from,
            "date_to": scope.date_to,
        },
        "repair": repair,
        "summary": summary,
        "items": [item.__dict__ for item in items],
    }
    log.info("reconciliation", loans=len(loans), repair=repair, **summary)
    return report


def _repair(
    session: Session, erp: ErpGateway, row: LoanTransaction | None, cid: str
) -> dict[str, Any]:
    """Re-post the entry the ERP is missing, from the Fineract breakdown we recorded. Idempotent."""
    if row is None or not row.fineract_breakdown:
        return {
            "action": "none",
            "reason": "no local transaction with a Fineract breakdown; manual follow-up",
        }
    breakdown = FineractBreakdown(
        **{k: Decimal(v) for k, v in json.loads(row.fineract_breakdown).items()}
    )
    payload = journal_entry_payload(
        row.operation_type,
        row.external_reference,
        row.loan_reference,
        row.fineract_transaction_id,
        row.transaction_date,
        breakdown,
    )
    try:
        result = erp.post_journal_entry(payload, cid)
    except (ErpUnavailableError, ErpTimeoutError) as exc:
        return {"action": "post", "outcome": "deferred", "error": str(exc)}
    except ErpConflictError as exc:
        return {"action": "post", "outcome": "conflict", "error": str(exc)}
    row.erp_entry_id = result.entry_id
    if row.state in (TxState.FINERACT_DONE, TxState.ERP_PENDING, TxState.NEEDS_RECONCILIATION):
        row.state = TxState.COMPLETED
        row.last_error = None
    session.commit()
    log.info(
        "reconciliation repair",
        external_reference=row.external_reference,
        erp_transaction_id=result.entry_id,
        replayed=result.replayed,
        status=row.state,
    )
    return {
        "action": "post",
        "outcome": "replayed" if result.replayed else "posted",
        "erp_entry_id": result.entry_id,
    }
