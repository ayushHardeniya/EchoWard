# Agora Integration

Agora Conversational AI is EchoWard's real-time voice foundation — not a cosmetic add-on. This
document explains exactly how EchoWard uses it: the agent architecture, the live-transcript path
that feeds incident intelligence, the proactive-voice path that lets EchoWard speak up on its own,
and the agent lifecycle.

## Why Agora

EchoWard's core value only exists if it can genuinely participate in a live incident call — hear
everyone, be heard, and do so with low enough latency to be useful mid-incident. Agora provides all
three pieces required for that as one managed product:

- an **RTC room** the browser and the AI agent both join as ordinary participants,
- a **managed Conversational AI pipeline** (ASR → LLM → TTS) that makes the agent speak and listen
  without EchoWard's backend touching audio directly, and
- an **RTM signaling channel** carrying live transcripts and letting the backend make the agent
  speak on demand.

Nothing in this project reimplements audio transport, turn-taking, or interruption handling —
that's Agora's job, used as-is.

## Agent Architecture

```
Incident participants
        │  join the RTC channel, publish mic, subscribe to remote audio
        ▼
Agora RTC room
        │  EchoWard's agent joins the same room as a participant
        ▼
EchoWard Conversational AI Agent  (published Agent Builder pipeline)
        │  managed ASR → LLM → TTS
        ▼
voice response, spoken back into the room
```

`POST /api/agora/agent/start` (`app/agora.py`) mints an RTC token for a fixed agent uid
(`AGORA_AGENT_UID`) and calls Agora's Conversational AI Engine REST API (`.../join`) with:

- `pipeline_id` — the published Agent Builder pipeline id (`AGORA_AGENT_PIPELINE_ID`), the single
  source of truth for the agent's ASR/LLM/TTS behavior. The backend does **not** duplicate
  vendor/model config in the join payload — the published pipeline (Deepgram ASR → an OpenAI model
  → MiniMax TTS, in this project's published pipeline) owns that entirely.
- the RTC `channel`/`token`/agent `uid` under `properties`, with `remote_rtc_uids: ["*"]` so the
  agent listens to every participant in the room, not just whoever started it.

`POST /api/agora/agent/stop` calls Agora's `.../agents/{agentId}/leave` to remove it. No separate
Deepgram/OpenAI/MiniMax/Gemini API key is required for the voice loop — Agora authenticates and
bills those vendors on EchoWard's behalf via `credential_mode: "managed"`.

## Live Transcript Ingestion

```
Participant speech
        ▼
Agora Conversational AI  (managed ASR)
        ▼
RTM transcript message   (Signaling channel, not the RTC data channel)
        ▼
Frontend (agora-rtm-sdk, liveTranscript.ts)
        ▼
EchoWard conversation endpoint   POST /api/incidents/{id}/conversation
        ▼
Incident intelligence   (Gemini structured extraction)
        ▼
SQLite
        ▼
WebSocket dashboard broadcast
```

Live transcript delivery required more than just joining the room. Verification against a real
Agora project found that the RTC `stream-message` data channel never carries Conversational AI
transcripts in this configuration — delivery is over the **Signaling (RTM)** channel, and requires
three fields on the agent's join request (`app/agora.py`'s `build_agent_join_payload`):

- `advanced_features.enable_rtm: true`
- `parameters.data_channel: "rtm"`
- `parameters.transcript: {enable: true, protocol_version: "v2"}`

The frontend subscribes to that RTM channel with `agora-rtm-sdk` (not the RTC SDK's data-channel
events) and parses each message (`frontend/src/lib/liveTranscript.ts`) — confirmed against a real
captured payload to be flat JSON with fields `object` (`"user_transcription"` /
`"assistant_transcription"`), `text`, `final`, `turn_id`, and `uid`. Parsing fails closed (returns
`null`, never throws) on anything unparseable, and a client-side `TranscriptDeduper` ensures a
re-delivered "final" segment for the same turn isn't submitted to incident intelligence twice.
Only finalized (`final: true`) human segments are submitted; the agent's own speech
(`object: "assistant_transcription"`) is filtered out before it ever reaches incident intelligence.

A best-effort webhook adapter also exists (`POST /api/agora/webhook/{incident_id}`,
`app/agora_events.py`), built against Agora's documented Conversational AI webhook shape as a
fallback ingestion path — the RTM pipeline above is the primary, tested path used by the running
application.

## Proactive Voice

```
Incident state
        ▼
Coordination finding   (app/coordination.py — conflict, action awaiting
        │                confirmation, or missing information)
        ▼
Voice intervention engine   (app/voice.py — eligibility, dedup, cooldown)
        ▼
Agora agent /speak   (POST .../agents/{agentId}/speak)
        ▼
Incident room   (spoken out loud through the agent's TTS)
```

`app/voice.py` is deliberately not a second reasoning system — it only reads the `IncidentState`
that incident and coordination intelligence already produced (never the raw transcript, never a
second LLM call for *what* to say) and decides *whether* and *when* to speak. It's eligible to
intervene on three finding types only — conflicts, an action awaiting confirmation, and missing
information — guarded by two mechanisms so it doesn't talk after every transcript segment:

- **Duplicate suppression** — a finding's `dedup_key` is spoken at most once ever.
- **A cooldown** between any two proactive interventions for the same incident.

A human directly asking "EchoWard, what's the status?" (detected with a deterministic keyword
check, no LLM call) always gets an answer — the existing situational-summary finding, spoken on
request — and bypasses the cooldown. The result of an action a human just confirmed is announced
the same way. Every call into Agora's `/speak` endpoint fails safe: a missing agent registration,
missing credentials, or a failed Agora request just logs and returns `False`, and never breaks
incident processing.

## Agent Lifecycle

- **Start:** `POST /api/agora/agent/start {channel, incident_id}` — mints the agent's RTC token,
  calls Agora's `.../join`, and (if `incident_id` was supplied) registers the returned `agent_id`
  against that incident in `app/voice.py`'s in-process registry, so proactive interventions and
  status answers know which live agent to speak through.
- **Stop:** `POST /api/agora/agent/stop {agent_id}` — calls Agora's `.../leave` and removes the
  registry entry.
- **No cross-restart persistence** — the agent registry is a plain in-process dict, matching
  `app/realtime.py`'s WebSocket connection manager. A backend restart drops it; a fresh
  `agent/start` in the same channel re-registers.
- Two Conversational AI agents cannot usefully run in the same channel — starting a second one just
  creates a second `agent_id`; the UI doesn't prevent this.

## Model Configuration

Only what's actually configured is documented here — no credentials or account-specific values:

- **Voice loop (managed):** ASR/LLM/TTS vendor and model selection live entirely in the published
  Agent Builder pipeline in the Agora Console (`AGORA_AGENT_PIPELINE_ID`), not in this codebase.
  This project's published pipeline uses Deepgram (ASR) → an OpenAI model (LLM) → MiniMax (TTS).
- **Incident intelligence (separate from the voice loop):** `GEMINI_API_KEY` / `GEMINI_MODEL`
  (default `gemini-flash-latest`) — used only by the structured extraction and coordination
  gap-detection calls, never by the Agora voice pipeline above.
- **RTC/Conversational AI credentials:** `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`,
  `AGORA_CUSTOMER_ID`, `AGORA_CUSTOMER_SECRET`, `AGORA_CONVO_AI_BASE_URL`,
  `AGORA_TOKEN_EXPIRE_SECONDS`, `AGORA_AGENT_UID` — see `backend/.env.example` for the full,
  commented list. None of these ever reach the frontend; the browser only ever receives a
  short-lived RTC token and the (non-secret) `app_id`.

## Hackathon Requirement

PS41 requires a **live voice room the AI can join and participate in**, not a text chatbot with a
voice bolted on afterward. Agora is the only component in this system that makes that possible:
every incident statement EchoWard reasons about arrives because Agora's managed pipeline
transcribed it, and every spoken word from EchoWard — whether a direct response or a proactive
intervention — leaves through Agora's TTS into the same room. The Agora layer (§"Real-Time Voice
Layer" in [ARCHITECTURE.md](../ARCHITECTURE.md)) and EchoWard's own intelligence layer are
intentionally kept as two distinct integrations: Agora is never asked to reason about incident
state, and EchoWard's Gemini-backed intelligence never touches audio — but the two are wired
together tightly enough (RTM transcript in, `/speak` out) that Agora is structurally load-bearing,
not decorative.
