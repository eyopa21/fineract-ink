"""HTTP client for the customized Fineract. Thin, typed, with explicit failure classes so callers
can tell "never reached Fineract" (safe to retry) from "sent, outcome unknown" (look up, never
blindly retry)."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

from app.config import Settings, get_settings
from app.faults import get_fault_state

log = structlog.get_logger()


class FineractUnavailableError(Exception):
    """Connection could not be established: nothing was sent, retrying is safe."""


class FineractTimeoutError(Exception):
    """The request was sent but no response arrived: the outcome is unknown, look it up."""


class FineractConflictError(Exception):
    """HTTP 409: same external id, different payload."""

    def __init__(self, message: str, body: dict[str, Any]):
        super().__init__(message)
        self.body = body


class FineractClientError(Exception):
    """Any other 4xx/5xx from Fineract."""

    def __init__(self, status_code: int, body: dict[str, Any]):
        super().__init__(f"Fineract returned {status_code}: {body}")
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class GLAccountResult:
    fineract_id: int
    external_id: str
    replayed: bool


class FineractGateway(Protocol):
    def create_gl_account(
        self, payload: dict[str, Any], correlation_id: str
    ) -> GLAccountResult: ...

    def find_gl_account_by_external_id(
        self, external_id: str, correlation_id: str
    ) -> int | None: ...


class FineractAdminGateway(FineractGateway, Protocol):
    """Generic verbs used by the Part B setup command."""

    def get(self, path: str, correlation_id: str) -> tuple[int, Any]: ...

    def post(self, path: str, payload: dict[str, Any], correlation_id: str) -> tuple[int, Any]: ...

    def put(self, path: str, payload: dict[str, Any], correlation_id: str) -> tuple[int, Any]: ...


class FineractClient:
    def __init__(
        self,
        settings: Settings | None = None,
        username: str | None = None,
        password: str | None = None,
    ):
        self._settings = settings or get_settings()
        s = self._settings
        self._http = httpx.Client(
            base_url=s.fineract_base_url,
            auth=(username or s.fineract_username, password or s.fineract_password),
            headers={
                "Fineract-Platform-TenantId": s.fineract_tenant_id,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(
                connect=s.fineract_connect_timeout_s,
                read=s.fineract_read_timeout_s,
                write=10,
                pool=5,
            ),
        )

    def _request(
        self, method: str, path: str, correlation_id: str, json: Any | None = None
    ) -> httpx.Response:
        fault = get_fault_state().mode
        if fault == "fineract_down":
            raise FineractUnavailableError("injected fault: fineract_down")
        try:
            response = self._http.request(
                method, path, json=json, headers={"X-Correlation-ID": correlation_id}
            )
        except httpx.ConnectError as exc:
            raise FineractUnavailableError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise FineractTimeoutError(str(exc)) from exc
        if fault == "fineract_timeout_after_send" and method != "GET":
            log.warning("fault injected", mode=fault, fineract_status=response.status_code)
            raise FineractTimeoutError("injected fault: response dropped after send")
        return response

    def create_gl_account(self, payload: dict[str, Any], correlation_id: str) -> GLAccountResult:
        response = self._request("POST", "/glaccounts", correlation_id, json=payload)
        body = response.json() if response.content else {}
        if response.status_code == 409:
            raise FineractConflictError(body.get("defaultUserMessage", "conflict"), body)
        if response.status_code >= 400:
            raise FineractClientError(response.status_code, body)
        changes = body.get("changes") or {}
        return GLAccountResult(
            fineract_id=int(body["resourceId"]),
            external_id=str(body.get("resourceExternalId", payload.get("externalId"))),
            replayed=bool(changes.get("idempotentReplay", False)),
        )

    def find_gl_account_by_external_id(self, external_id: str, correlation_id: str) -> int | None:
        response = self._request("GET", "/glaccounts", correlation_id)
        if response.status_code >= 400:
            raise FineractClientError(
                response.status_code, response.json() if response.content else {}
            )
        for account in response.json():
            if account.get("externalId") == external_id:
                return int(account["id"])
        return None

    # generic verbs for configuration (Part B setup) and lookups; faults and timeouts apply
    def get(self, path: str, correlation_id: str) -> tuple[int, Any]:
        response = self._request("GET", path, correlation_id)
        return response.status_code, (response.json() if response.content else None)

    def post(self, path: str, payload: dict[str, Any], correlation_id: str) -> tuple[int, Any]:
        response = self._request("POST", path, correlation_id, json=payload)
        return response.status_code, (response.json() if response.content else None)

    def put(self, path: str, payload: dict[str, Any], correlation_id: str) -> tuple[int, Any]:
        response = self._request("PUT", path, correlation_id, json=payload)
        return response.status_code, (response.json() if response.content else None)

    def close(self) -> None:
        self._http.close()
