"""ERP account type -> Fineract GL account type. The one tested place for this mapping (Part A).

Fineract classification_enum: ASSET=1, LIABILITY=2, EQUITY=3, INCOME=4, EXPENSE=5.
Usage is always DETAIL (1): loan products can only post to detail accounts.
"""

from dataclasses import dataclass
from typing import Any

ERP_TO_FINERACT_TYPE: dict[str, int] = {
    "ASSET": 1,
    "LIABILITY": 2,
    "EQUITY": 3,
    "INCOME": 4,
    "EXPENSE": 5,
}
FINERACT_USAGE_DETAIL = 1


class UnknownAccountTypeError(ValueError):
    pass


def fineract_gl_type(erp_type: str) -> int:
    try:
        return ERP_TO_FINERACT_TYPE[erp_type.strip().upper()]
    except KeyError as exc:
        raise UnknownAccountTypeError(
            f"no Fineract GL type for ERP account type {erp_type!r}"
        ) from exc


@dataclass(frozen=True)
class ErpAccount:
    code: str
    name: str
    type: str
    currency: str
    external_reference: str


def fineract_gl_account_payload(account: ErpAccount) -> dict[str, Any]:
    """Body for POST /glaccounts on the customized Fineract. externalId carries the ERP reference so
    Fineract itself refuses duplicates and returns the existing account on an identical replay."""
    return {
        "name": account.name,
        "glCode": account.code,
        "type": fineract_gl_type(account.type),
        "usage": FINERACT_USAGE_DETAIL,
        "manualEntriesAllowed": True,
        "description": f"ERP account {account.code} ({account.external_reference})",
        "externalId": account.external_reference,
    }
