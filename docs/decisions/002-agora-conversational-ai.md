# 002 — Agora Conversational AI as the Voice Foundation

## Context

PS41 requires a voice-native AI Incident Commander: the AI must be able to join a live voice room,
listen continuously, and speak — not operate as a text-only chatbot with voice bolted on. The
hackathon itself is built around Agora's Conversational AI product. EchoWard also needs a separate
intelligence layer to turn conversation into structured incident state, which is a fundamentally
different kind of reasoning than real-time speech handling.

## Decision

Use Agora's RTC room plus its managed Conversational AI Engine (managed ASR/LLM/TTS pipeline,
published via Agent Builder) as the entire real-time voice layer, and keep it structurally separate
from EchoWard's own intelligence layer, which uses Gemini for structured extraction and
coordination reasoning. The two integrations never share a model or a call path: Agora's pipeline
only makes EchoWard audible and responsive in the room; Gemini only ever sees a text conversation
turn or a structured incident-state summary.

## Why

- Agora already solves real-time transport, turn-taking, interruption handling, and managed
  ASR/LLM/TTS as one product — reimplementing any of that would be pure waste for this project's
  goals.
- Keeping the voice pipeline's LLM (whatever the published Agent Builder pipeline specifies —
  Deepgram → an OpenAI model → MiniMax TTS in this project) separate from the incident-intelligence
  LLM (Gemini, called directly from the backend) means each can be reasoned about, tested, and
  changed independently. The voice pipeline has no notion of "incident state"; the intelligence
  layer never touches audio.
- Live transcript delivery turned out to require Agora's RTM (Signaling) channel, not the RTC data
  channel, plus specific join-payload flags (`enable_rtm`, `data_channel: "rtm"`,
  `transcript.protocol_version: "v2"`) — discovered by testing against a real Agora project. This
  confirmed Agora's transcript path is a first-class, if non-obvious, part of the integration, not
  an afterthought.
- Proactive voice (EchoWard speaking up unprompted) reuses the same agent via Agora's `/speak`
  endpoint rather than a separate TTS integration, so there's exactly one voice output path for the
  whole system.

## Consequences

- EchoWard's voice behavior (tone, latency, interruption handling) is bounded by what the published
  Agent Builder pipeline and Agora's managed pipeline support — customizing it further means
  changing the published pipeline in the Agora Console, not application code.
- The webhook-based transcript adapter (`app/agora_events.py`) exists only as a documented-shape
  fallback; the RTM live-transcript path is the primary, verified ingestion route.
- No separate Deepgram/OpenAI/MiniMax API key is needed for the voice loop — Agora authenticates and
  bills those vendors on EchoWard's behalf (`credential_mode: "managed"`) — but this also means
  EchoWard cannot swap the voice pipeline's underlying model without a change in the Agora Console.
- Two agents cannot usefully run in the same room, and agent state isn't persisted across a backend
  restart — acceptable constraints for a single-incident-room hackathon demo.
