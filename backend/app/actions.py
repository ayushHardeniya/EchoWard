"""Action execution lifecycle (M5): prepare -> human confirmation -> execute.

Mirrors app/intelligence.py's split for M2: this module owns the business
logic (validation, state transitions, timeline events, calling the tool
adapter), and is realtime-unaware - same as `process_conversation_turn` and
`coordination.refresh_coordination_findings`, the actual WebSocket broadcast
is a pure API-layer concern in app/incidents_api.py.

The critical product rule enforced here: EchoWard never autonomously executes
an action. `prepare_action()` only ever produces `awaiting_confirmation` -
never anything closer to execution - and `confirm_and_execute_action()` is the
only function that calls a ToolAdapter, and only after independently
re-checking (not trusting a prior check) that:
  - the action belongs to the given incident
  - the action is actually in `awaiting_confirmation`
(so a completed/failed/already-executing action, or one from another
incident, can never be executed by calling this twice or out of order).
"""

import logging
from datetime import UTC, datetime

from app import coordination, incident_db, tools
from app.db import get_connection
from app.incident_models import ActionStatus, IncidentState

logger = logging.getLogger("echoward.actions")


class IncidentNotFoundError(Exception):
    def __init__(self, incident_id: str) -> None:
        super().__init__(f"Incident not found: {incident_id}")
        self.incident_id = incident_id


class ActionNotFoundError(Exception):
    def __init__(self, incident_id: str, action_id: str) -> None:
        super().__init__(f"Action not found on incident {incident_id}: {action_id}")
        self.incident_id = incident_id
        self.action_id = action_id


class ActionStateError(Exception):
    """The action exists but isn't in a state that allows the requested

    transition (e.g. confirming something not awaiting confirmation, or
    preparing something already confirmed/executing/completed/failed).
    """


_PREPARABLE_STATUSES = (ActionStatus.pending, ActionStatus.in_progress, ActionStatus.awaiting_confirmation)


def prepare_action(incident_id: str, action_id: str, action_type: str, target: str, reason: str) -> IncidentState:
    """Validate a structured tool-action proposal and attach it to an action,

    moving it to `awaiting_confirmation`. Never executes anything - this is
    the "prepare" half of "prepare != execute".

    Raises IncidentNotFoundError, ActionNotFoundError, ActionStateError, or
    tools.ActionValidationError (if action_type/target isn't allowlisted).
    """
    if incident_db.get_incident(incident_id) is None:
        raise IncidentNotFoundError(incident_id)

    action = incident_db.get_action(incident_id, action_id)
    if action is None:
        raise ActionNotFoundError(incident_id, action_id)
    if action.status not in _PREPARABLE_STATUSES:
        raise ActionStateError(
            f"Action {action_id} is {action.status.value} and can no longer be prepared "
            "(it has already been confirmed, executed, or resolved)."
        )

    # Raises tools.ActionValidationError if not allowlisted - nothing is
    # written until this passes.
    tools.validate_action(action_type=action_type, target=target, reason=reason, incident_id=incident_id)

    when = datetime.now(UTC)
    with get_connection() as conn:
        incident_db.prepare_action(conn, action_id, action_type, target, reason, when)
        incident_db.insert_timeline_event(
            conn,
            incident_id,
            f'Action ready for execution, awaiting human confirmation: "{action.description}" '
            f"({action_type} on {target}).",
            "system",
            when,
        )

    state = coordination.refresh_coordination_findings(incident_id)
    assert state is not None  # existence was checked above
    return state


def confirm_and_execute_action(
    incident_id: str, action_id: str, confirmed_by: str | None = None
) -> IncidentState:
    """The only function allowed to call a ToolAdapter. Requires the action to

    already be `awaiting_confirmation` - there is no path from `pending`
    straight to execution, and no way to re-execute a `completed`/`failed`/
    `executing` action by calling this again.

    Raises IncidentNotFoundError, ActionNotFoundError, or ActionStateError.
    Adapter failures are captured as a `failed` ToolResult, never raised -
    the incident stays open and visible, nothing is retried automatically.
    """
    if incident_db.get_incident(incident_id) is None:
        raise IncidentNotFoundError(incident_id)

    action = incident_db.get_action(incident_id, action_id)
    if action is None:
        raise ActionNotFoundError(incident_id, action_id)
    if action.status != ActionStatus.awaiting_confirmation:
        raise ActionStateError(
            f"Action {action_id} is {action.status.value}, not awaiting_confirmation - "
            "it cannot be confirmed/executed right now."
        )
    assert action.action_type is not None and action.target is not None and action.reason is not None

    who = confirmed_by or "Operator"

    with get_connection() as conn:
        incident_db.update_action_status(conn, action_id, ActionStatus.confirmed, datetime.now(UTC))
        incident_db.insert_timeline_event(
            conn, incident_id, f'Action confirmed by {who}: "{action.description}".', who, datetime.now(UTC)
        )
        incident_db.update_action_status(conn, action_id, ActionStatus.executing, datetime.now(UTC))
        incident_db.insert_timeline_event(
            conn, incident_id, f'Action execution started: "{action.description}".', "system", datetime.now(UTC)
        )

    # The tool call itself is outside the transaction above - a real adapter
    # may do network I/O, and we never want to hold a SQLite write lock across
    # that. validate_action() re-runs here too: defense in depth, since this
    # is the one place that actually calls execute().
    tool_action = tools.validate_action(
        action_type=action.action_type, target=action.target, reason=action.reason, incident_id=incident_id
    )
    logger.info("Executing action: incident=%s action=%s type=%s", incident_id, action_id, action.action_type)
    result = tools.default_adapter.execute(tool_action)

    final_status = ActionStatus.completed if result.success else ActionStatus.failed
    when = datetime.now(UTC)
    with get_connection() as conn:
        incident_db.set_action_result(conn, action_id, final_status, result, when)
        event = "Action completed" if result.success else "Action failed"
        incident_db.insert_timeline_event(conn, incident_id, f"{event}: {result.message}", "system", when)

    logger.info(
        "Action execution finished: incident=%s action=%s success=%s", incident_id, action_id, result.success
    )

    state = coordination.refresh_coordination_findings(incident_id)
    assert state is not None  # existence was checked above
    return state
