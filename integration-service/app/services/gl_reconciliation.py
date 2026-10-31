"""Stretch (Part L): GL master-data reconciliation, Fineract GL accounts versus the ERP chart of
accounts.

Keying: by external reference (the ERP's external_reference, stored as Fineract's externalId by the
Part A sync). A Fineract GL account with no external reference was not created by the sync and
is reported MISSING_IN_ERP, which is the example the brief gives. Comparison per matched pair:
code, name, type (ERP ASSET/LIABILITY/... against Fineract's classification via the Part A
mapping table).

Statuses: MATCHED, MISSING_IN_ERP, MISSING_IN_FINERACT, ATTRIBUTE_MISMATCH. Read-only: the repair
for MISSING_IN_FINERACT is the existing create-only sync (POST /api/accounts/sync), named in the
item.
"""

from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.erp import ErpGateway
from app.clients.fineract import FineractLoanGateway
from app.gl_mapping import ERP_TO_FINERACT_TYPE, ErpAccount
from app.models import GLAccountMapping

log = structlog.get_logger()
FINERACT_TO_ERP_TYPE = {v: k for k, v in ERP_TO_FINERACT_TYPE.items()}


@dataclass(frozen=True)
class FineractGl:
    external_reference: str | None
    code: str
    name: str
    type: str  # ERP vocabulary (ASSET ...) derived from Fineract's classification id
    fineract_id: int
    disabled: bool


@dataclass
class GlItem:
    key: str
    status: str
    erp: dict[str, Any] | None = None
    fineract: dict[str, Any] | None = None
    mapping_state: str | None = None
    note: str | None = None


def fineract_gl_accounts(raw: list[dict[str, Any]]) -> list[FineractGl]:
    out: list[FineractGl] = []
    for a in raw:
        type_id = (a.get("type") or {}).get("id")
        out.append(
            FineractGl(
                external_reference=a.get("externalId") or None,
                code=str(a.get("glCode")),
                name=str(a.get("name")),
                type=FINERACT_TO_ERP_TYPE.get(int(type_id), "UNKNOWN")
                if type_id is not None
                else "UNKNOWN",
                fineract_id=int(a["id"]),
                disabled=bool(a.get("disabled")),
            )
        )
    return out


def compare_gl(erp: list[ErpAccount], fineract: list[FineractGl]) -> list[GlItem]:
    """Pure: no I/O."""
    fin_by_ref = {f.external_reference: f for f in fineract if f.external_reference}
    items: list[GlItem] = []
    for e in sorted(erp, key=lambda x: x.external_reference):
        f = fin_by_ref.pop(e.external_reference, None)
        erp_view = {"code": e.code, "name": e.name, "type": e.type}
        if f is None:
            items.append(
                GlItem(
                    e.external_reference,
                    "MISSING_IN_FINERACT",
                    erp=erp_view,
                    note="run POST /api/accounts/sync",
                )
            )
            continue
        fin_view = {"code": f.code, "name": f.name, "type": f.type, "disabled": f.disabled}
        diffs = [
            k
            for k in ("code", "name", "type")
            if str(erp_view[k]).strip() != str(fin_view[k]).strip()
        ]
        if diffs:
            items.append(
                GlItem(
                    e.external_reference,
                    "ATTRIBUTE_MISMATCH",
                    erp_view,
                    fin_view,
                    note=f"differs in {', '.join(diffs)}",
                )
            )
        else:
            items.append(GlItem(e.external_reference, "MATCHED", erp_view, fin_view))
    for f in sorted(fineract, key=lambda x: (x.external_reference or "", x.code)):
        if f.external_reference and f.external_reference not in fin_by_ref:
            continue  # matched above
        key = f.external_reference or f"fineract:{f.code}"
        note = (
            "created outside the ERP sync (no external reference)"
            if not f.external_reference
            else "external reference unknown to the ERP"
        )
        items.append(
            GlItem(
                key,
                "MISSING_IN_ERP",
                fineract={"code": f.code, "name": f.name, "type": f.type, "disabled": f.disabled},
                note=note,
            )
        )
    return items


def summarise_gl(items: list[GlItem]) -> dict[str, int]:
    counts = {"MATCHED": 0, "MISSING_IN_ERP": 0, "MISSING_IN_FINERACT": 0, "ATTRIBUTE_MISMATCH": 0}
    for i in items:
        counts[i.status] = counts.get(i.status, 0) + 1
    return counts


def reconcile_gl(
    session: Session, fineract: FineractLoanGateway, erp: ErpGateway, cid: str
) -> dict[str, Any]:
    erp_accounts = erp.list_accounts(cid)
    fin = fineract_gl_accounts(fineract.list_gl_accounts(cid))
    items = compare_gl(erp_accounts, fin)
    mappings = {
        m.external_reference: m.status for m in session.scalars(select(GLAccountMapping)).all()
    }
    for item in items:
        item.mapping_state = mappings.get(item.key)
    summary = summarise_gl(items)
    log.info(
        "gl reconciliation", erp_accounts=len(erp_accounts), fineract_accounts=len(fin), **summary
    )
    return {"summary": summary, "items": [i.__dict__ for i in items]}
