"""Shared fixtures: in-memory SQLite schema, seeded chart, authenticated test client."""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("API_KEY", "test-key")

from app import models
from app.db import Base, get_engine, get_session_factory
from app.faults import set_fault_state
from app.main import app
from app.seed import seed_chart_of_accounts

_ = models


@pytest.fixture(autouse=True)
def fresh_schema() -> Iterator[None]:
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    set_fault_state("none", 0.0)
    yield
    set_fault_state("none", 0.0)


@pytest.fixture
def session() -> Iterator[Session]:
    with get_session_factory()() as s:
        yield s


@pytest.fixture
def seeded(session: Session) -> int:
    return seed_chart_of_accounts(session)


@pytest.fixture
def client() -> TestClient:
    c = TestClient(app, raise_server_exceptions=False)
    c.headers.update({"X-API-Key": "test-key"})
    return c
