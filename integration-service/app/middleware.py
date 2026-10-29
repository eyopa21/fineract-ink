"""Correlation ID propagation and partner API-key check."""

import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

CORRELATION_HEADER = "X-Correlation-ID"
API_KEY_HEADER = "X-API-Key"


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
                body = {
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "missing or invalid X-API-Key",
                        "details": [],
                    },
                    "correlation_id": cid,
                }
                return JSONResponse(
                    status_code=401, content=body, headers={CORRELATION_HEADER: cid}
                )
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = cid
        return response
