"""Part B setup: idempotent, ordered, maps the product to SYNCED GL ids, least-privilege role."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.setup import SetupError, run_setup
from app.setup_spec import (
    API_ROLE_PERMISSIONS,
    LOAN_PRODUCT_GL_MAPPINGS,
    loan_product_payload,
    role_permissions_payload,
)
from tests.conftest import FakeErp, FakeFineract

SETTINGS = Settings(api_key="", sync_backoff_s=0, loan_product_name="SME Asset Finance")

NINE = [
    ("1100", "Bank", "ASSET"),
    ("1200", "Loan Receivable", "ASSET"),
    ("1300", "Transfers in Suspense", "ASSET"),
    ("2100", "Overpayment Liability", "LIABILITY"),
    ("4000", "Interest Income", "INCOME"),
    ("4100", "Fee Income", "INCOME"),
    ("4200", "Penalty Income", "INCOME"),
    ("4300", "Recoveries", "INCOME"),
    ("5100", "Write-off Expense", "EXPENSE"),
]


@dataclass
class FakeFineractAdmin(FakeFineract):
    """Adds a tiny in-memory Fineract configuration surface on top of the GL fake."""

    offices: list[dict[str, Any]] = field(
        default_factory=lambda: [{"id": 1, "name": "Head Office"}]
    )
    currencies: list[str] = field(default_factory=lambda: ["USD"])
    products: list[dict[str, Any]] = field(default_factory=list)
    roles: list[dict[str, Any]] = field(default_factory=list)
    users: list[dict[str, Any]] = field(default_factory=list)
    role_permissions: dict[int, dict[str, bool]] = field(default_factory=dict)
    writes: list[tuple[str, str]] = field(default_factory=list)

    def get(self, path: str, correlation_id: str) -> tuple[int, Any]:
        if path == "/offices":
            return 200, self.offices
        if path.startswith("/currencies"):
            return 200, {"selectedCurrencyOptions": [{"code": c} for c in self.currencies]}
        if path == "/loanproducts":
            return 200, self.products
        if path == "/roles":
            return 200, self.roles
        if path == "/users":
            return 200, self.users
        return 404, {}

    def post(self, path: str, payload: dict[str, Any], correlation_id: str) -> tuple[int, Any]:
        self.writes.append(("POST", path))
        if path == "/loanproducts":
            self.products.append({"id": 7, "name": payload["name"], "payload": payload})
            return 200, {"resourceId": 7}
        if path == "/roles":
            self.roles.append({"id": 3, "name": payload["name"]})
            return 200, {"resourceId": 3}
        if path == "/users":
            self.users.append({"id": 5, "username": payload["username"], "payload": payload})
            return 200, {"resourceId": 5}
        return 404, {}

    def put(self, path: str, payload: dict[str, Any], correlation_id: str) -> tuple[int, Any]:
        self.writes.append(("PUT", path))
        if path == "/offices/1":
            self.offices[0]["name"] = payload["name"]
            return 200, {"resourceId": 1}
        if path == "/currencies":
            self.currencies = list(payload["currencies"])
            return 200, {"changes": payload}
        if path.startswith("/roles/") and path.endswith("/permissions"):
            self.role_permissions[int(path.split("/")[2])] = payload["permissions"]
            return 200, {"resourceId": 3}
        return 404, {}


@pytest.fixture
def admin() -> FakeFineractAdmin:
    return FakeFineractAdmin()


@pytest.fixture
def erp_nine() -> FakeErp:
    from app.gl_mapping import ErpAccount

    return FakeErp([ErpAccount(c, n, t, "RWF", f"GL-{c}") for c, n, t in NINE])


def test_setup_configures_everything_in_order(
    session: Session, admin: FakeFineractAdmin, erp_nine: FakeErp
) -> None:
    report = run_setup(session, admin, erp_nine, SETTINGS)

    assert report.office == {
        "id": 1,
        "name": "Kigali Main Office",
        "changed": True,
        "previous": "Head Office",
    }
    assert report.currency["changed"] and "RWF" in report.currency["enabled"]
    assert report.gl_sync["created"] == 9 and report.gl_sync["failed"] == 0
    assert report.loan_product["changed"] and report.loan_product["id"] == 7
    assert report.role["id"] == 3 and report.api_user["username"] == "integration-api"

    # the product is mapped to the ids Fineract assigned during the sync, never hard-coded
    product = admin.products[0]["payload"]
    gl_ids = report.gl_sync["gl_ids"]
    for role, ref in LOAN_PRODUCT_GL_MAPPINGS.items():
        assert product[role] == gl_ids[ref]
    assert product["accountingRule"] == 2
    assert (product["interestRatePerPeriod"], product["interestRateFrequencyType"]) == (18, 3)
    assert (product["numberOfRepayments"], product["repaymentFrequencyType"]) == (12, 2)
    assert product["multiDisburseLoan"] is False and product["currencyCode"] == "RWF"
    assert product["digitsAfterDecimal"] == 0

    # the GL sync happened before the product was created
    paths = [p for _, p in admin.writes]
    assert paths.index("/loanproducts") > 0
    assert set(admin.role_permissions[3]) == set(API_ROLE_PERMISSIONS)
    assert admin.users[0]["payload"]["roles"] == [3]


def test_setup_is_idempotent(session: Session, admin: FakeFineractAdmin, erp_nine: FakeErp) -> None:
    run_setup(session, admin, erp_nine, SETTINGS)
    writes_after_first = len(admin.writes)
    report = run_setup(session, admin, erp_nine, SETTINGS)

    assert not report.office["changed"] and not report.currency["changed"]
    assert report.gl_sync["already_synced"] == 9
    assert not report.loan_product["changed"] and not report.role["changed"]
    assert not report.api_user["changed"]
    # only the declarative permissions PUT repeats
    assert [w for w in admin.writes[writes_after_first:]] == [("PUT", "/roles/3/permissions")]
    assert len(admin.products) == 1 and len(admin.roles) == 1 and len(admin.users) == 1


def test_setup_refuses_to_create_the_product_when_a_required_account_is_not_synced(
    session: Session, admin: FakeFineractAdmin, erp_nine: FakeErp
) -> None:
    # Fineract already holds GL-1200 with a different payload (the manual-test situation)
    admin.accounts["GL-1200"] = (4, {"name": "Cash at Bangalore", "externalId": "GL-1200"})
    with pytest.raises(SetupError) as exc:
        run_setup(session, admin, erp_nine, SETTINGS)
    assert "GL-1200" in str(exc.value) and "FAILED" in str(exc.value)
    assert admin.products == [], "no product without its GL accounts"


def test_loan_product_payload_requires_every_mapping() -> None:
    with pytest.raises(ValueError, match="missing GL account ids"):
        loan_product_payload("x", "X", "RWF", {"GL-1100": 1})


def test_role_is_least_privilege() -> None:
    perms = role_permissions_payload()["permissions"]
    assert all(perms.values())
    for forbidden in (
        "ALL_FUNCTIONS",
        "ALL_FUNCTIONS_READ",
        "CREATE_USER",
        "DELETE_LOAN",
        "DELETE_CLIENT",
    ):
        assert forbidden not in perms
    assert {"CREATE_GLACCOUNT", "DISBURSE_LOAN", "REPAYMENT_LOAN", "READ_JOURNALENTRY"} <= set(
        perms
    )
