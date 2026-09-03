from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app import incident_db
from app.db import get_connection
from app.incident_models import ConflictStatement, ConflictStatus
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


def _create_conflict(incident_id: str) -> str:
    now = datetime.now(UTC)
    with get_connection() as conn:
        conflict = incident_db.insert_conflict(
            conn,
            incident_id,
            "Database health",
            [
                ConflictStatement(source="Engineer", statement="The database is overloaded."),
                ConflictStatement(source="Support", statement="The database looks healthy."),
            ],
            now,
        )
    return conflict.id


def test_resolve_conflict_endpoint() -> None:
    created = client.post("/api/incidents", json={"title": "Conflict resolve test"}).json()
    incident_id = created["id"]
    conflict_id = _create_conflict(incident_id)

    res = client.post(f"/api/incidents/{incident_id}/conflicts/{conflict_id}/resolve")
    assert res.status_code == 200
    state = res.json()
    resolved = next(c for c in state["conflicts"] if c["id"] == conflict_id)
    assert resolved["status"] == "resolved"

    fetched = incident_db.get_conflict(conflict_id)
    assert fetched is not None
    assert fetched.status == ConflictStatus.resolved

    # Resolving is the only sanctioned mutation - the underlying statements are untouched.
    assert len(fetched.statements) == 2


def test_resolve_conflict_endpoint_removes_coordination_finding() -> None:
    created = client.post("/api/incidents", json={"title": "Conflict finding cleanup test"}).json()
    incident_id = created["id"]
    conflict_id = _create_conflict(incident_id)

    state = client.get(f"/api/incidents/{incident_id}/state").json()
    # No conversation turn ran, so coordination findings haven't been refreshed yet -
    # trigger a refresh via a status change (a normal, already-tested broadcast trigger).
    client.patch(f"/api/incidents/{incident_id}/status", json={"status": "identified"})
    state = client.get(f"/api/incidents/{incident_id}/state").json()
    assert any(f["type"] == "conflict" for f in state["coordination_findings"])

    res = client.post(f"/api/incidents/{incident_id}/conflicts/{conflict_id}/resolve")
    assert res.status_code == 200
    assert not any(f["type"] == "conflict" for f in res.json()["coordination_findings"])


def test_resolve_conflict_endpoint_incident_not_found() -> None:
    res = client.post("/api/incidents/does-not-exist/conflicts/whatever/resolve")
    assert res.status_code == 404


def test_resolve_conflict_endpoint_conflict_not_found() -> None:
    created = client.post("/api/incidents", json={"title": "Missing conflict test"}).json()
    res = client.post(f"/api/incidents/{created['id']}/conflicts/does-not-exist/resolve")
    assert res.status_code == 404


def test_resolve_conflict_endpoint_rejects_conflict_from_another_incident() -> None:
    incident_a = client.post("/api/incidents", json={"title": "Incident A"}).json()["id"]
    incident_b = client.post("/api/incidents", json={"title": "Incident B"}).json()["id"]
    conflict_id = _create_conflict(incident_a)

    res = client.post(f"/api/incidents/{incident_b}/conflicts/{conflict_id}/resolve")
    assert res.status_code == 404

    # The conflict itself must be untouched by the rejected cross-incident call.
    fetched = incident_db.get_conflict(conflict_id)
    assert fetched is not None
    assert fetched.status == ConflictStatus.unresolved
