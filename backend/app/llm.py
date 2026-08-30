import logging

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger("echoward.llm")


class LLMOutputError(Exception):
    """The LLM call failed, or its response didn't match the requested schema."""


class LLMNotConfiguredError(LLMOutputError):
    """GEMINI_API_KEY is not set."""


def generate_structured(prompt: str, schema: type[BaseModel]) -> BaseModel:
    """Call Gemini and return a validated instance of `schema`.

    Raises LLMNotConfiguredError / LLMOutputError instead of ever returning
    partial or fabricated data — callers must not persist anything on failure.
    """
    settings = get_settings()
    if not settings.gemini_configured:
        raise LLMNotConfiguredError(
            "GEMINI_API_KEY is not configured (see backend/.env.example)."
        )

    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
            ),
        )
    except Exception as exc:  # Gemini SDK raises various transport/API error types.
        logger.error("Gemini API call failed: %s", exc)
        raise LLMOutputError(f"Gemini API call failed: {exc}") from exc

    parsed = getattr(response, "parsed", None)
    if parsed is None:
        logger.error("Gemini response did not match schema %s: %r", schema.__name__, response.text)
        raise LLMOutputError(
            f"Gemini response did not match the expected {schema.__name__} schema."
        )
    return parsed
