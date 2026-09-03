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
import re
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


# --- Deterministic fact-vs-hypothesis contradiction detection -------------------
# The LLM's own conflict detection (see EXTRACTION_INSTRUCTIONS above) only ever
# compares the ONE new statement being extracted, in a single call, against
# recent context - it can classify a statement as a plain FACT without also
# flagging a CONFLICT against an existing HYPOTHESIS, even when a human would
# clearly see the contradiction (e.g. "the database is overloaded" earlier,
# "database metrics look normal" later). This is a narrow, deterministic
# backstop for exactly that one case - a small curated set of opposite
# incident-state word pairs, gated by requiring the two statements to also
# share a real subject word, so it can never fire on two unrelated statements
# that merely happen to use one of these words. Not a general contradiction/NLP
# engine - it only catches direct state reversals ("X is <bad state>" vs "X is
# <good state>"), which is what this kind of live-incident corrections/updates
# statement almost always looks like.
_CONTRADICTORY_STATE_PAIRS: list[tuple[frozenset[str], frozenset[str]]] = [
    (
        frozenset({"overloaded", "overload", "overloading"}),
        frozenset({"normal", "nominal", "healthy", "fine", "stable", "ok", "okay"}),
    ),
    (
        frozenset({"down", "failing", "failed", "unavailable", "offline", "crashed", "crashing"}),
        frozenset({"up", "available", "online", "healthy", "working", "normal", "stable"}),
    ),
    (
        frozenset({"high", "spiking", "elevated", "surging"}),
        frozenset({"low", "normal", "nominal"}),
    ),
    (
        frozenset({"slow", "degraded", "lagging"}),
        frozenset({"fast", "normal", "nominal", "healthy"}),
    ),
    (
        frozenset({"corrupt", "corrupted"}),
        frozenset({"intact", "fine", "normal"}),
    ),
]

_SUBJECT_WORD_MIN_LENGTH = 4


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _shares_subject(a: str, b: str) -> bool:
    """True if the two statements share at least one non-trivial word - used only

    as a topic gate alongside _CONTRADICTORY_STATE_PAIRS, not a general similarity
    measure, so a short length filter (no stopword list) is enough here.
    """
    wa = {w for w in _words(a) if len(w) >= _SUBJECT_WORD_MIN_LENGTH}
    wb = {w for w in _words(b) if len(w) >= _SUBJECT_WORD_MIN_LENGTH}
    return bool(wa & wb)


def _detect_contradiction(fact_statement: str, hypothesis_statement: str) -> bool:
    """True if `fact_statement` and `hypothesis_statement` are about the same

    subject and assert opposite incident states - see _CONTRADICTORY_STATE_PAIRS.
    """
    if not _shares_subject(fact_statement, hypothesis_statement):
        return False
    fact_words, hyp_words = _words(fact_statement), _words(hypothesis_statement)
    return any(
        (fact_words & side_a and hyp_words & side_b) or (fact_words & side_b and hyp_words & side_a)
        for side_a, side_b in _CONTRADICTORY_STATE_PAIRS
    )


def _log_event(
    conn: sqlite3.Connection,
    changes: ConversationChanges,
    incident_id: str,
    event: str,
    source: str,
    when: datetime,
) -> None:
    changes.timeline_events.append(incident_db.insert_timeline_event(conn, incident_id, event, source, when))


def _same_conflict(existing: Conflict, topic: str, statements: list[ConflictStatement]) -> bool:
    """True if `existing` represents the same underlying disagreement as the

    proposed (topic, statements).

    Two signals, either sufficient on its own: a fuzzy-matching topic (the
    original signal, still what dedupes repeated LLM-detected conflicts on the
    same subject), or a fuzzy-matching statement (new): the LLM path and the
    deterministic fact-vs-hypothesis backstop can both fire for the exact same
    contradiction in the same turn but produce differently-worded topics (a
    short LLM-chosen label vs. the backstop's "Possible contradiction: <the
    hypothesis statement>") - their *statements*, however, are always the
    hypothesis/fact text already on record, so a shared statement is a much
    more reliable "same disagreement" signal across detection paths than topic
    text ever is. Without this, the two paths would silently create two
    separate Conflict rows for one disagreement.
    """
    if _fuzzy_match(existing.topic, topic):
        return True
    return any(_fuzzy_match(es.statement, ns.statement) for es in existing.statements for ns in statements)


def _apply_conflict(
    conn: sqlite3.Connection,
    changes: ConversationChanges,
    incident_id: str,
    topic: str,
    statements: list[ConflictStatement],
    occurred_at: datetime,
    open_conflicts: list[Conflict],
) -> list[Conflict]:
    """Match-or-create a Conflict (see _same_conflict), log it, and return the

    updated open_conflicts list. Shared by both LLM-detected conflicts
    (analysis.conflicts below) and the deterministic fact-vs-hypothesis
    contradiction check, so both paths dedupe/merge the same way - never two
    different conflict rows for what a human would see as the same disagreement.
    """
    match = next((oc for oc in open_conflicts if _same_conflict(oc, topic, statements)), None)
    if match is not None:
        updated = incident_db.append_conflict_statements(conn, match.id, statements)
        changes.conflicts_updated.append(updated)
        open_conflicts = [updated if oc.id == updated.id else oc for oc in open_conflicts]
    else:
        created = incident_db.insert_conflict(conn, incident_id, topic, statements, occurred_at)
        changes.conflicts_created.append(created)
        open_conflicts = [*open_conflicts, created]
    _log_event(conn, changes, incident_id, f"Conflicting information: {topic}", "system", occurred_at)
    return open_conflicts


def apply_analysis(
    conn: sqlite3.Connection,
    incident_id: str,
    speaker: str,
    analysis: ConversationAnalysis,
    occurred_at: datetime,
) -> ConversationChanges:
    """Persist an already-validated ConversationAnalysis. Never partially fails silently."""
    changes = ConversationChanges()
    # Snapshot of hypotheses/conflicts that already existed *before* this turn -
    # only used by the deterministic contradiction check below, so a fact can
    # only conflict with an existing hypothesis, never one proposed in this same
    # turn (see EXTRACTION_INSTRUCTIONS: EchoWard never invents a root cause, and
    # this keeps that check scoped to "existing hypothesis + later fact" only).
    open_hypotheses = incident_db.list_open_hypotheses(conn, incident_id)
    open_conflicts = incident_db.list_open_conflicts(conn, incident_id)

    for f in analysis.facts:
        fact = incident_db.insert_fact(conn, incident_id, f.statement, speaker, occurred_at, f.confidence)
        changes.facts.append(fact)
        _log_event(conn, changes, incident_id, f"Fact reported: {f.statement}", speaker, occurred_at)

        contradicted = next(
            (h for h in open_hypotheses if _detect_contradiction(fact.statement, h.statement)), None
        )
        if contradicted is not None:
            topic = f"Possible contradiction: {contradicted.statement}"
            statements = [
                ConflictStatement(source=contradicted.source, statement=contradicted.statement),
                ConflictStatement(source=speaker, statement=fact.statement),
            ]
            open_conflicts = _apply_conflict(conn, changes, incident_id, topic, statements, occurred_at, open_conflicts)

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

    for c in analysis.conflicts:
        statements = [ConflictStatement(source=s.source, statement=s.statement) for s in c.statements]
        open_conflicts = _apply_conflict(conn, changes, incident_id, c.topic, statements, occurred_at, open_conflicts)

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
