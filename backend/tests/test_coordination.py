"""M4 coordination-intelligence tests.

Split into two layers, matching app/coordination.py:
- Pure unit tests of `analyze_incident_deterministic` against hand-built
  IncidentState objects (no DB, no LLM, no API).
- Integration tests through the real API (incident_db + FastAPI routes) that
  exercise persistence, dedup/reconciliation, and the realtime broadcast.

The optional LLM-backed missing-information check is always mocked, never
called for real - same convention as test_intelligence.py.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import coordination
from app.coordination import CoordinationGapAnalysis, analyze_incident_deterministic
from app.incident_models import (
    Action,
    ActionStatus,
    Conflict,
    ConflictStatement,
    ConflictStatus,
    ConversationAnalysis,
    CoordinationFindingType,
    Decision,
    ExtractedAction,
    ExtractedCoordinationGap,
    Hypothesis,
    HypothesisStatus,
    Incident,
    IncidentState,
    IncidentStatus,
    UnresolvedQuestion,
)
from app.main import app

client = TestClient(app)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _incident(**overrides) -> Incident:
    defaults = dict(
        id="inc-1", title="Payments outage", status=IncidentStatus.investigating, created_at=T0, updated_at=T0
    )
    defaults.update(overrides)
    return Incident(**defaults)


def _state(**overrides) -> IncidentState:
    defaults = dict(incident=_incident())
    defaults.update(overrides)
    return IncidentState(**defaults)


# --- Pure deterministic-analysis tests -------------------------------------------


def test_unresolved_conflict_produces_finding() -> None:
    conflict = Conflict(
        id="c1",
        incident_id="inc-1",
        topic="Database load",
        statements=[
            ConflictStatement(source="Engineer", statement="The database is overloaded."),
            ConflictStatement(source="Support", statement="The database dashboard looks normal."),
        ],
        involved_sources=["Engineer", "Support"],
        status=ConflictStatus.unresolved,
        detected_at=T0,
    )
    state = _state(conflicts=[conflict])

    findings = analyze_incident_deterministic(state)
    conflict_findings = [f for f in findings if f.type == CoordinationFindingType.conflict]
    assert len(conflict_findings) == 1
    assert conflict_findings[0].dedup_key == "conflict:c1"
    assert "Engineer" in conflict_findings[0].description
    assert "Support" in conflict_findings[0].description


def test_resolved_conflict_produces_no_finding() -> None:
    conflict = Conflict(
        id="c1",
        incident_id="inc-1",
        topic="Database load",
        statements=[ConflictStatement(source="Engineer", statement="The database is overloaded.")],
        involved_sources=["Engineer"],
        status=ConflictStatus.resolved,
        detected_at=T0,
    )
    findings = analyze_incident_deterministic(_state(conflicts=[conflict]))
    assert not any(f.type == CoordinationFindingType.conflict for f in findings)


def test_unowned_action_is_detected() -> None:
    action = Action(
        id="a1", incident_id="inc-1", description="Roll back payment service.", owner=None,
        status=ActionStatus.pending, created_at=T0, updated_at=T0,
    )
    findings = analyze_incident_deterministic(_state(actions=[action]))
    unowned = [f for f in findings if f.type == CoordinationFindingType.unowned_action]
    assert len(unowned) == 1
    assert unowned[0].dedup_key == "unowned_action:a1"
    assert "Roll back payment service" in unowned[0].description


def test_owned_action_produces_no_unowned_finding() -> None:
    action = Action(
        id="a1", incident_id="inc-1", description="Roll back payment service.", owner="Bob",
        status=ActionStatus.pending, created_at=T0, updated_at=T0,
    )
    findings = analyze_incident_deterministic(_state(actions=[action]))
    assert not any(f.type == CoordinationFindingType.unowned_action for f in findings)


def test_stale_action_detected_when_conversation_moves_on() -> None:
    action = Action(
        id="a1", incident_id="inc-1", description="Roll back payment service.", owner="Bob",
        status=ActionStatus.pending, created_at=T0, updated_at=T0,
    )
    later_hypothesis = Hypothesis(
        id="h1", incident_id="inc-1", statement="Maybe the cache is cold.", source="Alice",
        timestamp=T0 + timedelta(minutes=5),
    )
    findings = analyze_incident_deterministic(_state(actions=[action], hypotheses=[later_hypothesis]))
    stale = [f for f in findings if f.type == CoordinationFindingType.stale_action]
    assert len(stale) == 1
    assert stale[0].dedup_key == "stale_action:a1"


def test_action_not_stale_without_later_activity() -> None:
    action = Action(
        id="a1", incident_id="inc-1", description="Roll back payment service.", owner="Bob",
        status=ActionStatus.pending, created_at=T0, updated_at=T0,
    )
    findings = analyze_incident_deterministic(_state(actions=[action]))
    assert not any(f.type == CoordinationFindingType.stale_action for f in findings)


def test_decision_without_any_action_produces_followup_finding() -> None:
    decision = Decision(id="d1", incident_id="inc-1", decision="Proceed with rollback.", decided_by="Bob", timestamp=T0)
    findings = analyze_incident_deterministic(_state(decisions=[decision]))
    followups = [f for f in findings if f.type == CoordinationFindingType.decision_followup]
    assert len(followups) == 1
    assert followups[0].dedup_key == "decision_followup:d1"
    assert "no action" in followups[0].description.lower()


def test_decision_with_unowned_matching_action_produces_owner_finding() -> None:
    decision = Decision(
        id="d1", incident_id="inc-1", decision="Proceed with payment rollback.", decided_by="Bob", timestamp=T0
    )
    action = Action(
        id="a1", incident_id="inc-1", description="Rollback payment service deployment.", owner=None,
        status=ActionStatus.pending, created_at=T0, updated_at=T0,
    )
    findings = analyze_incident_deterministic(_state(decisions=[decision], actions=[action]))
    followups = [f for f in findings if f.type == CoordinationFindingType.decision_followup]
    assert len(followups) == 1
    assert "no owner" in followups[0].description.lower()
    assert followups[0].related_ids == ["d1", "a1"]


def test_decision_matches_action_despite_compound_word_spelling() -> None:
    # Regression test for the exact task-spec demo phrasing: "rollback" (one
    # word, in the decision) vs "roll back" (two words, in the action) must
    # still be recognized as the same follow-through action.
    decision = Decision(id="d1", incident_id="inc-1", decision="Proceed with rollback.", decided_by="Bob", timestamp=T0)
    action = Action(
        id="a1", incident_id="inc-1", description="Roll back payment service.", owner="Bob",
        status=ActionStatus.pending, created_at=T0, updated_at=T0,
    )
    findings = analyze_incident_deterministic(_state(decisions=[decision], actions=[action]))
    assert not any(f.type == CoordinationFindingType.decision_followup for f in findings)


def test_decision_matches_unowned_compound_word_action_still_flags_owner_gap() -> None:
    # Same compound-word phrasing, but the action has no owner - the decision
    # must still be linked to it (not "no tracked follow-up"), and the gap
    # reported must be specifically about the missing owner.
    decision = Decision(id="d1", incident_id="inc-1", decision="Proceed with rollback.", decided_by="Bob", timestamp=T0)
    action = Action(
        id="a1", incident_id="inc-1", description="Roll back payment service.", owner=None,
        status=ActionStatus.pending, created_at=T0, updated_at=T0,
    )
    findings = analyze_incident_deterministic(_state(decisions=[decision], actions=[action]))
    followups = [f for f in findings if f.type == CoordinationFindingType.decision_followup]
    assert len(followups) == 1
    assert "no owner" in followups[0].description.lower()
    assert "no tracked follow-up" not in followups[0].title.lower()
    assert followups[0].related_ids == ["d1", "a1"]

    # The action itself is still independently flagged as unowned.
    assert any(f.type == CoordinationFindingType.unowned_action and f.related_ids == ["a1"] for f in findings)


def test_decision_with_owned_matching_action_produces_no_finding() -> None:
    decision = Decision(
        id="d1", incident_id="inc-1", decision="Proceed with payment rollback.", decided_by="Bob", timestamp=T0
    )
    action = Action(
        id="a1", incident_id="inc-1", description="Rollback payment service deployment.", owner="Bob",
        status=ActionStatus.pending, created_at=T0, updated_at=T0,
    )
    findings = analyze_incident_deterministic(_state(decisions=[decision], actions=[action]))
    assert not any(f.type == CoordinationFindingType.decision_followup for f in findings)


def test_hypothesis_acted_on_is_flagged_as_risk() -> None:
    hypothesis = Hypothesis(
        id="h1", incident_id="inc-1", statement="Payment DB saturation is causing the outage.",
        source="Alice", timestamp=T0, status=HypothesisStatus.proposed,
    )
    decision = Decision(
        id="d1", incident_id="inc-1", decision="Since the DB is saturated, let's scale it up.",
        decided_by="Bob", timestamp=T0 + timedelta(minutes=2),
    )
    findings = analyze_incident_deterministic(_state(hypotheses=[hypothesis], decisions=[decision]))
    risks = [f for f in findings if f.type == CoordinationFindingType.hypothesis_risk]
    assert len(risks) == 1
    assert risks[0].dedup_key == "hypothesis_risk:h1"
    assert risks[0].related_ids == ["h1", "d1"]


def test_unrelated_decision_does_not_flag_hypothesis_risk() -> None:
    hypothesis = Hypothesis(
        id="h1", incident_id="inc-1", statement="Payment DB saturation is causing the outage.",
        source="Alice", timestamp=T0, status=HypothesisStatus.proposed,
    )
    decision = Decision(
        id="d1", incident_id="inc-1", decision="Notify customer support of the ongoing incident.",
        decided_by="Bob", timestamp=T0 + timedelta(minutes=2),
    )
    findings = analyze_incident_deterministic(_state(hypotheses=[hypothesis], decisions=[decision]))
    assert not any(f.type == CoordinationFindingType.hypothesis_risk for f in findings)


def test_confirmed_hypothesis_is_never_flagged_as_risk() -> None:
    hypothesis = Hypothesis(
        id="h1", incident_id="inc-1", statement="Payment DB saturation is causing the outage.",
        source="Alice", timestamp=T0, status=HypothesisStatus.supported,
    )
    decision = Decision(
        id="d1", incident_id="inc-1", decision="Since the DB is saturated, let's scale it up.",
        decided_by="Bob", timestamp=T0 + timedelta(minutes=2),
    )
    findings = analyze_incident_deterministic(_state(hypotheses=[hypothesis], decisions=[decision]))
    assert not any(f.type == CoordinationFindingType.hypothesis_risk for f in findings)


def test_open_question_produces_unresolved_risk_finding() -> None:
    question = UnresolvedQuestion(id="q1", incident_id="inc-1", question="Is the rollback safe?", created_at=T0)
    findings = analyze_incident_deterministic(_state(unresolved_questions=[question]))
    risks = [f for f in findings if f.type == CoordinationFindingType.unresolved_risk]
    assert len(risks) == 1
    assert risks[0].dedup_key == "unresolved_risk:q1"


def test_situational_summary_always_present_and_reflects_counts() -> None:
    action = Action(
        id="a1", incident_id="inc-1", description="Roll back payment service.", owner=None,
        status=ActionStatus.pending, created_at=T0, updated_at=T0,
    )
    findings = analyze_incident_deterministic(_state(actions=[action]))
    summaries = [f for f in findings if f.type == CoordinationFindingType.situational_summary]
    assert len(summaries) == 1
    assert "1 action(s) open (1 unowned)" in summaries[0].description


def test_empty_incident_produces_only_situational_summary() -> None:
    findings = analyze_incident_deterministic(_state())
    assert len(findings) == 1
    assert findings[0].type == CoordinationFindingType.situational_summary


# --- Integration tests: persistence, dedup, realtime -----------------------------


def _create_incident(title: str) -> str:
    return client.post("/api/incidents", json={"title": title}).json()["id"]


def _send_turn(incident_id: str, analysis: ConversationAnalysis, speaker: str = "Alice", text: str = "update") -> dict:
    with patch("app.intelligence.analyze_conversation", return_value=analysis):
        res = client.post(
            f"/api/incidents/{incident_id}/conversation", json={"speaker": speaker, "text": text}
        )
    assert res.status_code == 200
    return res.json()


def test_unowned_action_appears_in_state_via_conversation_endpoint() -> None:
    incident_id = _create_incident("Coordination unowned action test")
    analysis = ConversationAnalysis(actions=[ExtractedAction(description="Roll back payment service.")])

    body = _send_turn(incident_id, analysis)

    findings = body["state"]["coordination_findings"]
    unowned = [f for f in findings if f["type"] == "unowned_action"]
    assert len(unowned) == 1
    assert unowned[0]["status"] == "open"
    assert "Roll back payment service" in unowned[0]["description"]


def test_assigning_owner_resolves_the_unowned_finding() -> None:
    incident_id = _create_incident("Coordination resolve test")
    analysis = ConversationAnalysis(actions=[ExtractedAction(description="Roll back payment service.")])
    _send_turn(incident_id, analysis)

    reassign = ConversationAnalysis(actions=[ExtractedAction(description="Roll back payment service.", owner="Bob")])
    body = _send_turn(incident_id, reassign, speaker="Bob", text="I'll take the rollback.")

    findings = body["state"]["coordination_findings"]
    assert not any(f["type"] == "unowned_action" for f in findings)


def test_repeated_analysis_does_not_duplicate_findings() -> None:
    incident_id = _create_incident("Coordination dedup test")
    analysis = ConversationAnalysis(actions=[ExtractedAction(description="Roll back payment service.")])
    first_body = _send_turn(incident_id, analysis)
    first_unowned = [f for f in first_body["state"]["coordination_findings"] if f["type"] == "unowned_action"]
    assert len(first_unowned) == 1
    first_id = first_unowned[0]["id"]

    # A second, unrelated turn triggers a second coordination refresh - the
    # still-unowned action must not produce a second finding, and - crucially -
    # reconciliation must update the *same* row rather than resolving it and
    # creating a new one under a different id.
    from app.incident_models import ExtractedFact

    other_turn = ConversationAnalysis(facts=[ExtractedFact(statement="Latency is elevated.")])
    second_body = _send_turn(incident_id, other_turn, text="Latency looks elevated too.")

    second_unowned = [f for f in second_body["state"]["coordination_findings"] if f["type"] == "unowned_action"]
    assert len(second_unowned) == 1
    assert second_unowned[0]["id"] == first_id
    assert second_unowned[0]["status"] == "open"


def test_conflict_finding_appears_in_state() -> None:
    incident_id = _create_incident("Coordination conflict test")
    from app.incident_models import ExtractedConflict, ExtractedConflictStatement

    analysis = ConversationAnalysis(
        conflicts=[
            ExtractedConflict(
                topic="Database load",
                statements=[
                    ExtractedConflictStatement(source="Engineer", statement="The database is overloaded."),
                    ExtractedConflictStatement(source="Support", statement="The database dashboard looks normal."),
                ],
            )
        ]
    )
    body = _send_turn(incident_id, analysis)

    findings = body["state"]["coordination_findings"]
    conflicts = [f for f in findings if f["type"] == "conflict"]
    assert len(conflicts) == 1
    assert conflicts[0]["severity"] == "high"


def test_situational_summary_present_after_first_turn() -> None:
    incident_id = _create_incident("Coordination summary test")
    from app.incident_models import ExtractedFact

    analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Payments failing for ~30%.")])
    body = _send_turn(incident_id, analysis)

    findings = body["state"]["coordination_findings"]
    assert any(f["type"] == "situational_summary" for f in findings)


def test_status_change_triggers_coordination_refresh() -> None:
    incident_id = _create_incident("Coordination status test")
    res = client.patch(f"/api/incidents/{incident_id}/status", json={"status": "identified"})
    assert res.status_code == 200
    findings = res.json()["coordination_findings"]
    assert any(f["type"] == "situational_summary" for f in findings)


def test_no_op_conversation_does_not_broadcast_coordination_update() -> None:
    incident_id = _create_incident("Coordination no-op broadcast test")

    with client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws:
        ws.receive_json()  # initial state

        with patch("app.intelligence.analyze_conversation", return_value=ConversationAnalysis()):
            res = client.post(
                f"/api/incidents/{incident_id}/conversation",
                json={"speaker": "Alice", "text": "Good morning everyone."},
            )
        assert res.status_code == 200

        status_res = client.patch(f"/api/incidents/{incident_id}/status", json={"status": "identified"})
        assert status_res.status_code == 200

        update = ws.receive_json()

    # The first broadcast received after the no-op turn is the status change,
    # confirming the no-op conversation triggered neither analysis nor a broadcast.
    assert update["state"]["incident"]["status"] == "identified"


def test_realtime_broadcast_includes_coordination_findings() -> None:
    incident_id = _create_incident("Coordination realtime test")
    analysis = ConversationAnalysis(actions=[ExtractedAction(description="Roll back payment service.")])

    with client.websocket_connect(f"/api/incidents/{incident_id}/stream") as ws:
        ws.receive_json()  # initial state
        with patch("app.intelligence.analyze_conversation", return_value=analysis):
            res = client.post(
                f"/api/incidents/{incident_id}/conversation",
                json={"speaker": "Alice", "text": "We need to roll back."},
            )
        assert res.status_code == 200
        update = ws.receive_json()

    findings = update["state"]["coordination_findings"]
    assert any(f["type"] == "unowned_action" for f in findings)


def test_missing_information_llm_check_is_mocked_and_optional() -> None:
    incident_id = _create_incident("Coordination missing-info test")
    gap_analysis = CoordinationGapAnalysis(
        gaps=[
            ExtractedCoordinationGap(
                title="Affected regions unknown",
                description="Impact is reported but affected regions are not confirmed.",
                severity="high",
            )
        ]
    )
    from app.incident_models import ExtractedFact

    turn_analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Payments are failing for many users.")])

    with patch.object(coordination, "generate_structured", return_value=gap_analysis):
        body = _send_turn(incident_id, turn_analysis)

    findings = body["state"]["coordination_findings"]
    gaps = [f for f in findings if f["type"] == "missing_information"]
    assert len(gaps) == 1
    assert gaps[0]["severity"] == "high"
    assert "regions" in gaps[0]["description"].lower()


def test_missing_information_defaults_to_empty_without_llm_configured() -> None:
    # No GEMINI_API_KEY in the automated test environment - the optional check
    # must fail safe rather than break the conversation endpoint.
    incident_id = _create_incident("Coordination no-LLM test")
    from app.incident_models import ExtractedFact

    analysis = ConversationAnalysis(facts=[ExtractedFact(statement="Payments are failing for many users.")])
    body = _send_turn(incident_id, analysis)

    findings = body["state"]["coordination_findings"]
    assert not any(f["type"] == "missing_information" for f in findings)
