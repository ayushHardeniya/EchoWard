import logging
import secrets

import httpx
from fastapi import APIRouter, HTTPException

from app import voice
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
    """Build an Agora AccessToken2 token carrying both RTC and RTM (Signaling)
    privileges for the given uid.

    RTM privileges are required for Agora's Conversational AI live-transcript
    feature (M6.1) — transcripts are delivered over Signaling, not the RTC
    data-stream channel, per Agora's Conversational AI docs. `uid` is passed
    as a string to `build_token_with_rtm`: this is a no-op for the RTC service
    (ServiceRtc normalizes to `str(uid)` internally either way — see
    app/vendor/agora_token2/AccessToken2.py — so this is not a behavior change
    for RTC-only callers), but required for the RTM service, which calls
    `.encode()` directly on whatever it's given and would raise on an int.

    One token, one Agora API call — no separate RTM token/fetch needed, since
    AccessToken2 embeds multiple service privileges in a single signed token.

    Raises ValueError on bad inputs.
    """
    rtc_role = Role_Publisher if role == "publisher" else Role_Subscriber
    token = RtcTokenBuilder.build_token_with_rtm(
        app_id, app_certificate, channel, str(uid), rtc_role, expire_seconds, expire_seconds
    )
    if not token:
        # The vendored builder returns "" instead of raising when app_id/app_certificate
        # aren't well-formed (Agora issues 32-char hex values for both).
        raise ValueError(
            "Failed to build Agora RTC/RTM token — AGORA_APP_ID/AGORA_APP_CERTIFICATE are "
            "missing or malformed (expected 32-character hex strings)."
        )
    return token


def build_agent_join_payload(
    settings: Settings, channel: str, agent_uid: int, agent_token: str
) -> dict:
    """Build the request body for the Conversational AI Engine `join` endpoint.

    ASR/LLM/TTS are supplied by the published Agent Builder pipeline
    (`pipeline_id`, top-level per the current API), not duplicated here.

    `advanced_features.enable_rtm` + `parameters.data_channel: "rtm"` (M6.1):
    required by Agora's Conversational AI docs for live-transcript delivery —
    without them the agent has no reason to publish transcripts anywhere, and
    the Console-published pipeline (ASR/LLM/TTS) is otherwise untouched by
    this. `agent_token` must already carry RTM privileges for this to work —
    see `build_rtc_token`, which is the only place this argument is produced.

    `parameters.transcript.enable: true` + `protocol_version: "v2"`: the
    `data_channel: "rtm"` setting alone only routes transcript delivery onto
    Signaling — it does not itself turn transcription on, and omitting the
    protocol version leaves the agent on whatever the API's default is. Both
    are required by the current Conversational AI join API to reliably get
    `user_transcription`/`assistant_transcription` RTM messages at all; their
    absence was the most likely cause of the previously-unreliable transcript
    delivery this milestone was fixing.
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
            "advanced_features": {"enable_rtm": True},
            "parameters": {
                "data_channel": "rtm",
                "transcript": {"enable": True, "protocol_version": "v2"},
            },
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
    if body.incident_id:
        # M6.2: register this incident<->agent link so app/voice.py knows which
        # live agent to speak through for this incident's coordination findings.
        voice.register_agent(body.incident_id, data["agent_id"])
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

    voice.unregister_agent(body.agent_id)
    logger.info("EchoWard agent stopped: agent_id=%s", body.agent_id)
    return StopAgentResponse(agent_id=body.agent_id, status="STOPPED")
