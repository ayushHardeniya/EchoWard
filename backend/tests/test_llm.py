import pytest

from app.incident_models import ConversationAnalysis
from app.llm import LLMNotConfiguredError, generate_structured


def test_generate_structured_requires_gemini_api_key() -> None:
    # No GEMINI_API_KEY in the automated test environment (see backend/.env.example);
    # this must fail before ever attempting a network call.
    with pytest.raises(LLMNotConfiguredError):
        generate_structured("irrelevant prompt", ConversationAnalysis)
