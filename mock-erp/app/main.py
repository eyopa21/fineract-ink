"""Mock ERP: FastAPI entry point.

Owns the chart of accounts and the journal. Enforces its own uniqueness (database constraints),
rejects unbalanced entries, and is idempotent on external_reference for both accounts and entries.
"""

from fastapi import Depends, FastAPI, Response, status
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.db import database_is_reachable
from app.errors import install_error_handlers
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware
from app.routers import accounts, admin, journal_entries

configure_logging(get_settings().log_level)

# Declares the header to OpenAPI so Swagger UI shows an Authorize button and sends it. Enforcement
# itself happens in RequestContextMiddleware, which runs before any route.
api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="Shared secret (API_KEY setting)"
)
secured = [Depends(api_key_header)]

app = FastAPI(
    title="Mock ERP",
    version="0.2.0",
    description=(
        "Accounting master for the Fineract integration assessment. Chart of accounts and "
        "double-entry journal. All /api and /admin calls need the X-API-Key header; every "
        "response carries X-Correlation-ID."
    ),
)
app.add_middleware(RequestContextMiddleware)
install_error_handlers(app)
app.include_router(accounts.router, dependencies=secured)
app.include_router(journal_entries.router, dependencies=secured)
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
