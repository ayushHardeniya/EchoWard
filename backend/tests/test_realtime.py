from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import incident_db
from app import intelligence as intelligence_module
from app.incident_models import ConversationAnalysis, ExtractedFact
from app.main import app

client = TestClient(app)


def _create_incident(title: str) -> str:
    return client.post("/api/incidents", json={"title": title}).json()["id"]


def test_stream_sends_initial_state_on_connect() -> None:
    incident_id = _create_incident("Stream initial state test")
    with client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws:
        message = ws.receive_json()
    assert message["type"] == "incident.updated"
    assert message["incident_id"] == incident_id
    assert message["state"]["incident"]["id"] == incident_id
    assert message["state"]["facts"] == []


def test_stream_rejects_unknown_incident() -> None:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/incidents/does-not-exist/stream"):
            pass
    assert exc_info.value.code == 4404


def test_stream_broadcasts_after_conversation_turn_with_changes() -> None:
    incident_id = _create_incident("Broadcast test")
    analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Payments failing for ~30%")])

    with client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws:
        ws.receive_json()  # initial state

        with patch.object(intelligence_module, "analyze_conversation", return_value=analysis):
            res = client.post(
                f"/api/incidents/{incident_id}/conversation",
                json={"speaker": "Alice", "text": "Payments are failing for about 30% of users."},
            )
        assert res.status_code == 200

        update = ws.receive_json()

    assert update["type"] == "incident.updated"
    assert len(update["state"]["facts"]) == 1
    assert update["state"]["facts"][0]["statement"] == "Payments failing for ~30%"


def test_stream_does_not_broadcast_for_no_op_conversation() -> None:
    incident_id = _create_incident("No-op broadcast test")

    with client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws:
        ws.receive_json()  # initial state

        with patch.object(intelligence_module, "analyze_conversation", return_value=ConversationAnalysis()):
            res = client.post(
                f"/api/incidents/{incident_id}/conversation",
                json={"speaker": "Alice", "text": "Good morning everyone."},
            )
        assert res.status_code == 200

        # Nothing else was sent - send a status update instead, which we know
        # broadcasts, and confirm it's the *first* thing received after the no-op.
        status_res = client.patch(f"/api/incidents/{incident_id}/status", json={"status": "identified"})
        assert status_res.status_code == 200

        update = ws.receive_json()

    assert update["state"]["incident"]["status"] == "identified"
    assert update["state"]["facts"] == []  # confirms the no-op conversation produced nothing


def test_stream_broadcasts_on_status_change() -> None:
    incident_id = _create_incident("Status broadcast test")

    with client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws:
        ws.receive_json()  # initial state
        res = client.patch(f"/api/incidents/{incident_id}/status", json={"status": "mitigating"})
        assert res.status_code == 200
        update = ws.receive_json()

    assert update["state"]["incident"]["status"] == "mitigating"


def test_multiple_connected_clients_all_receive_the_update() -> None:
    incident_id = _create_incident("Multi-client broadcast test")
    analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Multi-client fact")])

    with (
        client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws1,
        client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws2,
    ):
        ws1.receive_json()
        ws2.receive_json()

        with patch.object(intelligence_module, "analyze_conversation", return_value=analysis):
            client.post(
                f"/api/incidents/{incident_id}/conversation",
                json={"speaker": "Bob", "text": "Something happened."},
            )

        update1 = ws1.receive_json()
        update2 = ws2.receive_json()

    assert len(update1["state"]["facts"]) == 1
    assert len(update2["state"]["facts"]) == 1
    assert update1["state"]["facts"][0]["statement"] == "Multi-client fact"


def test_disconnect_removes_connection_from_manager() -> None:
    from app.realtime import manager

    incident_id = _create_incident("Disconnect cleanup test")
    assert manager.connection_count(incident_id) == 0

    with client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws:
        ws.receive_json()
        assert manager.connection_count(incident_id) == 1

    assert manager.connection_count(incident_id) == 0


def test_update_incident_status_endpoint() -> None:
    incident_id = _create_incident("Status endpoint test")
    res = client.patch(f"/api/incidents/{incident_id}/status", json={"status": "resolved"})
    assert res.status_code == 200
    assert res.json()["incident"]["status"] == "resolved"

    fetched = incident_db.get_incident(incident_id)
    assert fetched is not None
    assert fetched.status.value == "resolved"


def test_update_incident_status_rejects_unknown_value() -> None:
    incident_id = _create_incident("Status validation test")
    res = client.patch(f"/api/incidents/{incident_id}/status", json={"status": "on_fire"})
    assert res.status_code == 422


def test_update_incident_status_not_found() -> None:
    res = client.patch("/api/incidents/does-not-exist/status", json={"status": "resolved"})
    assert res.status_code == 404
