"""Integration Service: FastAPI entry point."""

from fastapi import FastAPI, Response, status

from app.config import get_settings
from app.db import database_is_reachable
from app.logging import configure_logging

configure_logging(get_settings().log_level)

app = FastAPI(title="Integration Service", version="0.1.0")


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
