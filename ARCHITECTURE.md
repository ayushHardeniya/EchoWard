# EchoWard Architecture

This document describes the architecture of EchoWard as shipped for the EchoSphere: Agora
Conversational AI Hackathon 2026. It describes the final system, not its milestone-by-milestone
build order — see `git log` if that history is of interest.

![EchoWard Architecture](docs/assets/product-architecture.png)

## System Overview

EchoWard is a voice-native AI incident commander. It joins a live technical incident room, turns
the conversation happening there into structured incident state, identifies coordination gaps the
team should pay attention to, can speak up proactively when something needs attention, and can
safely execute a small set of pre-approved operational actions once a human explicitly confirms
them.

The core product principle:

> **EchoWard does not pretend to know what is true.**

Every extracted item carries its epistemic status — a statement someone reported is a `Fact`
(always `confidence = "reported"`, never independently verified); a suspected explanation is a
`Hypothesis` (proposed / supported / refuted, never silently promoted); a contradiction between two
statements is surfaced as a `Conflict` for a human to resolve, never adjudicated by the system.
There is no `root_cause` field anywhere in the data model — a suspected root cause is just a
`Hypothesis` like any other.

The system is organized around four concerns, each covered in its own section below: the
**real-time voice layer** (Agora), **incident intelligence** (conversation → structured state),
**coordination intelligence** (state → what needs attention), and the **human-controlled action
path** (proposal → confirmation → execution).

## High-Level Architecture

```
Participants
    │  mic/speaker              ▲
    ▼                           │ proactive /speak
Agora RTC room ──▶ Agora Conversational AI Engine (managed ASR/LLM/TTS)
                           │  agent joins the room; RTM transcript stream
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│ EchoWard backend (FastAPI, one process)                               │
│                                                                        │
│  app/agora.py, app/agora_events.py — RTC token + agent lifecycle      │
│                           │                                           │
│                           ▼                                           │
│  Incident Intelligence (app/intelligence.py, app/llm.py)              │
│    conversation turn → Gemini structured extraction →                 │
│    facts / hypotheses / decisions / actions / timeline                │
│                           │                                           │
│                           ▼                                           │
│  Coordination Intelligence (app/coordination.py) ─────────────────┐   │
│    incident state → conflicts / unowned+stale actions /           │   │
│    decision follow-through / hypothesis-as-fact risk /            │   │
│    unresolved risks / situational summary                         │   │
│                           │                       (proactive voice)│   │
│                           ▼                     app/voice.py ──────┘   │
│  Human-Controlled Action Path (app/tools.py, app/actions.py)          │
│    prepare → human confirmation → validate → execute → result         │
│                           │                                           │
│                           ▼                                           │
│              SQLite (app/incident_db.py)                              │
│                           │                                           │
│                           ▼                                           │
│         WebSocket fanout (app/realtime.py)                            │
└──────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼
                  Incident Dashboard (Next.js)
```

All backend concerns — voice/agent lifecycle, incident intelligence, coordination intelligence,
proactive voice, action execution — run in one FastAPI process and share one SQLite file. There is
no message queue, no separate service boundary, and no distributed state between them; they are
Python modules calling each other directly, sequenced by the API layer in `app/incidents_api.py`.

## Real-Time Voice Layer

Agora owns real-time voice transport end-to-end; EchoWard's backend only mediates *access* to it.

- **Agora RTC** — the browser joins an Agora RTC channel with the Agora RTC Web SDK
  (`agora-rtc-sdk-ng`), publishing its microphone track and subscribing to other participants'
  audio (`frontend/src/lib/useAgoraRoom.ts`). The backend's only role is minting short-lived RTC
  tokens (`POST /api/agora/token`, `app/agora.py`) — it never sees or proxies audio.
- **Conversational AI Agent** — `POST /api/agora/agent/start` mints a token for a fixed agent uid
  and calls Agora's Conversational AI Engine REST API (`.../join`) with a `pipeline_id` (published
  in the Agora Console's Agent Builder) plus the RTC channel/token/uid. The agent joins the same RTC
  channel as any other participant, listens to everyone (`remote_rtc_uids: ["*"]`), and speaks back
  into the room. `POST /api/agora/agent/stop` removes it.
- **Managed ASR / LLM / TTS** — the speech-to-text, conversational reasoning, and text-to-speech
  that make EchoWard audible in the room are entirely Agora's managed pipeline
  (`credential_mode: "managed"`), configured once in the Agora Console (Deepgram ASR → an OpenAI
  model → MiniMax TTS in this project), not in application code. **This LLM is not Gemini** —
  Gemini is used elsewhere, for incident and coordination intelligence, a separate integration from
  the voice loop (see [docs/AGORA.md](docs/AGORA.md)).
- **RTM transcript path** — live transcript delivery is over Agora's Signaling (RTM) channel, not
  the RTC data channel: the agent join request sets `advanced_features.enable_rtm`,
  `parameters.data_channel: "rtm"`, and `parameters.transcript: {enable: true, protocol_version:
  "v2"}` (`app/agora.py`), and the frontend subscribes via `agora-rtm-sdk`
  (`frontend/src/lib/liveTranscript.ts`, `useAgoraRoom.ts`). Finalized human segments are
  deduplicated client-side and submitted to `POST /api/incidents/{id}/conversation`.
- **Proactive `/speak` path** — the backend can make the agent say something unprompted through
  Agora's `.../agents/{agentId}/speak` REST endpoint (`app/voice.py`), used to surface conflicts,
  actions awaiting confirmation, and missing-information gaps out loud (see "Coordination
  Intelligence" below).
- **Interruption handling** is Agora's built-in VAD/turn-taking behavior; the app implements no
  custom duplex/interrupt logic.

## EchoWard Intelligence Layer

One conversation turn — a `speaker` and `text` — flows through a strictly sequenced pipeline
(`app/intelligence.py`):

```
Conversation turn (speaker, text)
        │
        ▼
analyze_conversation()  — Gemini structured-output call (app/llm.py)
        │                  no side effects; can fail without touching the DB
        ▼
ConversationAnalysis    — validated structured proposal (facts, hypotheses,
                           decisions, actions, questions, conflicts)
        │
        ▼
apply_analysis()        — dedup against existing open items, then SQLite writes,
                           one transaction
        │
        ▼
facts / hypotheses / decisions / actions / timeline / unresolved questions / conflicts
```

The extraction call (pure, no writes) always completes before any database write starts — if
extraction fails or returns unparseable output, nothing is persisted. This makes "never corrupt
incident state on malformed AI output" true by construction.

Deduplication is deterministic, not LLM-driven: a new action/conflict is matched against existing
open ones for the same incident using `difflib.SequenceMatcher` on the description/topic text.
Conflict *detection* (recognizing that a new statement contradicts something already known) is
LLM-driven, using recent facts/hypotheses/decisions/open actions as prompt context — plus a
deterministic backstop that directly compares a new hypothesis against existing facts, so a
contradiction can't be missed purely because the LLM didn't flag it. The LLM never decides who is
right, only that a contradiction exists.

The Gemini integration (`app/llm.py`) calls `google-genai` with `response_schema` set to the
Pydantic `ConversationAnalysis` model directly — no hand-rolled JSON parsing — and retries
transient failures. If `GEMINI_API_KEY` isn't configured, the conversation endpoint returns a clear
`503`; if the model call fails after retries or returns non-conformant output, it returns `502` and
writes nothing.

`IncidentState` (`app/incident_models.py`) is the full, structured snapshot of one incident —
`incident`, `facts[]`, `hypotheses[]`, `decisions[]`, `actions[]`, `timeline[]`,
`unresolved_questions[]`, `conflicts[]`, `coordination_findings[]` — and is the single object every
other layer reads from and writes into. `GET /api/incidents/{id}/state` and every
`WS /api/incidents/{id}/stream` message return exactly this shape, so the frontend has one incident
model, not a REST shape and a separate WebSocket shape.

Conflicts are resolved only by an explicit human action: `POST
/api/incidents/{id}/conflicts/{conflict_id}/resolve` is the sole path that moves a `Conflict` out of
`unresolved` — EchoWard/Gemini never picks a side.

## Coordination Intelligence

Coordination intelligence (`app/coordination.py`) reads the current `IncidentState` — never the raw
transcript — and produces `CoordinationFinding` rows: what the team needs to pay attention to
*next*, distinct from *what happened*. It never executes anything and never resolves anything
itself.

Seven checks, six of them deterministic (no LLM call, always available):

| Check | Basis |
|---|---|
| Conflicts | mirrors existing unresolved `Conflict` records |
| Unowned actions | any pending/in-progress `Action` with `owner is None` |
| Stale actions | an owned action whose `updated_at == created_at` while something else in the incident moved on since |
| Decision follow-through | word-overlap match between a `Decision` and `Action` descriptions; flags "no tracked follow-up" or "no assigned owner" |
| Hypothesis-as-fact risk | a still-`proposed` `Hypothesis` whose wording overlaps a later decision/action — flags the risk without ever promoting the hypothesis |
| Unresolved risks | mirrors open `UnresolvedQuestion`s |
| Situational summary | one always-present, pure count-based checkpoint per incident |

The one optional, LLM-backed check is **missing/incomplete information** (e.g. "impact is reported
but affected regions aren't confirmed") — inherently semantic, so it calls Gemini
(`generate_structured(..., CoordinationGapAnalysis)`) with a compact structured-state summary, never
the transcript. If `GEMINI_API_KEY` isn't set or the call fails, it fails safe to an empty list —
every other check keeps working with zero external dependencies.

Findings are reconciled, not append-only: each proposed finding carries a `dedup_key` (e.g.
`unowned_action:{id}`), upserted so a repeated observation updates in place, and any previously open
finding whose key isn't proposed on a given refresh flips to `resolved` (e.g. assigning an owner
resolves `unowned_action:{id}` automatically). `IncidentState.coordination_findings` only ever
returns `status = "open"` findings, most severe first.

**Proactive voice** (`app/voice.py`) sits directly on top of this layer: after a state-changing
operation, a small subset of finding types warrants an unprompted spoken intervention — conflicts,
an action awaiting confirmation, and missing-information gaps. Everything else (unowned/stale
actions, decision follow-up, hypothesis risk, open questions) stays dashboard-only, so EchoWard
doesn't narrate every coordination nit. Two guards keep it from being noisy: a finding's `dedup_key`
is spoken at most once ever, and a cooldown separates any two proactive interventions for the same
incident. A human directly asking "EchoWard, what's the status?" always gets an answer (the
situational-summary finding, spoken on request) and bypasses the cooldown; so does announcing the
result of an action a human just confirmed. Message text is generated deterministically from the
finding/action data — no second LLM call for what to say.

Coordination refreshes on the same triggers the realtime layer broadcasts on — a conversation turn
that actually changed state, a status change, and (see below) an action being prepared or
confirmed — so the dashboard, the API response, and the broadcast never disagree about findings for
the same event.

## Human-Controlled Action Path

The safety-critical part of the system: EchoWard can propose an operational action, but only a
human confirmation and a fixed allowlist stand between a proposal and anything actually happening.

```
Structured action proposal
        │  (action_type, target, reason — never free text/shell/URL/code)
        ▼
Prepare               app/tools.py: validate_action() against a fixed
        │              allowlist (ALLOWED_ACTIONS) — never executes
        ▼
Human Confirmation    explicit POST .../confirm from the dashboard;
        │              server re-checks persisted state, not client input
        ▼
Validate (again)      defense in depth — re-run before the adapter is called
        ▼
Execute               app/tools.py: DemoIncidentToolAdapter — deterministic,
        │              sandboxed, no real system touched
        ▼
Result                ToolResult(success, message, external_id, metadata)
        │
        ▼
Incident State        Action.status + tool_result updated, timeline event
                       added, coordination refreshed, broadcast over WebSocket
```

**Action lifecycle**, exactly as implemented (`ActionStatus`):

```
pending → awaiting_confirmation → confirmed → executing → completed | failed
```

`POST /api/incidents/{id}/actions/{action_id}/prepare` validates a proposed `action_type`/`target`
against the allowlist and moves it to `awaiting_confirmation` — it never executes anything.
`POST /api/incidents/{id}/actions/{action_id}/confirm` is the *only* code path that calls the tool
adapter, and only after independently re-checking (never trusting a prior check) that the action
belongs to the given incident and is currently `awaiting_confirmation` — a completed/failed action
returns `409` on a second confirm attempt instead of running again.

**Allowed action(s):** one entry — `rollback_payment_service` on target `payment-service` —
deliberately a single strong integration rather than a general plugin framework. Any other
`action_type`/`target` combination is rejected before anything is persisted or shown as "ready to
confirm."

**Tool adapter:** `DemoIncidentToolAdapter` is a **sandbox/demo implementation, not a real
production remediation system**. It validates its input against the same allowlist again
(fail-closed if ever reached out of band), never makes a network call or touches a real service,
and returns a deterministic, clearly `[DEMO]`-labeled result — a reason containing `"force_failure"`
makes it fail, anything else succeeds. A genuine integration (an internal remediation API,
PagerDuty, etc.) would implement the same `ToolAdapter` protocol and replace `default_adapter`;
nothing above this module would need to change.

**Safety boundary:** the LLM is never given the ability to call the adapter, invent an
`action_type`/`target`, or reach `app/tools.py`/`app/actions.py` at all — it only ever produces the
initial `pending` action description during incident intelligence extraction. Everything from
"prepare" onward is driven by explicit requests validated server-side, not by conversational
language like "yeah" or "let's do it."

## Realtime Dashboard

`WS /api/incidents/{incident_id}/stream` (`app/incidents_api.py`, fanout in `app/realtime.py`):

- On connect: 404s (via a WebSocket close with code `4404`) if the incident doesn't exist,
  otherwise accepts and immediately sends the current full `IncidentState` as an `incident.updated`
  message.
- After that, the same connection receives one more `incident.updated` message — always the
  *complete* state, never a diff — per meaningful change, for as long as it stays open.
- **Broadcast only follows a completed, committed write.** A conversation turn's DB write, a status
  change, a conflict resolution, or an action prepare/confirm all fully complete (and trigger a
  coordination refresh) *before* the broadcast is even considered. A no-op conversation turn
  (nothing extracted) does not broadcast at all.
- Connections are a plain in-process `dict[incident_id, set[WebSocket]]`
  (`IncidentConnectionManager`) — no Redis or external pub/sub, correct because there is exactly one
  backend process and SQLite is already the shared source of truth. A backend restart drops all
  connections.
- **Frontend reconnect behavior** (`frontend/src/lib/useIncidentStream.ts`): on an unexpected close,
  the hook reconnects with exponential backoff (1s → 2s → 4s → … capped at 10s, reset on success),
  shown as a small connection-state dot in the dashboard header. On every successful (re)connect it
  also issues a REST `GET .../state` refetch as belt-and-braces insurance against a missed message.
- `GET /api/incidents/{id}/state` returns the identical shape, so the dashboard fetches state over
  REST immediately on mount, independent of how long the WebSocket handshake takes.

## Persistence

SQLite (`app/incident_db.py`), accessed directly via the stdlib `sqlite3` module — no ORM, one
table per record type: `incidents`, `facts`, `hypotheses`, `decisions`, `actions`,
`timeline_events`, `unresolved_questions`, `conflicts`, `coordination_findings`,
`voice_interventions`. All backend concerns share the same database file and one transaction per
write path (a conversation turn's extraction writes, or an action confirmation's writes, each
commit atomically).

This is intentionally sufficient for a hackathon prototype with one backend process and one
incident room at a time — **not** a distributed or production-scale data layer. There is no
connection pooling, no read replica, and no migration framework beyond a couple of idempotent
`ALTER TABLE ... ADD COLUMN` statements run on startup.

## Deployment

Backend on [Render](https://render.com) (free-tier web service), frontend on
[Vercel](https://vercel.com) — no Docker, SQLite unchanged. `render.yaml` at the repo root is the
Render Blueprint (`rootDir: backend`, `pip install -r requirements.txt`,
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/health`); secret-valued env vars
are declared with `sync: false` and set only via the Render dashboard. Vercel needs no config file —
just Project Settings → Root Directory = `frontend` and `NEXT_PUBLIC_API_URL` pointed at the Render
backend. See the README's "Deployment" section for the full walkthrough.

## Design Constraints / Intentional Simplicity

This is a hackathon prototype, deliberately built without infrastructure it doesn't need:

- **No microservices, Kafka, Redis, or Kubernetes.** One FastAPI process, one SQLite file, one
  in-process WebSocket fanout dict. There is exactly one incident room and one backend instance at a
  time in this deployment shape — nothing here needs a message broker or a distributed cache.
- **No ORM.** Direct `sqlite3` through small helpers, because the schema is small and stable enough
  not to need one.
- **No general plugin/tool framework** for actions — one allowlisted action type, added as a
  concrete, testable integration rather than a speculative abstraction.
- **No custom voice/interrupt logic** — Agora's managed pipeline and built-in turn-taking are used
  as-is.

These are scope decisions for this prototype's constraints (single room, single process, hackathon
timeline), not a claim that they'd hold at a different scale — see
[docs/decisions/001-lightweight-architecture.md](docs/decisions/001-lightweight-architecture.md).

## Known Prototype Limitations

- The action-execution tool adapter is a **deterministic sandbox**, not a real production
  remediation system — it validates and returns structured results but never calls a real rollback
  API.
- The realtime layer is a **single in-process WebSocket manager** — state fanout is scoped to one
  backend process; a restart drops live connections (clients reconnect and refetch automatically).
- The Agora → intelligence **webhook adapter** (`app/agora_events.py`) is a documented-shape,
  best-effort fallback; the primary, tested ingestion path is the RTM live-transcript pipeline
  described above.
- The **missing-information coordination check** and the **proactive voice layer** both depend on
  an optional/external service (Gemini, Agora's `/speak` endpoint respectively) and fail safe (empty
  findings / a skipped intervention) if that service is unavailable — every deterministic check
  keeps working regardless.
- **No production authentication or RBAC** — any client that can reach the API can create
  incidents, send conversation turns, change status, resolve conflicts, or confirm actions. "Human
  confirmation" means "an explicit server request was made," not "an authenticated, authorized human
  made it."
- **Incident status has no transition rules** (any status can go to any other) and no automated
  triggers — a direct, human-driven field, not a workflow engine.
- Fact/hypothesis/conflict classification is a **prompting discipline**, enforced by the extraction
  system prompt (plus a deterministic contradiction backstop), not by exhaustive independent
  verification — it holds only as well as the LLM follows it in the general case.
- Render's free-tier filesystem is ephemeral, so `backend/data/echoward.db` is wiped on every
  redeploy/restart, and the free instance spins down after ~15 minutes idle — accepted for this
  hackathon deployment (see README's "Deployment" section).

## Roadmap

The hackathon feature set described above is frozen for submission. Natural next steps beyond this
scope: a real (non-demo) remediation integration behind the same `ToolAdapter` protocol,
authentication/RBAC, a distributed realtime layer if this ever needs more than one backend process,
and richer per-speaker attribution on the transcript path.
