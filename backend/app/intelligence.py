"""Conversation -> structured incident intelligence.

This is the M2 core: takes one conversation turn (speaker + text), asks an LLM to
extract only what's actually supported by that statement (facts/hypotheses/
decisions/actions/questions/conflicts), and persists the result to SQLite.

The extraction call (`analyze_conversation`) and the persistence call
(`apply_analysis`) are kept as separate, independently testable functions:
tests monkeypatch `generate_structured` to avoid any real LLM call, then assert
against what `apply_analysis` actually wrote.
"""

import difflib
import logging
import sqlite3
from datetime import UTC, datetime

from app import incident_db
from app.db import get_connection
from app.incident_models import (
    ActionStatus,
    Conflict,
    ConflictStatement,
    ConversationAnalysis,
    ConversationChanges,
    ConversationTurnResponse,
)
from app.llm import generate_structured

logger = logging.getLogger("echoward.intelligence")

CONTEXT_ITEM_LIMIT = 5
FUZZY_MATCH_THRESHOLD = 0.55

EXTRACTION_INSTRUCTIONS = """You are EchoWard's incident-intelligence extraction engine.

You read ONE new statement from a live technical incident call and decide what, if
anything, should be added to the incident record. EchoWard is not a transcription
bot — it maintains a shared operational picture, and it must never invent certainty.

Classify content using these categories:

- FACT: something the speaker reports as directly observed/known right now (a
  metric, an error, an observed behavior). Extract it attributed to them.
- HYPOTHESIS: something proposed, suspected, guessed, or inferred — including
  tentative language ("I think", "maybe", "could be") AND statements asserted
  with strong confidence ("definitely", "certainly") that are not themselves a
  direct observation. Strong wording does not make a claim a fact — a hypothesis
  is still a hypothesis no matter how confidently it's stated.
- DECISION: an explicit choice or plan the group is committing to ("let's roll
  back X", "we will do Y").
- ACTION: a concrete task, optionally with an owner. Only set an owner if this
  statement identifies who is doing it — if the current speaker claims
  ownership themselves ("I'll handle it", "I'm on it"), the owner is the
  current speaker (their name is given to you below). Do not create both a
  decision and an action for the exact same statement — prefer the decision
  alone unless a concrete task/owner is also being established.
- UNRESOLVED_QUESTION: an open question raised that this statement doesn't
  answer.
- CONFLICT: this statement contradicts a fact or hypothesis already listed in
  "Incident context so far" below. Include BOTH the prior statement (with its
  original source, exactly as given in the context) and the new statement
  (with the current speaker) in `statements`. Never decide who is right —
  just surface the disagreement. Do not invent a conflict with something not
  present in the given context.
- TIMELINE_EVENT: a short, neutral, standalone note worth logging
  chronologically that ISN'T already captured by one of the categories above
  (e.g. "Incident reported"). Leave this empty most of the time — do not
  restate a fact/decision/action you already extracted above.

Rules:
- If the statement is small talk, acknowledgement, or has no useful incident
  content, return every list empty. Do not force every sentence into an item.
- Never invent information not present in the statement or the provided
  context: no invented owners, no invented root cause, no invented resolution.
- Write each extracted item as a clear, standalone statement in third person
  (not "I"/"you" — use the actual name), independently understandable without
  the raw quote.
"""


def _build_context(incident_id: str) -> str:
    facts = incident_db.list_facts(incident_id)[-CONTEXT_ITEM_LIMIT:]
    hypotheses = incident_db.list_hypotheses(incident_id)[-CONTEXT_ITEM_LIMIT:]
    decisions = incident_db.list_decisions(incident_id)[-CONTEXT_ITEM_LIMIT:]
    open_actions = [
        a for a in incident_db.list_actions(incident_id) if a.status in (ActionStatus.pending, ActionStatus.in_progress)
    ][-CONTEXT_ITEM_LIMIT:]

    sections: list[str] = []
    if facts:
        sections.append("Known facts:\n" + "\n".join(f"- ({f.source}) {f.statement}" for f in facts))
    if hypotheses:
        sections.append(
            "Open hypotheses:\n" + "\n".join(f"- ({h.source}) {h.statement}" for h in hypotheses)
        )
    if decisions:
        sections.append("Decisions already made:\n" + "\n".join(f"- {d.decision}" for d in decisions))
    if open_actions:
        sections.append(
            "Open action items:\n"
            + "\n".join(f"- {a.description} (owner: {a.owner or 'unassigned'})" for a in open_actions)
        )
    return "\n\n".join(sections) if sections else "No incident context recorded yet."


def _build_prompt(speaker: str, text: str, context: str) -> str:
    return (
        f"{EXTRACTION_INSTRUCTIONS}\n\n"
        f"--- Incident context so far ---\n{context}\n\n"
        f'--- New statement ---\nSpeaker: {speaker}\nText: "{text}"\n\n'
        "Return the structured update as JSON matching the schema."
    )


def analyze_conversation(incident_id: str, speaker: str, text: str) -> ConversationAnalysis:
    """Call the LLM and return validated structured output. Raises LLMOutputError."""
    context = _build_context(incident_id)
    prompt = _build_prompt(speaker, text, context)
    result = generate_structured(prompt, ConversationAnalysis)
    return result  # type: ignore[return-value]


def _fuzzy_match(a: str, b: str, threshold: float = FUZZY_MATCH_THRESHOLD) -> bool:
    return difflib.SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio() >= threshold


def _log_event(
    conn: sqlite3.Connection,
    changes: ConversationChanges,
    incident_id: str,
    event: str,
    source: str,
    when: datetime,
) -> None:
    changes.timeline_events.append(incident_db.insert_timeline_event(conn, incident_id, event, source, when))


def apply_analysis(
    conn: sqlite3.Connection,
    incident_id: str,
    speaker: str,
    analysis: ConversationAnalysis,
    occurred_at: datetime,
) -> ConversationChanges:
    """Persist an already-validated ConversationAnalysis. Never partially fails silently."""
    changes = ConversationChanges()

    for f in analysis.facts:
        fact = incident_db.insert_fact(conn, incident_id, f.statement, speaker, occurred_at, f.confidence)
        changes.facts.append(fact)
        _log_event(conn, changes, incident_id, f"Fact reported: {f.statement}", speaker, occurred_at)

    for h in analysis.hypotheses:
        hyp = incident_db.insert_hypothesis(conn, incident_id, h.statement, speaker, occurred_at)
        changes.hypotheses.append(hyp)
        _log_event(conn, changes, incident_id, f"Hypothesis proposed: {h.statement}", speaker, occurred_at)

    for d in analysis.decisions:
        decided_by = d.decided_by or speaker
        decision = incident_db.insert_decision(conn, incident_id, d.decision, decided_by, occurred_at)
        changes.decisions.append(decision)
        _log_event(conn, changes, incident_id, f"Decision: {d.decision}", decided_by, occurred_at)

    if analysis.actions:
        open_actions = incident_db.list_open_actions(conn, incident_id)
        for a in analysis.actions:
            match = next((oa for oa in open_actions if _fuzzy_match(oa.description, a.description)), None)
            if match is not None:
                if a.owner and a.owner != match.owner:
                    updated = incident_db.update_action_owner(conn, match.id, a.owner, occurred_at)
                    changes.actions_updated.append(updated)
                    open_actions = [updated if oa.id == updated.id else oa for oa in open_actions]
                    _log_event(
                        conn,
                        changes,
                        incident_id,
                        f"{a.owner} assigned to: {updated.description}",
                        speaker,
                        occurred_at,
                    )
                # Same action re-mentioned with nothing new: no-op, avoid a duplicate row.
            else:
                created = incident_db.insert_action(conn, incident_id, a.description, a.owner, occurred_at)
                changes.actions_created.append(created)
                open_actions.append(created)
                owner_note = f" (owner: {created.owner})" if created.owner else " (owner not yet assigned)"
                _log_event(
                    conn, changes, incident_id, f"Action: {created.description}{owner_note}", speaker, occurred_at
                )

    for q in analysis.unresolved_questions:
        question = incident_db.insert_question(conn, incident_id, q.question, occurred_at)
        changes.unresolved_questions.append(question)
        _log_event(conn, changes, incident_id, f"Open question: {q.question}", speaker, occurred_at)

    if analysis.conflicts:
        open_conflicts = incident_db.list_open_conflicts(conn, incident_id)
        for c in analysis.conflicts:
            statements = [ConflictStatement(source=s.source, statement=s.statement) for s in c.statements]
            match: Conflict | None = next(
                (oc for oc in open_conflicts if _fuzzy_match(oc.topic, c.topic)), None
            )
            if match is not None:
                updated = incident_db.append_conflict_statements(conn, match.id, statements)
                changes.conflicts_updated.append(updated)
                open_conflicts = [updated if oc.id == updated.id else oc for oc in open_conflicts]
            else:
                created = incident_db.insert_conflict(conn, incident_id, c.topic, statements, occurred_at)
                changes.conflicts_created.append(created)
                open_conflicts.append(created)
            _log_event(conn, changes, incident_id, f"Conflicting information: {c.topic}", "system", occurred_at)

    for t in analysis.timeline_events:
        changes.timeline_events.append(
            incident_db.insert_timeline_event(conn, incident_id, t.event, speaker, occurred_at)
        )

    if not changes.is_empty:
        incident_db.touch_incident(conn, incident_id, occurred_at)

    return changes


class IncidentNotFoundError(Exception):
    def __init__(self, incident_id: str) -> None:
        super().__init__(f"Incident not found: {incident_id}")
        self.incident_id = incident_id


def process_conversation_turn(
    incident_id: str, speaker: str, text: str, occurred_at: datetime | None = None
) -> ConversationTurnResponse:
    """The full M2 pipeline for one conversation turn: analyze, persist, return state.

    Raises IncidentNotFoundError, or LLMNotConfiguredError/LLMOutputError from
    app.llm — callers must not catch-and-continue on those, since nothing should
    be written to the incident when extraction fails.
    """
    if incident_db.get_incident(incident_id) is None:
        raise IncidentNotFoundError(incident_id)

    when = occurred_at or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)

    logger.info("Analyzing conversation turn: incident=%s speaker=%s", incident_id, speaker)
    analysis = analyze_conversation(incident_id, speaker, text)

    with get_connection() as conn:
        changes = apply_analysis(conn, incident_id, speaker, analysis, when)

    state = incident_db.get_incident_state(incident_id)
    assert state is not None  # existence was checked above

    logger.info(
        "Conversation turn applied: incident=%s facts=%d hypotheses=%d decisions=%d "
        "actions=%d conflicts=%d questions=%d",
        incident_id,
        len(changes.facts),
        len(changes.hypotheses),
        len(changes.decisions),
        len(changes.actions_created) + len(changes.actions_updated),
        len(changes.conflicts_created) + len(changes.conflicts_updated),
        len(changes.unresolved_questions),
    )
    return ConversationTurnResponse(changes=changes, state=state)
