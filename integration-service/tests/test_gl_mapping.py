"""The one place ERP account types map to Fineract GL types."""

import pytest

from app.gl_mapping import (
    ErpAccount,
    UnknownAccountTypeError,
    fineract_gl_account_payload,
    fineract_gl_type,
)


@pytest.mark.parametrize(
    ("erp_type", "expected"),
    [("ASSET", 1), ("LIABILITY", 2), ("EQUITY", 3), ("INCOME", 4), ("EXPENSE", 5), (" income ", 4)],
)
def test_every_erp_type_maps_to_a_fineract_classification(erp_type: str, expected: int) -> None:
    assert fineract_gl_type(erp_type) == expected


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(UnknownAccountTypeError):
        fineract_gl_type("CASH")


def test_fineract_payload_carries_the_external_reference_and_detail_usage() -> None:
    account = ErpAccount("1200", "Loan Receivable", "ASSET", "RWF", "GL-1200")
    payload = fineract_gl_account_payload(account)
    assert payload == {
        "name": "Loan Receivable",
        "glCode": "1200",
        "type": 1,
        "usage": 1,
        "manualEntriesAllowed": True,
        "description": "ERP account 1200 (GL-1200)",
        "externalId": "GL-1200",
    }
