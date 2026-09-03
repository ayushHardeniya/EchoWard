from datetime import UTC, datetime

import pytest

from app import incident_db, intelligence
from app.db import get_connection
from app.incident_models import (
    ConversationAnalysis,
    ExtractedAction,
    ExtractedConflict,
    ExtractedConflictStatement,
    ExtractedDecision,
    ExtractedFact,
    ExtractedHypothesis,
    ExtractedQuestion,
    ExtractedTimelineEvent,
)
from app.llm import LLMOutputError


def _now() -> datetime:
    return datetime.now(UTC)


# --- apply_analysis: persistence of each extracted item type -----------------------


def test_apply_analysis_persists_fact() -> None:
    incident = incident_db.create_incident("Fact persistence test")
    analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Payments failing for ~30% of users")])
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "Alice", analysis, _now())

    assert len(changes.facts) == 1
    assert changes.facts[0].source == "Alice"
    assert not changes.is_empty
    assert len(changes.timeline_events) == 1

    state = incident_db.get_incident_state(incident.id)
    assert state is not None
    assert len(state.facts) == 1
    assert state.incident.updated_at >= state.incident.created_at


def test_apply_analysis_persists_hypothesis() -> None:
    incident = incident_db.create_incident("Hypothesis persistence test")
    analysis = ConversationAnalysis(hypotheses=[ExtractedHypothesis(statement="Connection pool exhaustion")])
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "Bob", analysis, _now())

    assert len(changes.hypotheses) == 1
    assert changes.hypotheses[0].source == "Bob"
    assert changes.hypotheses[0].status.value == "proposed"


def test_apply_analysis_decision_defaults_decided_by_to_speaker() -> None:
    incident = incident_db.create_incident("Decision default test")
    analysis = ConversationAnalysis(decisions=[ExtractedDecision(decision="Roll back the deployment")])
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "Carol", analysis, _now())

    assert len(changes.decisions) == 1
    assert changes.decisions[0].decided_by == "Carol"


def test_apply_analysis_decision_respects_explicit_decided_by() -> None:
    incident = incident_db.create_incident("Decision explicit test")
    analysis = ConversationAnalysis(
        decisions=[ExtractedDecision(decision="Roll back the deployment", decided_by="Dana")]
    )
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "Carol", analysis, _now())

    assert changes.decisions[0].decided_by == "Dana"


def test_apply_analysis_creates_action_with_owner() -> None:
    incident = incident_db.create_incident("Action with owner test")
    analysis = ConversationAnalysis(
        actions=[ExtractedAction(description="Check payment service logs", owner="Sarah")]
    )
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "David", analysis, _now())

    assert len(changes.actions_created) == 1
    assert changes.actions_created[0].owner == "Sarah"
    assert changes.actions_created[0].status.value == "pending"


def test_apply_analysis_creates_action_without_owner_preserves_unresolved_ownership() -> None:
    incident = incident_db.create_incident("Unowned action test")
    analysis = ConversationAnalysis(actions=[ExtractedAction(description="Check payment service logs")])
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "David", analysis, _now())

    assert len(changes.actions_created) == 1
    assert changes.actions_created[0].owner is None


def test_apply_analysis_records_unresolved_question() -> None:
    incident = incident_db.create_incident("Question test")
    analysis = ConversationAnalysis(unresolved_questions=[ExtractedQuestion(question="Who owns billing?")])
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "Alice", analysis, _now())

    assert len(changes.unresolved_questions) == 1
    assert changes.unresolved_questions[0].status.value == "open"


def test_apply_analysis_records_explicit_timeline_event() -> None:
    incident = incident_db.create_incident("Timeline test")
    analysis = ConversationAnalysis(timeline_events=[ExtractedTimelineEvent(event="Incident reported")])
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "Alice", analysis, _now())

    assert len(changes.timeline_events) == 1
    assert changes.timeline_events[0].event == "Incident reported"


def test_apply_analysis_empty_result_makes_no_changes() -> None:
    incident = incident_db.create_incident("No-op test")
    analysis = ConversationAnalysis()
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "Alice", analysis, _now())

    assert changes.is_empty
    state = incident_db.get_incident_state(incident.id)
    assert state is not None
    assert state.incident.updated_at == state.incident.created_at


# --- Action deduplication -----------------------------------------------------------


def test_apply_analysis_assigns_owner_to_existing_unowned_action() -> None:
    incident = incident_db.create_incident("Action dedup test")
    first = ConversationAnalysis(
        actions=[ExtractedAction(description="Roll back the latest payment deployment")]
    )
    second = ConversationAnalysis(
        actions=[ExtractedAction(description="Roll back the latest payment deployment", owner="Sarah")]
    )
    with get_connection() as conn:
        intelligence.apply_analysis(conn, incident.id, "Carol", first, _now())
        changes2 = intelligence.apply_analysis(conn, incident.id, "Carol", second, _now())

    assert len(changes2.actions_created) == 0
    assert len(changes2.actions_updated) == 1
    assert changes2.actions_updated[0].owner == "Sarah"

    actions = incident_db.list_actions(incident.id)
    assert len(actions) == 1  # updated in place, not duplicated
    assert actions[0].owner == "Sarah"


def test_apply_analysis_ignores_repeated_action_mention_without_new_info() -> None:
    incident = incident_db.create_incident("Repeat mention test")
    analysis = ConversationAnalysis(actions=[ExtractedAction(description="Check payment service logs")])
    with get_connection() as conn:
        intelligence.apply_analysis(conn, incident.id, "David", analysis, _now())
        changes2 = intelligence.apply_analysis(conn, incident.id, "David", analysis, _now())

    assert len(changes2.actions_created) == 0
    assert len(changes2.actions_updated) == 0
    assert len(incident_db.list_actions(incident.id)) == 1


# --- Conflict handling ----------------------------------------------------------------


def test_apply_analysis_creates_conflict_with_both_statements() -> None:
    incident = incident_db.create_incident("Conflict creation test")
    analysis = ConversationAnalysis(
        conflicts=[
            ExtractedConflict(
                topic="Database CPU",
                statements=[
                    ExtractedConflictStatement(source="Alice", statement="Database CPU is at 95%"),
                    ExtractedConflictStatement(source="Bob", statement="Database CPU is normal"),
                ],
            )
        ]
    )
    with get_connection() as conn:
        changes = intelligence.apply_analysis(conn, incident.id, "Bob", analysis, _now())

    assert len(changes.conflicts_created) == 1
    conflict = changes.conflicts_created[0]
    assert conflict.status.value == "unresolved"
    assert conflict.involved_sources == ["Alice", "Bob"]
    assert len(conflict.statements) == 2


def test_apply_analysis_merges_conflict_on_same_topic() -> None:
    incident = incident_db.create_incident("Conflict merge test")
    first = ConversationAnalysis(
        conflicts=[
            ExtractedConflict(
                topic="Database CPU",
                statements=[
                    ExtractedConflictStatement(source="Alice", statement="Database CPU is at 95%"),
                    ExtractedConflictStatement(source="Bob", statement="Database CPU is normal"),
                ],
            )
        ]
    )
    second = ConversationAnalysis(
        conflicts=[
            ExtractedConflict(
                topic="Database CPU",
                statements=[ExtractedConflictStatement(source="Carol", statement="CPU looks fine on my end too")],
            )
        ]
    )
    with get_connection() as conn:
        intelligence.apply_analysis(conn, incident.id, "Bob", first, _now())
        changes2 = intelligence.apply_analysis(conn, incident.id, "Carol", second, _now())

    assert len(changes2.conflicts_created) == 0
    assert len(changes2.conflicts_updated) == 1

    conflicts = incident_db.list_conflicts(incident.id)
    assert len(conflicts) == 1
    assert len(conflicts[0].statements) == 3
    assert conflicts[0].involved_sources == ["Alice", "Bob", "Carol"]


# --- Deterministic fact-vs-hypothesis contradiction detection ------------------------
# The LLM's own conflict detection (EXTRACTION_INSTRUCTIONS) only sees ONE new
# statement per call and can classify it as a plain FACT without also flagging a
# conflict against an existing HYPOTHESIS - these tests exercise the narrow,
# deterministic backstop for exactly that case (see intelligence._detect_contradiction),
# entirely separate from (and never invoking) the LLM.


def test_contradictory_fact_creates_conflict_against_existing_hypothesis() -> None:
    incident = incident_db.create_incident("Contradiction detection test")
    hypothesis_analysis = ConversationAnalysis(
        hypotheses=[ExtractedHypothesis(statement="The payment database is overloaded.")]
    )
    fact_analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Database metrics look normal.")])

    with get_connection() as conn:
        intelligence.apply_analysis(conn, incident.id, "Alice", hypothesis_analysis, _now())
        changes = intelligence.apply_analysis(conn, incident.id, "Bob", fact_analysis, _now())

    assert len(changes.conflicts_created) == 1
    conflict = changes.conflicts_created[0]
    # Requirement 3: created unresolved, and stays that way until a human acts.
    assert conflict.status.value == "unresolved"
    assert conflict.involved_sources == ["Alice", "Bob"]
    statements = {s.statement for s in conflict.statements}
    assert "The payment database is overloaded." in statements
    assert "Database metrics look normal." in statements

    # EchoWard must not decide who's right or invent a root cause - the
    # hypothesis itself is untouched (still proposed, never promoted to a fact).
    hypotheses = incident_db.list_hypotheses(incident.id)
    assert len(hypotheses) == 1
    assert hypotheses[0].status.value == "proposed"

    state = incident_db.get_incident_state(incident.id)
    assert state is not None
    assert len(state.conflicts) == 1
    assert state.conflicts[0].status.value == "unresolved"


def test_contradictory_fact_does_not_duplicate_conflict_on_repeated_processing() -> None:
    incident = incident_db.create_incident("Contradiction dedup test")
    hypothesis_analysis = ConversationAnalysis(
        hypotheses=[ExtractedHypothesis(statement="The payment database is overloaded.")]
    )
    fact_analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Database metrics look normal.")])

    with get_connection() as conn:
        intelligence.apply_analysis(conn, incident.id, "Alice", hypothesis_analysis, _now())
        changes1 = intelligence.apply_analysis(conn, incident.id, "Bob", fact_analysis, _now())
        changes2 = intelligence.apply_analysis(conn, incident.id, "Bob", fact_analysis, _now())

    assert len(changes1.conflicts_created) == 1
    assert len(changes2.conflicts_created) == 0
    assert len(changes2.conflicts_updated) == 1  # merged into the same conflict, not duplicated

    conflicts = incident_db.list_conflicts(incident.id)
    assert len(conflicts) == 1
    assert conflicts[0].status.value == "unresolved"


def test_unrelated_hypothesis_and_fact_do_not_create_a_conflict() -> None:
    incident = incident_db.create_incident("Unrelated statements test")
    hypothesis_analysis = ConversationAnalysis(
        hypotheses=[ExtractedHypothesis(statement="The payment database is overloaded.")]
    )
    fact_analysis = ConversationAnalysis(facts=[ExtractedFact(statement="The login page CSS is broken.")])

    with get_connection() as conn:
        intelligence.apply_analysis(conn, incident.id, "Alice", hypothesis_analysis, _now())
        changes = intelligence.apply_analysis(conn, incident.id, "Bob", fact_analysis, _now())

    assert len(changes.conflicts_created) == 0
    assert incident_db.list_conflicts(incident.id) == []


def test_same_subject_fact_without_opposite_state_does_not_create_a_conflict() -> None:
    # Sharing a subject word alone must not be enough - only an actual curated
    # opposite-state pair (see _CONTRADICTORY_STATE_PAIRS) should ever fire.
    incident = incident_db.create_incident("Same subject, no contradiction test")
    hypothesis_analysis = ConversationAnalysis(
        hypotheses=[ExtractedHypothesis(statement="The payment database is overloaded.")]
    )
    fact_analysis = ConversationAnalysis(
        facts=[ExtractedFact(statement="The payment database was restarted at noon.")]
    )

    with get_connection() as conn:
        intelligence.apply_analysis(conn, incident.id, "Alice", hypothesis_analysis, _now())
        changes = intelligence.apply_analysis(conn, incident.id, "Bob", fact_analysis, _now())

    assert len(changes.conflicts_created) == 0


# --- Cross-detection-path conflict dedup ----------------------------------------------
# The LLM's own CONFLICT extraction and the deterministic fact-vs-hypothesis backstop
# above can both fire for the identical underlying contradiction in the same turn (the
# LLM might extract a statement as both a FACT and a CONFLICT at once) - _same_conflict
# is what makes them merge into one Conflict instead of two. Topic text alone can't do
# this: the LLM picks its own short topic label while the backstop's is derived from
# the hypothesis statement, so they rarely fuzzy-match each other - only the underlying
# *statement* text (which both paths draw from the same source-of-truth records) does.


def test_llm_and_deterministic_conflict_for_same_contradiction_merge_into_one() -> None:
    incident = incident_db.create_incident("Cross-detection conflict merge test")
    hypothesis_analysis = ConversationAnalysis(
        hypotheses=[ExtractedHypothesis(statement="The payment database is overloaded.")]
    )
    # Same turn: the LLM extracts the new statement as both a FACT and its own
    # CONFLICT (a differently-worded topic than the backstop would produce) -
    # this is what previously produced two separate Conflict rows.
    turn_analysis = ConversationAnalysis(
        facts=[ExtractedFact(statement="Database metrics look normal.")],
        conflicts=[
            ExtractedConflict(
                topic="Payment database health",
                statements=[
                    ExtractedConflictStatement(source="Alice", statement="The payment database is overloaded."),
                    ExtractedConflictStatement(source="Bob", statement="Database metrics look normal."),
                ],
            )
        ],
    )

    with get_connection() as conn:
        intelligence.apply_analysis(conn, incident.id, "Alice", hypothesis_analysis, _now())
        changes = intelligence.apply_analysis(conn, incident.id, "Bob", turn_analysis, _now())
        # Repeated processing of the same combined turn must stay idempotent too.
        changes_repeat = intelligence.apply_analysis(conn, incident.id, "Bob", turn_analysis, _now())

    # One created (whichever path ran first this turn), one merged into it - not two creates.
    assert len(changes.conflicts_created) == 1
    assert len(changes.conflicts_updated) == 1
    assert len(changes_repeat.conflicts_created) == 0

    conflicts = incident_db.list_conflicts(incident.id)
    assert len(conflicts) == 1
    assert conflicts[0].status.value == "unresolved"


def test_deterministic_conflict_does_not_merge_into_unrelated_existing_conflict() -> None:
    incident = incident_db.create_incident("Unrelated conflicts stay separate test")
    unrelated_conflict_analysis = ConversationAnalysis(
        conflicts=[
            ExtractedConflict(
                topic="Checkout latency",
                statements=[
                    ExtractedConflictStatement(source="Carol", statement="Checkout latency spiked to 4 seconds."),
                    ExtractedConflictStatement(source="Dave", statement="Checkout latency looks normal to me."),
                ],
            )
        ]
    )
    hypothesis_analysis = ConversationAnalysis(
        hypotheses=[ExtractedHypothesis(statement="The payment database is overloaded.")]
    )
    fact_analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Database metrics look normal.")])

    with get_connection() as conn:
        intelligence.apply_analysis(conn, incident.id, "Carol", unrelated_conflict_analysis, _now())
        intelligence.apply_analysis(conn, incident.id, "Alice", hypothesis_analysis, _now())
        changes = intelligence.apply_analysis(conn, incident.id, "Bob", fact_analysis, _now())

    assert len(changes.conflicts_created) == 1  # a new, second conflict - not merged into the unrelated one

    conflicts = incident_db.list_conflicts(incident.id)
    assert len(conflicts) == 2
    topics = {c.topic for c in conflicts}
    assert "Checkout latency" in topics
    assert all(c.status.value == "unresolved" for c in conflicts)


# --- process_conversation_turn: LLM-call error handling ------------------------------


def test_process_conversation_turn_raises_when_incident_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(intelligence.IncidentNotFoundError):
        intelligence.process_conversation_turn("does-not-exist", "Alice", "hello")


def test_process_conversation_turn_propagates_llm_error_without_writing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = incident_db.create_incident("Malformed output test")

    def _boom(incident_id: str, speaker: str, text: str) -> ConversationAnalysis:
        raise LLMOutputError("Gemini returned invalid JSON")

    monkeypatch.setattr(intelligence, "analyze_conversation", _boom)

    with pytest.raises(LLMOutputError):
        intelligence.process_conversation_turn(incident.id, "Alice", "garbled input")

    state = incident_db.get_incident_state(incident.id)
    assert state is not None
    assert state.facts == []
    assert state.timeline == []
    assert state.incident.updated_at == state.incident.created_at


def test_process_conversation_turn_no_actionable_information(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = incident_db.create_incident("Small talk test")
    monkeypatch.setattr(
        intelligence, "analyze_conversation", lambda incident_id, speaker, text: ConversationAnalysis()
    )

    result = intelligence.process_conversation_turn(incident.id, "Alice", "Good morning everyone.")
    assert result.changes.is_empty
    assert result.state.facts == []
    assert result.state.incident.updated_at == result.state.incident.created_at


# --- Full example scenario from the M2 spec -------------------------------------------


def test_full_incident_scenario_produces_coherent_state(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = incident_db.create_incident("Payments incident")

    turns = [
        (
            "Alice",
            "Payments are failing for about 30% of users.",
            ConversationAnalysis(
                facts=[ExtractedFact(statement="Payments are failing for approximately 30% of users.")]
            ),
        ),
        (
            "Bob",
            "I think the database connection pool is exhausted.",
            ConversationAnalysis(
                hypotheses=[
                    ExtractedHypothesis(
                        statement="Database connection pool exhaustion may be contributing to the incident."
                    )
                ]
            ),
        ),
        (
            "Dana",
            "Database CPU is actually normal.",
            ConversationAnalysis(
                conflicts=[
                    ExtractedConflict(
                        topic="Database health",
                        statements=[
                            ExtractedConflictStatement(
                                source="Bob", statement="Database connection pool may be exhausted"
                            ),
                            ExtractedConflictStatement(source="Dana", statement="Database CPU is normal"),
                        ],
                    )
                ]
            ),
        ),
        (
            "Carol",
            "Let's roll back the latest payment deployment.",
            ConversationAnalysis(
                decisions=[ExtractedDecision(decision="Roll back the latest payment service deployment.")]
            ),
        ),
        (
            "Carol",
            "Sarah, you handle the rollback.",
            ConversationAnalysis(
                actions=[
                    ExtractedAction(
                        description="Roll back the latest payment service deployment", owner="Sarah"
                    )
                ]
            ),
        ),
    ]

    call_log: list[tuple[str, str]] = []

    def fake_analyze(incident_id: str, speaker: str, text: str) -> ConversationAnalysis:
        call_log.append((speaker, text))
        for s, t, analysis in turns:
            if s == speaker and t == text:
                return analysis
        raise AssertionError(f"Unexpected turn: {speaker!r} {text!r}")

    monkeypatch.setattr(intelligence, "analyze_conversation", fake_analyze)

    for speaker, text, _ in turns:
        intelligence.process_conversation_turn(incident.id, speaker, text)

    assert len(call_log) == 5

    state = incident_db.get_incident_state(incident.id)
    assert state is not None

    assert len(state.facts) == 1
    assert "30%" in state.facts[0].statement
    assert state.facts[0].source == "Alice"

    assert len(state.hypotheses) == 1
    assert "connection pool" in state.hypotheses[0].statement.lower()
    assert state.hypotheses[0].source == "Bob"

    assert len(state.conflicts) == 1
    assert state.conflicts[0].status.value == "unresolved"
    assert state.conflicts[0].involved_sources == ["Bob", "Dana"]
    assert len(state.conflicts[0].statements) == 2

    assert len(state.decisions) == 1
    assert "roll back" in state.decisions[0].decision.lower()
    assert state.decisions[0].decided_by == "Carol"

    assert len(state.actions) == 1
    assert state.actions[0].owner == "Sarah"
    assert state.actions[0].status.value == "pending"

    assert len(state.timeline) >= 5  # one entry per created item, at minimum
    assert state.incident.updated_at > state.incident.created_at
