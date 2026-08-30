"""Adapter from Agora Conversational AI transcript events into the M2 intelligence
pipeline.

STATUS: best-effort / UNVERIFIED against a live Agora account (see CLAUDE.md and
README "Known limitations"). Agora's Conversational AI Engine can deliver
conversation transcripts to a backend via webhook notifications (productId 17),
documented at https://docs.agora.io/en/conversational-ai/develop/event-types —
eventType 103 ("agent history") carries the full session transcript as
`payload.contents[]`, each item `{"role": "user"|"assistant", "content": "..."}`.

That documented shape is what this module parses. What could NOT be verified
here (no live Agora project available): whether eventType 103 only fires after
a session ends (as the docs summary suggests) vs. incrementally, the exact
webhook registration/auth mechanism, and whether per-speaker attribution beyond
"user"/"assistant" is available anywhere in the payload. Until verified, every
"user" turn is attributed to the generic speaker "Participant" rather than a
named individual — see `KNOWN_LIMITATION` below.

Remaining wiring step for a real deployment: register this endpoint's URL
(`POST /api/agora/webhook/{incident_id}`) as the project's Conversational AI
webhook target in the Agora Console, and re-verify the payload shape against a
real event before relying on it. Nothing else in the intelligence pipeline
needs to change to support that — `process_conversation_turn` is the same
function the explicit `/conversation` endpoint uses.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.intelligence import IncidentNotFoundError, process_conversation_turn
from app.llm import LLMOutputError

logger = logging.getLogger("echoward.agora_events")

router = APIRouter(prefix="/api/agora", tags=["agora-events"])

KNOWN_LIMITATION = (
    "Agora's documented transcript payload does not carry a per-participant "
    "speaker id, only role='user'|'assistant'. Human turns are attributed to "
    "the generic speaker 'Participant' until a richer signal is available."
)

AGENT_HISTORY_EVENT_TYPE = 103
TURNS_FINISHED_EVENT_TYPE = 112


class AgoraTranscriptContent(BaseModel):
    role: str
    content: str


class AgoraAgentHistoryPayload(BaseModel):
    agent_id: str | None = None
    channel: str | None = None
    contents: list[AgoraTranscriptContent] = []


class AgoraWebhookEnvelope(BaseModel):
    noticeId: str | None = None
    productId: int | None = None
    eventType: int | None = None
    payload: dict = {}


class WebhookIngestResult(BaseModel):
    incident_id: str
    event_type: int | None
    turns_ingested: int
    turns_skipped: int
    note: str = KNOWN_LIMITATION


def extract_user_turns(envelope: AgoraWebhookEnvelope) -> list[str]:
    """Best-effort extraction of human speech from a transcript-bearing event."""
    if envelope.eventType not in (AGENT_HISTORY_EVENT_TYPE, TURNS_FINISHED_EVENT_TYPE):
        return []
    try:
        history = AgoraAgentHistoryPayload.model_validate(envelope.payload)
    except Exception as exc:
        logger.warning("Could not parse Agora transcript payload: %s", exc)
        return []
    return [item.content for item in history.contents if item.role == "user" and item.content.strip()]


@router.post("/webhook/{incident_id}", response_model=WebhookIngestResult)
async def agora_webhook(incident_id: str, body: dict) -> WebhookIngestResult:
    """Receive an Agora Conversational AI webhook notification and feed any human
    speech in it through the same intelligence pipeline as POST .../conversation.

    See module docstring: unverified against a live Agora account. Always
    returns 200 (never raises on a malformed/unexpected Agora payload) so Agora
    doesn't retry-storm us over an unrecognized event; an unknown incident_id
    still surfaces as a normal error since that reflects a webhook misconfigured
    by us, not a payload problem from Agora.
    """
    try:
        envelope = AgoraWebhookEnvelope.model_validate(body)
    except Exception as exc:
        logger.warning("Ignoring unparseable Agora webhook payload: %s", exc)
        return WebhookIngestResult(incident_id=incident_id, event_type=None, turns_ingested=0, turns_skipped=0)

    turns = extract_user_turns(envelope)
    ingested = 0
    skipped = 0
    for text in turns:
        try:
            process_conversation_turn(incident_id, speaker="Participant", text=text)
            ingested += 1
        except IncidentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LLMOutputError as exc:
            logger.error("Skipping Agora transcript turn for incident=%s: %s", incident_id, exc)
            skipped += 1

    logger.info(
        "Agora webhook processed: incident=%s event_type=%s ingested=%d skipped=%d",
        incident_id,
        envelope.eventType,
        ingested,
        skipped,
    )
    return WebhookIngestResult(
        incident_id=incident_id, event_type=envelope.eventType, turns_ingested=ingested, turns_skipped=skipped
    )
