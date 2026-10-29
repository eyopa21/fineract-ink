"""HTTP client for the Mock ERP."""

from typing import Any, Protocol

import httpx

from app.config import Settings, get_settings
from app.faults import get_fault_state
from app.gl_mapping import ErpAccount


class ErpUnavailableError(Exception):
    """Connection failed or the ERP answered 503: nothing was changed, retrying is safe."""


class ErpTimeoutError(Exception):
    """Sent but no response: outcome unknown, look it up by external_reference."""


class ErpClientError(Exception):
    def __init__(self, status_code: int, body: dict[str, Any]):
        super().__init__(f"ERP returned {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class ErpGateway(Protocol):
    def list_accounts(self, correlation_id: str) -> list[ErpAccount]: ...


class ErpClient:
    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        self._http = httpx.Client(
            base_url=s.erp_base_url,
            headers={"X-API-Key": s.erp_api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(
                connect=s.erp_connect_timeout_s, read=s.erp_read_timeout_s, write=10, pool=5
            ),
        )

    def _request(
        self, method: str, path: str, correlation_id: str, json: Any | None = None
    ) -> httpx.Response:
        if get_fault_state().mode == "erp_down":
            raise ErpUnavailableError("injected fault: erp_down")
        try:
            response = self._http.request(
                method, path, json=json, headers={"X-Correlation-ID": correlation_id}
            )
        except httpx.ConnectError as exc:
            raise ErpUnavailableError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ErpTimeoutError(str(exc)) from exc
        if response.status_code == 503:
            raise ErpUnavailableError("ERP answered 503")
        return response

    def list_accounts(self, correlation_id: str) -> list[ErpAccount]:
        response = self._request("GET", "/api/accounts", correlation_id)
        if response.status_code >= 400:
            raise ErpClientError(response.status_code, response.json() if response.content else {})
        return [
            ErpAccount(
                code=a["code"],
                name=a["name"],
                type=a["type"],
                currency=a["currency"],
                external_reference=a["external_reference"],
            )
            for a in response.json()
        ]

    def close(self) -> None:
        self._http.close()
