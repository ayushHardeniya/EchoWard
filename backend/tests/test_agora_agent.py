import base64

from fastapi.testclient import TestClient

from app.agora import build_agent_join_payload
from app.config import Settings
from app.main import app

client = TestClient(app)


def _configured_settings(**overrides) -> Settings:
    defaults = dict(
        agora_app_id="a" * 32,
        agora_app_certificate="b" * 32,
        agora_customer_id="cid",
        agora_customer_secret="csecret",
        agora_agent_pipeline_id="pipeline-123",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_build_agent_join_payload_shape() -> None:
    settings = _configured_settings()
    payload = build_agent_join_payload(
        settings, channel="incident-room", agent_uid=9999, agent_token="fake-token"
    )

    # pipeline_id must be a top-level field, per the current Conversational AI
    # Engine API — not nested under `properties`.
    assert payload["pipeline_id"] == "pipeline-123"
    assert "pipeline_id" not in payload["properties"]

    props = payload["properties"]
    assert props["channel"] == "incident-room"
    assert props["token"] == "fake-token"
    assert props["agent_rtc_uid"] == "9999"
    assert props["remote_rtc_uids"] == ["*"]

    # ASR/LLM/TTS are supplied by the published Agent Builder pipeline, not
    # duplicated here.
    assert "asr" not in props
    assert "llm" not in props
    assert "tts" not in props


def test_agora_convo_ai_configured_requires_pipeline_id() -> None:
    settings = _configured_settings(agora_agent_pipeline_id="")
    assert settings.agora_convo_ai_configured is False

    settings = _configured_settings()
    assert settings.agora_convo_ai_configured is True


def test_start_agent_requires_convo_ai_configuration() -> None:
    # No backend/.env in the automated test suite, so credentials are unset.
    response = client.post("/api/agora/agent/start", json={"channel": "incident-room"})
    assert response.status_code == 503
    assert "AGORA_" in response.json()["detail"]


def test_start_agent_requires_pipeline_id_even_with_credentials(monkeypatch) -> None:
    from app import agora

    settings = _configured_settings(agora_agent_pipeline_id="")
    monkeypatch.setattr(agora, "get_settings", lambda: settings)

    response = client.post("/api/agora/agent/start", json={"channel": "incident-room"})
    assert response.status_code == 503
    assert "AGORA_AGENT_PIPELINE_ID" in response.json()["detail"]


def test_start_agent_validates_channel_name() -> None:
    response = client.post("/api/agora/agent/start", json={"channel": ""})
    assert response.status_code == 422


def test_start_agent_join_url_and_auth(monkeypatch) -> None:
    import httpx

    from app import agora

    settings = _configured_settings()
    monkeypatch.setattr(agora, "get_settings", lambda: settings)

    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"agent_id": "agent-1", "status": "RUNNING"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def post(self, url, json=None, auth=None):
            captured["url"] = url
            captured["json"] = json
            captured["auth"] = auth
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = client.post("/api/agora/agent/start", json={"channel": "incident-room"})
    assert response.status_code == 200

    assert captured["url"] == (
        f"https://api.agora.io/api/conversational-ai-agent/v2/projects/{settings.agora_app_id}/join"
    )
    assert captured["json"]["pipeline_id"] == "pipeline-123"

    auth = captured["auth"]
    assert isinstance(auth, httpx.BasicAuth)
    decoded = base64.b64decode(auth._auth_header.split(" ")[1]).decode()
    assert decoded == "cid:csecret"


def test_stop_agent_requires_convo_ai_configuration() -> None:
    response = client.post("/api/agora/agent/stop", json={"agent_id": "abc123"})
    assert response.status_code == 503


def test_stop_agent_validates_agent_id() -> None:
    response = client.post("/api/agora/agent/stop", json={"agent_id": ""})
    assert response.status_code == 422
