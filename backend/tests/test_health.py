from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "echoward-backend"
    assert body["database_connected"] is True


def test_health_response_schema() -> None:
    response = client.get("/health")
    body = response.json()
    assert set(body.keys()) == {"status", "service", "environment", "database_connected"}
