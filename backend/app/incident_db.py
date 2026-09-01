import json
import sqlite3
import uuid
from datetime import UTC, datetime

from app.db import get_connection
from app.incident_models import (
    Action,
    ActionStatus,
    Conflict,
    ConflictStatement,
    ConflictStatus,
    CoordinationFinding,
    CoordinationFindingStatus,
    CoordinationFindingType,
    CoordinationSeverity,
    Decision,
    Fact,
    Hypothesis,
    HypothesisStatus,
    Incident,
    IncidentState,
    IncidentStatus,
    QuestionStatus,
    TimelineEvent,
    UnresolvedQuestion,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    statement TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    statement TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    decision TEXT NOT NULL,
    decided_by TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    description TEXT NOT NULL,
    owner TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS timeline_events (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unresolved_questions (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflicts (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    topic TEXT NOT NULL,
    statements TEXT NOT NULL,
    involved_sources TEXT NOT NULL,
    status TEXT NOT NULL,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coordination_findings (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    dedup_key TEXT NOT NULL,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    related_ids TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (incident_id, dedup_key)
);
"""


def init_incident_schema() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


# --- Incident --------------------------------------------------------------


def create_incident(title: str) -> Incident:
    now = _now()
    incident = Incident(
        id=_new_id(), title=title, status=IncidentStatus.investigating, created_at=now, updated_at=now
    )
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO incidents (id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (incident.id, incident.title, incident.status.value, _iso(now), _iso(now)),
        )
    return incident


def get_incident(incident_id: str) -> Incident | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    return _row_to_incident(row) if row else None


def touch_incident(conn: sqlite3.Connection, incident_id: str, when: datetime) -> None:
    conn.execute("UPDATE incidents SET updated_at = ? WHERE id = ?", (_iso(when), incident_id))


def update_incident_status(incident_id: str, status: IncidentStatus, when: datetime | None = None) -> Incident | None:
    """Set incident.status directly - the only sanctioned mutation of it (see IncidentStatus in
    incident_models.py: investigating/identified/mitigating/resolved, no ad hoc states)."""
    when = when or _now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _iso(when), incident_id),
        )
    return get_incident(incident_id)


def _row_to_incident(row: sqlite3.Row) -> Incident:
    return Incident(
        id=row["id"],
        title=row["title"],
        status=IncidentStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


# --- Facts -------------------------------------------------------------------


def insert_fact(
    conn: sqlite3.Connection, incident_id: str, statement: str, source: str, timestamp: datetime, confidence: str
) -> Fact:
    fact = Fact(
        id=_new_id(),
        incident_id=incident_id,
        statement=statement,
        source=source,
        timestamp=timestamp,
        confidence=confidence,
    )
    conn.execute(
        "INSERT INTO facts (id, incident_id, statement, source, timestamp, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (fact.id, incident_id, statement, source, _iso(timestamp), confidence),
    )
    return fact


def list_facts(incident_id: str) -> list[Fact]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM facts WHERE incident_id = ? ORDER BY timestamp ASC", (incident_id,)
        ).fetchall()
    return [
        Fact(
            id=r["id"],
            incident_id=r["incident_id"],
            statement=r["statement"],
            source=r["source"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
            confidence=r["confidence"],
        )
        for r in rows
    ]


# --- Hypotheses ----------------------------------------------------------------


def insert_hypothesis(
    conn: sqlite3.Connection, incident_id: str, statement: str, source: str, timestamp: datetime
) -> Hypothesis:
    hyp = Hypothesis(
        id=_new_id(),
        incident_id=incident_id,
        statement=statement,
        source=source,
        timestamp=timestamp,
        status=HypothesisStatus.proposed,
    )
    conn.execute(
        "INSERT INTO hypotheses (id, incident_id, statement, source, timestamp, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (hyp.id, incident_id, statement, source, _iso(timestamp), hyp.status.value),
    )
    return hyp


def list_hypotheses(incident_id: str) -> list[Hypothesis]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM hypotheses WHERE incident_id = ? ORDER BY timestamp ASC", (incident_id,)
        ).fetchall()
    return [
        Hypothesis(
            id=r["id"],
            incident_id=r["incident_id"],
            statement=r["statement"],
            source=r["source"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
            status=HypothesisStatus(r["status"]),
        )
        for r in rows
    ]


# --- Decisions -----------------------------------------------------------------


def insert_decision(
    conn: sqlite3.Connection,
    incident_id: str,
    decision_text: str,
    decided_by: str | None,
    timestamp: datetime,
) -> Decision:
    decision = Decision(
        id=_new_id(), incident_id=incident_id, decision=decision_text, decided_by=decided_by, timestamp=timestamp
    )
    conn.execute(
        "INSERT INTO decisions (id, incident_id, decision, decided_by, timestamp) VALUES (?, ?, ?, ?, ?)",
        (decision.id, incident_id, decision_text, decided_by, _iso(timestamp)),
    )
    return decision


def list_decisions(incident_id: str) -> list[Decision]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM decisions WHERE incident_id = ? ORDER BY timestamp ASC", (incident_id,)
        ).fetchall()
    return [
        Decision(
            id=r["id"],
            incident_id=r["incident_id"],
            decision=r["decision"],
            decided_by=r["decided_by"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
        )
        for r in rows
    ]


# --- Actions ---------------------------------------------------------------------


def insert_action(
    conn: sqlite3.Connection, incident_id: str, description: str, owner: str | None, when: datetime
) -> Action:
    action = Action(
        id=_new_id(),
        incident_id=incident_id,
        description=description,
        owner=owner,
        status=ActionStatus.pending,
        created_at=when,
        updated_at=when,
    )
    conn.execute(
        "INSERT INTO actions (id, incident_id, description, owner, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (action.id, incident_id, description, owner, action.status.value, _iso(when), _iso(when)),
    )
    return action


def update_action_owner(conn: sqlite3.Connection, action_id: str, owner: str, when: datetime) -> Action:
    conn.execute("UPDATE actions SET owner = ?, updated_at = ? WHERE id = ?", (owner, _iso(when), action_id))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    return _row_to_action(row)


def _row_to_action(row: sqlite3.Row) -> Action:
    return Action(
        id=row["id"],
        incident_id=row["incident_id"],
        description=row["description"],
        owner=row["owner"],
        status=ActionStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def list_actions(incident_id: str) -> list[Action]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM actions WHERE incident_id = ? ORDER BY created_at ASC", (incident_id,)
        ).fetchall()
    return [_row_to_action(r) for r in rows]


def list_open_actions(conn: sqlite3.Connection, incident_id: str) -> list[Action]:
    """Pending/in-progress actions, queried within an existing transaction (for dedup matching)."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM actions WHERE incident_id = ? AND status IN (?, ?) ORDER BY created_at ASC",
        (incident_id, ActionStatus.pending.value, ActionStatus.in_progress.value),
    ).fetchall()
    return [_row_to_action(r) for r in rows]


# --- Timeline --------------------------------------------------------------------


def insert_timeline_event(
    conn: sqlite3.Connection, incident_id: str, event: str, source: str, timestamp: datetime
) -> TimelineEvent:
    evt = TimelineEvent(id=_new_id(), incident_id=incident_id, timestamp=timestamp, event=event, source=source)
    conn.execute(
        "INSERT INTO timeline_events (id, incident_id, timestamp, event, source) VALUES (?, ?, ?, ?, ?)",
        (evt.id, incident_id, _iso(timestamp), event, source),
    )
    return evt


def list_timeline(incident_id: str) -> list[TimelineEvent]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM timeline_events WHERE incident_id = ? ORDER BY timestamp ASC", (incident_id,)
        ).fetchall()
    return [
        TimelineEvent(
            id=r["id"],
            incident_id=r["incident_id"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
            event=r["event"],
            source=r["source"],
        )
        for r in rows
    ]


# --- Unresolved questions ----------------------------------------------------------


def insert_question(conn: sqlite3.Connection, incident_id: str, question: str, when: datetime) -> UnresolvedQuestion:
    q = UnresolvedQuestion(
        id=_new_id(), incident_id=incident_id, question=question, status=QuestionStatus.open, created_at=when
    )
    conn.execute(
        "INSERT INTO unresolved_questions (id, incident_id, question, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (q.id, incident_id, question, q.status.value, _iso(when)),
    )
    return q


def list_questions(incident_id: str) -> list[UnresolvedQuestion]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM unresolved_questions WHERE incident_id = ? ORDER BY created_at ASC", (incident_id,)
        ).fetchall()
    return [
        UnresolvedQuestion(
            id=r["id"],
            incident_id=r["incident_id"],
            question=r["question"],
            status=QuestionStatus(r["status"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]


# --- Conflicts -----------------------------------------------------------------------


def insert_conflict(
    conn: sqlite3.Connection,
    incident_id: str,
    topic: str,
    statements: list[ConflictStatement],
    when: datetime,
) -> Conflict:
    involved = sorted({s.source for s in statements})
    conflict = Conflict(
        id=_new_id(),
        incident_id=incident_id,
        topic=topic,
        statements=statements,
        involved_sources=involved,
        status=ConflictStatus.unresolved,
        detected_at=when,
    )
    conn.execute(
        "INSERT INTO conflicts (id, incident_id, topic, statements, involved_sources, status, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            conflict.id,
            incident_id,
            topic,
            json.dumps([s.model_dump() for s in statements]),
            json.dumps(involved),
            conflict.status.value,
            _iso(when),
        ),
    )
    return conflict


def append_conflict_statements(
    conn: sqlite3.Connection, conflict_id: str, new_statements: list[ConflictStatement]
) -> Conflict:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM conflicts WHERE id = ?", (conflict_id,)).fetchone()
    existing = _row_to_conflict(row)
    merged_statements = existing.statements + new_statements
    involved = sorted({s.source for s in merged_statements})
    conn.execute(
        "UPDATE conflicts SET statements = ?, involved_sources = ? WHERE id = ?",
        (
            json.dumps([s.model_dump() for s in merged_statements]),
            json.dumps(involved),
            conflict_id,
        ),
    )
    return existing.model_copy(update={"statements": merged_statements, "involved_sources": involved})


def _row_to_conflict(row: sqlite3.Row) -> Conflict:
    return Conflict(
        id=row["id"],
        incident_id=row["incident_id"],
        topic=row["topic"],
        statements=[ConflictStatement(**s) for s in json.loads(row["statements"])],
        involved_sources=json.loads(row["involved_sources"]),
        status=ConflictStatus(row["status"]),
        detected_at=datetime.fromisoformat(row["detected_at"]),
    )


def list_conflicts(incident_id: str) -> list[Conflict]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM conflicts WHERE incident_id = ? ORDER BY detected_at ASC", (incident_id,)
        ).fetchall()
    return [_row_to_conflict(r) for r in rows]


def list_open_conflicts(conn: sqlite3.Connection, incident_id: str) -> list[Conflict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM conflicts WHERE incident_id = ? AND status = ? ORDER BY detected_at ASC",
        (incident_id, ConflictStatus.unresolved.value),
    ).fetchall()
    return [_row_to_conflict(r) for r in rows]


# --- Coordination findings (M4) -----------------------------------------------
# One row per (incident, dedup_key) - app/coordination.py owns dedup_key format
# and is the only writer that decides *when* to upsert/resolve a finding; this
# module only knows how to persist/read whatever it's given.

_SEVERITY_RANK_SQL = "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END"


def _row_to_coordination_finding(row: sqlite3.Row) -> CoordinationFinding:
    return CoordinationFinding(
        id=row["id"],
        incident_id=row["incident_id"],
        dedup_key=row["dedup_key"],
        type=CoordinationFindingType(row["type"]),
        severity=CoordinationSeverity(row["severity"]),
        title=row["title"],
        description=row["description"],
        related_ids=json.loads(row["related_ids"]),
        status=CoordinationFindingStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def upsert_coordination_finding(
    conn: sqlite3.Connection,
    incident_id: str,
    dedup_key: str,
    type_: CoordinationFindingType,
    severity: CoordinationSeverity,
    title: str,
    description: str,
    related_ids: list[str],
    when: datetime,
) -> CoordinationFinding:
    """Insert a new finding, or update the existing one for (incident_id, dedup_key)

    in place - re-opening it if it had been resolved. A no-op (no write) if the
    content is already identical and already open, so `updated_at` doesn't churn
    on every analysis pass when nothing actually changed.
    """
    conn.row_factory = sqlite3.Row
    related_json = json.dumps(related_ids)
    existing = conn.execute(
        "SELECT * FROM coordination_findings WHERE incident_id = ? AND dedup_key = ?",
        (incident_id, dedup_key),
    ).fetchone()

    if existing is not None:
        unchanged = (
            existing["type"] == type_.value
            and existing["severity"] == severity.value
            and existing["title"] == title
            and existing["description"] == description
            and existing["related_ids"] == related_json
            and existing["status"] == CoordinationFindingStatus.open.value
        )
        if unchanged:
            return _row_to_coordination_finding(existing)
        conn.execute(
            "UPDATE coordination_findings SET type = ?, severity = ?, title = ?, description = ?, "
            "related_ids = ?, status = ?, updated_at = ? WHERE id = ?",
            (
                type_.value,
                severity.value,
                title,
                description,
                related_json,
                CoordinationFindingStatus.open.value,
                _iso(when),
                existing["id"],
            ),
        )
        row = conn.execute(
            "SELECT * FROM coordination_findings WHERE id = ?", (existing["id"],)
        ).fetchone()
        return _row_to_coordination_finding(row)

    finding = CoordinationFinding(
        id=_new_id(),
        incident_id=incident_id,
        dedup_key=dedup_key,
        type=type_,
        severity=severity,
        title=title,
        description=description,
        related_ids=related_ids,
        status=CoordinationFindingStatus.open,
        created_at=when,
        updated_at=when,
    )
    conn.execute(
        "INSERT INTO coordination_findings (id, incident_id, dedup_key, type, severity, title, "
        "description, related_ids, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            finding.id,
            incident_id,
            dedup_key,
            type_.value,
            severity.value,
            title,
            description,
            related_json,
            finding.status.value,
            _iso(when),
            _iso(when),
        ),
    )
    return finding


def resolve_coordination_finding(conn: sqlite3.Connection, finding_id: str, when: datetime) -> None:
    """Mark a finding resolved - a no-op if it's already resolved."""
    conn.execute(
        "UPDATE coordination_findings SET status = ?, updated_at = ? WHERE id = ? AND status != ?",
        (
            CoordinationFindingStatus.resolved.value,
            _iso(when),
            finding_id,
            CoordinationFindingStatus.resolved.value,
        ),
    )


def list_coordination_findings(incident_id: str) -> list[CoordinationFinding]:
    """All findings regardless of status, for reconciliation - see app/coordination.py."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM coordination_findings WHERE incident_id = ? ORDER BY created_at ASC",
            (incident_id,),
        ).fetchall()
    return [_row_to_coordination_finding(r) for r in rows]


def list_open_coordination_findings(incident_id: str) -> list[CoordinationFinding]:
    """Open findings only, most severe first - what the dashboard/API actually shows."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM coordination_findings WHERE incident_id = ? AND status = ? "
            f"ORDER BY {_SEVERITY_RANK_SQL} ASC, created_at DESC",
            (incident_id, CoordinationFindingStatus.open.value),
        ).fetchall()
    return [_row_to_coordination_finding(r) for r in rows]


# --- Full state --------------------------------------------------------------------


def get_incident_state(incident_id: str) -> IncidentState | None:
    incident = get_incident(incident_id)
    if incident is None:
        return None
    return IncidentState(
        incident=incident,
        facts=list_facts(incident_id),
        hypotheses=list_hypotheses(incident_id),
        decisions=list_decisions(incident_id),
        actions=list_actions(incident_id),
        timeline=list_timeline(incident_id),
        unresolved_questions=list_questions(incident_id),
        conflicts=list_conflicts(incident_id),
        coordination_findings=list_open_coordination_findings(incident_id),
    )
