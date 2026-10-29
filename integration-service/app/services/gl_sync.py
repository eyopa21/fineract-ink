"""GL account sync: ERP chart of accounts -> Fineract GL accounts, create-only, idempotent.

Per account (keyed by external_reference):
  SYNC_PENDING ──(Fineract 200)──▶ SYNCED
  SYNC_PENDING ──(unavailable, retries exhausted)──▶ FAILED   (retried on the next sync call)
  SYNC_PENDING ──(409 conflict / 4xx)──▶ FAILED               (needs a human: payload differs)
  FAILED ──(next sync call)──▶ SYNC_PENDING ──▶ ...
  SYNCED: never touched again (create-only; updates and deletions are out of scope)

Layers that make "send the same account 4 times" yield one of everything:
  1. this table: unique external_reference, SYNCED rows are skipped;
  2. Fineract: unique external_id, identical replay returns the existing account (Part A patch);
  3. a Fineract timeout is resolved by looking the account up by external id, never by blind retry.
"""

import time
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.erp import ErpGateway
from app.clients.fineract import (
    FineractClientError,
    FineractConflictError,
    FineractGateway,
    FineractTimeoutError,
    FineractUnavailableError,
)
from app.config import get_settings
from app.gl_mapping import ErpAccount, fineract_gl_account_payload, fineract_gl_type
from app.models import GLAccountMapping, SyncStatus

log = structlog.get_logger()


@dataclass
class SyncReport:
    requested: int = 0
    created: int = 0
    replayed: int = 0
    already_synced: int = 0
    failed: int = 0
    resolved_by_lookup: int = 0
    accounts: list[dict[str, object]] = field(default_factory=list)


def _upsert_pending(session: Session, account: ErpAccount) -> GLAccountMapping:
    mapping = session.scalar(
        select(GLAccountMapping).where(
            GLAccountMapping.external_reference == account.external_reference
        )
    )
    if mapping is None:
        mapping = GLAccountMapping(
            external_reference=account.external_reference,
            erp_code=account.code,
            erp_name=account.name,
            erp_type=account.type,
            currency=account.currency,
            fineract_gl_type=fineract_gl_type(account.type),
            status=SyncStatus.SYNC_PENDING,
        )
        session.add(mapping)
    elif mapping.status is SyncStatus.FAILED:
        mapping.status = SyncStatus.SYNC_PENDING
        mapping.last_error = None
    session.commit()
    return mapping


def sync_one(
    session: Session,
    fineract: FineractGateway,
    account: ErpAccount,
    correlation_id: str,
    report: SyncReport,
) -> GLAccountMapping:
    settings = get_settings()
    bound = log.bind(external_reference=account.external_reference, erp_code=account.code)
    mapping = _upsert_pending(session, account)
    if mapping.status is SyncStatus.SYNCED:
        report.already_synced += 1
        bound.info(
            "gl sync skipped",
            status=mapping.status,
            fineract_gl_account_id=mapping.fineract_gl_account_id,
        )
        return mapping

    payload = fineract_gl_account_payload(account)
    attempt = 0
    while True:
        attempt += 1
        mapping.attempts += 1
        try:
            result = fineract.create_gl_account(payload, correlation_id)
            mapping.fineract_gl_account_id = result.fineract_id
            mapping.status = SyncStatus.SYNCED
            mapping.last_error = None
            session.commit()
            if result.replayed:
                report.replayed += 1
            else:
                report.created += 1
            bound.info(
                "gl sync done",
                status=mapping.status,
                fineract_gl_account_id=result.fineract_id,
                replayed=result.replayed,
            )
            return mapping
        except FineractTimeoutError as exc:
            # sent, outcome unknown: look it up by external id instead of re-sending
            bound.warning("fineract timeout after send, looking up", error=str(exc))
            found = fineract.find_gl_account_by_external_id(
                account.external_reference, correlation_id
            )
            if found is not None:
                mapping.fineract_gl_account_id = found
                mapping.status = SyncStatus.SYNCED
                mapping.last_error = None
                session.commit()
                report.resolved_by_lookup += 1
                bound.info(
                    "gl sync resolved by lookup",
                    status=mapping.status,
                    fineract_gl_account_id=found,
                )
                return mapping
            error = f"timeout and no account found by external id: {exc}"
        except FineractUnavailableError as exc:
            error = f"fineract unavailable: {exc}"
        except (FineractConflictError, FineractClientError) as exc:
            # permanent: the payload differs or Fineract rejected it; a retry cannot fix it
            mapping.status = SyncStatus.FAILED
            mapping.last_error = str(exc)[:1000]
            session.commit()
            report.failed += 1
            bound.error(
                "gl sync failed permanently", status=mapping.status, error=mapping.last_error
            )
            return mapping

        if attempt >= settings.sync_max_attempts:
            mapping.status = SyncStatus.FAILED
            mapping.last_error = error[:1000]
            session.commit()
            report.failed += 1
            bound.error(
                "gl sync failed, retries exhausted",
                status=mapping.status,
                attempts=mapping.attempts,
                error=error,
            )
            return mapping
        delay = settings.sync_backoff_s * (2 ** (attempt - 1))
        bound.warning("gl sync retrying", attempt=attempt, delay_s=delay, error=error)
        time.sleep(delay)


def sync_all(
    session: Session, fineract: FineractGateway, erp: ErpGateway, correlation_id: str
) -> SyncReport:
    """Pull the ERP chart of accounts and sync every account. Safe to call any number of times."""
    report = SyncReport()
    accounts = erp.list_accounts(correlation_id)
    report.requested = len(accounts)
    for account in accounts:
        mapping = sync_one(session, fineract, account, correlation_id, report)
        report.accounts.append(
            {
                "external_reference": mapping.external_reference,
                "erp_code": mapping.erp_code,
                "status": mapping.status,
                "fineract_gl_account_id": mapping.fineract_gl_account_id,
                "attempts": mapping.attempts,
                "last_error": mapping.last_error,
            }
        )
    log.info(
        "gl sync finished",
        requested=report.requested,
        created=report.created,
        replayed=report.replayed,
        already_synced=report.already_synced,
        resolved_by_lookup=report.resolved_by_lookup,
        failed=report.failed,
    )
    return report
