# 001 — Lightweight Architecture

## Context

EchoWard needed to go from zero to a working voice-native incident commander within a hackathon
timeline, with one incident room and one backend instance running at a time for the demo. The
temptation in an "AI agent" project is to reach for a multi-service, message-queue-backed
architecture up front.

## Decision

Build EchoWard as a single Next.js frontend, a single FastAPI backend process, and a single SQLite
database file — no ORM, no message queue, no Redis/pub-sub, no Kubernetes, no multi-agent
framework. Realtime fanout is an in-process WebSocket connection manager; the intelligence and
coordination layers are plain Python modules called directly from the API layer.

## Why

- The actual concurrency requirement is small: one incident room, a handful of dashboard clients
  watching one incident at a time. A single process with one shared SQLite file satisfies that with
  far less to build, deploy, and debug than a distributed system.
- Every extra moving part (a queue, a cache, a second service) is another thing that can fail
  independently during a live demo, for no functional benefit at this scale.
- Free-tier deployment (Render + Vercel) is straightforward for exactly this shape — a single web
  service and a static/SSR frontend — and would need significantly more infrastructure work for a
  distributed design.
- SQLite via the stdlib `sqlite3` module, with one table per record type and no migration
  framework beyond a couple of idempotent `ALTER TABLE` statements, is sufficient for a schema this
  small and this stable.

## Consequences

- The realtime layer is scoped to a single backend process; a restart drops all live WebSocket
  connections (clients reconnect and refetch automatically — a brief blip, not data loss, since
  SQLite remains the source of truth).
- Render's free-tier ephemeral filesystem means the SQLite file is wiped on every redeploy/restart —
  acceptable for a demo deployment, not for a durable production one.
- There is no connection pooling, read replica, or horizontal scaling story — if EchoWard ever
  needed to serve many concurrent incident rooms across multiple backend instances, the realtime
  fanout and SQLite persistence would both need to be revisited (e.g. a shared pub/sub and a
  networked database). That work is explicitly out of scope for this hackathon submission.
