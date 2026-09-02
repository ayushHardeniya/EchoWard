"""Proactive voice interventions (M6.2): the layer between M4's coordination

findings / M5's action lifecycle and Agora's Conversational AI `/speak` REST
API - deciding WHEN EchoWard should say something out loud, generating a
short deterministic message, and calling Agora to say it.

Deliberately NOT a second reasoning system: it only reads the IncidentState
M2/M3/M4 already produce (never the raw transcript, never another LLM call)
and only ever *speaks* - the M5 prepare/confirm/execute boundary is completely
untouched by this module; it can describe a proposed action and ask for
confirmation, but nothing here ever calls a ToolAdapter.

Two eligibility layers keep this from being annoying, per the product
requirement ("do NOT speak after every transcript"):
  - duplicate suppression: a given dedup_key (a CoordinationFinding's, or a
    synthetic one for an action result) is spoken at most once ever, tracked
    in the voice_interventions table - once M4 resolves/replaces a finding, a
    genuinely new problem gets a fresh dedup_key and can be spoken again.
  - a cooldown between any two PROACTIVE interventions for the same incident
    (see COOLDOWN below), so a single conversation turn that produces several
    findings at once doesn't trigger a burst of speech. Human-initiated
    moments (an explicit status request, or the result of an action a human
    just confirmed) bypass the cooldown check - the human is owed an answer
    regardless of timing - but still update the cooldown clock so a proactive
    nudge doesn't immediately follow.

Agora's REST API is never allowed to affect incident processing: every public
function here fails safe (returns without raising) on a missing agent
registration, missing credentials, or an Agora request failure - the only
side effect of a failure is a log line and a `success=False` row in
voice_interventions (which still counts for dedup/cooldown, so a persistently
failing call doesn't retry-storm every turn).
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from app import incident_db
from app.config import get_settings
from app.db import get_connection
from app.incident_models import Action, ActionStatus, CoordinationFinding, CoordinationFindingType, IncidentState

logger = logging.getLogger("echoward.voice")

# How long EchoWard stays quiet after a PROACTIVE intervention before it will
# volunteer another one. Human-initiated moments (status request, action
# result) bypass this - see module docstring.
COOLDOWN = timedelta(seconds=45)

# Agora's /speak text limit is 512 bytes - stay comfortably under it so a
# multi-byte character near the edge can never push it over.
MAX_MESSAGE_CHARS = 480

# Finding types that warrant an unprompted spoken intervention, most urgent
# first - deliberately a small subset of everything M4 tracks. Conflicts,
# missing information, and an action awaiting confirmation are the only
# findings the product spec calls out as voice-worthy; everything else
# (unowned/stale actions, decision follow-up, hypothesis risk, unresolved
# questions) stays dashboard-only (see CoordinationPanel) so EchoWard doesn't
# nag about every coordination nit.
_ELIGIBLE_TYPES_BY_PRIORITY = (
    CoordinationFindingType.conflict,
    CoordinationFindingType.action_awaiting_confirmation,
    CoordinationFindingType.missing_information,
)

_STATUS_REQUEST_RE = re.compile(
    r"echoward.{0,40}\b(status|update|summary)\b|\b(status|update|summary)\b.{0,40}echoward", re.I
)


# --- Agent registry ------------------------------------------------------------
# Plain in-process dict, not a database table or external broker - mirrors
# app/realtime.py's connection manager (same "single process, no persistence
# needed across a restart" reasoning as that module). Populated by
# POST /api/agora/agent/start when the frontend supplies an incident_id (see
# app/agora.py), so this module knows which live Agora agent to speak through
# for a given incident's coordination findings.

_incident_agents: dict[str, str] = {}


def register_agent(incident_id: str, agent_id: str) -> None:
    _incident_agents[incident_id] = agent_id


def unregister_agent(agent_id: str) -> None:
    for incident_id, registered in list(_incident_agents.items()):
        if registered == agent_id:
            del _incident_agents[incident_id]


def get_agent_id(incident_id: str) -> str | None:
    return _incident_agents.get(incident_id)


# --- Agora /speak call -----------------------------------------------------------


def speak(agent_id: str, text: str, *, interruptable: bool = True) -> bool:
    """Call Agora's Conversational AI Engine `speak` REST endpoint (broadcasts

    a custom message through the agent's TTS). Returns whether the call
    succeeded - never raises, so a failure here can never break incident
    processing (see module docstring).
    """
    settings = get_settings()
    if not settings.agora_convo_ai_configured:
        logger.info("Skipping voice intervention: Agora Conversational AI is not configured")
        return False

    url = f"{settings.agora_convo_ai_base_url}/{settings.agora_app_id}/agents/{agent_id}/speak"
    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(
                url,
                json={"text": text[:MAX_MESSAGE_CHARS], "priority": "APPEND", "interruptable": interruptable},
                auth=httpx.BasicAuth(settings.agora_customer_id, settings.agora_customer_secret),
            )
            response.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        logger.error("Agora /speak rejected: status=%s body=%s", exc.response.status_code, exc.response.text)
        return False
    except httpx.RequestError as exc:
        logger.error("Agora /speak request failed: %s", exc)
        return False


# --- Message generation (deterministic, no LLM) -----------------------------------


def _finding_message(finding: CoordinationFinding, state: IncidentState) -> str:
    if finding.type == CoordinationFindingType.conflict:
        conflict = next((c for c in state.conflicts if c.id in finding.related_ids), None)
        topic = conflict.topic if conflict else finding.title
        return f"I'm seeing conflicting reports about {topic}. Can someone confirm which one is accurate?"
    if finding.type == CoordinationFindingType.action_awaiting_confirmation:
        action = next((a for a in state.actions if a.id in finding.related_ids), None)
        description = action.description if action else finding.title
        return f"I've prepared to {description}. Can I get confirmation to proceed?"
    if finding.type == CoordinationFindingType.missing_information:
        return f"Quick check — {finding.description}"
    return finding.description


def _action_result_message(action: Action) -> str:
    if action.status == ActionStatus.completed:
        return f"Done — {action.description} completed successfully."
    if action.status == ActionStatus.failed:
        detail = action.tool_result.message if action.tool_result else "no further details available"
        return f"Heads up — {action.description} did not complete. {detail}"
    return f"{action.description} is now {action.status.value}."


def is_status_request(text: str) -> bool:
    """Deterministic keyword check for "hey EchoWard, what's the status?" -

    no LLM call needed for this; the answer just reuses M4's existing
    situational-summary finding instead of generating new text.
    """
    return bool(_STATUS_REQUEST_RE.search(text))


def _status_summary_message(state: IncidentState) -> str | None:
    summary = next(
        (f for f in state.coordination_findings if f.type == CoordinationFindingType.situational_summary), None
    )
    return summary.description if summary else None


# --- Eligibility, cooldown, persistence --------------------------------------------


def _select_finding(incident_id: str, state: IncidentState) -> CoordinationFinding | None:
    for finding_type in _ELIGIBLE_TYPES_BY_PRIORITY:
        for finding in state.coordination_findings:
            if finding.type == finding_type and not incident_db.has_spoken(incident_id, finding.dedup_key):
                return finding
    return None


def _cooldown_active(incident_id: str) -> bool:
    last = incident_db.last_voice_intervention_at(incident_id)
    return last is not None and datetime.now(UTC) - last < COOLDOWN


def _speak_and_record(incident_id: str, agent_id: str, dedup_key: str, message: str) -> bool:
    success = speak(agent_id, message)
    with get_connection() as conn:
        incident_db.insert_voice_intervention(conn, incident_id, dedup_key, message, success, datetime.now(UTC))
    return success


def maybe_intervene(incident_id: str, state: IncidentState) -> bool:
    """The core M6.2 entry point for PROACTIVE interventions - call after any

    incident-state-changing operation (a conversation turn, an action being
    prepared) with the fresh state. Speaks at most once per call, respects
    the cooldown, and never raises. Returns whether EchoWard actually spoke
    (so callers know whether the dashboard needs a fresh broadcast).
    """
    try:
        agent_id = get_agent_id(incident_id)
        if agent_id is None:
            return False
        if _cooldown_active(incident_id):
            return False
        finding = _select_finding(incident_id, state)
        if finding is None:
            return False

        message = _finding_message(finding, state)
        success = _speak_and_record(incident_id, agent_id, finding.dedup_key, message)
        logger.info(
            "Voice intervention %s: incident=%s type=%s",
            "spoken" if success else "attempted (Agora call failed)",
            incident_id,
            finding.type.value,
        )
        return success
    except Exception:
        logger.exception("Voice intervention failed unexpectedly for incident=%s", incident_id)
        return False


def maybe_answer_status_request(incident_id: str, state: IncidentState, turn_text: str) -> bool:
    """A human directly asking EchoWard for status always gets an answer -

    bypasses the cooldown (but still updates it), and can be asked again
    later, so it doesn't go through the once-ever dedup_key suppression
    conflict/missing-info findings use.
    """
    try:
        if not is_status_request(turn_text):
            return False
        agent_id = get_agent_id(incident_id)
        if agent_id is None:
            return False
        message = _status_summary_message(state)
        if message is None:
            return False

        dedup_key = f"status_request:{uuid.uuid4().hex}"
        success = _speak_and_record(incident_id, agent_id, dedup_key, message)
        logger.info("Voice status request %s: incident=%s", "answered" if success else "attempted", incident_id)
        return success
    except Exception:
        logger.exception("Voice status-request handling failed unexpectedly for incident=%s", incident_id)
        return False


def announce_action_result(incident_id: str, action: Action) -> bool:
    """Called right after a human-confirmed action finishes executing (M5) -

    always announces (bypasses cooldown, like a status request), but is
    naturally idempotent per action via the action_result:{id} dedup_key,
    which matters because the caller (confirm_and_execute_action) can only
    ever run once per action anyway.
    """
    try:
        if action.status not in (ActionStatus.completed, ActionStatus.failed):
            return False
        dedup_key = f"action_result:{action.id}"
        if incident_db.has_spoken(incident_id, dedup_key):
            return False
        agent_id = get_agent_id(incident_id)
        if agent_id is None:
            return False

        message = _action_result_message(action)
        success = _speak_and_record(incident_id, agent_id, dedup_key, message)
        logger.info(
            "Voice action-result announcement %s: incident=%s action=%s",
            "spoken" if success else "attempted",
            incident_id,
            action.id,
        )
        return success
    except Exception:
        logger.exception("Voice action-result announcement failed unexpectedly for incident=%s", incident_id)
        return False
