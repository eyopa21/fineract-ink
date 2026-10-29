"""Correlation ID propagation, API-key check and fault injection, as one ASGI middleware."""

import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.faults import get_fault_state, sleep_for_fault

CORRELATION_HEADER = "X-Correlation-ID"
API_KEY_HEADER = "X-API-Key"
OPEN_PATHS = ("/health", "/ready", "/docs", "/openapi.json", "/redoc")


def correlation_id_of(request: Request) -> str:
    cid = getattr(request.state, "correlation_id", None)
    return cid if isinstance(cid, str) else "unknown"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cid = request.headers.get(CORRELATION_HEADER) or str(uuid.uuid4())
        request.state.correlation_id = cid
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=cid, path=request.url.path, method=request.method
        )

        path = request.url.path
        if path.startswith("/api") or path.startswith("/admin"):
            expected = get_settings().api_key
            if expected and request.headers.get(API_KEY_HEADER) != expected:
                return self._error(cid, 401, "UNAUTHORIZED", "missing or invalid X-API-Key")

        if path.startswith("/api"):
            fault = get_fault_state()
            if fault.mode == "down":
                structlog.get_logger().warning("fault injected", mode="down")
                return self._error(cid, 503, "SERVICE_UNAVAILABLE", "ERP is down (injected fault)")
            if fault.mode == "timeout":
                structlog.get_logger().warning(
                    "fault injected", mode="timeout", delay=fault.delay_seconds
                )
                await run_in_threadpool(sleep_for_fault, fault)

        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = cid
        return response

    @staticmethod
    def _error(cid: str, status: int, code: str, message: str) -> JSONResponse:
        body = {"error": {"code": code, "message": message, "details": []}, "correlation_id": cid}
        return JSONResponse(status_code=status, content=body, headers={CORRELATION_HEADER: cid})
