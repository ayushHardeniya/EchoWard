"""Coordination intelligence (M4): reasons over the CURRENT incident state (not

the raw conversation) to surface what the incident team needs to pay attention
to next - conflicts awaiting resolution, unowned/stale actions, decisions with
no tracked follow-up, hypotheses being acted on as if confirmed, open risks,
and semantic information gaps.

This is a read/derive layer on top of the existing M2/M3 incident state - it
never mutates a Fact/Hypothesis/Decision/Action/Conflict, and it never promotes
a hypothesis to a fact. It only produces/updates/resolves CoordinationFinding
rows, keyed by a stable `dedup_key` so repeated analysis passes update existing
findings in place instead of duplicating them.

Everything except `analyze_missing_information` is deterministic and requires
no LLM. That one optional check calls Gemini with the structured state (never
the raw transcript) to catch semantic gaps the structured fields can't express
on their own (e.g. "impact is known but affected scope isn't"); it fails safe
(returns no findings) if the LLM isn't configured or the call fails, so the
rest of coordination intelligence keeps working without it.
"""

import difflib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app import incident_db
from app.db import get_connection
from app.incident_models import (
    ActionStatus,
    ConflictStatus,
    CoordinationFindingStatus,
    CoordinationFindingType,
    CoordinationGapAnalysis,
    CoordinationSeverity,
    HypothesisStatus,
    IncidentState,
    QuestionStatus,
)
from app.llm import LLMOutputError, generate_structured

logger = logging.getLogger("echoward.coordination")

HYPOTHESIS_RISK_OVERLAP_THRESHOLD = 0.2
DECISION_FOLLOWUP_OVERLAP_THRESHOLD = 0.25
MISSING_INFO_DEDUP_THRESHOLD = 0.5

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in", "on",
    "for", "and", "or", "this", "that", "it", "its", "we", "our", "us", "let", "lets", "since",
    "so", "just", "will", "would", "could", "should", "can", "may", "might", "do", "does", "did",
    "not", "no", "yes", "if", "then", "than", "with", "from", "by", "as", "at", "but", "up",
}


def _significant_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 2 and w not in _STOPWORDS}


def _compound_joins(text: str) -> set[str]:
    """Adjacent word pairs joined solid (e.g. "roll", "back" -> "rollback"),

    so a compound term spelled as one word in one text ("rollback") is still
    recognized against its split/hyphenated form elsewhere ("roll back",
    "roll-back") - hyphens already tokenize the same as spaces via the regex
    in `_significant_words`, so only the solid-vs-split case needs this.
    Skipped when both halves are stopwords, to avoid meaningless joins like
    "of-the" inflating matches.
    """
    raw_words = re.findall(r"[a-z0-9]+", text.lower())
    joins = set()
    for w1, w2 in zip(raw_words, raw_words[1:]):
        if w1 in _STOPWORDS and w2 in _STOPWORDS:
            continue
        joined = w1 + w2
        if len(joined) >= 4:
            joins.add(joined)
    return joins


def _word_overlap_ratio(a: str, b: str) -> float:
    """Fraction of the smaller word-set shared between two statements - a cheap,

    deterministic proxy for "are these two statements about the same thing",
    used only to connect a hypothesis/decision to a later action/decision, not
    to judge whether they agree.

    A compound-word fallback (see `_compound_joins`) can only ever raise this
    ratio, never lower it: when neither text contains a same-concept compound
    spelled differently, `compound_hits` is 0 and the result is identical to
    plain word overlap, so normal-case matching is unchanged.
    """
    wa, wb = _significant_words(a), _significant_words(b)
    if not wa or not wb:
        return 0.0
    denom = min(len(wa), len(wb))
    primary = len(wa & wb) / denom

    compound_hits = len((wa & _compound_joins(b)) | (wb & _compound_joins(a)))
    if not compound_hits:
        return primary
    return min(1.0, primary + compound_hits / denom)


def _text_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


@dataclass
class ProposedFinding:
    dedup_key: str
    type: CoordinationFindingType
    severity: CoordinationSeverity
    title: str
    description: str
    related_ids: list[str] = field(default_factory=list)


# --- Deterministic checks -------------------------------------------------------


def _conflict_findings(state: IncidentState) -> list[ProposedFinding]:
    """Mirror M3's existing unresolved Conflict records as findings needing

    attention - M4 reuses conflict detection/representation rather than
    building a second one, per the product principle that conflicts are
    surfaced, never adjudicated.
    """
    findings = []
    for c in state.conflicts:
        if c.status != ConflictStatus.unresolved:
            continue
        statement_text = "; ".join(f'{s.source}: "{s.statement}"' for s in c.statements)
        findings.append(
            ProposedFinding(
                dedup_key=f"conflict:{c.id}",
                type=CoordinationFindingType.conflict,
                severity=CoordinationSeverity.high,
                title=f"Conflicting reports: {c.topic}",
                description=f"{statement_text}. Requires human resolution - EchoWard has not decided who is right.",
                related_ids=[c.id],
            )
        )
    return findings


def _unowned_action_findings(state: IncidentState) -> list[ProposedFinding]:
    findings = []
    for a in state.actions:
        if a.owner is None and a.status in (ActionStatus.pending, ActionStatus.in_progress):
            findings.append(
                ProposedFinding(
                    dedup_key=f"unowned_action:{a.id}",
                    type=CoordinationFindingType.unowned_action,
                    severity=CoordinationSeverity.high,
                    title="Action has no owner",
                    description=f'"{a.description}" is not yet assigned to anyone.',
                    related_ids=[a.id],
                )
            )
    return findings


def _all_state_timestamps(state: IncidentState) -> list[datetime]:
    timestamps = [f.timestamp for f in state.facts]
    timestamps += [h.timestamp for h in state.hypotheses]
    timestamps += [d.timestamp for d in state.decisions]
    timestamps += [t.timestamp for t in state.timeline]
    timestamps += [a.created_at for a in state.actions]
    return timestamps


def _stale_action_findings(state: IncidentState) -> list[ProposedFinding]:
    """An action that was assigned an owner but has had no status/owner change

    since creation, while the rest of the conversation has kept moving (i.e.
    some other record in the incident is timestamped after it) - a follow-up
    gap distinct from an unowned action. Uses only the incident's own relative
    timestamps, never a wall-clock timeout, so it stays demo-reliable.
    """
    findings = []
    all_timestamps = _all_state_timestamps(state)
    latest = max(all_timestamps) if all_timestamps else None
    if latest is None:
        return findings
    for a in state.actions:
        if (
            a.owner is not None
            and a.status == ActionStatus.pending
            and a.updated_at == a.created_at
            and latest > a.created_at
        ):
            findings.append(
                ProposedFinding(
                    dedup_key=f"stale_action:{a.id}",
                    type=CoordinationFindingType.stale_action,
                    severity=CoordinationSeverity.medium,
                    title="Action has no follow-up",
                    description=(
                        f'"{a.description}" was assigned to {a.owner}, but there has been no status '
                        "update since while the conversation moved on."
                    ),
                    related_ids=[a.id],
                )
            )
    return findings


def _decision_followup_findings(state: IncidentState) -> list[ProposedFinding]:
    findings = []
    for d in state.decisions:
        best_action = None
        best_ratio = 0.0
        for a in state.actions:
            ratio = _word_overlap_ratio(d.decision, a.description)
            if ratio > best_ratio:
                best_ratio, best_action = ratio, a

        if best_action is None or best_ratio < DECISION_FOLLOWUP_OVERLAP_THRESHOLD:
            findings.append(
                ProposedFinding(
                    dedup_key=f"decision_followup:{d.id}",
                    type=CoordinationFindingType.decision_followup,
                    severity=CoordinationSeverity.high,
                    title="Decision has no tracked follow-up",
                    description=f'"{d.decision}" was decided, but no action is tracking its execution.',
                    related_ids=[d.id],
                )
            )
        elif best_action.owner is None:
            findings.append(
                ProposedFinding(
                    dedup_key=f"decision_followup:{d.id}",
                    type=CoordinationFindingType.decision_followup,
                    severity=CoordinationSeverity.high,
                    title="Decision has no assigned follow-up owner",
                    description=(
                        f'"{d.decision}" has an associated action ("{best_action.description}") '
                        "but it has no owner yet."
                    ),
                    related_ids=[d.id, best_action.id],
                )
            )
    return findings


def _hypothesis_risk_findings(state: IncidentState) -> list[ProposedFinding]:
    """Flag a still-unconfirmed hypothesis when a later decision/action's text

    suggests the team is treating it as established - EchoWard never promotes
    the hypothesis itself, only raises the risk.
    """
    findings = []
    for h in state.hypotheses:
        if h.status != HypothesisStatus.proposed:
            continue

        candidates: list[tuple[str, datetime, str, str]] = []
        candidates += [
            ("decision", d.timestamp, d.decision, d.id) for d in state.decisions if d.timestamp >= h.timestamp
        ]
        candidates += [
            ("action", a.created_at, a.description, a.id) for a in state.actions if a.created_at >= h.timestamp
        ]
        candidates.sort(key=lambda c: c[1])

        match = next(
            (c for c in candidates if _word_overlap_ratio(h.statement, c[2]) >= HYPOTHESIS_RISK_OVERLAP_THRESHOLD),
            None,
        )
        if match is not None:
            kind, _, text, entity_id = match
            findings.append(
                ProposedFinding(
                    dedup_key=f"hypothesis_risk:{h.id}",
                    type=CoordinationFindingType.hypothesis_risk,
                    severity=CoordinationSeverity.medium,
                    title="Team may be acting on an unconfirmed hypothesis",
                    description=(
                        f'"{h.statement}" is still an unconfirmed hypothesis, but the {kind} '
                        f'"{text}" appears to treat it as established.'
                    ),
                    related_ids=[h.id, entity_id],
                )
            )
    return findings


def _action_awaiting_confirmation_findings(state: IncidentState) -> list[ProposedFinding]:
    """An action EchoWard has prepared but a human hasn't confirmed yet (M5) -

    resolves automatically once the action moves past this status (confirmed,
    executing, completed, or failed), since it's simply not proposed anymore.
    """
    findings = []
    for a in state.actions:
        if a.status != ActionStatus.awaiting_confirmation:
            continue
        findings.append(
            ProposedFinding(
                dedup_key=f"action_awaiting_confirmation:{a.id}",
                type=CoordinationFindingType.action_awaiting_confirmation,
                severity=CoordinationSeverity.high,
                title="Action ready for execution, awaiting confirmation",
                description=f'"{a.description}" has been prepared and needs human confirmation to execute.',
                related_ids=[a.id],
            )
        )
    return findings


def _action_failed_findings(state: IncidentState) -> list[ProposedFinding]:
    """A confirmed action whose execution failed (M5) - stays visible until a

    human addresses it; EchoWard never silently retries or substitutes
    another action.
    """
    findings = []
    for a in state.actions:
        if a.status != ActionStatus.failed:
            continue
        result_note = f" Result: {a.tool_result.message}" if a.tool_result else ""
        findings.append(
            ProposedFinding(
                dedup_key=f"action_failed:{a.id}",
                type=CoordinationFindingType.action_failed,
                severity=CoordinationSeverity.high,
                title="Action execution failed",
                description=f'"{a.description}" did not complete successfully.{result_note}',
                related_ids=[a.id],
            )
        )
    return findings


def _unresolved_question_findings(state: IncidentState) -> list[ProposedFinding]:
    findings = []
    for q in state.unresolved_questions:
        if q.status != QuestionStatus.open:
            continue
        findings.append(
            ProposedFinding(
                dedup_key=f"unresolved_risk:{q.id}",
                type=CoordinationFindingType.unresolved_risk,
                severity=CoordinationSeverity.medium,
                title="Open question remains unanswered",
                description=q.question,
                related_ids=[q.id],
            )
        )
    return findings


def _situational_summary_finding(state: IncidentState) -> ProposedFinding:
    """A single, always-present per-incident checkpoint - compact enough to

    read at a glance now, and to speak aloud once M6 adds voice summaries.
    """
    open_actions = [a for a in state.actions if a.status in (ActionStatus.pending, ActionStatus.in_progress)]
    unowned = [a for a in open_actions if a.owner is None]
    awaiting_confirmation = [a for a in state.actions if a.status == ActionStatus.awaiting_confirmation]
    failed_actions = [a for a in state.actions if a.status == ActionStatus.failed]
    open_conflicts = [c for c in state.conflicts if c.status == ConflictStatus.unresolved]
    open_questions = [q for q in state.unresolved_questions if q.status == QuestionStatus.open]
    open_hypotheses = [h for h in state.hypotheses if h.status == HypothesisStatus.proposed]

    parts = [
        f"{len(open_actions)} action(s) open ({len(unowned)} unowned)",
        f"{len(open_conflicts)} unresolved conflict(s)",
        f"{len(open_questions)} open question(s)",
        f"{len(open_hypotheses)} unconfirmed hypothesis(es)" if open_hypotheses else "no hypotheses proposed yet",
    ]
    if awaiting_confirmation:
        parts.append(f"{len(awaiting_confirmation)} action(s) awaiting confirmation")
    if failed_actions:
        parts.append(f"{len(failed_actions)} action(s) failed execution")

    return ProposedFinding(
        dedup_key="situational_summary",
        type=CoordinationFindingType.situational_summary,
        severity=CoordinationSeverity.info,
        title="Incident checkpoint",
        description=". ".join(parts) + ".",
    )


def analyze_incident_deterministic(state: IncidentState) -> list[ProposedFinding]:
    """Pure function, no I/O: current state in, proposed findings out."""
    findings: list[ProposedFinding] = []
    findings += _conflict_findings(state)
    findings += _unowned_action_findings(state)
    findings += _stale_action_findings(state)
    findings += _decision_followup_findings(state)
    findings += _hypothesis_risk_findings(state)
    findings += _action_awaiting_confirmation_findings(state)
    findings += _action_failed_findings(state)
    findings += _unresolved_question_findings(state)
    findings.append(_situational_summary_finding(state))
    return findings


# --- Optional LLM-backed check: semantic information gaps -----------------------

MISSING_INFO_INSTRUCTIONS = """You are EchoWard's coordination-intelligence layer.

You review the CURRENT STRUCTURED STATE of a live incident (not the raw
conversation) and identify concrete, material information gaps that block the
team from coordinating effectively - e.g. impact is reported but affected
scope isn't confirmed, a mitigation is being discussed with no confirmation
it's safe, or a suspected cause is discussed without supporting evidence.

Only report a gap that is clearly supported by what's missing from the state
below - never invent facts, never guess an answer, never assume information
that isn't there. Do not report vague or generic gaps ("more information is
needed"); each gap must reference something specific already in the state.

If the state already covers everything necessary given what's known so far,
return an empty list. Do not force a finding.

For each gap, return a short title, a one-sentence description a responder
could act on immediately, and a severity of "low", "medium", or "high"."""


def _build_missing_info_prompt(state: IncidentState) -> str:
    sections: list[str] = [f"Incident: {state.incident.title} (status: {state.incident.status.value})"]
    if state.facts:
        sections.append("Facts:\n" + "\n".join(f"- ({f.source}) {f.statement}" for f in state.facts))
    if state.hypotheses:
        open_hyps = [h for h in state.hypotheses if h.status == HypothesisStatus.proposed]
        if open_hyps:
            sections.append("Unconfirmed hypotheses:\n" + "\n".join(f"- ({h.source}) {h.statement}" for h in open_hyps))
    if state.decisions:
        sections.append("Decisions:\n" + "\n".join(f"- {d.decision}" for d in state.decisions))
    if state.actions:
        sections.append(
            "Actions:\n"
            + "\n".join(
                f"- {a.description} (owner: {a.owner or 'unassigned'}, status: {a.status.value})"
                for a in state.actions
            )
        )
    open_questions = [q for q in state.unresolved_questions if q.status == QuestionStatus.open]
    if open_questions:
        sections.append("Open questions:\n" + "\n".join(f"- {q.question}" for q in open_questions))
    open_conflicts = [c for c in state.conflicts if c.status == ConflictStatus.unresolved]
    if open_conflicts:
        sections.append("Unresolved conflicts:\n" + "\n".join(f"- {c.topic}" for c in open_conflicts))

    body = "\n\n".join(sections)
    return (
        f"{MISSING_INFO_INSTRUCTIONS}\n\n--- Current incident state ---\n{body}\n\n"
        "Return the gaps as JSON matching the schema."
    )


def analyze_missing_information(state: IncidentState) -> list[ProposedFinding]:
    """Optional semantic gap detection. Fails safe (empty list) if the LLM isn't

    configured or the call fails - coordination intelligence must stay usable
    without it. Never touches Fact/Hypothesis records, so it has no channel to
    promote a hypothesis to a fact.
    """
    try:
        result = generate_structured(_build_missing_info_prompt(state), CoordinationGapAnalysis)
    except LLMOutputError as exc:
        logger.info("Coordination missing-information analysis skipped: %s", exc)
        return []

    findings = []
    for i, gap in enumerate(result.gaps):  # type: ignore[attr-defined]
        severity_value = gap.severity if gap.severity in {"low", "medium", "high"} else "medium"
        findings.append(
            ProposedFinding(
                # Placeholder key - _reconcile_missing_information() below replaces this
                # with a matched existing finding's key, or a fresh stable one, before persisting.
                dedup_key=f"missing_information:new:{i}",
                type=CoordinationFindingType.missing_information,
                severity=CoordinationSeverity(severity_value),
                title=gap.title,
                description=gap.description,
            )
        )
    return findings


def _reconcile_missing_information(state: IncidentState) -> list[ProposedFinding]:
    """Fuzzy-match freshly proposed gaps against already-open missing_information

    findings (LLM phrasing drifts turn to turn even for "the same" gap) so they
    update in place instead of multiplying; a genuinely new gap gets a fresh key.
    """
    proposed = analyze_missing_information(state)
    if not proposed:
        return []

    existing_open = [
        f
        for f in incident_db.list_coordination_findings(state.incident.id)
        if f.type == CoordinationFindingType.missing_information and f.status == CoordinationFindingStatus.open
    ]
    matched_keys: set[str] = set()
    for gap in proposed:
        match = next(
            (
                e
                for e in existing_open
                if e.dedup_key not in matched_keys
                and _text_similarity(e.description, gap.description) >= MISSING_INFO_DEDUP_THRESHOLD
            ),
            None,
        )
        if match is not None:
            gap.dedup_key = match.dedup_key
            matched_keys.add(match.dedup_key)
        else:
            gap.dedup_key = f"missing_information:{uuid.uuid4().hex}"
    return proposed


# --- Persistence / reconciliation ------------------------------------------------


def _persist_reconciled(incident_id: str, proposed: list[ProposedFinding]) -> None:
    existing = incident_db.list_coordination_findings(incident_id)
    existing_by_key = {f.dedup_key: f for f in existing}
    seen_keys: set[str] = set()
    now = datetime.now(UTC)

    with get_connection() as conn:
        for p in proposed:
            incident_db.upsert_coordination_finding(
                conn, incident_id, p.dedup_key, p.type, p.severity, p.title, p.description, p.related_ids, now
            )
            seen_keys.add(p.dedup_key)

        for key, existing_finding in existing_by_key.items():
            if key not in seen_keys and existing_finding.status == CoordinationFindingStatus.open:
                incident_db.resolve_coordination_finding(conn, existing_finding.id, now)


def _carried_forward_missing_information(incident_id: str) -> list[ProposedFinding]:
    """Re-propose existing open missing_information findings unchanged, without

    calling Gemini. Used when a refresh pass deliberately skips the LLM check
    (see `include_missing_information` below) - without this, their absence
    from the proposed set would make `_persist_reconciled` treat them as
    "no longer applicable" and resolve them, which is wrong: skipping the
    check this round says nothing about whether the gap is still real.
    """
    return [
        ProposedFinding(
            dedup_key=f.dedup_key,
            type=f.type,
            severity=f.severity,
            title=f.title,
            description=f.description,
            related_ids=f.related_ids,
        )
        for f in incident_db.list_coordination_findings(incident_id)
        if f.type == CoordinationFindingType.missing_information and f.status == CoordinationFindingStatus.open
    ]


def refresh_coordination_findings(
    incident_id: str, *, include_missing_information: bool = True
) -> IncidentState | None:
    """The M4 entry point: recompute coordination findings for one incident and

    persist the result. Returns the incident's fresh full state (coordination
    findings included) for the caller to broadcast/return, or None if the
    incident doesn't exist.

    `include_missing_information` gates the one Gemini-backed check
    (`analyze_missing_information`, via `_reconcile_missing_information`) -
    every other check here is deterministic and always runs. Defaults to True
    (unchanged behavior for conversation turns and status changes). A caller
    that must stay Gemini-independent and low-latency - e.g. the explicit
    human conflict-resolve endpoint - passes False; existing open
    missing_information findings are then carried forward unchanged rather
    than dropped, so a deterministic-only refresh can never resolve them.
    """
    state = incident_db.get_incident_state(incident_id)
    if state is None:
        return None

    proposed = analyze_incident_deterministic(state)
    if include_missing_information:
        proposed += _reconcile_missing_information(state)
    else:
        proposed += _carried_forward_missing_information(incident_id)
    _persist_reconciled(incident_id, proposed)

    return incident_db.get_incident_state(incident_id)
