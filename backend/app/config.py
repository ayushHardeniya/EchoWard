from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    cors_origins: str = "http://localhost:3000"
    database_path: str = "data/echoward.db"

    gemini_api_key: str = ""

    # Agora App ID/Certificate: from the Agora Console project (console.agora.io).
    # The certificate must never be sent to the browser.
    agora_app_id: str = ""
    agora_app_certificate: str = ""

    # Agora RESTful API credentials (Console > Project > RESTful API), used for
    # Basic auth when calling the Conversational AI Engine REST API. Distinct
    # from the App ID/Certificate above.
    agora_customer_id: str = ""
    agora_customer_secret: str = ""

    agora_convo_ai_base_url: str = "https://api.agora.io/api/conversational-ai-agent/v2/projects"
    agora_token_expire_seconds: int = 3600

    # Fixed RTC uid the EchoWard agent joins each channel as.
    agora_agent_uid: int = 9999

    # Agora-managed ASR/LLM/TTS vendor+model preset (credential_mode="managed"),
    # so no separate vendor API keys (Gemini, Deepgram, etc.) are required for
    # the voice MVP. See CLAUDE.md for details and how to swap to a custom LLM.
    agora_asr_vendor: str = "deepgram"
    agora_asr_model: str = "nova-3"
    agora_asr_language: str = "en-US"
    agora_llm_vendor: str = "openai"
    agora_llm_model: str = "gpt-4o-mini"
    agora_tts_vendor: str = "minimax"
    agora_tts_model: str = "speech-2.6-turbo"
    agora_tts_voice_id: str = "English_captivating_female1"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def agora_configured(self) -> bool:
        return bool(self.agora_app_id and self.agora_app_certificate)

    @property
    def agora_convo_ai_configured(self) -> bool:
        return self.agora_configured and bool(self.agora_customer_id and self.agora_customer_secret)

    @property
    def database_full_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
