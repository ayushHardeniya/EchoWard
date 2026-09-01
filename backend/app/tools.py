"""Tool execution boundary (M5).

This is the ONLY place EchoWard is allowed to affect anything outside its own
database. Nothing upstream - the LLM, the coordination layer, the API route -
is allowed to call an adapter's `execute()` with untrusted input. The actual
call chain is:

    structured action proposal (POST .../prepare)
        -> validate_action() against ALLOWED_ACTIONS (app layer, this module)
        -> persisted as an Action in `awaiting_confirmation`
        -> human clicks Confirm & Execute (POST .../confirm)
        -> app/actions.py re-checks the action's own persisted fields, state,
           and incident ownership
        -> default_adapter.execute(...) - the only call that "does" anything

The LLM never sees this module and never calls `execute()` directly. It also
never gets to invent an action_type/target - those are only ever what a human
selected in the dashboard, then re-validated here before anything is stored.

`DemoIncidentToolAdapter` is a sandbox: it validates a target and returns a
structured result, exactly like an external incident-remediation API would,
but never touches a real system. It exists so the whole prepare/confirm/
execute/result path is real and testable end-to-end. A genuine integration
(PagerDuty, an internal remediation API, etc.) would implement the same
`ToolAdapter` protocol and replace `default_adapter` - nothing above this
module would need to change.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.incident_models import ToolResult


class ActionValidationError(Exception):
    """A proposed action_type/target/reason isn't valid - raised before

    anything is persisted or shown to a human as "ready to confirm".
    """


@dataclass(frozen=True)
class ToolAction:
    """A structured, already-validated request to an external tool - never

    arbitrary text, shell commands, or URLs. Only ever constructed by
    `validate_action()` below.
    """

    action_type: str
    target: str
    reason: str
    incident_id: str


class ToolAdapter(Protocol):
    def execute(self, action: ToolAction) -> ToolResult: ...


# --- Allowlist ------------------------------------------------------------------
# The complete set of action_type -> allowed targets EchoWard may ever send to
# an adapter. Anything else is rejected in `validate_action`, before it's ever
# persisted or surfaced as "ready to confirm" - this is the actual safety
# boundary, not the adapter. One entry for this milestone, per the "one strong
# integration" scope decision - not a general plugin framework.

ALLOWED_ACTIONS: dict[str, frozenset[str]] = {
    "rollback_payment_service": frozenset({"payment-service"}),
}


def validate_action(*, action_type: str, target: str, reason: str, incident_id: str) -> ToolAction:
    """Raises ActionValidationError if action_type/target isn't on the

    allowlist, or required fields are missing. Never trusts input from an LLM
    or the frontend without this check.
    """
    if action_type not in ALLOWED_ACTIONS:
        raise ActionValidationError(f"Unknown or disallowed action type: {action_type!r}")
    if target not in ALLOWED_ACTIONS[action_type]:
        raise ActionValidationError(f"Target {target!r} is not allowed for action type {action_type!r}")
    if not reason.strip():
        raise ActionValidationError("A reason is required to prepare an action.")
    if not incident_id:
        raise ActionValidationError("incident_id is required.")
    return ToolAction(action_type=action_type, target=target, reason=reason.strip(), incident_id=incident_id)


# --- Demo/sandbox adapter --------------------------------------------------------
# NOT a real integration. Simulates an external incident-remediation tool's API
# boundary (validate target, "execute", return a structured result) so the
# execution path and result plumbing are real and testable without touching a
# real system. All demo-generated data is explicitly labeled "[DEMO]".


class DemoIncidentToolAdapter:
    """Deterministic sandbox adapter. Never touches a real system.

    Supports a deterministic failure mode for tests/demos: a reason containing
    "force_failure" (case-insensitive) makes execution fail instead of
    succeed, so the failure path doesn't depend on random or flaky behavior.
    """

    def execute(self, action: ToolAction) -> ToolResult:
        now = datetime.now(UTC)

        if action.action_type not in ALLOWED_ACTIONS or action.target not in ALLOWED_ACTIONS[action.action_type]:
            # Unreachable via the normal flow (validate_action already checked
            # this before the action was ever persisted) - fail closed rather
            # than assume, in case this adapter is ever called some other way.
            return ToolResult(
                success=False,
                message=f"[DEMO] Rejected: {action.action_type!r} on {action.target!r} is not allowlisted.",
                executed_at=now,
            )

        if "force_failure" in action.reason.lower():
            return ToolResult(
                success=False,
                message="[DEMO] Rollback could not be completed.",
                executed_at=now,
                metadata={"target": action.target, "adapter": "demo"},
            )

        return ToolResult(
            success=True,
            message=(
                "[DEMO] Rollback completed successfully. Payment error rate is "
                "returning toward baseline."
            ),
            external_id=f"demo-rollback-{uuid.uuid4().hex[:8]}",
            executed_at=now,
            metadata={"target": action.target, "adapter": "demo"},
        )


default_adapter: ToolAdapter = DemoIncidentToolAdapter()
