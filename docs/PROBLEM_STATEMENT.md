# EchoSphere PS41 — Voice AI Incident Commander

EchoWard was built for **PS41: Voice AI Incident Commander**, part of the EchoSphere: Agora
Conversational AI Hackathon 2026.

## Problem Statement

Technical incidents are coordinated live, by voice, across a group of engineers who are all
talking at once — reporting symptoms, proposing theories, making decisions, and calling out
actions — while nobody is dedicated to keeping a clean, shared record of what's actually known.
The challenge: build a voice-native AI Incident Commander that joins that live conversation,
listens continuously, and maintains a structured, shared picture of the incident in real time —
distinguishing confirmed facts from hypotheses, surfacing conflicting information instead of
silently picking a side, tracking decisions and their owners, and only ever taking action with
explicit human confirmation.

## The Problem

In practice, live incident calls suffer from the same handful of failure modes every time:

- **Fragmented live communication.** The only record of what was said is whoever's memory (or a
  scrollback in a chat window) — nothing structured survives the call.
- **Facts mixed with hypotheses.** "It's the database" and "someone saw a database error" get
  repeated back interchangeably within minutes, and by the time someone writes the postmortem,
  nobody can reconstruct which was which.
- **Conflicting information.** Two people report different things (a rollback happened / didn't
  happen; the error rate is up / back to normal) and the group either stalls arguing about whose
  version is right, or one report just gets quietly dropped.
- **Forgotten decisions.** A decision gets made verbally ("let's roll back the payment service")
  and no one turns it into a tracked action with an owner — it re-surfaces ten minutes later as if
  it never happened.
- **Unclear ownership.** Actions exist only as spoken commitments ("I'll take that") with no
  durable record of who owns what or whether it's stalled.
- **Manual coordination overhead.** Someone on the call has to simultaneously fight the fire *and*
  play scribe/coordinator — chasing owners, restating the current state, and re-explaining context
  to anyone who joins late.

## EchoWard's Approach

EchoWard sits in the room as a participant, not a bolt-on transcription tool. It listens over Agora
Conversational AI, extracts structured incident state from what's said, and reasons over that state
to proactively flag what needs attention — without ever fabricating certainty it doesn't have. The
one place it's allowed to affect anything outside its own database — executing an operational
action — is deliberately narrow and always requires an explicit human confirmation. See
[README.md](../README.md) for the full picture and [ARCHITECTURE.md](../ARCHITECTURE.md) for the
technical detail.

## Requirement → Implementation Mapping

| PS41 requirement | EchoWard implementation |
|---|---|
| Live voice room the AI can join and participate in | Agora Conversational AI agent joins the Agora RTC room via managed ASR/LLM/TTS ([docs/AGORA.md](AGORA.md)) |
| Continuous listening / transcript ingestion | Agora RTM live transcript stream → frontend → `POST /api/incidents/{id}/conversation` |
| Structured facts / hypotheses / decisions / actions | Incident intelligence pipeline (Gemini structured extraction) → `IncidentState` |
| Distinguish confirmed vs. uncertain information | Every `Fact` carries a `confidence` field; every `Hypothesis` a `status` (proposed/supported/refuted); no `root_cause` field anywhere |
| Detect and surface conflicting information | LLM-driven conflict detection plus a deterministic hypothesis-vs-fact contradiction backstop → `Conflict` records, resolved only by an explicit human action |
| Surface missing information / coordination gaps | Coordination intelligence: unowned/stale actions, decisions without follow-through, hypothesis-as-fact risk, unresolved questions, optional LLM-backed missing-information check |
| Incident timeline | Persisted `timeline_events`, newest-first on the dashboard |
| Spoken status updates from the AI | Proactive voice interventions (`app/voice.py`) over Agora's `/speak` API, plus an on-demand spoken status summary |
| Human confirmation before operational actions | Prepare → Confirm → Execute action lifecycle, allowlist-validated, human-gated at every step |
| Execution results fed back into incident state | `ToolResult` persisted on the `Action`, a timeline event added, coordination and the dashboard refreshed |
| Live, shared incident view for the team | WebSocket-driven dashboard (`IncidentDashboard`), one shared `IncidentState` per incident |

EchoWard does not integrate with Jira, Slack, or PagerDuty, and does not implement authentication
or a workflow engine — none of those are part of this implementation, and none are claimed above.
