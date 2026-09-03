# 003 — Human-Gated Action Execution

## Context

EchoWard's coordination layer can identify that an operational action is needed (e.g. "roll back
the payment service"), and it has a live voice channel it could, in principle, use to just announce
that it's doing something. Letting an LLM-derived proposal directly trigger a real operational
change is an obvious and serious safety hazard — a misheard statement, a hallucinated detail, or an
ambiguous "yeah, let's do it" from a stressed engineer could otherwise translate directly into an
unwanted production change.

## Decision

EchoWard never autonomously executes an operational action. Every action follows an explicit
**Prepare → Confirm → Execute** lifecycle:

1. **Prepare** (`POST .../actions/{id}/prepare`) validates a proposed `action_type`/`target` against
   a fixed allowlist and moves the action to `awaiting_confirmation`. This step never executes
   anything.
2. **Confirm** (`POST .../actions/{id}/confirm`) is the *only* code path allowed to call a tool
   adapter, and only after independently re-checking — never trusting the prior check — that the
   action belongs to the given incident and is currently `awaiting_confirmation`.
3. **Execute** runs through a single allowlisted `ToolAdapter` implementation, which validates the
   same allowlist again (defense in depth) before doing anything.

The LLM is never given a path to the tool-execution module at all — it only ever produces an
action's initial free-text description during extraction. `action_type`/`target` are always
attached by an explicit, server-validated request, never inferred from conversational phrasing.

## Why

- Confirmation must be an explicit, auditable server action, not something inferred from natural
  language ("yeah", "let's do it", "sounds good") — conversational affirmation is inherently
  ambiguous and easy to misinterpret, especially under ASR noise.
- A fixed allowlist (one entry for this milestone: `rollback_payment_service` on
  `payment-service`) is a stronger safety boundary than a general plugin/tool framework would be at
  this stage — it's trivially auditable, and anything not on the list is rejected before it's ever
  shown as "ready to confirm."
- Re-validating at confirm time (not just at prepare time) and re-checking the action's persisted
  status means the API itself makes duplicate or out-of-order execution impossible — a
  `completed`/`failed` action returns `409` on a second confirm attempt rather than running again.
- The demo tool adapter is a deliberate sandbox (clearly `[DEMO]`-labeled results, no real network
  call) so the entire prepare/confirm/execute/result path is real and testable without needing a
  genuine remediation system for the hackathon.

## Consequences

- Only one action type is currently supported end-to-end; adding a new one means adding it to the
  allowlist and giving it a canonical description, not building a general framework.
- The current tool adapter never touches a real system — a genuine integration (an internal
  remediation API, PagerDuty, etc.) would implement the same `ToolAdapter` protocol and replace
  `default_adapter`, with no change needed above that module.
- "Human confirmation" in this implementation means "an explicit, valid server request was
  received" — there is no authentication/RBAC layer yet distinguishing *which* human confirmed it,
  which is an explicit, accepted limitation of this prototype (see
  [ARCHITECTURE.md](../../ARCHITECTURE.md)'s "Known Prototype Limitations").
