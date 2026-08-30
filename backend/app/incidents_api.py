import logging

from fastapi import APIRouter, HTTPException

from app import incident_db
from app.incident_models import (
    ConversationTurnRequest,
    ConversationTurnResponse,
    CreateIncidentRequest,
    Incident,
    IncidentState,
)
from app.intelligence import IncidentNotFoundError, process_conversation_turn
from app.llm import LLMNotConfiguredError, LLMOutputError

logger = logging.getLogger("echoward.incidents")

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.post("", response_model=Incident, status_code=201)
def create_incident(body: CreateIncidentRequest) -> Incident:
    incident = incident_db.create_incident(body.title)
    logger.info("Created incident: id=%s title=%r", incident.id, incident.title)
    return incident


@router.get("/{incident_id}", response_model=Incident)
def get_incident(incident_id: str) -> Incident:
    incident = incident_db.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    return incident


@router.get("/{incident_id}/state", response_model=IncidentState)
def get_incident_state(incident_id: str) -> IncidentState:
    state = incident_db.get_incident_state(incident_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    return state


@router.post("/{incident_id}/conversation", response_model=ConversationTurnResponse)
def post_conversation_turn(incident_id: str, body: ConversationTurnRequest) -> ConversationTurnResponse:
    try:
        return process_conversation_turn(incident_id, body.speaker, body.text, body.occurred_at)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMNotConfiguredError as exc:
        logger.error("Conversation analysis skipped, LLM not configured: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMOutputError as exc:
        logger.error("Conversation analysis failed for incident=%s: %s", incident_id, exc)
        raise HTTPException(
            status_code=502, detail=f"Incident intelligence extraction failed: {exc}"
        ) from exc
