"""One error envelope for every failure: {"error": {code, message, details}, "correlation_id"}."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.clients.erp import ErpClientError, ErpConflictError, ErpTimeoutError, ErpUnavailableError
from app.clients.fineract import (
    FineractClientError,
    FineractConflictError,
    FineractTimeoutError,
    FineractUnavailableError,
)
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

    # Upstream outages anywhere in the request path (reads included) answer in the envelope with a
    # status the caller can act on. Writes translate these earlier, after recording state.
    @app.exception_handler(FineractUnavailableError)
    async def _fineract_down(request: Request, exc: FineractUnavailableError) -> JSONResponse:
        log.warning("fineract unavailable", error=str(exc))
        return error_response(
            request,
            503,
            "UPSTREAM_UNAVAILABLE",
            f"fineract: unavailable ({exc})",
            [{"system": "fineract"}],
        )

    @app.exception_handler(ErpUnavailableError)
    async def _erp_down(request: Request, exc: ErpUnavailableError) -> JSONResponse:
        log.warning("erp unavailable", error=str(exc))
        return error_response(
            request, 503, "UPSTREAM_UNAVAILABLE", f"erp: unavailable ({exc})", [{"system": "erp"}]
        )

    @app.exception_handler(FineractTimeoutError)
    async def _fineract_timeout(request: Request, exc: FineractTimeoutError) -> JSONResponse:
        log.warning("fineract timeout", error=str(exc))
        return error_response(
            request, 504, "UPSTREAM_TIMEOUT", f"fineract: timeout ({exc})", [{"system": "fineract"}]
        )

    @app.exception_handler(ErpTimeoutError)
    async def _erp_timeout(request: Request, exc: ErpTimeoutError) -> JSONResponse:
        log.warning("erp timeout", error=str(exc))
        return error_response(
            request, 504, "UPSTREAM_TIMEOUT", f"erp: timeout ({exc})", [{"system": "erp"}]
        )

    @app.exception_handler(FineractConflictError)
    async def _fineract_conflict(request: Request, exc: FineractConflictError) -> JSONResponse:
        return error_response(
            request, 409, "CONFLICT", f"fineract: {exc}", [{"system": "fineract"}]
        )

    @app.exception_handler(ErpConflictError)
    async def _erp_conflict(request: Request, exc: ErpConflictError) -> JSONResponse:
        return error_response(request, 409, "CONFLICT", f"erp: {exc}", [{"system": "erp"}])

    @app.exception_handler(FineractClientError)
    async def _fineract_error(request: Request, exc: FineractClientError) -> JSONResponse:
        body = exc.body if isinstance(exc.body, dict) else {}
        message = body.get("defaultUserMessage") or str(exc)
        details = [
            {
                "parameter": e.get("parameterName"),
                "message": e.get("defaultUserMessage"),
                "code": e.get("userMessageGlobalisationCode"),
            }
            for e in body.get("errors", [])
        ]
        if exc.status_code == 400:
            log.warning("fineract validation error", details=details)
            return error_response(request, 422, "VALIDATION_ERROR", f"fineract: {message}", details)
        if exc.status_code == 403:
            log.warning("fineract business rule", details=details)
            return error_response(request, 422, "BUSINESS_RULE", f"fineract: {message}", details)
        if exc.status_code == 404:
            return error_response(request, 404, "NOT_FOUND", f"fineract: {message}", details)
        log.error("fineract error", status=exc.status_code, body=body)
        return error_response(
            request,
            502,
            "UPSTREAM_ERROR",
            f"fineract: {message}",
            [{"system": "fineract", "upstream_status": exc.status_code}, *details],
        )

    @app.exception_handler(ErpClientError)
    async def _erp_error(request: Request, exc: ErpClientError) -> JSONResponse:
        log.error("erp rejected request", status=exc.status_code, body=exc.body)
        return error_response(
            request,
            502,
            "UPSTREAM_ERROR",
            f"erp: {exc}",
            [{"system": "erp", "upstream_status": exc.status_code}],
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", error=repr(exc))
        return error_response(request, 500, "INTERNAL_ERROR", "unexpected error", [])
