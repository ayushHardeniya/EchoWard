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

    # Used by the incident-intelligence extraction service (app/llm.py), NOT by the
    # Agora voice agent (which uses Agora-managed LLM credentials, see below).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"

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

    # Published Agent Builder pipeline id (Console > Agent Builder), passed as a
    # top-level `pipeline_id` on the Conversational AI Engine `join` call. The
    # published pipeline is the source of truth for ASR/LLM/TTS — we no longer
    # send a separate vendor/model config in the join payload.
    agora_agent_pipeline_id: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def agora_configured(self) -> bool:
        return bool(self.agora_app_id and self.agora_app_certificate)

    @property
    def agora_convo_ai_configured(self) -> bool:
        return (
            self.agora_configured
            and bool(self.agora_customer_id and self.agora_customer_secret)
            and bool(self.agora_agent_pipeline_id)
        )

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def database_full_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
