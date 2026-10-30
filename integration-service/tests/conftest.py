"""Fixtures: in-memory SQLite schema, fake Fineract and ERP gateways, authenticated client."""

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("SYNC_BACKOFF_S", "0")
os.environ.setdefault("SYNC_MAX_ATTEMPTS", "3")
os.environ.setdefault("RECOVER_ON_STARTUP", "false")

from app import models
from app.clients.erp import (
    ErpConflictError,
    ErpUnavailableError,
    JournalEntryResult,
)
from app.clients.fineract import (
    FineractClientError,
    FineractConflictError,
    FineractTimeoutError,
    FineractUnavailableError,
    GLAccountResult,
)
from app.db import Base, get_engine, get_session_factory
from app.deps import get_erp, get_fineract
from app.faults import set_fault_state
from app.gl_mapping import ErpAccount
from app.main import app

_ = models

SEED = [
    ErpAccount("1100", "Bank", "ASSET", "RWF", "GL-1100"),
    ErpAccount("1200", "Loan Receivable", "ASSET", "RWF", "GL-1200"),
    ErpAccount("4000", "Interest Income", "INCOME", "RWF", "GL-4000"),
]


@dataclass
class FakeFineract:
    """Behaves like the patched Fineract: unique external id, replay on identical payload, 409 on a
    different payload. `script` lets a test inject failures for the next calls."""

    accounts: dict[str, tuple[int, dict[str, Any]]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    script: list[str] = field(default_factory=list)  # "down" | "timeout_after_send" | "ok"
    next_id: int = 1

    def create_gl_account(self, payload: dict[str, Any], correlation_id: str) -> GLAccountResult:
        self.calls.append(payload)
        behaviour = self.script.pop(0) if self.script else "ok"
        if behaviour == "down":
            raise FineractUnavailableError("connection refused")
        ext = payload["externalId"]
        if ext in self.accounts:
            existing_id, existing_payload = self.accounts[ext]
            if existing_payload != payload:
                raise FineractConflictError("different payload", {"httpStatusCode": "409"})
            result = GLAccountResult(existing_id, ext, replayed=True)
        else:
            self.accounts[ext] = (self.next_id, payload)
            result = GLAccountResult(self.next_id, ext, replayed=False)
            self.next_id += 1
        if behaviour == "timeout_after_send":
            raise FineractTimeoutError("read timed out")
        return result

    def find_gl_account_by_external_id(self, external_id: str, correlation_id: str) -> int | None:
        found = self.accounts.get(external_id)
        return found[0] if found else None

    # ---- clients / loans / transactions (Parts C-E). Mirrors Fineract: external ids unique,
    # one disbursement per loan, repayments split 10% interest / 90% principal, loan closes at zero.
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    loans: dict[str, dict[str, Any]] = field(default_factory=dict)
    transactions: dict[str, dict[str, Any]] = field(default_factory=dict)
    tx_script: list[str] = field(default_factory=list)  # "down" | "timeout_after_send" | "ok"
    next_tx_id: int = 100

    def create_client(self, payload: dict[str, Any], correlation_id: str) -> int:
        uid = payload["externalId"]
        if uid in self.clients:
            raise FineractConflictError("client externalId exists", {})
        self.clients[uid] = {
            "id": 500 + len(self.clients),
            "externalId": uid,
            "displayName": payload["fullname"],
            "active": True,
            "status": {"value": "Active"},
            "activationDate": [2026, 1, 10],
            "officeName": "Kigali Main Office",
        }
        return int(self.clients[uid]["id"])

    def get_client_by_external_id(
        self, client_uid: str, correlation_id: str
    ) -> dict[str, Any] | None:
        return self.clients.get(client_uid)

    def create_loan(self, payload: dict[str, Any], correlation_id: str) -> int:
        ref = payload["externalId"]
        if ref in self.loans:
            raise FineractConflictError("loan externalId exists", {})
        if int(payload["principal"]) < 100000:
            raise FineractClientError(
                400,
                {
                    "defaultUserMessage": "Validation errors exist.",
                    "errors": [
                        {
                            "parameterName": "principal",
                            "defaultUserMessage": (
                                "The parameter `principal` amount must be between "
                                "100000 and 50000000 ."
                            ),
                            "userMessageGlobalisationCode": (
                                "validation.msg.loan.principal.amount.is.not.within.min.max.range"
                            ),
                        }
                    ],
                },
            )
        loan_id = 900 + len(self.loans)
        self.loans[ref] = {
            "id": loan_id,
            "externalId": ref,
            "principal": payload["principal"],
            "status": "SUBMITTED",
            "outstanding": Decimal(0),
            "transactions": [],
        }
        return loan_id

    def loan_command_by_external_id(
        self, loan_reference: str, command: str, payload: dict[str, Any], correlation_id: str
    ) -> dict[str, Any]:
        loan = self.loans[loan_reference]
        if command == "approve":
            loan["status"] = "APPROVED"
            return {"resourceId": loan["id"]}
        if command == "disburse":
            return self._transaction(
                loan,
                "Disbursement",
                payload["externalId"],
                Decimal(payload["transactionAmount"]),
                payload["actualDisbursementDate"],
            )
        raise AssertionError(command)

    def loan_transaction_by_external_id(
        self, loan_reference: str, command: str, payload: dict[str, Any], correlation_id: str
    ) -> dict[str, Any]:
        assert command == "repayment"
        loan = self.loans[loan_reference]
        return self._transaction(
            loan,
            "Repayment",
            payload["externalId"],
            Decimal(payload["transactionAmount"]),
            payload["transactionDate"],
        )

    def _transaction(
        self, loan: dict[str, Any], kind: str, ext: str, amount: Decimal, when: str
    ) -> dict[str, Any]:
        behaviour = self.tx_script.pop(0) if self.tx_script else "ok"
        if behaviour == "down":
            raise FineractUnavailableError("connection refused")
        if behaviour == "lock_conflict":
            raise FineractConflictError("optimistic lock", {"httpStatusCode": "409"})
        if ext in self.transactions:
            raise FineractConflictError("transaction externalId exists", {})
        if kind == "Disbursement":
            if loan["status"] != "APPROVED":
                raise FineractClientError(
                    403,
                    {
                        "defaultUserMessage": "Loan must be approved and not yet disbursed",
                        "errors": [],
                    },
                )
            loan["status"] = "ACTIVE"
            loan["outstanding"] = amount
            tx = {
                "id": self.next_tx_id,
                "externalId": ext,
                "amount": str(amount),
                "principalPortion": str(amount),
                "type": {"value": "Disbursement"},
                "date": [2026, 1, 15],
                "outstandingLoanBalance": str(loan["outstanding"]),
            }
        else:
            if loan["status"] != "ACTIVE":
                raise FineractClientError(
                    403, {"defaultUserMessage": "Loan is not active", "errors": []}
                )
            interest = (amount * Decimal("0.10")).quantize(Decimal("1"))
            principal = amount - interest
            loan["outstanding"] -= principal
            if loan["outstanding"] <= 0:
                loan["status"] = "CLOSED"
            tx = {
                "id": self.next_tx_id,
                "externalId": ext,
                "amount": str(amount),
                "principalPortion": str(principal),
                "interestPortion": str(interest),
                "feeChargesPortion": "0",
                "penaltyChargesPortion": "0",
                "type": {"value": "Repayment"},
                "date": [2026, 2, 15],
                "outstandingLoanBalance": str(loan["outstanding"]),
            }
        self.next_tx_id += 1
        self.transactions[ext] = tx
        loan["transactions"].append(tx)
        if behaviour == "timeout_after_send":
            raise FineractTimeoutError("read timed out")
        return {"resourceId": tx["id"], "loanId": loan["id"]}

    def get_loan_by_external_id(
        self, loan_reference: str, correlation_id: str
    ) -> dict[str, Any] | None:
        loan = self.loans.get(loan_reference)
        if loan is None:
            return None
        return {
            "id": loan["id"],
            "externalId": loan_reference,
            "status": {
                "code": f"loanStatusType.{loan['status'].lower()}",
                "value": loan["status"].title(),
                "active": loan["status"] == "ACTIVE",
                "pendingApproval": loan["status"] == "SUBMITTED",
                "closedObligationsMet": loan["status"] == "CLOSED",
            },
            "summary": {
                "principalDisbursed": str(loan["principal"]),
                "principalOutstanding": str(loan["outstanding"]),
                "totalOutstanding": str(loan["outstanding"]),
            },
            "transactions": loan["transactions"],
        }

    def get_loan_transaction_by_external_ids(
        self, loan_reference: str, transaction_reference: str, correlation_id: str
    ) -> dict[str, Any] | None:
        return self.transactions.get(transaction_reference)

    def find_loan_product_id(self, name: str, correlation_id: str) -> int | None:
        return 1


@dataclass
class FakeErp:
    accounts: list[ErpAccount] = field(default_factory=lambda: list(SEED))
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    posts: list[dict[str, Any]] = field(default_factory=list)
    script: list[str] = field(default_factory=list)  # "down" | "ok"

    def list_accounts(self, correlation_id: str) -> list[ErpAccount]:
        return list(self.accounts)

    def post_journal_entry(
        self, payload: dict[str, Any], correlation_id: str
    ) -> JournalEntryResult:
        behaviour = self.script.pop(0) if self.script else "ok"
        if behaviour == "down":
            raise ErpUnavailableError("connection refused")
        self.posts.append(payload)
        ref = payload["external_reference"]
        if ref in self.entries:
            if self.entries[ref]["payload"] != payload:
                raise ErpConflictError("different payload")
            return JournalEntryResult(self.entries[ref]["id"], replayed=True)
        self.entries[ref] = {"id": len(self.entries) + 1, "payload": payload}
        return JournalEntryResult(self.entries[ref]["id"], replayed=False)

    def get_journal_entry(
        self, external_reference: str, correlation_id: str
    ) -> dict[str, Any] | None:
        found = self.entries.get(external_reference)
        return {"entry_id": found["id"], **found["payload"]} if found else None


@pytest.fixture(autouse=True)
def fresh_schema() -> Iterator[None]:
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    set_fault_state("none")
    yield
    set_fault_state("none")
    app.dependency_overrides.clear()


@pytest.fixture
def session() -> Iterator[Any]:
    with get_session_factory()() as s:
        yield s


@pytest.fixture
def fineract() -> FakeFineract:
    return FakeFineract()


@pytest.fixture
def erp() -> FakeErp:
    return FakeErp()


@pytest.fixture
def client(fineract: FakeFineract, erp: FakeErp) -> TestClient:
    app.dependency_overrides[get_fineract] = lambda: fineract
    app.dependency_overrides[get_erp] = lambda: erp
    c = TestClient(app, raise_server_exceptions=False)
    c.headers.update({"X-API-Key": "test-key"})
    return c
