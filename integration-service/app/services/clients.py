"""Part C: clients keyed by client_uid. Fineract stores client_uid as the client externalId; the
numeric Fineract id lives only in the local mapping table."""

from datetime import date
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients.fineract import FineractConflictError, FineractLoanGateway
from app.errors import ConflictError, NotFoundError
from app.models import Client
from app.services.fineract_dates import DATE_FORMAT, LOCALE, from_fineract, to_fineract

log = structlog.get_logger()
LEGAL_FORM_ENTITY = 2  # SMEs are legal entities, created with a single fullname


def fineract_client_payload(
    client_uid: str, full_name: str, activation_date: date, office_id: int
) -> dict[str, Any]:
    return {
        "officeId": office_id,
        "legalFormId": LEGAL_FORM_ENTITY,
        "fullname": full_name,
        "externalId": client_uid,
        "active": True,
        "activationDate": to_fineract(activation_date),
        "submittedOnDate": to_fineract(activation_date),
        "locale": LOCALE,
        "dateFormat": DATE_FORMAT,
    }


def create_client(
    session: Session,
    fineract: FineractLoanGateway,
    client_uid: str,
    full_name: str,
    activation_date: date,
    cid: str,
) -> Client:
    """client_uid is unique at the database level: the INSERT is the guard, Fineract's externalId
    uniqueness is the backstop. A Fineract conflict (uid already there from a lost response) is
    resolved by adopting the existing Fineract client rather than failing."""
    log.info("client create requested", client_uid=client_uid)
    row = Client(client_uid=client_uid, full_name=full_name)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError(
            f"client_uid {client_uid} already exists", [{"field": "client_uid"}]
        ) from None

    try:
        fineract_id = fineract.create_client(
            fineract_client_payload(client_uid, full_name, activation_date, 1), cid
        )
    except FineractConflictError:
        existing = fineract.get_client_by_external_id(client_uid, cid)
        if existing is None:
            raise
        fineract_id = int(existing["id"])
        log.warning("client already in fineract, adopted", client_uid=client_uid)
    except Exception:
        # nothing was created in Fineract: release the uid so the caller can retry
        session.delete(row)
        session.commit()
        raise
    row.fineract_client_id = fineract_id
    session.commit()
    log.info("client created", client_uid=client_uid, status="ACTIVE")
    return row


def get_client_view(
    session: Session, fineract: FineractLoanGateway, client_uid: str, cid: str
) -> dict[str, Any]:
    row = session.scalar(select(Client).where(Client.client_uid == client_uid))
    if row is None:
        raise NotFoundError(f"client {client_uid} not found")
    remote = fineract.get_client_by_external_id(client_uid, cid)
    if remote is None:
        raise NotFoundError(f"client {client_uid} is mapped but missing in Fineract")
    status = remote.get("status", {})
    return {
        "client_uid": client_uid,
        "full_name": remote.get("displayName") or row.full_name,
        "status": status.get("value", "UNKNOWN").upper(),
        "active": bool(remote.get("active")),
        "activation_date": from_fineract(remote.get("activationDate")),
        "office": remote.get("officeName"),
    }
