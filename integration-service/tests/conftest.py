"""Fixtures: in-memory SQLite schema, fake Fineract and ERP gateways, authenticated client."""

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("SYNC_BACKOFF_S", "0")
os.environ.setdefault("SYNC_MAX_ATTEMPTS", "3")

from app import models
from app.clients.fineract import (
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


@dataclass
class FakeErp:
    accounts: list[ErpAccount] = field(default_factory=lambda: list(SEED))

    def list_accounts(self, correlation_id: str) -> list[ErpAccount]:
        return list(self.accounts)


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
