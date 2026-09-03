from datetime import UTC, datetime

from app import incident_db
from app.db import get_connection, init_db
from app.incident_models import ActionStatus, ConflictStatement, ConflictStatus


def test_create_and_get_incident() -> None:
    incident = incident_db.create_incident("Checkout errors spiking")
    fetched = incident_db.get_incident(incident.id)
    assert fetched is not None
    assert fetched.title == "Checkout errors spiking"
    assert fetched.status.value == "investigating"


def test_get_incident_state_composes_all_sections() -> None:
    incident = incident_db.create_incident("Composed state test")
    now = datetime.now(UTC)

    with get_connection() as conn:
        incident_db.insert_fact(conn, incident.id, "CPU at 95%", "Alice", now, "reported")
        incident_db.insert_hypothesis(conn, incident.id, "Pool exhaustion", "Bob", now)
        incident_db.insert_decision(conn, incident.id, "Roll back deploy", "Carol", now)
        incident_db.insert_action(conn, incident.id, "Roll back deploy", "Sarah", now)
        incident_db.insert_timeline_event(conn, incident.id, "Incident reported", "system", now)
        incident_db.insert_question(conn, incident.id, "Who owns billing?", now)
        incident_db.insert_conflict(
            conn,
            incident.id,
            "Database CPU",
            [
                ConflictStatement(source="Alice", statement="CPU at 95%"),
                ConflictStatement(source="Bob", statement="CPU is normal"),
            ],
            now,
        )

    state = incident_db.get_incident_state(incident.id)
    assert state is not None
    assert len(state.facts) == 1
    assert len(state.hypotheses) == 1
    assert len(state.decisions) == 1
    assert len(state.actions) == 1
    assert len(state.timeline) == 1
    assert len(state.unresolved_questions) == 1
    assert len(state.conflicts) == 1
    assert state.conflicts[0].involved_sources == ["Alice", "Bob"]


def test_get_incident_state_missing_incident_returns_none() -> None:
    assert incident_db.get_incident_state("nope") is None


def test_update_action_owner() -> None:
    incident = incident_db.create_incident("Ownership test")
    now = datetime.now(UTC)
    with get_connection() as conn:
        action = incident_db.insert_action(conn, incident.id, "Check logs", None, now)
        assert action.owner is None
        updated = incident_db.update_action_owner(conn, action.id, "Sarah", now)
        assert updated.owner == "Sarah"

    actions = incident_db.list_actions(incident.id)
    assert len(actions) == 1
    assert actions[0].owner == "Sarah"


def test_list_open_actions_excludes_completed() -> None:
    incident = incident_db.create_incident("Open actions test")
    now = datetime.now(UTC)
    with get_connection() as conn:
        incident_db.insert_action(conn, incident.id, "Pending task", None, now)
        completed = incident_db.insert_action(conn, incident.id, "Done task", "Bob", now)
        conn.execute(
            "UPDATE actions SET status = ? WHERE id = ?", (ActionStatus.completed.value, completed.id)
        )
        open_actions = incident_db.list_open_actions(conn, incident.id)
    assert len(open_actions) == 1
    assert open_actions[0].description == "Pending task"


def test_append_conflict_statements_merges_sources() -> None:
    incident = incident_db.create_incident("Conflict merge test")
    now = datetime.now(UTC)
    with get_connection() as conn:
        conflict = incident_db.insert_conflict(
            conn,
            incident.id,
            "Database CPU",
            [
                ConflictStatement(source="Alice", statement="CPU at 95%"),
                ConflictStatement(source="Bob", statement="CPU is normal"),
            ],
            now,
        )
        updated = incident_db.append_conflict_statements(
            conn, conflict.id, [ConflictStatement(source="Carol", statement="CPU looks fine to me too")]
        )
    assert len(updated.statements) == 3
    assert updated.involved_sources == ["Alice", "Bob", "Carol"]

    conflicts = incident_db.list_conflicts(incident.id)
    assert len(conflicts) == 1
    assert len(conflicts[0].statements) == 3


def test_update_conflict_status_resolves_it() -> None:
    incident = incident_db.create_incident("Conflict resolution test")
    now = datetime.now(UTC)
    with get_connection() as conn:
        conflict = incident_db.insert_conflict(
            conn,
            incident.id,
            "Database CPU",
            [
                ConflictStatement(source="Alice", statement="CPU at 95%"),
                ConflictStatement(source="Bob", statement="CPU is normal"),
            ],
            now,
        )
    assert conflict.status == ConflictStatus.unresolved

    updated = incident_db.update_conflict_status(conflict.id, ConflictStatus.resolved)
    assert updated is not None
    assert updated.status == ConflictStatus.resolved

    fetched = incident_db.get_conflict(conflict.id)
    assert fetched is not None
    assert fetched.status == ConflictStatus.resolved
    # The conflict's statements/topic are untouched by resolution - only status moves.
    assert len(fetched.statements) == 2


def test_get_conflict_missing_returns_none() -> None:
    assert incident_db.get_conflict("nope") is None


def test_incident_survives_reinitializing_the_schema() -> None:
    """Simulates a backend restart: init_db()/init_incident_schema() re-run against
    the same DB file, and previously written data is still there afterward."""
    incident = incident_db.create_incident("Restart survival test")
    now = datetime.now(UTC)
    with get_connection() as conn:
        incident_db.insert_fact(conn, incident.id, "Payments failing for 30% of users", "Alice", now, "reported")

    init_db()
    incident_db.init_incident_schema()

    fetched = incident_db.get_incident(incident.id)
    assert fetched is not None
    assert fetched.title == "Restart survival test"
    facts = incident_db.list_facts(incident.id)
    assert len(facts) == 1
    assert facts[0].statement == "Payments failing for 30% of users"
