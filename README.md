# EchoWard

Voice-native AI Incident Commander for the EchoSphere: Agora Conversational AI Hackathon 2026.

EchoWard joins a live technical incident room, listens to the conversation, and keeps a
continuously updated shared picture of the incident — facts, hypotheses, decisions, actions,
owners, and timeline — while surfacing conflicts and missing information instead of guessing
at root cause.

See [CLAUDE.md](./CLAUDE.md) for architecture, conventions, and current build status.

## Repository structure

```
echoward/
├── backend/     FastAPI + SQLite backend
├── frontend/    Next.js + TypeScript frontend
└── CLAUDE.md    Architecture, decisions, status
```

## Prerequisites

- Node.js 20+ and npm
- Python 3.12+
- An [Agora](https://console.agora.io) account (free) with a project that has **RTC** and
  **Conversational AI** enabled — required only to actually run the voice room; the app runs and
  its tests pass without one.

## Backend setup (FastAPI)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # fill in real values as needed

uvicorn app.main:app --reload    # runs on http://localhost:8000
```

Run tests and lint:

```bash
pytest
ruff check .
```

## Frontend setup (Next.js)

```bash
cd frontend
npm install

cp .env.example .env.local       # fill in real values as needed

npm run dev                      # runs on http://localhost:3000
```

Lint, type-check, and build:

```bash
npm run lint
npx tsc --noEmit
npm run build
```

## Verifying the foundation

1. Start the backend (`uvicorn app.main:app --reload`) — visit http://localhost:8000/health
2. Start the frontend (`npm run dev`) — visit http://localhost:3000 and confirm it shows the
   backend's health status (service, environment, database connection). *(This check was replaced
   by the incident-room UI in Phase 2 — see below for what's on the page now.)*

## Configuring Agora

1. Create a project at [console.agora.io](https://console.agora.io) (or use an existing one).
   Make sure **App Certificate** is enabled (Project settings) — Conversational AI requires it.
2. On the project's config page, copy the **App ID** and **App Certificate** into
   `backend/.env` as `AGORA_APP_ID` and `AGORA_APP_CERTIFICATE`.
3. In the Console, open **RESTful API** (usually under project or account settings) and generate
   a **Customer ID** / **Customer Secret** pair. These are *not* the App ID/Certificate — they're
   separate credentials used only for the Conversational AI Engine's REST API. Put them in
   `backend/.env` as `AGORA_CUSTOMER_ID` / `AGORA_CUSTOMER_SECRET`.
4. Make sure **Conversational AI Engine** is enabled for the project (Agora may require opting in
   via the Console or CLI: `agora project env --feature convoai`, depending on account type).
5. Leave the `AGORA_ASR_*` / `AGORA_LLM_*` / `AGORA_TTS_*` variables at their defaults to start —
   they select Agora's **managed-credential** presets (Deepgram / OpenAI gpt-4o-mini / MiniMax),
   so no separate Deepgram/OpenAI/MiniMax/Gemini API key is needed for the voice loop itself.
   If your account's managed-preset catalog differs, `agent/start` will return a `502` with
   Agora's rejection message in the response `detail` — adjust these vendor/model values to match
   what's available on your account (see Agora Console > Agent Studio for the current list).
6. The frontend needs no separate Agora env var — it gets `app_id` back from the backend's token
   endpoint. Its `.env.local` only needs `NEXT_PUBLIC_API_URL`.

## Starting a voice room

1. Run the backend and frontend (see above), both with real `.env` values configured.
2. Open http://localhost:3000, enter a room/channel name (e.g. `incident-room`) and your display
   name, and click **Join Room**. Allow microphone access when prompted.
3. Click **Start EchoWard**. This calls the backend, which starts an Agora Conversational AI
   agent in the same channel — it should appear in the participant list within a few seconds.
4. Speak — e.g. *"Payments are failing for a large number of users."* EchoWard should respond
   out loud through your speakers.
5. Click **Stop EchoWard** to remove the agent, or **Leave Room** to leave (which also stops the
   agent automatically if it's still running).

## Testing the voice interaction

Automated tests (`pytest` in `backend/`) cover token generation, request validation, and the
agent-start payload shape — all without needing a real Agora account or network access.

Actually hearing the voice loop work requires a real Agora project (see above) and a browser with
a microphone. Manual verification procedure:

1. Configure `backend/.env` with real Agora credentials (App ID/Certificate + Customer ID/Secret).
2. Start the backend and frontend, open http://localhost:3000 in **two separate browser
   tabs/windows** (or two devices), and join the **same** channel name from both.
3. Confirm both tabs show each other in the participant list, and that speaking in one is audible
   in the other (basic RTC sanity check, before involving EchoWard).
4. From either tab, click **Start EchoWard**. Confirm it appears in both tabs' participant lists.
5. Speak an incident statement (see the example above) and confirm EchoWard responds audibly in
   *both* tabs (it's in the same shared channel).
6. While EchoWard is mid-response, start speaking again and confirm it stops/yields cleanly
   (Agora's built-in interruption handling) rather than talking over you indefinitely or getting
   stuck.
7. Click **Stop EchoWard** and confirm it leaves the participant list in both tabs.
8. Click **Leave Room** in both tabs and confirm the connection state returns to "Disconnected"
   with no lingering audio.

## Known limitations

- The Agora integration is implemented against Agora's documented API shapes but has not been
  exercised against a real Agora account in this environment — see CLAUDE.md's "Known
  limitations" under Agora integration for specifics (managed-preset availability can vary by
  account, no agent-state persistence across backend restarts, etc.).
- No incident intelligence yet: EchoWard responds conversationally but does not extract or track
  facts/hypotheses/decisions from the conversation. That's the next milestone.
- No dashboard, no persistent incident record, no Slack/Jira/PagerDuty integration, no
  authentication — all intentionally out of scope for this phase.
- Frontend has no automated test runner yet (lint + `tsc --noEmit` + `next build` only).

## Status

Phase 1 (foundation) and Phase 2 (Agora voice MVP) complete: runnable Next.js frontend + FastAPI
backend with SQLite wiring, health check, and a working Agora RTC + Conversational AI voice room
(pending live-account verification — see above). Incident intelligence is not yet implemented —
see CLAUDE.md for the current milestone.
