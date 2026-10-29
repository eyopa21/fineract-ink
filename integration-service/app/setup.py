"""Part B: configure Fineract. Idempotent; run as `python -m app.setup` (make setup).

Order (it matters): office and currency -> GL sync (Part A) -> loan product mapped to the SYNCED
GL accounts -> least-privilege role and API user -> verification with that user's credentials.
Uses the admin credentials from settings only here; the running service uses the API user.
"""

import json
import sys
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.erp import ErpGateway
from app.clients.fineract import FineractAdminGateway
from app.config import Settings, get_settings
from app.models import GLAccountMapping, SyncStatus
from app.services.gl_sync import sync_all
from app.setup_spec import (
    DATE_FORMAT,
    LOAN_PRODUCT_GL_MAPPINGS,
    LOCALE,
    api_user_payload,
    loan_product_payload,
    role_permissions_payload,
)

log = structlog.get_logger()


class SetupError(Exception):
    pass


@dataclass
class SetupReport:
    office: dict[str, Any] = field(default_factory=dict)
    currency: dict[str, Any] = field(default_factory=dict)
    gl_sync: dict[str, Any] = field(default_factory=dict)
    loan_product: dict[str, Any] = field(default_factory=dict)
    role: dict[str, Any] = field(default_factory=dict)
    api_user: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


def _expect(status: int, body: Any, what: str, *ok: int) -> Any:
    if status not in (ok or (200,)):
        raise SetupError(f"{what}: Fineract returned {status}: {body}")
    return body


def setup_office(fineract: FineractAdminGateway, settings: Settings, cid: str) -> dict[str, Any]:
    offices = _expect(*fineract.get("/offices", cid), "list offices")
    head = next(o for o in offices if o["id"] == 1)
    previous = str(head["name"])
    if previous == settings.office_name:
        return {"id": 1, "name": previous, "changed": False}
    payload = {"name": settings.office_name, "locale": LOCALE, "dateFormat": DATE_FORMAT}
    _expect(*fineract.put("/offices/1", payload, cid), "rename head office")
    log.info("office renamed", office=settings.office_name, previous=previous)
    return {"id": 1, "name": settings.office_name, "changed": True, "previous": previous}


def setup_currency(fineract: FineractAdminGateway, settings: Settings, cid: str) -> dict[str, Any]:
    body = _expect(
        *fineract.get("/currencies?fields=selectedCurrencyOptions", cid), "list currencies"
    )
    selected = [c["code"] for c in body.get("selectedCurrencyOptions", [])]
    if settings.currency_code in selected:
        return {"enabled": selected, "changed": False}
    payload = {"currencies": sorted({*selected, settings.currency_code})}
    _expect(*fineract.put("/currencies", payload, cid), "enable currency")
    log.info("currency enabled", currency=settings.currency_code)
    return {"enabled": payload["currencies"], "changed": True}


def setup_gl_sync(
    session: Session, fineract: FineractAdminGateway, erp: ErpGateway, cid: str
) -> dict[str, Any]:
    report = sync_all(session, fineract, erp, cid)
    required = set(LOAN_PRODUCT_GL_MAPPINGS.values())
    rows = {
        m.external_reference: m
        for m in session.scalars(
            select(GLAccountMapping).where(GLAccountMapping.external_reference.in_(required))
        )
    }
    not_ready = {
        ref: (
            rows[ref].status if ref in rows else "MISSING",
            rows[ref].last_error if ref in rows else None,
        )
        for ref in sorted(required)
        if ref not in rows or rows[ref].status is not SyncStatus.SYNCED
    }
    if not_ready:
        raise SetupError(
            "GL accounts the loan product needs are not SYNCED; fix and re-run setup: "
            + json.dumps(
                {k: {"status": str(v[0]), "last_error": v[1]} for k, v in not_ready.items()}
            )
        )
    return {
        "requested": report.requested,
        "created": report.created,
        "already_synced": report.already_synced,
        "failed": report.failed,
        "gl_ids": {ref: rows[ref].fineract_gl_account_id for ref in sorted(required)},
    }


def setup_loan_product(
    fineract: FineractAdminGateway, settings: Settings, gl_ids: dict[str, int], cid: str
) -> dict[str, Any]:
    products = _expect(*fineract.get("/loanproducts", cid), "list loan products")
    existing = next((p for p in products if p["name"] == settings.loan_product_name), None)
    if existing is not None:
        return {"id": existing["id"], "name": existing["name"], "changed": False}
    payload = loan_product_payload(
        settings.loan_product_name, settings.loan_product_short_name, settings.currency_code, gl_ids
    )
    body = _expect(*fineract.post("/loanproducts", payload, cid), "create loan product")
    log.info(
        "loan product created", loan_product_id=body["resourceId"], name=settings.loan_product_name
    )
    return {
        "id": body["resourceId"],
        "name": settings.loan_product_name,
        "changed": True,
        "payload": payload,
    }


def setup_role(fineract: FineractAdminGateway, settings: Settings, cid: str) -> dict[str, Any]:
    roles = _expect(*fineract.get("/roles", cid), "list roles")
    existing = next((r for r in roles if r["name"] == settings.api_role_name), None)
    if existing is None:
        body = _expect(
            *fineract.post(
                "/roles",
                {
                    "name": settings.api_role_name,
                    "description": "Least privilege for the Integration Service",
                },
                cid,
            ),
            "create role",
        )
        role_id = int(body["resourceId"])
        changed = True
    else:
        role_id = int(existing["id"])
        changed = False
    # permissions are declarative: PUT the full set every run
    payload = role_permissions_payload()
    _expect(*fineract.put(f"/roles/{role_id}/permissions", payload, cid), "set role permissions")
    return {
        "id": role_id,
        "name": settings.api_role_name,
        "changed": changed,
        "permissions": sorted(payload["permissions"]),
    }


def setup_api_user(
    fineract: FineractAdminGateway, settings: Settings, role_id: int, office_id: int, cid: str
) -> dict[str, Any]:
    users = _expect(*fineract.get("/users", cid), "list users")
    existing = next((u for u in users if u["username"] == settings.api_user_username), None)
    if existing is not None:
        return {"id": existing["id"], "username": existing["username"], "changed": False}
    payload = api_user_payload(
        settings.api_user_username, settings.api_user_password, role_id, office_id
    )
    body = _expect(*fineract.post("/users", payload, cid), "create api user")
    log.info("api user created", username=settings.api_user_username)
    return {"id": body["resourceId"], "username": settings.api_user_username, "changed": True}


def run_setup(
    session: Session,
    fineract: FineractAdminGateway,
    erp: ErpGateway,
    settings: Settings,
    cid: str = "setup",
) -> SetupReport:
    report = SetupReport()
    report.office = setup_office(fineract, settings, cid)
    report.currency = setup_currency(fineract, settings, cid)
    report.gl_sync = setup_gl_sync(session, fineract, erp, cid)
    report.loan_product = setup_loan_product(fineract, settings, report.gl_sync["gl_ids"], cid)
    report.role = setup_role(fineract, settings, cid)
    report.api_user = setup_api_user(
        fineract, settings, report.role["id"], report.office["id"], cid
    )
    return report


def verify_with_api_user(settings: Settings, loan_product_id: int, cid: str) -> dict[str, Any]:
    """Prove the least-privilege user can read what the service needs and nothing more."""
    from app.clients.fineract import FineractClient

    client = FineractClient(settings, settings.api_user_username, settings.api_user_password)
    try:
        product_status, product = client.get(f"/loanproducts/{loan_product_id}", cid)
        denied_status, _ = client.get("/users", cid)  # READ_USER is not granted
        return {
            "username": settings.api_user_username,
            "read_loan_product": product_status,
            "loan_product_name": product.get("name") if isinstance(product, dict) else None,
            "read_users_denied": denied_status,
        }
    finally:
        client.close()


def main() -> int:
    from app.clients.erp import ErpClient
    from app.clients.fineract import FineractClient
    from app.db import get_session_factory
    from app.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level)
    fineract = FineractClient(
        settings, settings.fineract_admin_username, settings.fineract_admin_password
    )
    erp = ErpClient(settings)
    try:
        with get_session_factory()() as session:
            report = run_setup(session, fineract, erp, settings)
        report.verification = verify_with_api_user(
            settings, int(report.loan_product["id"]), "setup-verify"
        )
    except SetupError as exc:
        log.error("setup failed", error=str(exc))
        print(json.dumps({"status": "FAILED", "error": str(exc)}, indent=2))
        return 1
    finally:
        fineract.close()
        erp.close()
    print(json.dumps({"status": "OK", **report.as_dict()}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
