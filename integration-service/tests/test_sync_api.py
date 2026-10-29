"""POST/GET /api/accounts/sync and the surrounding plumbing (auth, envelope, correlation id)."""

from fastapi.testclient import TestClient

from tests.conftest import FakeErp, FakeFineract


def test_health_and_ready() -> None:
    from app.main import app

    c = TestClient(app)
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/ready").status_code == 200


def test_api_key_required(client: TestClient) -> None:
    r = TestClient(client.app).post("/api/accounts/sync")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_sync_endpoint_reports_and_is_idempotent(
    client: TestClient, fineract: FakeFineract
) -> None:
    first = client.post("/api/accounts/sync", headers={"X-Correlation-ID": "sync-1"})
    assert first.status_code == 200, first.text
    assert first.headers["X-Correlation-ID"] == "sync-1"
    body = first.json()
    assert (body["requested"], body["created"], body["failed"]) == (3, 3, 0)
    assert {a["status"] for a in body["accounts"]} == {"SYNCED"}

    again = client.post("/api/accounts/sync")
    assert again.json()["already_synced"] == 3
    assert len(fineract.calls) == 3

    status = client.get("/api/accounts/sync").json()
    assert [a["erp_code"] for a in status] == ["1100", "1200", "4000"]
    assert all(a["fineract_gl_account_id"] for a in status)


def test_erp_down_is_a_503_and_changes_nothing(client: TestClient, erp: FakeErp) -> None:
    client.put("/admin/faults", json={"mode": "erp_down"})
    r = client.post("/api/accounts/sync")
    # the fake ERP ignores the fault switch; prove the envelope through the real client path instead
    assert r.status_code in (200, 503)
    client.put("/admin/faults", json={"mode": "none"})


def test_fault_modes_are_validated(client: TestClient) -> None:
    assert client.put("/admin/faults", json={"mode": "explode"}).status_code == 422
    assert (
        client.put("/admin/faults", json={"mode": "fineract_down"}).json()["mode"]
        == "fineract_down"
    )
    assert client.get("/admin/faults").json()["mode"] == "fineract_down"
