"""M5 action-execution-lifecycle tests.

Covers app/tools.py (allowlist validation + sandbox adapter), app/actions.py
(prepare/confirm-and-execute business logic), and the two new
/api/incidents/{id}/actions/{action_id}/{prepare,confirm} endpoints -
including the realtime broadcast and M4 coordination refresh both trigger on.

Every test drives the real FastAPI app end-to-end (no mocking of app/actions.py
or app/tools.py itself - the sandbox adapter is already deterministic, so there's
nothing to mock). The one thing seeded directly via incident_db is the initial
`pending` Action, standing in for what app/intelligence.py would normally create
from a conversation turn - M5 doesn't change how actions come into existence,
only what happens to them afterward.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app import incident_db
from app.db import get_connection
from app.incident_models import ActionStatus
from app.main import app

client = TestClient(app)


def _create_incident(title: str) -> str:
    return client.post("/api/incidents", json={"title": title}).json()["id"]


def _insert_pending_action(incident_id: str, description: str = "Roll back payment service") -> str:
    with get_connection() as conn:
        action = incident_db.insert_action(conn, incident_id, description, None, datetime.now(UTC))
    return action.id


VALID_BODY = {"action_type": "rollback_payment_service", "target": "payment-service", "reason": "Error rate spiking."}


def test_prepare_action_moves_to_awaiting_confirmation() -> None:
    incident_id = _create_incident("Prepare test")
    action_id = _insert_pending_action(incident_id)

    res = client.post(f"/api/incidents/{incident_id}/actions/{action_id}/prepare", json=VALID_BODY)
    assert res.status_code == 200

    action = next(a for a in res.json()["actions"] if a["id"] == action_id)
    assert action["status"] == "awaiting_confirmation"
    assert action["action_type"] == "rollback_payment_service"
    assert action["target"] == "payment-service"


def test_confirm_requires_prior_preparation() -> None:
    # A plain "yeah" / "let's do it" in conversation never reaches this
    # endpoint - but even a direct API call must be rejected while the
    # action is still merely `pending`, never `awaiting_confirmation`.
    incident_id = _create_incident("Confirm without prepare test")
    action_id = _insert_pending_action(incident_id)

    res = client.post(f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={})
    assert res.status_code == 409


def test_prepare_rejects_unknown_action_type() -> None:
    incident_id = _create_incident("Invalid action type test")
    action_id = _insert_pending_action(incident_id)

    res = client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/prepare",
        json={"action_type": "delete_database", "target": "payment-service", "reason": "why not"},
    )
    assert res.status_code == 400

    action = incident_db.get_action(incident_id, action_id)
    assert action is not None
    assert action.status == ActionStatus.pending


def test_prepare_rejects_disallowed_target() -> None:
    incident_id = _create_incident("Invalid target test")
    action_id = _insert_pending_action(incident_id)

    res = client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/prepare",
        json={"action_type": "rollback_payment_service", "target": "auth-service", "reason": "Error rate spiking."},
    )
    assert res.status_code == 400

    action = incident_db.get_action(incident_id, action_id)
    assert action is not None
    assert action.status == ActionStatus.pending


def test_prepare_rejects_action_from_another_incident() -> None:
    incident_a = _create_incident("Incident A")
    incident_b = _create_incident("Incident B")
    action_id = _insert_pending_action(incident_a)

    res = client.post(f"/api/incidents/{incident_b}/actions/{action_id}/prepare", json=VALID_BODY)
    assert res.status_code == 404


def test_prepare_not_found_incident() -> None:
    res = client.post("/api/incidents/does-not-exist/actions/some-action/prepare", json=VALID_BODY)
    assert res.status_code == 404


def test_prepare_not_found_action() -> None:
    incident_id = _create_incident("Missing action test")
    res = client.post(f"/api/incidents/{incident_id}/actions/does-not-exist/prepare", json=VALID_BODY)
    assert res.status_code == 404


def test_confirm_executes_through_adapter_and_completes() -> None:
    incident_id = _create_incident("Confirm success test")
    action_id = _insert_pending_action(incident_id)
    client.post(f"/api/incidents/{incident_id}/actions/{action_id}/prepare", json=VALID_BODY)

    res = client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={"confirmed_by": "Sarah"}
    )
    assert res.status_code == 200

    action = next(a for a in res.json()["actions"] if a["id"] == action_id)
    assert action["status"] == "completed"
    assert action["tool_result"]["success"] is True
    assert action["tool_result"]["external_id"] is not None


def test_confirm_creates_timeline_events() -> None:
    incident_id = _create_incident("Timeline test")
    action_id = _insert_pending_action(incident_id)
    client.post(f"/api/incidents/{incident_id}/actions/{action_id}/prepare", json=VALID_BODY)
    res = client.post(f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={})

    events = " | ".join(e["event"] for e in res.json()["timeline"])
    assert "awaiting human confirmation" in events
    assert "confirmed by" in events
    assert "execution started" in events
    assert "Action completed" in events


def test_confirm_failure_path_marks_action_failed_and_incident_stays_open() -> None:
    incident_id = _create_incident("Failure test")
    action_id = _insert_pending_action(incident_id)
    client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/prepare",
        json={
            "action_type": "rollback_payment_service",
            "target": "payment-service",
            "reason": "force_failure for test determinism",
        },
    )

    res = client.post(f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={})
    assert res.status_code == 200

    body = res.json()
    action = next(a for a in body["actions"] if a["id"] == action_id)
    assert action["status"] == "failed"
    assert action["tool_result"]["success"] is False
    assert body["incident"]["status"] != "resolved"

    events = " | ".join(e["event"] for e in body["timeline"])
    assert "Action failed" in events


def test_completed_action_cannot_be_confirmed_again() -> None:
    incident_id = _create_incident("Double execution test")
    action_id = _insert_pending_action(incident_id)
    client.post(f"/api/incidents/{incident_id}/actions/{action_id}/prepare", json=VALID_BODY)
    client.post(f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={})

    res = client.post(f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={})
    assert res.status_code == 409

    # Still exactly one completed action - no duplicate record was created.
    actions = incident_db.list_actions(incident_id)
    assert len(actions) == 1
    assert actions[0].status == ActionStatus.completed


def test_failed_action_cannot_be_confirmed_again() -> None:
    incident_id = _create_incident("Failed re-confirm test")
    action_id = _insert_pending_action(incident_id)
    client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/prepare",
        json={"action_type": "rollback_payment_service", "target": "payment-service", "reason": "force_failure"},
    )
    client.post(f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={})

    res = client.post(f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={})
    assert res.status_code == 409


def test_coordination_refresh_reflects_action_lifecycle() -> None:
    incident_id = _create_incident("Coordination refresh test")
    action_id = _insert_pending_action(incident_id)

    prepared = client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/prepare", json=VALID_BODY
    ).json()
    findings = {f["type"]: f for f in prepared["coordination_findings"]}
    assert "action_awaiting_confirmation" in findings

    confirmed = client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={}
    ).json()
    findings = {f["type"] for f in confirmed["coordination_findings"]}
    assert "action_awaiting_confirmation" not in findings  # resolved once past that status


def test_coordination_refresh_flags_failed_action() -> None:
    incident_id = _create_incident("Coordination failure test")
    action_id = _insert_pending_action(incident_id)
    client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/prepare",
        json={"action_type": "rollback_payment_service", "target": "payment-service", "reason": "force_failure"},
    )
    confirmed = client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={}
    ).json()
    findings = {f["type"] for f in confirmed["coordination_findings"]}
    assert "action_failed" in findings


def test_realtime_broadcast_reflects_prepare_and_confirm() -> None:
    incident_id = _create_incident("Realtime action test")
    action_id = _insert_pending_action(incident_id)

    with client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws:
        ws.receive_json()  # initial state

        client.post(f"/api/incidents/{incident_id}/actions/{action_id}/prepare", json=VALID_BODY)
        prepared_update = ws.receive_json()

        client.post(f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={})
        confirmed_update = ws.receive_json()

    prepared_action = next(a for a in prepared_update["state"]["actions"] if a["id"] == action_id)
    assert prepared_action["status"] == "awaiting_confirmation"

    confirmed_action = next(a for a in confirmed_update["state"]["actions"] if a["id"] == action_id)
    assert confirmed_action["status"] == "completed"
    assert confirmed_action["tool_result"]["success"] is True


def test_end_to_end_vertical_slice_proposed_to_completed() -> None:
    """proposed -> awaiting confirmation -> confirm -> execute -> success -> updated incident state."""
    incident_id = _create_incident("E2E vertical slice")
    action_id = _insert_pending_action(incident_id, "Roll back payment service to previous version")

    initial = incident_db.get_action(incident_id, action_id)
    assert initial is not None
    assert initial.status == ActionStatus.pending

    prepare_res = client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/prepare",
        json={
            "action_type": "rollback_payment_service",
            "target": "payment-service",
            "reason": "Payment error rate spiking after latest deploy.",
        },
    )
    assert prepare_res.status_code == 200
    prepared_action = next(a for a in prepare_res.json()["actions"] if a["id"] == action_id)
    assert prepared_action["status"] == "awaiting_confirmation"

    confirm_res = client.post(
        f"/api/incidents/{incident_id}/actions/{action_id}/confirm", json={"confirmed_by": "Incident Commander"}
    )
    assert confirm_res.status_code == 200
    final_state = confirm_res.json()

    final_action = next(a for a in final_state["actions"] if a["id"] == action_id)
    assert final_action["status"] == "completed"
    assert final_action["tool_result"]["success"] is True

    assert any("Action completed" in e["event"] for e in final_state["timeline"])
    assert not any(f["type"] == "action_awaiting_confirmation" for f in final_state["coordination_findings"])
