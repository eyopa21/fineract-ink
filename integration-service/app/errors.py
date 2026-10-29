"""One error envelope for every failure: {"error": {code, message, details}, "correlation_id"}."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.middleware import correlation_id_of

log = structlog.get_logger()


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


class NotFoundError(ApiError):
    def __init__(self, message: str):
        super().__init__(404, "NOT_FOUND", message)


class ConflictError(ApiError):
    def __init__(self, message: str, details: list[dict[str, Any]] | None = None):
        super().__init__(409, "CONFLICT", message, details)


class UnprocessableError(ApiError):
    def __init__(self, message: str, details: list[dict[str, Any]] | None = None):
        super().__init__(422, "VALIDATION_ERROR", message, details)


class UpstreamUnavailableError(ApiError):
    def __init__(self, system: str, message: str):
        super().__init__(503, "UPSTREAM_UNAVAILABLE", f"{system}: {message}", [{"system": system}])


def error_response(
    request: Request, status_code: int, code: str, message: str, details: list[dict[str, Any]]
) -> JSONResponse:
    cid = correlation_id_of(request)
    body = {"error": {"code": code, "message": message, "details": details}, "correlation_id": cid}
    return JSONResponse(status_code=status_code, content=body, headers={"X-Correlation-ID": cid})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        log.warning("request failed", status=exc.status_code, code=exc.code, message=exc.message)
        return error_response(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg", "")}
            for e in exc.errors()
        ]
        log.warning("validation failed", status=422, details=details)
        return error_response(
            request, 422, "VALIDATION_ERROR", "request validation failed", details
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", error=repr(exc))
        return error_response(request, 500, "INTERNAL_ERROR", "unexpected error", [])
