import logging
import secrets

import httpx
from fastapi import APIRouter, HTTPException

from app.config import Settings, get_settings
from app.schemas import (
    RtcTokenRequest,
    RtcTokenResponse,
    StartAgentRequest,
    StartAgentResponse,
    StopAgentRequest,
    StopAgentResponse,
)
from app.vendor.agora_token2 import Role_Publisher, Role_Subscriber, RtcTokenBuilder

logger = logging.getLogger("echoward.agora")

router = APIRouter(prefix="/api/agora", tags=["agora"])

def build_rtc_token(
    app_id: str,
    app_certificate: str,
    channel: str,
    uid: int,
    role: str,
    expire_seconds: int,
) -> str:
    """Build an Agora AccessToken2 RTC token. Raises ValueError on bad inputs."""
    rtc_role = Role_Publisher if role == "publisher" else Role_Subscriber
    token = RtcTokenBuilder.build_token_with_uid(
        app_id, app_certificate, channel, uid, rtc_role, expire_seconds, expire_seconds
    )
    if not token:
        # The vendored builder returns "" instead of raising when app_id/app_certificate
        # aren't well-formed (Agora issues 32-char hex values for both).
        raise ValueError(
            "Failed to build Agora RTC token — AGORA_APP_ID/AGORA_APP_CERTIFICATE are "
            "missing or malformed (expected 32-character hex strings)."
        )
    return token


def build_agent_join_payload(
    settings: Settings, channel: str, agent_uid: int, agent_token: str
) -> dict:
    """Build the request body for the Conversational AI Engine `join` endpoint.

    ASR/LLM/TTS are supplied by the published Agent Builder pipeline
    (`pipeline_id`, top-level per the current API), not duplicated here.
    """
    return {
        "name": f"echoward-{channel}-{secrets.token_hex(4)}",
        "pipeline_id": settings.agora_agent_pipeline_id,
        "properties": {
            "channel": channel,
            "token": agent_token,
            "agent_rtc_uid": str(agent_uid),
            "remote_rtc_uids": ["*"],
            "idle_timeout": 120,
        },
    }


def _require_agora_configured(settings: Settings) -> None:
    if not settings.agora_configured:
        raise HTTPException(
            status_code=503,
            detail="Agora is not configured: set AGORA_APP_ID and AGORA_APP_CERTIFICATE "
            "in backend/.env (see .env.example).",
        )


def _require_convo_ai_configured(settings: Settings) -> None:
    _require_agora_configured(settings)
    if not settings.agora_convo_ai_configured:
        raise HTTPException(
            status_code=503,
            detail="Agora Conversational AI is not configured: set AGORA_CUSTOMER_ID, "
            "AGORA_CUSTOMER_SECRET (Console > Project > RESTful API), and "
            "AGORA_AGENT_PIPELINE_ID (Console > Agent Builder) in backend/.env.",
        )


@router.post("/token", response_model=RtcTokenResponse)
def create_rtc_token(body: RtcTokenRequest) -> RtcTokenResponse:
    settings = get_settings()
    _require_agora_configured(settings)

    try:
        token = build_rtc_token(
            settings.agora_app_id,
            settings.agora_app_certificate,
            body.channel,
            body.uid,
            body.role,
            settings.agora_token_expire_seconds,
        )
    except ValueError as exc:
        logger.error("RTC token generation failed for channel=%s: %s", body.channel, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("Issued RTC token: channel=%s uid=%s role=%s", body.channel, body.uid, body.role)
    return RtcTokenResponse(
        app_id=settings.agora_app_id,
        channel=body.channel,
        uid=body.uid,
        token=token,
        expires_in=settings.agora_token_expire_seconds,
    )


@router.post("/agent/start", response_model=StartAgentResponse)
async def start_agent(body: StartAgentRequest) -> StartAgentResponse:
    settings = get_settings()
    _require_convo_ai_configured(settings)

    agent_uid = settings.agora_agent_uid
    try:
        agent_token = build_rtc_token(
            settings.agora_app_id,
            settings.agora_app_certificate,
            body.channel,
            agent_uid,
            "publisher",
            settings.agora_token_expire_seconds,
        )
    except ValueError as exc:
        logger.error("Agent token generation failed for channel=%s: %s", body.channel, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    payload = build_agent_join_payload(settings, body.channel, agent_uid, agent_token)
    join_url = f"{settings.agora_convo_ai_base_url}/{settings.agora_app_id}/join"

    logger.info("Starting EchoWard agent: channel=%s agent_uid=%s", body.channel, agent_uid)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                join_url,
                json=payload,
                auth=httpx.BasicAuth(settings.agora_customer_id, settings.agora_customer_secret),
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Agora agent join rejected: status=%s body=%s",
            exc.response.status_code,
            exc.response.text,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Agora rejected the agent start request ({exc.response.status_code}): "
            f"{exc.response.text}",
        ) from exc
    except httpx.RequestError as exc:
        logger.error("Agora agent join request failed: %s", exc)
        raise HTTPException(
            status_code=502, detail=f"Could not reach Agora Conversational AI API: {exc}"
        ) from exc

    data = response.json()
    logger.info(
        "EchoWard agent started: agent_id=%s channel=%s", data.get("agent_id"), body.channel
    )
    return StartAgentResponse(
        agent_id=data["agent_id"],
        channel=body.channel,
        agent_uid=agent_uid,
        status=data.get("status", "RUNNING"),
    )


@router.post("/agent/stop", response_model=StopAgentResponse)
async def stop_agent(body: StopAgentRequest) -> StopAgentResponse:
    settings = get_settings()
    _require_convo_ai_configured(settings)

    leave_url = (
        f"{settings.agora_convo_ai_base_url}/{settings.agora_app_id}/agents/"
        f"{body.agent_id}/leave"
    )

    logger.info("Stopping EchoWard agent: agent_id=%s", body.agent_id)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                leave_url,
                auth=httpx.BasicAuth(settings.agora_customer_id, settings.agora_customer_secret),
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Agora agent leave rejected: status=%s body=%s",
            exc.response.status_code,
            exc.response.text,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Agora rejected the agent stop request ({exc.response.status_code}): "
            f"{exc.response.text}",
        ) from exc
    except httpx.RequestError as exc:
        logger.error("Agora agent leave request failed: %s", exc)
        raise HTTPException(
            status_code=502, detail=f"Could not reach Agora Conversational AI API: {exc}"
        ) from exc

    logger.info("EchoWard agent stopped: agent_id=%s", body.agent_id)
    return StopAgentResponse(agent_id=body.agent_id, status="STOPPED")
