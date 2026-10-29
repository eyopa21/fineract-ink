"""FastAPI dependencies for outbound gateways. Tests override these with fakes."""

from functools import lru_cache

from app.clients.erp import ErpClient, ErpGateway
from app.clients.fineract import FineractClient, FineractGateway


@lru_cache
def _fineract_singleton() -> FineractClient:
    return FineractClient()


@lru_cache
def _erp_singleton() -> ErpClient:
    return ErpClient()


def get_fineract() -> FineractGateway:
    return _fineract_singleton()


def get_erp() -> ErpGateway:
    return _erp_singleton()
