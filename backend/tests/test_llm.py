from unittest.mock import MagicMock

import httpx
import pytest
from google.genai import errors as genai_errors

from app import llm
from app.config import Settings
from app.incident_models import ConversationAnalysis
from app.llm import LLMNotConfiguredError, LLMOutputError, generate_structured


def test_generate_structured_requires_gemini_api_key() -> None:
    # No GEMINI_API_KEY in the automated test environment (see backend/.env.example);
    # this must fail before ever attempting a network call.
    with pytest.raises(LLMNotConfiguredError):
        generate_structured("irrelevant prompt", ConversationAnalysis)


def _server_error(code: int = 503) -> genai_errors.ServerError:
    return genai_errors.ServerError(code, {"message": "temporarily unavailable", "status": "UNAVAILABLE"}, None)


def _rate_limit_error_with_retry_info() -> genai_errors.ClientError:
    body = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "rate limited",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "1s"}],
        }
    }
    return genai_errors.ClientError(429, body, None)


def _quota_exhausted_error() -> genai_errors.ClientError:
    # A sustained daily quota exhaustion: same code/status as a transient
    # rate limit, but with no RetryInfo detail - this is the field that must
    # distinguish the two, not the message text.
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota exceeded for today"}}
    return genai_errors.ClientError(429, body, None)


def _bad_request_error() -> genai_errors.ClientError:
    body = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad request"}}
    return genai_errors.ClientError(400, body, None)


@pytest.fixture
def gemini_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: Settings(gemini_api_key="test-key", gemini_model="gemini-flash-latest"),
    )


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    monkeypatch.setattr(llm.genai, "Client", lambda **kwargs: client)
    monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)
    return client


def test_retries_on_server_error_then_succeeds(gemini_configured: None, mock_client: MagicMock) -> None:
    success = MagicMock(parsed=ConversationAnalysis())
    mock_client.models.generate_content.side_effect = [_server_error(), success]

    result = generate_structured("prompt", ConversationAnalysis)

    assert result is success.parsed
    assert mock_client.models.generate_content.call_count == 2


def test_retries_on_rate_limit_with_retry_info_then_succeeds(
    gemini_configured: None, mock_client: MagicMock
) -> None:
    success = MagicMock(parsed=ConversationAnalysis())
    mock_client.models.generate_content.side_effect = [_rate_limit_error_with_retry_info(), success]

    result = generate_structured("prompt", ConversationAnalysis)

    assert result is success.parsed
    assert mock_client.models.generate_content.call_count == 2


def test_does_not_retry_sustained_quota_exhaustion(gemini_configured: None, mock_client: MagicMock) -> None:
    mock_client.models.generate_content.side_effect = _quota_exhausted_error()

    with pytest.raises(LLMOutputError):
        generate_structured("prompt", ConversationAnalysis)

    assert mock_client.models.generate_content.call_count == 1


def test_does_not_retry_permanent_client_error(gemini_configured: None, mock_client: MagicMock) -> None:
    mock_client.models.generate_content.side_effect = _bad_request_error()

    with pytest.raises(LLMOutputError):
        generate_structured("prompt", ConversationAnalysis)

    assert mock_client.models.generate_content.call_count == 1


def test_gives_up_after_max_retries_on_persistent_transient_error(
    gemini_configured: None, mock_client: MagicMock
) -> None:
    mock_client.models.generate_content.side_effect = _server_error()

    with pytest.raises(LLMOutputError):
        generate_structured("prompt", ConversationAnalysis)

    assert mock_client.models.generate_content.call_count == llm.MAX_RETRIES + 1


def test_retries_on_network_transport_error(gemini_configured: None, mock_client: MagicMock) -> None:
    success = MagicMock(parsed=ConversationAnalysis())
    mock_client.models.generate_content.side_effect = [httpx.ConnectError("connection refused"), success]

    result = generate_structured("prompt", ConversationAnalysis)

    assert result is success.parsed
    assert mock_client.models.generate_content.call_count == 2


def test_uses_gemini_model_from_settings_not_a_hardcoded_value(
    monkeypatch: pytest.MonkeyPatch, mock_client: MagicMock
) -> None:
    # Guards against the model name being hardcoded anywhere in app/llm.py -
    # whatever GEMINI_MODEL resolves to in Settings must be exactly what's
    # sent to the Gemini API, so backend/.env is the single source of truth.
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: Settings(gemini_api_key="test-key", gemini_model="some-configured-model"),
    )
    mock_client.models.generate_content.return_value = MagicMock(parsed=ConversationAnalysis())

    generate_structured("prompt", ConversationAnalysis)

    assert mock_client.models.generate_content.call_args.kwargs["model"] == "some-configured-model"
