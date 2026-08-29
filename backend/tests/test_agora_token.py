import pytest
from fastapi.testclient import TestClient

from app.agora import build_rtc_token
from app.main import app

client = TestClient(app)


def test_build_rtc_token_produces_a_token() -> None:
    token = build_rtc_token(
        app_id="a" * 32,
        app_certificate="b" * 32,
        channel="incident-room",
        uid=12345,
        role="publisher",
        expire_seconds=3600,
    )
    assert token.startswith("007")  # AccessToken2 version prefix
    assert len(token) > 20


def test_build_rtc_token_rejects_malformed_credentials() -> None:
    with pytest.raises(ValueError):
        build_rtc_token(
            app_id="not-a-valid-app-id",
            app_certificate="also-not-valid",
            channel="incident-room",
            uid=1,
            role="publisher",
            expire_seconds=3600,
        )


def test_token_endpoint_requires_agora_configuration() -> None:
    # The automated test suite runs without a backend/.env file, so Agora
    # credentials are unset and the endpoint should fail clearly (not silently).
    response = client.post(
        "/api/agora/token", json={"channel": "incident-room", "uid": 1001}
    )
    assert response.status_code == 503
    assert "AGORA_APP_ID" in response.json()["detail"]


def test_token_endpoint_validates_channel_name() -> None:
    response = client.post("/api/agora/token", json={"channel": "", "uid": 1001})
    assert response.status_code == 422


def test_token_endpoint_validates_uid() -> None:
    response = client.post(
        "/api/agora/token", json={"channel": "incident-room", "uid": -5}
    )
    assert response.status_code == 422


def test_token_endpoint_rejects_invalid_role() -> None:
    response = client.post(
        "/api/agora/token",
        json={"channel": "incident-room", "uid": 1001, "role": "admin"},
    )
    assert response.status_code == 422
