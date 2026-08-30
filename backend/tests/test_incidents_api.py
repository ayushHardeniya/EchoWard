from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_incident() -> None:
    res = client.post("/api/incidents", json={"title": "Payments outage"})
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "Payments outage"
    assert body["status"] == "investigating"
    assert body["id"]
    assert body["created_at"] == body["updated_at"]


def test_create_incident_requires_title() -> None:
    res = client.post("/api/incidents", json={"title": ""})
    assert res.status_code == 422


def test_get_incident() -> None:
    created = client.post("/api/incidents", json={"title": "DB latency spike"}).json()
    res = client.get(f"/api/incidents/{created['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == created["id"]


def test_get_incident_not_found() -> None:
    res = client.get("/api/incidents/does-not-exist")
    assert res.status_code == 404


def test_get_incident_state_empty_initially() -> None:
    created = client.post("/api/incidents", json={"title": "Fresh incident"}).json()
    res = client.get(f"/api/incidents/{created['id']}/state")
    assert res.status_code == 200
    state = res.json()
    assert state["incident"]["id"] == created["id"]
    for key in (
        "facts",
        "hypotheses",
        "decisions",
        "actions",
        "timeline",
        "unresolved_questions",
        "conflicts",
    ):
        assert state[key] == []


def test_get_incident_state_not_found() -> None:
    res = client.get("/api/incidents/does-not-exist/state")
    assert res.status_code == 404


def test_conversation_endpoint_requires_gemini_configuration() -> None:
    # No GEMINI_API_KEY in the automated test environment (see backend/.env.example).
    created = client.post("/api/incidents", json={"title": "No LLM configured"}).json()
    res = client.post(
        f"/api/incidents/{created['id']}/conversation",
        json={"speaker": "Alice", "text": "Payments are failing."},
    )
    assert res.status_code == 503
    assert "GEMINI_API_KEY" in res.json()["detail"]


def test_conversation_endpoint_not_found_incident() -> None:
    res = client.post(
        "/api/incidents/does-not-exist/conversation",
        json={"speaker": "Alice", "text": "Payments are failing."},
    )
    assert res.status_code == 404


def test_conversation_endpoint_validates_request() -> None:
    created = client.post("/api/incidents", json={"title": "Validation check"}).json()
    res = client.post(f"/api/incidents/{created['id']}/conversation", json={"speaker": "", "text": "x"})
    assert res.status_code == 422
