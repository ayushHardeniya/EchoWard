# EchoWard Architecture

This document describes the current, implemented architecture of EchoWard as of milestone M5
(Coordinate + Act). It is a living document — it will be updated as M6 and M7 land, not a frozen
hackathon snapshot.

## 1. System Overview

EchoWard is a voice-native AI incident commander. It joins a live technical incident room, turns
the conversation happening there into structured incident state, identifies coordination gaps the
team should pay attention to, and can safely execute a small set of pre-approved operational
actions once a human explicitly confirms them.

The core product principle:

> **EchoWard does not pretend to know what is true.**

Every extracted item carries its epistemic status — a statement someone reported is a `Fact`
(always `confidence = "reported"`, never independently verified); a suspected explanation is a
`Hypothesis` (proposed / supported / refuted, never silently promoted); a contradiction between
two statements is surfaced as a `Conflict` for humans to resolve, never adjudicated by the system.
There is no `root_cause` field anywhere in the data model — a suspected root cause is just a
`Hypothesis` like any other.

The system is organized around four distinct concerns, each covered in its own section below:

- **Conversation** — the live voice exchange in the Agora room. EchoWard listens and can speak,
  but this layer has no notion of "incident state" — it's audio in, audio out.
- **Structured incident state** — one conversation turn at a time, an LLM call extracts
  facts/hypotheses/decisions/actions/timeline events/conflicts, which are persisted as the
  incident's single source of truth (§4, §5).
- **Coordination intelligence** — a layer that reads that structured state (never the raw
  transcript) and surfaces what needs attention: unresolved conflicts, unowned or stale actions,
  decisions without follow-through, hypotheses being treated as fact, and open risks (§6).
- **Controlled action execution** — the one place EchoWard is allowed to affect anything outside
  its own database: a structured action proposal, validated against a fixed allowlist, requires
  explicit human confirmation before an adapter executes it (§7).

## 2. High-Level Architecture

```
Participants
    │
    ▼
Agora RTC room  ◀──────────────────────────────────────────────┐
    │                                                            │
    │ real-time voice                                            │ mic/speaker
    ▼                                                            │
Agora Conversational AI Engine (managed ASR/LLM/TTS pipeline)     │
    │                                                            │
    │ agent joins the same RTC room as a participant             │
    ▼                                                            │
┌──────────────────────────────────────────────────────────────┴───┐
│ EchoWard backend (FastAPI, one process)                            │
│                                                                     │
│  app/agora.py, app/agora_events.py   — RTC token + agent lifecycle,│
│                                          best-effort transcript hook│
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │ Incident Intelligence (app/intelligence.py, app/llm.py)     │    │
│  │   conversation turn → Gemini structured extraction →        │    │
│  │   facts / hypotheses / decisions / actions / timeline /     │    │
│  │   unresolved questions / conflicts                          │    │
│  └───────────────────────────────────────────────────────────┘    │
│                          │                                          │
│                          ▼                                          │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │ Coordination Intelligence (app/coordination.py)             │    │
│  │   incident state → conflicts / unowned+stale actions /      │    │
│  │   decision follow-through / hypothesis-as-fact risk /       │    │
│  │   unresolved risks / situational summary                    │    │
│  └───────────────────────────────────────────────────────────┘    │
│                          │                                          │
│                          ▼                                          │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │ Action & Safety Layer (app/tools.py, app/actions.py)         │    │
│  │   prepare (allowlist-validated) → human confirmation →      │    │
│  │   allowlisted adapter execution → result                    │    │
│  └───────────────────────────────────────────────────────────┘    │
│                          │                                          │
│                          ▼                                          │
│              SQLite (app/incident_db.py)                            │
│                          │                                          │
│                          ▼                                          │
│         WebSocket fanout (app/realtime.py)                          │
└──────────────────────────┬───────────────────────────────────────┘
                            │
                            ▼
                  Incident Dashboard (Next.js)
```

All four backend concerns — voice/agent lifecycle, incident intelligence, coordination
intelligence, action execution — run in one FastAPI process and share one SQLite file. There is no
message queue, no separate service boundary, and no distributed state between them; they are
Python modules calling each other directly, sequenced by the API layer in `app/incidents_api.py`.

## 3. Agora Voice Layer

Agora owns real-time voice transport end-to-end; EchoWard's backend only mediates *access* to it.

- **RTC room** — the browser joins an Agora RTC channel using the Agora RTC Web SDK
  (`agora-rtc-sdk-ng`), publishing its microphone track and subscribing to other participants'
  audio (`frontend/src/lib/useAgoraRoom.ts`). The backend's only role here is minting short-lived
  RTC tokens (`POST /api/agora/token`, `app/agora.py`) — it never sees or proxies audio.
- **EchoWard as a participant** — `POST /api/agora/agent/start` mints a token for a fixed agent
  uid and calls Agora's Conversational AI Engine REST API (`.../join`) with a `pipeline_id`
  (published in the Agora Console's Agent Builder) plus the RTC channel/token/uid. The agent then
  joins the same RTC channel as any other participant, listening to everyone
  (`remote_rtc_uids: ["*"]`) and speaking back into the room. `POST /api/agora/agent/stop` removes
  it.
- **Managed ASR/LLM/TTS pipeline** — the actual speech-to-text, conversational reasoning, and
  text-to-speech that make EchoWard audible and responsive in the room are entirely Agora's managed
  pipeline (`credential_mode: "managed"`), configured once in the Agora Console, not in this
  codebase. **This LLM is not Gemini** — it is whatever model the published pipeline specifies
  (verified in this project's Console as Deepgram ASR → an OpenAI model → MiniMax TTS). Gemini is
  used elsewhere, for incident intelligence and coordination (§4, §6) — a completely separate
  integration from the voice loop.
- **Interruption handling** is Agora's built-in behavior (its VAD/turn-taking); the app implements
  no custom duplex/interrupt logic.
- **Browser mic/speaker flow** — join → `createMicrophoneAudioTrack()` → `publish()`; remote
  users' audio is subscribed and played on `user-published`, cleaned up on
  `user-unpublished`/`user-left`. Mute uses `track.setEnabled()`, not unpublish/republish.

The published pipeline and RTC token flow are confirmed working against a real Agora project. The
transcript webhook adapter (`app/agora_events.py`, `POST /api/agora/webhook/{incident_id}`) that
would feed live voice conversation into incident intelligence automatically is implemented against
Agora's documented webhook shape but has not been exercised against a real webhook delivery — see
§12.

## 4. Incident Intelligence

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
LLM-driven, using recent facts/hypotheses/decisions/open actions as prompt context — but the LLM
never decides who is right, only that a contradiction exists.

The Gemini integration (`app/llm.py`) calls `google-genai` with `response_schema` set to the
Pydantic `ConversationAnalysis` model directly — no hand-rolled JSON parsing. If `GEMINI_API_KEY`
isn't configured, the conversation endpoint returns a clear `503` rather than guessing; if the
model call fails or returns non-conformant output, it returns `502` and writes nothing.

All extracted state is persisted in SQLite (`app/incident_db.py`) — see §5, §9.

## 5. Incident State

`IncidentState` (`app/incident_models.py`) is the full, structured snapshot of one incident:

```
IncidentState
├── incident                  (id, title, status, timestamps)
├── facts[]                   statement, source, timestamp, confidence
├── hypotheses[]               statement, source, timestamp, status
├── decisions[]                decision, decided_by, timestamp
├── actions[]                  description, owner, status, action_type,
│                               target, reason, tool_result   (§7)
├── timeline[]                  event, source, timestamp
├── unresolved_questions[]      question, status
├── conflicts[]                 topic, statements[], involved_sources, status
└── coordination_findings[]     type, severity, title, description, related_ids
```

This is the single object every other layer reads from and writes into:

- Coordination intelligence (§6) computes findings *only* from `IncidentState`, never the raw
  transcript.
- The action/safety layer (§7) transitions `Action` records within this same state.
- `GET /api/incidents/{id}/state` and every `WS /api/incidents/{id}/stream` message return exactly
  this shape (`IncidentState.model_dump(mode="json")`) — the frontend has one incident model, not
  a REST shape and a separate WebSocket shape.

Using one shared, persisted state (rather than re-deriving findings from the transcript each time,
or keeping separate models per layer) is what lets coordination and action execution stay
consistent with what the dashboard shows, and lets a reconnecting client simply refetch it.

## 6. Coordination Intelligence (M4)

Coordination intelligence (`app/coordination.py`) reads the current `IncidentState` and produces
`CoordinationFinding` rows — what the team needs to pay attention to *next*, distinct from *what
happened* (§4/§5). It never executes anything and never resolves anything itself.

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

**Explicitly:**
- **Conflicts are surfaced, not automatically adjudicated** — coordination never picks a side.
- **Hypotheses are never automatically promoted to facts** — only flagged as a risk if they're
  being acted on as if confirmed.

Findings are reconciled, not append-only: each proposed finding carries a `dedup_key`
(e.g. `unowned_action:{id}`), upserted so a repeated observation updates in place, and any
previously open finding whose key isn't proposed on a given refresh flips to `resolved` (e.g.
assigning an owner resolves `unowned_action:{id}` automatically). `IncidentState.
coordination_findings` only ever returns `status = "open"` findings, most severe first.

Coordination refreshes on the same triggers M3 already broadcasts on — a conversation turn that
actually changed state, or an incident status change — and (as of M5) also after action
prepare/confirm, so the dashboard, the API response, and the broadcast never disagree about
findings for the same event.

## 7. Coordinate + Act (M5)

The safety-critical part of the system: EchoWard can propose an operational action, but only a
human confirmation and a fixed allowlist stand between a proposal and anything actually happening.

```
Structured action proposal
        │  (action_type, target, reason — never free text/shell/URL/code)
        ▼
Application validation           app/tools.py: validate_action() against
        │                        a fixed allowlist (ALLOWED_ACTIONS)
        ▼
Human confirmation               explicit POST .../confirm from the dashboard;
        │                        server re-checks persisted state, not client input
        ▼
Allowlisted tool adapter         app/tools.py: DemoIncidentToolAdapter —
        │                        deterministic, sandboxed, no real system touched
        ▼
Execution
        │
        ▼
Tool result                      ToolResult(success, message, external_id, metadata)
        │
        ▼
Incident state                   Action.status + Action.tool_result updated,
                                  timeline event added, coordination refreshed,
                                  broadcast over WebSocket
```

**Action lifecycle**, exactly as implemented (`ActionStatus`, `app/incident_models.py`):

```
pending → awaiting_confirmation → confirmed → executing → completed | failed
```

An `Action` starts as `pending` (created by incident intelligence, §4, same as before M5).
`POST /api/incidents/{id}/actions/{action_id}/prepare` validates a proposed `action_type`/`target`
against the allowlist and moves it to `awaiting_confirmation` — it never executes anything.
`POST /api/incidents/{id}/actions/{action_id}/confirm` is the *only* code path that calls the tool
adapter, and only after independently re-checking (never trusting a prior check) that the action
belongs to the given incident and is currently `awaiting_confirmation`.

**Allowed action(s):** one entry for this milestone — `rollback_payment_service` on target
`payment-service` — deliberately a single strong integration rather than a general plugin
framework. Any other `action_type`/`target` combination is rejected before anything is persisted
or shown as "ready to confirm."

**Validation performed before execution:**
- `action_type` and `target` are on the allowlist (checked in `prepare`, and again independently in
  `confirm` — defense in depth).
- The action belongs to the incident being acted on (`incident_db.get_action` is scoped by
  `incident_id`; an action id from another incident 404s).
- The action's persisted status is exactly `awaiting_confirmation` at confirm time — a
  `pending`, `confirmed`, `executing`, `completed`, or `failed` action cannot be (re-)confirmed,
  which is what makes duplicate execution impossible through the API: a `completed`/`failed`
  action returns `409` on a second confirm attempt instead of running again.

**Tool adapter:** `DemoIncidentToolAdapter` is a **sandbox/demo implementation, not a real
production remediation system**. It validates its input against the same allowlist again
(fail-closed if ever reached out of band), never makes a network call or touches a real service,
and returns a deterministic result — a reason containing `"force_failure"` makes it fail, anything
else succeeds — so the full path is real and testable without external dependencies. A genuine
integration (an internal remediation API, PagerDuty, etc.) would implement the same `ToolAdapter`
protocol and replace `default_adapter`; nothing above this module would need to change.

**Success path:** `Action.status → completed`, `tool_result` persisted, a timeline event added,
coordination findings refreshed, updated state broadcast over the incident's WebSocket stream.

**Failure path:** `Action.status → failed`, the failure `tool_result` persisted, a timeline event
added, the incident itself is left exactly as-is (no automatic status change), coordination
findings refreshed (a failed action produces its own `action_failed` finding), updated state
broadcast. Nothing is retried automatically.

The LLM is never given the ability to call the adapter, invent an `action_type`/`target`, or reach
this module at all — it only ever produces the initial `pending` action description during
incident intelligence extraction (§4). Everything from "prepare" onward is driven by explicit
requests validated server-side.

## 8. Realtime Architecture

`WS /api/incidents/{incident_id}/stream` (`app/incidents_api.py`, fanout in `app/realtime.py`):

- On connect: 404s (via a WebSocket close with code `4404`) if the incident doesn't exist,
  otherwise accepts and immediately sends the current full `IncidentState` as an
  `incident.updated` message.
- After that, the same connection receives one more `incident.updated` message — always the
  *complete* state, never a diff — per meaningful change, for as long as it stays open.
- **Broadcast only follows a completed, committed write.** A conversation turn's DB write, a
  status change, or an action prepare/confirm all fully complete (and, where relevant, trigger a
  coordination refresh) *before* the broadcast is even considered — a client can never receive
  state that wasn't already persisted. A no-op conversation turn (nothing extracted) does not
  broadcast at all.
- Connections are a plain in-process `dict[incident_id, set[WebSocket]]` (`IncidentConnectionManager`)
  — no Redis or external pub/sub, correct because there is exactly one backend process and SQLite
  is already the shared source of truth. A backend restart drops all connections.
- **Frontend reconnect behavior** (`frontend/src/lib/useIncidentStream.ts`): on an unexpected
  close, the hook reconnects with exponential backoff (1s → 2s → 4s → … capped at 10s, reset on
  success), shown as a small connection-state dot in the dashboard header. On every successful
  (re)connect it also issues a REST `GET .../state` refetch as belt-and-braces insurance against a
  missed message. The dashboard fetches state over REST immediately on mount regardless of how long
  the WebSocket handshake takes.

## 9. Data Layer

SQLite (`app/incident_db.py`), accessed directly via the stdlib `sqlite3` module — no ORM, one
table per record type: `incidents`, `facts`, `hypotheses`, `decisions`, `actions`,
`timeline_events`, `unresolved_questions`, `conflicts`, `coordination_findings`. All backend
concerns (Phase 1 health checks, M2 intelligence, M4 coordination, M5 actions) share the same
database file.

Notable choices:
- `Conflict.statements`/`involved_sources` are stored as JSON text columns (simplest correct
  option for a small, incident-scoped list — not worth a join table).
- `Action`'s M5 columns (`action_type`, `target`, `reason`, `tool_result`) were added via an
  idempotent `ALTER TABLE ... ADD COLUMN` migration run on startup, since SQLite's
  `CREATE TABLE IF NOT EXISTS` doesn't add columns to a table that already exists.
- A full conversation turn (extraction's DB writes) or an action confirmation's writes commit as
  one transaction each via `get_connection()`'s commit-on-success context manager.

This is intentionally sufficient for a hackathon prototype with one backend process and one
incident room at a time — **not** a distributed or production-scale data layer. There is no
connection pooling, no read replica, and no migration framework beyond the one ad hoc
`ALTER TABLE` above.

## 10. Frontend Architecture

Next.js 16 (App Router) + React 19 + TypeScript, Tailwind CSS v4.

- **`src/app/page.tsx`** — the voice-room UI: join/leave an Agora RTC channel, start/stop the
  EchoWard agent, participant list, and hosts `IncidentDashboard`.
- **`src/lib/useAgoraRoom.ts`** — Agora RTC client lifecycle (join, publish mic, subscribe to
  remote audio, mute, leave); dynamically imports `agora-rtc-sdk-ng` so it never runs during
  server-side prerender.
- **`src/lib/agora-api.ts`** — typed client for the backend's `/api/agora/*` endpoints (token,
  agent start/stop).
- **`src/components/IncidentDashboard.tsx`** — the live incident command view: status (with a
  lifecycle dropdown), the Coordination panel, facts/hypotheses (visually distinct
  confirmed-vs-unconfirmed), conflicts, actions (including M5's prepare/confirm controls per
  action), decisions, timeline, and unresolved questions. A small collapsed dev control at the
  bottom sends a test conversation turn without needing Agora.
- **`src/lib/incidents-api.ts`** — typed REST client for `/api/incidents/*` (create, get state,
  update status, send a conversation turn, prepare/confirm an action).
- **`src/lib/useIncidentStream.ts`** — the WebSocket-backed hook that keeps `IncidentState` in
  sync with the backend (see §8).

The frontend never touches Agora's App Certificate or the Conversational AI REST credentials —
only the browser-safe `app_id` (returned from the token endpoint) and short-lived RTC tokens reach
the client. It has no automated test runner yet; correctness is checked via `next lint`,
`tsc --noEmit`, and `next build`.

## 11. Safety Boundaries

- **Agora owns real-time voice transport and the conversation pipeline.** EchoWard's backend
  mediates access (tokens, agent lifecycle) but never proxies or inspects audio directly.
- **The EchoWard application owns structured incident intelligence**, derived from conversation
  turns, not from anything inside Agora's managed pipeline.
- **LLM output is always structured and schema-validated** (`response_schema` on every Gemini
  call) — never free text parsed by regex, and never written to the database if it fails to
  validate.
- **Coordination intelligence never autonomously resolves anything** — conflicts are surfaced for
  humans, hypotheses are never silently promoted to facts.
- **The LLM cannot directly execute arbitrary commands, shell calls, URLs, or code.** It can only
  ever produce a `pending` action's initial description during extraction — it has no path to
  `app/tools.py` or `app/actions.py`.
- **Actions are allowlisted**, both when prepared and again immediately before execution — one
  action type, one target, for this milestone.
- **Human confirmation is required before execution**, enforced server-side against persisted
  state (`awaiting_confirmation`), not inferred from conversational language like "yeah" or "let's
  do it," and not trusted from client input.
- **Tool execution occurs only through the controlled adapter** (`DemoIncidentToolAdapter`), which
  is itself a sandbox — no real external system is touched by the current implementation.

## 12. Current Architecture / Limitations

- The M5 tool adapter is a **deterministic sandbox/demo implementation**, not a real production
  remediation system — it validates and returns structured results but never calls a real
  rollback API.
- The realtime layer is a **single in-process WebSocket manager** — no Redis/pub-sub, so state
  fanout is scoped to one backend process; a restart drops live connections (clients reconnect and
  refetch automatically).
- The Agora → intelligence **webhook adapter is unverified against a live webhook delivery**, and
  can't attribute individual human speakers by name — Agora's documented transcript payload only
  distinguishes `role: "user"` vs `"assistant"`, so every human turn from that path would be logged
  under the generic speaker `"Participant"`. The tested, working ingestion path is the explicit
  `POST /api/incidents/{id}/conversation` endpoint.
- The **missing-information coordination check is an optional Gemini dependency** — every other
  coordination check (conflicts, unowned/stale actions, decision follow-through, hypothesis risk,
  unresolved risks, situational summary, and the M5 action-lifecycle findings) is deterministic
  and needs no external service.
- **No production authentication or RBAC** — any client that can reach the API can create
  incidents, send conversation turns, change status, or confirm actions. Human confirmation in §7
  means "an explicit server request was made," not "an authenticated, authorized human made it."
- **Incident status has no transition rules** (any status can go to any other) and no automated
  triggers — a direct, human-driven field, not a workflow engine.
- Fact/hypothesis/conflict classification is a **prompting discipline**, enforced by the
  extraction system prompt, not by independent verification code — it holds only as well as the
  LLM follows it.
- SQLite is intentionally sufficient for this prototype's single-process, single-incident-room-at-
  a-time scope — it is not a distributed data layer, and no migration framework beyond one ad hoc
  `ALTER TABLE` exists.

## 13. Roadmap

- **M0 — Foundation** — complete
- **M1 — Agora Voice MVP** — complete
- **M2 — Incident Intelligence** — complete
- **M3 — Live Incident State** — complete
- **M4 — Coordination Intelligence** — complete
- **M5 — Coordinate + Act** — complete
- **M6 — Voice-Native Commander / Hardening** — next
- **M7 — Finalize / Demo / Ship** — planned

This roadmap will be updated as M6 and M7 are implemented.
