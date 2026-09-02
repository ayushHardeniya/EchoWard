import logging
import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger("echoward.llm")

# Small, bounded retry for genuinely transient failures only (see _is_transient
# below) - not a general resilience layer. Total added worst-case latency is
# ~2s, which stays safe for a live demo turn.
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = (0.5, 1.5)


class LLMOutputError(Exception):
    """The LLM call failed, or its response didn't match the requested schema."""


class LLMNotConfiguredError(LLMOutputError):
    """GEMINI_API_KEY is not set."""


def _has_retry_info(exc: genai_errors.APIError) -> bool:
    """True if Gemini's structured error body includes a RetryInfo detail.

    Google attaches RetryInfo (a short suggested retry delay) to short-lived
    rate-limit errors. A sustained daily quota/RPD exhaustion is also a 429
    RESOURCE_EXHAUSTED, but is a hard stop with no such detail - checking for
    this structured field (not the error message text) is what lets a 429 be
    retried without retrying quota exhaustion.
    """
    details = exc.details
    if not isinstance(details, dict):
        return False
    error_body = details.get("error", details)
    if not isinstance(error_body, dict):
        return False
    return any(
        isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo")
        for d in error_body.get("details", [])
    )


def _is_transient(exc: Exception) -> bool:
    """Genuinely transient failures only: 5xx server errors, rate-limit 429s

    that Google itself flags as retryable, and network-level connect/read
    failures. Everything else (bad API key, malformed request, sustained
    quota exhaustion) is treated as permanent, matching prior behavior.
    """
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.ClientError):
        return exc.code == 429 and _has_retry_info(exc)
    return isinstance(exc, httpx.TransportError | httpx.TimeoutException)


def generate_structured(prompt: str, schema: type[BaseModel]) -> BaseModel:
    """Call Gemini and return a validated instance of `schema`.

    Raises LLMNotConfiguredError / LLMOutputError instead of ever returning
    partial or fabricated data — callers must not persist anything on failure.
    Retries up to MAX_RETRIES times, but only on transient errors (see
    _is_transient) - a permanent failure (bad key, malformed request,
    sustained quota exhaustion) still fails on the first attempt.
    """
    settings = get_settings()
    if not settings.gemini_configured:
        raise LLMNotConfiguredError(
            "GEMINI_API_KEY is not configured (see backend/.env.example)."
        )

    client = genai.Client(api_key=settings.gemini_api_key)
    response = None
    for attempt in range(MAX_RETRIES + 1):
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
            break
        except Exception as exc:  # Gemini SDK raises various transport/API error types.
            if attempt < MAX_RETRIES and _is_transient(exc):
                delay = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "Gemini API call failed with a transient error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    delay,
                    exc,
                )
                time.sleep(delay)
                continue
            logger.error("Gemini API call failed: %s", exc)
            raise LLMOutputError(f"Gemini API call failed: {exc}") from exc

    parsed = getattr(response, "parsed", None)
    if parsed is None:
        logger.error("Gemini response did not match schema %s: %r", schema.__name__, response.text)
        raise LLMOutputError(
            f"Gemini response did not match the expected {schema.__name__} schema."
        )
    return parsed
