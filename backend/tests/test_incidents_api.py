from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import incident_db
from app import intelligence as intelligence_module
from app.db import get_connection
from app.incident_models import (
    ConflictStatement,
    ConflictStatus,
    ConversationAnalysis,
    ExtractedConflict,
    ExtractedConflictStatement,
    ExtractedFact,
    ExtractedHypothesis,
)
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


def test_deterministically_detected_conflict_resolves_via_the_same_endpoint() -> None:
    # Requirement 4: a conflict created by the deterministic fact-vs-hypothesis
    # backstop (intelligence._detect_contradiction) must still go through the
    # exact same human-only resolution endpoint as an LLM-detected one - no
    # separate/new resolution path for it.
    created = client.post("/api/incidents", json={"title": "Deterministic conflict resolve test"}).json()
    incident_id = created["id"]

    hypothesis_analysis = ConversationAnalysis(
        hypotheses=[ExtractedHypothesis(statement="The payment database is overloaded.")]
    )
    fact_analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Database metrics look normal.")])

    with patch.object(intelligence_module, "analyze_conversation", return_value=hypothesis_analysis):
        client.post(
            f"/api/incidents/{incident_id}/conversation",
            json={"speaker": "Alice", "text": "The payment database is overloaded."},
        )
    with patch.object(intelligence_module, "analyze_conversation", return_value=fact_analysis):
        res = client.post(
            f"/api/incidents/{incident_id}/conversation",
            json={"speaker": "Bob", "text": "Actually, database metrics look normal."},
        )

    assert res.status_code == 200
    conflicts = res.json()["state"]["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["status"] == "unresolved"
    conflict_id = conflicts[0]["id"]

    resolve_res = client.post(f"/api/incidents/{incident_id}/conflicts/{conflict_id}/resolve")
    assert resolve_res.status_code == 200
    resolved = next(c for c in resolve_res.json()["conflicts"] if c["id"] == conflict_id)
    assert resolved["status"] == "resolved"


def test_merged_llm_and_deterministic_conflict_resolves_via_the_same_endpoint() -> None:
    # Requirement 4: even after the LLM-detected and deterministic-backstop
    # conflicts merge into one row (see test_intelligence.py's cross-detection
    # dedup tests), resolving it still goes through the same, only, human
    # resolution endpoint - no separate path for a "merged" conflict.
    created = client.post("/api/incidents", json={"title": "Merged conflict resolve test"}).json()
    incident_id = created["id"]

    hypothesis_analysis = ConversationAnalysis(
        hypotheses=[ExtractedHypothesis(statement="The payment database is overloaded.")]
    )
    turn_analysis = ConversationAnalysis(
        facts=[ExtractedFact(statement="Database metrics look normal.")],
        conflicts=[
            ExtractedConflict(
                topic="Payment database health",
                statements=[
                    ExtractedConflictStatement(source="Alice", statement="The payment database is overloaded."),
                    ExtractedConflictStatement(source="Bob", statement="Database metrics look normal."),
                ],
            )
        ],
    )

    with patch.object(intelligence_module, "analyze_conversation", return_value=hypothesis_analysis):
        client.post(
            f"/api/incidents/{incident_id}/conversation",
            json={"speaker": "Alice", "text": "The payment database is overloaded."},
        )
    with patch.object(intelligence_module, "analyze_conversation", return_value=turn_analysis):
        res = client.post(
            f"/api/incidents/{incident_id}/conversation",
            json={"speaker": "Bob", "text": "Actually, database metrics look normal."},
        )

    assert res.status_code == 200
    conflicts = res.json()["state"]["conflicts"]
    assert len(conflicts) == 1  # merged, not duplicated
    assert conflicts[0]["status"] == "unresolved"
    conflict_id = conflicts[0]["id"]

    resolve_res = client.post(f"/api/incidents/{incident_id}/conflicts/{conflict_id}/resolve")
    assert resolve_res.status_code == 200
    resolved = next(c for c in resolve_res.json()["conflicts"] if c["id"] == conflict_id)
    assert resolved["status"] == "resolved"


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
