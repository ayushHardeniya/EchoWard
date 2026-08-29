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
   backend's health status (service, environment, database connection).

## Status

Phase 1 (foundation) complete: runnable Next.js frontend + FastAPI backend with SQLite wiring,
health check, and basic tests. Agora voice integration and incident intelligence are not yet
implemented — see CLAUDE.md for the current milestone.
