"""Integration Service: FastAPI entry point.

Partner-facing API between the financing partner, Fineract and the ERP. This revision carries the
GL account sync (Part A, Python side); clients, loans, transactions and reconciliation follow.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Response, status
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.db import database_is_reachable
from app.errors import install_error_handlers
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware
from app.routers import accounts, admin, clients, loans, reconciliation, transactions

configure_logging(get_settings().log_level)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Part J: startup sweep. Any transaction interrupted by a crash is detected here and either
    completed or routed to reconciliation before the service accepts traffic."""
    if get_settings().recover_on_startup:
        from app.db import get_session_factory
        from app.deps import get_erp, get_fineract
        from app.services.transactions import recover

        try:
            with get_session_factory()() as session:
                recover(session, get_fineract(), get_erp(), "startup-sweep")
        except (
            Exception
        ) as exc:  # the sweep must never block startup; the admin endpoint can rerun it
            structlog.get_logger().error("startup recovery sweep failed", error=repr(exc))
    yield


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Partner API key")
secured = [Depends(api_key_header)]

app = FastAPI(
    lifespan=lifespan,
    title="Integration Service",
    version="0.2.0",
    description=(
        "Connects a financing partner to Apache Fineract and the ERP. Business identifiers only "
        "(client_uid, loan_reference, external_reference); Fineract ids never leave this service. "
        "All /api and /admin calls need X-API-Key; every response carries X-Correlation-ID."
    ),
)
app.add_middleware(RequestContextMiddleware)
install_error_handlers(app)
app.include_router(accounts.router, dependencies=secured)
app.include_router(clients.router, dependencies=secured)
app.include_router(loans.router, dependencies=secured)
app.include_router(transactions.router, dependencies=secured)
app.include_router(reconciliation.router, dependencies=secured)
app.include_router(admin.router, dependencies=secured)


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness: the process is up. Does not touch the database."""
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
def ready(response: Response) -> dict[str, str]:
    """Readiness: the database answers. Used by the compose health check."""
    if database_is_reachable():
        return {"status": "ready"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "database unreachable"}
