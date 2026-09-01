import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app import actions, coordination, incident_db, tools
from app.incident_models import (
    ConfirmActionRequest,
    ConversationTurnRequest,
    ConversationTurnResponse,
    CreateIncidentRequest,
    Incident,
    IncidentState,
    PrepareActionRequest,
    UpdateIncidentStatusRequest,
)
from app.intelligence import IncidentNotFoundError, process_conversation_turn
from app.llm import LLMNotConfiguredError, LLMOutputError
from app.realtime import manager

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


@router.patch("/{incident_id}/status", response_model=IncidentState)
async def update_incident_status(incident_id: str, body: UpdateIncidentStatusRequest) -> IncidentState:
    if incident_db.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")

    incident_db.update_incident_status(incident_id, body.status)
    # A status change is always a meaningful coordination-relevant event (e.g.
    # moving to "resolved" doesn't itself resolve open findings, but it's still
    # worth a fresh checkpoint) - recompute alongside it, same as M3's broadcast.
    state = await run_in_threadpool(coordination.refresh_coordination_findings, incident_id)
    assert state is not None
    logger.info("Incident status updated: id=%s status=%s", incident_id, body.status.value)

    await manager.broadcast_state(incident_id, state)
    return state


@router.post("/{incident_id}/conversation", response_model=ConversationTurnResponse)
async def post_conversation_turn(incident_id: str, body: ConversationTurnRequest) -> ConversationTurnResponse:
    try:
        # process_conversation_turn is a blocking call (SQLite + a synchronous Gemini
        # request) - run it off the event loop so the WebSocket broadcast below (and
        # other connections' traffic) isn't stalled while it's in flight.
        result = await run_in_threadpool(
            process_conversation_turn, incident_id, body.speaker, body.text, body.occurred_at
        )
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

    # The DB write inside process_conversation_turn has already completed (and
    # committed) by this point - broadcasting can only ever follow a successful,
    # persisted write, never precede or race it.
    if not result.changes.is_empty:
        # M4: recompute coordination findings over the just-updated state before
        # broadcasting, so the dashboard and this response agree on what's shown.
        state = await run_in_threadpool(coordination.refresh_coordination_findings, incident_id)
        if state is not None:
            result = result.model_copy(update={"state": state})
            await manager.broadcast_state(incident_id, state)

    return result


@router.post("/{incident_id}/actions/{action_id}/prepare", response_model=IncidentState)
async def prepare_action(incident_id: str, action_id: str, body: PrepareActionRequest) -> IncidentState:
    """Validates a structured action_type/target against the tool allowlist

    and attaches it to the action as `awaiting_confirmation`. Never executes
    anything - see app/tools.py's ALLOWED_ACTIONS and app/actions.py.
    """
    try:
        state = await run_in_threadpool(
            actions.prepare_action, incident_id, action_id, body.action_type, body.target, body.reason
        )
    except (IncidentNotFoundError, actions.IncidentNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except actions.ActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except tools.ActionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except actions.ActionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    logger.info("Action prepared: incident=%s action=%s type=%s", incident_id, action_id, body.action_type)
    await manager.broadcast_state(incident_id, state)
    return state


@router.post("/{incident_id}/actions/{action_id}/confirm", response_model=IncidentState)
async def confirm_action(incident_id: str, action_id: str, body: ConfirmActionRequest) -> IncidentState:
    """The only endpoint that causes anything to execute - requires the action

    to already be `awaiting_confirmation` (i.e. already prepared and
    allowlist-validated). This is the explicit human confirmation step:
    nothing executes without a request to this endpoint, and it cannot be
    called successfully more than once for the same action.
    """
    try:
        state = await run_in_threadpool(
            actions.confirm_and_execute_action, incident_id, action_id, body.confirmed_by
        )
    except (IncidentNotFoundError, actions.IncidentNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except actions.ActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except actions.ActionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    logger.info("Action confirmed and executed: incident=%s action=%s", incident_id, action_id)
    await manager.broadcast_state(incident_id, state)
    return state


@router.websocket("/{incident_id}/stream")
async def incident_stream(websocket: WebSocket, incident_id: str) -> None:
    """Live incident-state feed. Sends the current state immediately on connect,
    then a full `incident.updated` message after every meaningful conversation
    turn or status change - see CLAUDE.md "Realtime architecture (M3)"."""
    state = incident_db.get_incident_state(incident_id)
    if state is None:
        await websocket.close(code=4404, reason=f"Incident not found: {incident_id}")
        return

    await manager.connect(incident_id, websocket)
    logger.info(
        "Incident stream connected: incident=%s connections=%d",
        incident_id,
        manager.connection_count(incident_id),
    )
    try:
        await websocket.send_json(
            {"type": "incident.updated", "incident_id": incident_id, "state": state.model_dump(mode="json")}
        )
        while True:
            # No client->server protocol; this just blocks until disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(incident_id, websocket)
        logger.info(
            "Incident stream disconnected: incident=%s connections=%d",
            incident_id,
            manager.connection_count(incident_id),
        )
