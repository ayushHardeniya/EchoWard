from fastapi.testclient import TestClient

from app.agora import ECHOWARD_SYSTEM_PROMPT, build_agent_join_payload
from app.config import Settings
from app.main import app

client = TestClient(app)


def test_build_agent_join_payload_shape() -> None:
    settings = Settings(
        agora_app_id="a" * 32,
        agora_app_certificate="b" * 32,
        agora_customer_id="cid",
        agora_customer_secret="csecret",
    )
    payload = build_agent_join_payload(
        settings, channel="incident-room", agent_uid=9999, agent_token="fake-token"
    )

    props = payload["properties"]
    assert props["channel"] == "incident-room"
    assert props["token"] == "fake-token"
    assert props["agent_rtc_uid"] == "9999"
    assert props["remote_rtc_uids"] == ["*"]

    assert props["asr"]["credential_mode"] == "managed"
    assert props["llm"]["credential_mode"] == "managed"
    assert props["tts"]["credential_mode"] == "managed"

    system_message = props["llm"]["system_messages"][0]["content"]
    assert system_message == ECHOWARD_SYSTEM_PROMPT
    assert "EchoWard" in system_message
    assert "incident commander" in system_message


def test_start_agent_requires_convo_ai_configuration() -> None:
    # No backend/.env in the automated test suite, so credentials are unset.
    response = client.post("/api/agora/agent/start", json={"channel": "incident-room"})
    assert response.status_code == 503
    assert "AGORA_" in response.json()["detail"]


def test_start_agent_validates_channel_name() -> None:
    response = client.post("/api/agora/agent/start", json={"channel": ""})
    assert response.status_code == 422


def test_stop_agent_requires_convo_ai_configuration() -> None:
    response = client.post("/api/agora/agent/stop", json={"agent_id": "abc123"})
    assert response.status_code == 503


def test_stop_agent_validates_agent_id() -> None:
    response = client.post("/api/agora/agent/stop", json={"agent_id": ""})
    assert response.status_code == 422
