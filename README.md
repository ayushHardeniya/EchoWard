# EchoWard

Voice-native AI Incident Commander for the EchoSphere: Agora Conversational AI Hackathon 2026.

EchoWard joins a live technical incident room, listens to the conversation, and keeps a
continuously updated shared picture of the incident — facts, hypotheses, decisions, actions,
owners, and timeline — while surfacing conflicts and missing information instead of guessing
at root cause. On top of that shared picture, EchoWard's coordination-intelligence layer flags
what the team needs to pay attention to *next*: unresolved conflicts, unowned or stale actions,
decisions with no tracked follow-up, hypotheses being acted on as if confirmed, and open risks —
turning "here's what happened" into "here's what needs attention."

## Repository structure

```
echoward/
├── backend/     FastAPI + SQLite backend
└── frontend/    Next.js + TypeScript frontend
```

## Architecture

```
Browser (Next.js)                     FastAPI backend                   External services
──────────────────                    ────────────────                  ─────────────────
Agora RTC Web SDK  ── token/agent ──▶  app/agora.py            ──────▶  Agora RTC + Conversational
  (voice room)     ◀── audio ───────   app/agora_events.py     ◀──────  AI Engine (managed LLM/
                                         (transcript webhook,             ASR/TTS)
                                          unverified — see below)

IncidentDashboard  ── conversation ─▶  app/incidents_api.py
  (live UI)                              app/intelligence.py    ──────▶  Gemini (structured
                   ◀── WS broadcast ──   app/incident_db.py               extraction, +
                       (state)           app/realtime.py                 optional coordination
                                         app/coordination.py              gap-detection)
                                         (SQLite + in-process WebSocket fanout)
```

The voice loop, incident intelligence, and coordination intelligence are independent backend
concerns that share one FastAPI process and one SQLite file. A conversation turn can reach the
intelligence pipeline either directly (`POST /api/incidents/{id}/conversation`, what
`IncidentDashboard`'s dev control and the tests use) or — once wired up in a real Agora project —
via the `POST /api/agora/webhook/{incident_id}` adapter. After every meaningful state change (a
conversation turn that actually extracted something, or a status change), `app/coordination.py`
re-derives coordination findings from the incident's current structured state — never the raw
transcript — and the result is broadcast over `WS /api/incidents/{id}/stream` to every dashboard
watching that incident, alongside the rest of the incident state.

Coordination intelligence is mostly deterministic (conflict surfacing reuses the existing
Conflict records; unowned/stale actions, decisions without a tracked action, hypotheses being
acted on as fact, and open questions/risks are all computed directly from timestamps and text
overlap — no LLM call, always available). One check — semantic "missing information" gaps (e.g.
"impact is known but affected scope isn't") — optionally calls Gemini with the structured state
summary (not the transcript) and fails safe to no findings if no `GEMINI_API_KEY` is set, so the
rest of coordination intelligence works with zero external dependencies.

## Prerequisites

- Node.js 20+ and npm
- Python 3.12+
- An [Agora](https://console.agora.io) account (free) with a project that has **RTC** and
  **Conversational AI** enabled — required only to actually run the voice room; the app runs and
  its tests pass without one.
- A [Gemini API key](https://aistudio.google.com/apikey) (free tier) — required only to actually
  run incident-intelligence extraction; the app runs and its tests pass without one (the
  conversation endpoint returns a clear `503` instead).

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
5. In the Console's **Agent Builder**, publish a Conversational AI pipeline (ASR/LLM/TTS are
   configured there, using Agora's managed-credential presets — no separate Deepgram/OpenAI/
   MiniMax/Gemini API key needed). Copy its **pipeline ID** into `backend/.env` as
   `AGORA_AGENT_PIPELINE_ID`. The published pipeline is the source of truth for the agent's voice
   behavior — the backend only sends `pipeline_id` plus the RTC channel/token/uid on `agent/start`,
   it does not duplicate ASR/LLM/TTS config. If the pipeline id is missing or wrong, `agent/start`
   returns a clear `503`/`502` rather than silently failing.
6. The frontend needs no separate Agora env var — it gets `app_id` back from the backend's token
   endpoint. Its `.env.local` only needs `NEXT_PUBLIC_API_URL`.

## Configuring incident intelligence (Gemini)

1. Get a free API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. Put it in `backend/.env` as `GEMINI_API_KEY`. `GEMINI_MODEL` defaults to `gemini-flash-latest`
   and normally doesn't need changing.
3. This is separate from the Agora voice agent's LLM (which is Agora-managed, see above) — it's
   used only by `POST /api/incidents/{id}/conversation` to extract structured facts/hypotheses/
   decisions/actions/etc. from one statement at a time.

## Exercising incident intelligence without Agora

The intelligence layer is fully decoupled from the voice room — you can test it with `curl` alone,
no browser/microphone/Agora account needed:

```bash
# 1. Create an incident
curl -s -X POST http://localhost:8000/api/incidents \
  -H "Content-Type: application/json" \
  -d '{"title": "Payments outage"}'
# => {"id": "…", "title": "Payments outage", "status": "investigating", ...}

# 2. Feed it a conversation turn (requires GEMINI_API_KEY to be set)
curl -s -X POST http://localhost:8000/api/incidents/<incident_id>/conversation \
  -H "Content-Type: application/json" \
  -d '{"speaker": "Alice", "text": "Payments are failing for about 30% of users."}'
# => {"changes": {"facts": [...], ...}, "state": {...full incident state...}}

# 3. Read the current structured state at any time
curl -s http://localhost:8000/api/incidents/<incident_id>/state

# 4. Move the incident through its lifecycle
curl -s -X PATCH http://localhost:8000/api/incidents/<incident_id>/status \
  -H "Content-Type: application/json" \
  -d '{"status": "mitigating"}'
```

Or open http://localhost:3000, create/open an incident in the **Incident Command** panel, and use
the small "Dev: send a test conversation statement" control at the bottom of the dashboard — same
calls, no curl needed.

Automated tests (`pytest`) exercise the full pipeline — including the exact 5-turn scenario from
the M2 spec (fact → hypothesis → conflict → decision → action-with-owner) — with the LLM call
mocked, so `pytest` needs no `GEMINI_API_KEY` either.

## Exercising coordination intelligence

No separate setup needed — it runs automatically after every conversation turn or status change
that actually changes something. To see it:

1. Open the dashboard, create an incident, and use "Dev: send a test conversation statement" (or
   `curl` the `/conversation` endpoint, see above) to send a statement that creates an action with
   no owner, e.g. *"We should roll back the payment service."*
2. The **Coordination** panel (directly under the incident status, above Facts/Hypotheses) shows a
   `[HIGH] Action has no owner` finding, plus a compact checkpoint line ("N action(s) open (N
   unowned)...").
3. Send a follow-up statement assigning an owner, e.g. as "Bob": *"I'll take the rollback."* The
   finding disappears from Coordination on the next update — same live WebSocket broadcast as the
   rest of the dashboard, no manual refresh.
4. Sending two conflicting statements (see the M2 example above) also produces a `[HIGH]
   Conflicting reports: ...` coordination finding alongside the existing Conflicts panel — the
   panels represent the same underlying Conflict record, just surfaced two ways: full detail in
   Conflicts, an attention-grabbing summary in Coordination.

The optional semantic "missing information" check only fires with a real `GEMINI_API_KEY`
(same one used for extraction) — without it, every other coordination check above still works.

## Exercising the live dashboard / realtime updates

1. Start the backend and frontend. Open http://localhost:3000 in **two browser tabs**.
2. In tab 1, create an incident in the Incident Command panel. Copy the incident id shown under
   its title (`#…`).
3. In tab 2, paste that id into "Or paste an existing incident id" and click **Open**. Both tabs
   are now watching the same incident (each opens its own WebSocket connection to
   `/api/incidents/{id}/stream`).
4. In either tab, expand "Dev: send a test conversation statement" and send one (needs
   `GEMINI_API_KEY`), or change the status dropdown. **Both tabs update within roughly a second**,
   with no manual refresh — that's the realtime broadcast working.
5. To see the reconnect behavior: stop the backend (Ctrl-C) while a tab is open. Its connection dot
   turns red ("Disconnected"), then amber ("Reconnecting…") as it retries with backoff. Restart the
   backend — the tab reconnects and its state refreshes automatically.

Without a `GEMINI_API_KEY`, everything above still works except step 4's conversation turn (the
status-dropdown broadcast doesn't need Gemini at all, so that alone is enough to see realtime
updates end-to-end with zero external credentials).

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

- Coordination intelligence's text-matching (linking a decision to its follow-up action, or a
  hypothesis to a statement acting on it) is a deterministic word-overlap heuristic, not semantic
  understanding — it catches cases with genuine shared vocabulary (as in the worked payment-outage
  example) but can miss a connection stated in fully different words, or rarely over-match on
  incidental shared terms. The one LLM-assisted check (missing-information gaps) was verified
  reachable the same way the M2 extraction call was (see below) but not verified to produce
  correct gap analysis against a live Gemini call in this environment.
- The Agora voice integration is implemented against Agora's documented API shapes and now
  verified against a real Agora project's published Conversational AI pipeline; there is still no
  agent-state persistence across backend restarts (stop the agent manually via the Agora Console,
  or start a fresh one in the same channel, if the backend restarts mid-session).
- The Gemini extraction call was verified reachable (a deliberately invalid key reached Google's
  server and failed only on auth, confirming the model id/request/schema are structurally
  correct) but never verified to produce a correct extraction, since no real `GEMINI_API_KEY` was
  available in this environment.
- The Agora → intelligence webhook adapter (`POST /api/agora/webhook/{incident_id}`) is
  best-effort: built from Agora's documented webhook payload shape, but never exercised against a
  real webhook delivery. It also can't attribute individual human speakers by name — Agora's
  documented transcript payload only distinguishes `role: "user"` vs `"assistant"`, so every human
  turn is currently logged under the generic speaker `"Participant"`. The tested, working path for
  now is the explicit `POST /api/incidents/{id}/conversation` endpoint (see above).
- Fact/hypothesis/conflict classification is a prompting discipline enforced by the extraction
  system prompt, not by independent verification code — it's only as reliable as the LLM's
  adherence to those instructions.
- The realtime WebSocket layer is a single in-process connection manager (no Redis/pub-sub) — by
  design for this MVP, but it means state only fans out to clients connected to *this* backend
  process, and a backend restart drops all live connections (they reconnect and refetch
  automatically, so this is a brief blip, not data loss — SQLite is still the source of truth).
- The dashboard (including the M4 Coordination panel) was verified via `next build`/dev-server HTML
  output, `pytest`'s WebSocket tests, and a live `uvicorn` + Node `WebSocket` client round trip —
  not in an actual browser (none available in this environment). Layout/visual behavior should be
  sanity-checked in a real browser before a live demo.
- Incident status has no transition rules (any status → any other) and no automated triggers —
  it's a direct, human-driven field, not a workflow engine, by design for this milestone.
- No persistent incident record beyond SQLite, no Slack/Jira/PagerDuty integration, no
  authentication, no human approval workflow, no spoken incident summaries — all intentionally out
  of scope until later milestones.
- Frontend has no automated test runner yet (lint + `tsc --noEmit` + `next build` only).

## Status

M0 (foundation), M1 (Agora voice MVP), M2 (incident intelligence), M3 (live incident dashboard),
and M4 (coordination intelligence) are complete: a runnable Next.js frontend + FastAPI backend
with SQLite wiring, health check, a working Agora RTC + Conversational AI voice room, a
Gemini-backed pipeline that turns conversation turns into structured facts/hypotheses/decisions/
actions/timeline/conflicts, a WebSocket-driven live dashboard that keeps every connected browser
in sync with that state, and a coordination layer that reasons over that state to surface unowned/
stale actions, decisions without follow-through, hypotheses being treated as fact, unresolved
risks, and (optionally, with Gemini) semantic information gaps — pending live-account verification
for the external integrations (Agora, Gemini) as detailed above. Next milestone: tool execution +
human confirmation (M5), and spoken coordination summaries (M6).

## Deployment

Backend on [Render](https://render.com) (free-tier web service), frontend on
[Vercel](https://vercel.com) — no Docker, SQLite unchanged.

### Backend (Render)

This repo includes `render.yaml` (Blueprint) at the repo root — in the Render dashboard, **New >
Blueprint**, point it at this repo, and Render creates the service from that file: Python
environment, `rootDir: backend`, `pip install -r requirements.txt`, and
`uvicorn app.main:app --host 0.0.0.0 --port $PORT` as the start command (the app itself has no
hardcoded host/port — this is the only thing that changes between local `--reload` and
production). `/health` is used as the health check path.

The Blueprint declares every env var `app/config.py` reads, but leaves secret-valued ones
(`sync: false`) empty — fill those in via the Render dashboard after the first deploy, **never**
by editing `render.yaml`:

- **Secrets to set on Render:** `GEMINI_API_KEY`, `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`,
  `AGORA_CUSTOMER_ID`, `AGORA_CUSTOMER_SECRET`, `AGORA_AGENT_PIPELINE_ID` — same names/meanings as
  `backend/.env.example`.
- **Non-secret, already set by the Blueprint:** `APP_ENV=production`, `GEMINI_MODEL`,
  `DATABASE_PATH`, `AGORA_CONVO_AI_BASE_URL`, `AGORA_TOKEN_EXPIRE_SECONDS`, `AGORA_AGENT_UID`.
- **Set once you know your Vercel URL:** `CORS_ORIGINS` — the frontend's production origin(s),
  comma-separated (e.g. `https://echoward.vercel.app`). `CORSMiddleware` matches these exactly, so
  a Vercel *preview* deployment (a different random subdomain per PR) won't have CORS access
  unless its exact URL is added too.

**Known free-tier limitations** (not addressed here — would need a paid plan or a datastore
change, both out of scope for this hackathon deployment): Render's free web services have an
**ephemeral filesystem**, so the SQLite file is wiped on every redeploy/restart — this is a
hackathon demo deployment, not a durable one. Free services also spin down after ~15 minutes idle;
the first request after that wakes it up (30–60s cold start) and drops any open WebSocket
connection, which the frontend already reconnects with backoff (`useIncidentStream.ts`) — just
give the backend a minute to wake up before a live demo.

### Frontend (Vercel)

Plain Next.js app, no config file needed — in the Vercel dashboard, **New Project**, import this
repo, and set:

- **Root Directory:** `frontend` (this is a monorepo — Vercel needs to be told where
  `package.json` is; there's no `vercel.json` equivalent for this, it's a project setting).
- **Environment variable:** `NEXT_PUBLIC_API_URL` = the Render backend's public URL (e.g.
  `https://echoward-backend.onrender.com`). `NEXT_PUBLIC_*` vars are inlined at build time, so this
  must be set before the first build. The WebSocket URL (`useIncidentStream.ts`) is derived from
  this same value (`http`→`ws`, `https`→`wss`) — no separate WS env var needed.

Nothing else changes: `next build` already passes as-is (no `output: "standalone"` or other
Docker-oriented config — that's unnecessary for a standard Vercel deployment).
