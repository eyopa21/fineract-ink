from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_in_memory_database() -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
