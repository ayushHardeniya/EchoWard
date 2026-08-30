from fastapi.testclient import TestClient

from app import incident_db
from app.agora_events import AgoraWebhookEnvelope, extract_user_turns
from app.main import app

client = TestClient(app)


def test_extract_user_turns_from_agent_history_event() -> None:
    envelope = AgoraWebhookEnvelope(
        noticeId="n1",
        productId=17,
        eventType=103,
        payload={
            "agent_id": "agent-1",
            "channel": "incident-room",
            "contents": [
                {"role": "user", "content": "Payments are failing for 30% of users."},
                {"role": "assistant", "content": "Understood, tracking that."},
                {"role": "user", "content": "I think it's the database."},
            ],
        },
    )
    turns = extract_user_turns(envelope)
    assert turns == [
        "Payments are failing for 30% of users.",
        "I think it's the database.",
    ]


def test_extract_user_turns_ignores_unrelated_event_types() -> None:
    envelope = AgoraWebhookEnvelope(eventType=101, payload={"agent_id": "agent-1"})
    assert extract_user_turns(envelope) == []


def test_extract_user_turns_handles_malformed_payload_gracefully() -> None:
    envelope = AgoraWebhookEnvelope(eventType=103, payload={"contents": "not-a-list"})
    assert extract_user_turns(envelope) == []


def test_webhook_endpoint_requires_gemini_configuration() -> None:
    incident = incident_db.create_incident("Webhook test incident")
    payload = {
        "noticeId": "n1",
        "productId": 17,
        "eventType": 103,
        "payload": {
            "agent_id": "agent-1",
            "channel": "incident-room",
            "contents": [{"role": "user", "content": "Payments are failing."}],
        },
    }
    res = client.post(f"/api/agora/webhook/{incident.id}", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["turns_ingested"] == 0
    assert body["turns_skipped"] == 1  # LLM not configured -> skipped, not crashed


def test_webhook_endpoint_ignores_non_transcript_events() -> None:
    incident = incident_db.create_incident("Webhook non-transcript test")
    res = client.post(
        f"/api/agora/webhook/{incident.id}",
        json={"noticeId": "n1", "productId": 17, "eventType": 101, "payload": {}},
    )
    assert res.status_code == 200
    assert res.json() == {
        "incident_id": incident.id,
        "event_type": 101,
        "turns_ingested": 0,
        "turns_skipped": 0,
        "note": res.json()["note"],
    }


def test_webhook_endpoint_tolerates_garbage_body() -> None:
    incident = incident_db.create_incident("Webhook garbage test")
    res = client.post(f"/api/agora/webhook/{incident.id}", json={"totally": "unexpected"})
    assert res.status_code == 200
    assert res.json()["turns_ingested"] == 0
