"""M6.2 proactive-voice-intervention tests.

Covers app/voice.py (eligibility, deterministic message generation, dedup,
cooldown, the Agora /speak call) and its wiring into the conversation/prepare/
confirm endpoints in app/incidents_api.py. Every test either calls app/voice.py
directly against hand-built IncidentState objects, or drives the real FastAPI
app end-to-end with `app.intelligence.analyze_conversation` mocked - same
convention as test_coordination.py/test_actions.py. Agora's REST call is
always faked via a monkeypatched httpx.Client (never a real network call),
matching test_agora_agent.py's httpx.AsyncClient fake for /join.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app import incident_db, voice
from app.config import Settings
from app.db import get_connection
from app.incident_models import (
    Action,
    ActionStatus,
    ConversationAnalysis,
    CoordinationFinding,
    CoordinationFindingStatus,
    CoordinationFindingType,
    CoordinationSeverity,
    ExtractedAction,
    ExtractedConflict,
    ExtractedConflictStatement,
    Incident,
    IncidentState,
    IncidentStatus,
)
from app.main import app

client = TestClient(app)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _configured_settings(**overrides) -> Settings:
    defaults = dict(
        agora_app_id="a" * 32,
        agora_app_certificate="b" * 32,
        agora_customer_id="cid",
        agora_customer_secret="csecret",
        agora_agent_pipeline_id="pipeline-123",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _incident(**overrides) -> Incident:
    defaults = dict(
        id="inc-voice-1", title="Payments outage", status=IncidentStatus.investigating, created_at=T0, updated_at=T0
    )
    defaults.update(overrides)
    return Incident(**defaults)


def _finding(**overrides) -> CoordinationFinding:
    defaults = dict(
        id="f1",
        incident_id="inc-voice-1",
        type=CoordinationFindingType.conflict,
        severity=CoordinationSeverity.high,
        title="Conflicting reports: DB load",
        description="Conflict about DB load.",
        related_ids=["c1"],
        status=CoordinationFindingStatus.open,
        dedup_key="conflict:c1",
        created_at=T0,
        updated_at=T0,
    )
    defaults.update(overrides)
    return CoordinationFinding(**defaults)


class FakeSpeakResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]


class FakeSyncClient:
    """Stands in for httpx.Client, recording every /speak call made through it."""

    calls: list[dict] = []
    fail: bool = False
    raise_request_error: bool = False

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "FakeSyncClient":
        return self

    def __exit__(self, *args) -> None:
        pass

    def post(self, url, json=None, auth=None):
        type(self).calls.append({"url": url, "json": json, "auth": auth})
        if type(self).raise_request_error:
            raise httpx.RequestError("network down", request=None)  # type: ignore[arg-type]
        return FakeSpeakResponse(500 if type(self).fail else 200)


@pytest.fixture(autouse=True)
def _reset_fake_client():
    FakeSyncClient.calls = []
    FakeSyncClient.fail = False
    FakeSyncClient.raise_request_error = False
    yield


@pytest.fixture
def configured(monkeypatch):
    settings = _configured_settings()
    monkeypatch.setattr(voice, "get_settings", lambda: settings)
    monkeypatch.setattr(httpx, "Client", FakeSyncClient)
    return settings


# --- speak() request construction -------------------------------------------------


def test_speak_not_configured_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(voice, "get_settings", lambda: Settings())
    assert voice.speak("agent-1", "hello") is False
    assert FakeSyncClient.calls == []


def test_speak_request_shape(configured) -> None:
    settings = configured
    ok = voice.speak("agent-42", "Confirm the rollback?", interruptable=False)

    assert ok is True
    assert len(FakeSyncClient.calls) == 1
    call = FakeSyncClient.calls[0]
    assert call["url"] == (
        f"https://api.agora.io/api/conversational-ai-agent/v2/projects/{settings.agora_app_id}/agents/agent-42/speak"
    )
    assert call["json"] == {"text": "Confirm the rollback?", "priority": "APPEND", "interruptable": False}
    assert isinstance(call["auth"], httpx.BasicAuth)


def test_speak_truncates_long_text(configured) -> None:
    voice.speak("agent-1", "x" * 1000)
    assert len(FakeSyncClient.calls[0]["json"]["text"]) == voice.MAX_MESSAGE_CHARS


def test_speak_returns_false_on_http_error(configured) -> None:
    FakeSyncClient.fail = True
    assert voice.speak("agent-1", "hello") is False


def test_speak_returns_false_on_request_error(configured) -> None:
    FakeSyncClient.raise_request_error = True
    assert voice.speak("agent-1", "hello") is False


# --- eligibility ---------------------------------------------------------------


def test_only_designated_finding_types_are_eligible() -> None:
    state = IncidentState(
        incident=_incident(),
        coordination_findings=[
            _finding(id="f-unowned", type=CoordinationFindingType.unowned_action, dedup_key="unowned_action:a1"),
            _finding(id="f-stale", type=CoordinationFindingType.stale_action, dedup_key="stale_action:a1"),
            _finding(id="f-summary", type=CoordinationFindingType.situational_summary, dedup_key="situational_summary"),
        ],
    )
    assert voice._select_finding("inc-voice-1", state) is None


def test_conflict_is_eligible_and_prioritized_over_missing_information() -> None:
    state = IncidentState(
        incident=_incident(),
        coordination_findings=[
            _finding(id="f-gap", type=CoordinationFindingType.missing_information, dedup_key="missing_information:x"),
            _finding(id="f-conflict", type=CoordinationFindingType.conflict, dedup_key="conflict:c1"),
        ],
    )
    picked = voice._select_finding("inc-voice-1", state)
    assert picked is not None
    assert picked.type == CoordinationFindingType.conflict


# --- conflict -> intervention (end to end) -----------------------------------------


def _create_incident(title: str) -> str:
    return client.post("/api/incidents", json={"title": title}).json()["id"]


def _start_agent_for(incident_id: str, channel: str, agent_id: str = "agent-1") -> None:
    voice.register_agent(incident_id, agent_id)


def test_conflict_triggers_spoken_intervention(configured) -> None:
    incident_id = _create_incident("Voice conflict test")
    _start_agent_for(incident_id, "room-1")

    analysis = ConversationAnalysis(
        conflicts=[
            ExtractedConflict(
                topic="Database load",
                statements=[
                    ExtractedConflictStatement(source="Alice", statement="The database is overloaded."),
                    ExtractedConflictStatement(source="Bob", statement="DB metrics look healthy."),
                ],
            )
        ]
    )
    with patch("app.intelligence.analyze_conversation", return_value=analysis):
        res = client.post(f"/api/incidents/{incident_id}/conversation", json={"speaker": "Alice", "text": "update"})
    assert res.status_code == 200

    assert len(FakeSyncClient.calls) == 1
    assert "Database load" in FakeSyncClient.calls[0]["json"]["text"]

    state = res.json()["state"]
    assert state["last_voice_intervention"] is not None
    assert "Database load" in state["last_voice_intervention"]["message"]


# --- missing-information -> intervention -----------------------------------------


def test_missing_information_finding_triggers_intervention() -> None:
    state = IncidentState(
        incident=_incident(),
        coordination_findings=[
            _finding(
                id="f-gap",
                type=CoordinationFindingType.missing_information,
                dedup_key="missing_information:pct",
                description="Impact is reported but the affected percentage isn't confirmed.",
            )
        ],
    )
    voice.register_agent("inc-voice-1", "agent-1")
    with (
        patch.object(voice, "get_settings", lambda: _configured_settings()),
        patch.object(httpx, "Client", FakeSyncClient),
    ):
        spoke = voice.maybe_intervene("inc-voice-1", state)

    assert spoke is True
    assert "affected percentage" in FakeSyncClient.calls[0]["json"]["text"]
    assert incident_db.has_spoken("inc-voice-1", "missing_information:pct")


# --- duplicate suppression -------------------------------------------------------


def test_duplicate_finding_is_not_spoken_twice(configured) -> None:
    incident_id = "inc-voice-dedup"
    voice.register_agent(incident_id, "agent-1")

    finding = _finding(incident_id=incident_id, dedup_key="conflict:dup")
    state = IncidentState(incident=_incident(id=incident_id), coordination_findings=[finding])

    assert voice.maybe_intervene(incident_id, state) is True
    assert len(FakeSyncClient.calls) == 1

    # Same dedup_key, still open - a second refresh pass must not re-speak it.
    assert voice.maybe_intervene(incident_id, state) is False
    assert len(FakeSyncClient.calls) == 1


# --- cooldown / debounce ----------------------------------------------------------


def test_cooldown_suppresses_a_second_distinct_finding(configured) -> None:
    incident_id = "inc-voice-cooldown"
    voice.register_agent(incident_id, "agent-1")

    first = _finding(incident_id=incident_id, id="f1", type=CoordinationFindingType.conflict, dedup_key="conflict:1")
    state1 = IncidentState(incident=_incident(id=incident_id), coordination_findings=[first])
    assert voice.maybe_intervene(incident_id, state1) is True
    assert len(FakeSyncClient.calls) == 1

    # A second, genuinely different finding arrives immediately after - the
    # cooldown (not dedup) is what must block this one.
    second = _finding(
        incident_id=incident_id,
        id="f2",
        type=CoordinationFindingType.missing_information,
        dedup_key="missing_information:2",
    )
    state2 = IncidentState(incident=_incident(id=incident_id), coordination_findings=[first, second])
    assert voice.maybe_intervene(incident_id, state2) is False
    assert len(FakeSyncClient.calls) == 1


def test_cooldown_expires_after_the_configured_window(configured, monkeypatch) -> None:
    incident_id = "inc-voice-cooldown-expiry"
    voice.register_agent(incident_id, "agent-1")
    monkeypatch.setattr(voice, "COOLDOWN", timedelta(seconds=0))

    first = _finding(incident_id=incident_id, id="f1", dedup_key="conflict:1")
    state1 = IncidentState(incident=_incident(id=incident_id), coordination_findings=[first])
    assert voice.maybe_intervene(incident_id, state1) is True

    second = _finding(
        incident_id=incident_id,
        id="f2",
        type=CoordinationFindingType.missing_information,
        dedup_key="missing_information:2",
    )
    state2 = IncidentState(incident=_incident(id=incident_id), coordination_findings=[first, second])
    assert voice.maybe_intervene(incident_id, state2) is True
    assert len(FakeSyncClient.calls) == 2


def test_status_request_and_action_result_bypass_cooldown(configured) -> None:
    incident_id = "inc-voice-bypass"
    voice.register_agent(incident_id, "agent-1")

    first = _finding(incident_id=incident_id, id="f1", dedup_key="conflict:1")
    summary = _finding(
        incident_id=incident_id,
        id="f-summary",
        type=CoordinationFindingType.situational_summary,
        dedup_key="situational_summary",
        description="1 action(s) open (0 unowned). 0 unresolved conflict(s).",
    )
    state = IncidentState(incident=_incident(id=incident_id), coordination_findings=[first, summary])
    assert voice.maybe_intervene(incident_id, state) is True  # consumes cooldown

    assert voice.maybe_answer_status_request(incident_id, state, "Hey EchoWard, what's the status?") is True
    assert len(FakeSyncClient.calls) == 2

    action = Action(
        id="a1", incident_id=incident_id, description="Roll back payment service", status=ActionStatus.completed,
        created_at=T0, updated_at=T0,
    )
    assert voice.announce_action_result(incident_id, action) is True
    assert len(FakeSyncClient.calls) == 3


# --- Agora failure must not break incident processing ------------------------------


def test_agora_failure_does_not_break_conversation_processing(configured) -> None:
    FakeSyncClient.raise_request_error = True
    incident_id = _create_incident("Voice failure test")
    _start_agent_for(incident_id, "room-1")

    analysis = ConversationAnalysis(
        conflicts=[
            ExtractedConflict(
                topic="Database load",
                statements=[ExtractedConflictStatement(source="Alice", statement="DB is overloaded.")],
            )
        ]
    )
    with patch("app.intelligence.analyze_conversation", return_value=analysis):
        res = client.post(f"/api/incidents/{incident_id}/conversation", json={"speaker": "Alice", "text": "update"})

    assert res.status_code == 200
    body = res.json()
    assert any(f["type"] == "conflict" for f in body["state"]["coordination_findings"])
    # The intervention was attempted (and recorded) but never actually heard.
    assert body["state"]["last_voice_intervention"] is None


def test_no_agent_registered_is_a_safe_no_op(configured) -> None:
    incident_id = _create_incident("No agent registered test")
    # Deliberately never registered via voice.register_agent.

    analysis = ConversationAnalysis(
        conflicts=[
            ExtractedConflict(
                topic="Database load",
                statements=[ExtractedConflictStatement(source="Alice", statement="DB is overloaded.")],
            )
        ]
    )
    with patch("app.intelligence.analyze_conversation", return_value=analysis):
        res = client.post(f"/api/incidents/{incident_id}/conversation", json={"speaker": "Alice", "text": "update"})

    assert res.status_code == 200
    assert FakeSyncClient.calls == []
    assert res.json()["state"]["last_voice_intervention"] is None


# --- critical actions still require human confirmation ------------------------------


def test_prepared_action_triggers_checkpoint_message_without_executing(configured) -> None:
    incident_id = _create_incident("Voice checkpoint test")
    _start_agent_for(incident_id, "room-1")
    with get_connection() as conn:
        action = incident_db.insert_action(conn, incident_id, "Roll back payment service", None, T0)

    res = client.post(
        f"/api/incidents/{incident_id}/actions/{action.id}/prepare",
        json={"action_type": "rollback_payment_service", "target": "payment-service", "reason": "Error spike."},
    )
    assert res.status_code == 200

    assert len(FakeSyncClient.calls) == 1
    message = FakeSyncClient.calls[0]["json"]["text"]
    assert "confirmation" in message.lower()

    # Voice only narrated the checkpoint - the action is still sitting in
    # awaiting_confirmation, nothing was executed.
    prepared_action = incident_db.get_action(incident_id, action.id)
    assert prepared_action is not None
    assert prepared_action.status == ActionStatus.awaiting_confirmation
    assert prepared_action.tool_result is None


def test_announce_action_result_never_mutates_the_action(configured) -> None:
    incident_id = "inc-voice-no-mutate"
    voice.register_agent(incident_id, "agent-1")
    with get_connection() as conn:
        action = incident_db.insert_action(conn, incident_id, "Roll back payment service", "Bob", T0)
        incident_db.update_action_status(conn, action.id, ActionStatus.completed, T0)

    action = incident_db.get_action(incident_id, action.id)
    assert action is not None
    voice.announce_action_result(incident_id, action)

    unchanged = incident_db.get_action(incident_id, action.id)
    assert unchanged is not None
    assert unchanged.status == ActionStatus.completed
    assert unchanged.tool_result is None  # voice.py never touches tool_result/execution


def test_action_result_announced_after_confirm(configured) -> None:
    incident_id = _create_incident("Voice result announcement test")
    _start_agent_for(incident_id, "room-1")
    with get_connection() as conn:
        action = incident_db.insert_action(conn, incident_id, "Roll back payment service", None, T0)

    client.post(
        f"/api/incidents/{incident_id}/actions/{action.id}/prepare",
        json={"action_type": "rollback_payment_service", "target": "payment-service", "reason": "Error spike."},
    )
    calls_after_prepare = len(FakeSyncClient.calls)

    res = client.post(f"/api/incidents/{incident_id}/actions/{action.id}/confirm", json={"confirmed_by": "Sarah"})
    assert res.status_code == 200

    assert len(FakeSyncClient.calls) == calls_after_prepare + 1
    message = FakeSyncClient.calls[-1]["json"]["text"]
    assert "completed successfully" in message

    state = res.json()
    assert "completed successfully" in state["last_voice_intervention"]["message"]


# --- status request ---------------------------------------------------------------


def test_status_request_is_detected_deterministically() -> None:
    assert voice.is_status_request("Hey EchoWard, what's the status?") is True
    assert voice.is_status_request("Can you give me a status update, EchoWard?") is True
    assert voice.is_status_request("The database is still overloaded.") is False


def test_status_request_speaks_the_situational_summary(configured) -> None:
    incident_id = _create_incident("Voice status request test")
    _start_agent_for(incident_id, "room-1")

    analysis = ConversationAnalysis(actions=[ExtractedAction(description="Roll back payment service.")])
    with patch("app.intelligence.analyze_conversation", return_value=analysis):
        client.post(f"/api/incidents/{incident_id}/conversation", json={"speaker": "Alice", "text": "update"})
    calls_before = len(FakeSyncClient.calls)

    with patch("app.intelligence.analyze_conversation", return_value=ConversationAnalysis()):
        res = client.post(
            f"/api/incidents/{incident_id}/conversation",
            json={"speaker": "Bob", "text": "EchoWard, can I get a status update?"},
        )

    assert res.status_code == 200
    assert len(FakeSyncClient.calls) == calls_before + 1
    message = FakeSyncClient.calls[-1]["json"]["text"]
    assert "action(s) open" in message


def test_agent_registry_round_trips() -> None:
    assert voice.get_agent_id("inc-registry-test") is None
    voice.register_agent("inc-registry-test", "agent-99")
    assert voice.get_agent_id("inc-registry-test") == "agent-99"
    voice.unregister_agent("agent-99")
    assert voice.get_agent_id("inc-registry-test") is None
