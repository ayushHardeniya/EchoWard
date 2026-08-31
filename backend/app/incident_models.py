from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class IncidentStatus(str, Enum):
    investigating = "investigating"
    identified = "identified"
    mitigating = "mitigating"
    resolved = "resolved"


class ActionStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    blocked = "blocked"


class HypothesisStatus(str, Enum):
    proposed = "proposed"
    supported = "supported"
    refuted = "refuted"


class QuestionStatus(str, Enum):
    open = "open"
    answered = "answered"


class ConflictStatus(str, Enum):
    unresolved = "unresolved"
    resolved = "resolved"


# --- Domain / persisted records -----------------------------------------------


class Incident(BaseModel):
    id: str
    title: str
    status: IncidentStatus = IncidentStatus.investigating
    created_at: datetime
    updated_at: datetime


class Fact(BaseModel):
    id: str
    incident_id: str
    statement: str
    source: str
    timestamp: datetime
    # We only ever record what a participant reported, not something we've
    # independently verified — see product principle in CLAUDE.md.
    confidence: str = "reported"


class Hypothesis(BaseModel):
    id: str
    incident_id: str
    statement: str
    source: str
    timestamp: datetime
    status: HypothesisStatus = HypothesisStatus.proposed


class Decision(BaseModel):
    id: str
    incident_id: str
    decision: str
    decided_by: str | None = None
    timestamp: datetime


class Action(BaseModel):
    id: str
    incident_id: str
    description: str
    owner: str | None = None
    status: ActionStatus = ActionStatus.pending
    created_at: datetime
    updated_at: datetime


class TimelineEvent(BaseModel):
    id: str
    incident_id: str
    timestamp: datetime
    event: str
    source: str


class UnresolvedQuestion(BaseModel):
    id: str
    incident_id: str
    question: str
    status: QuestionStatus = QuestionStatus.open
    created_at: datetime


class ConflictStatement(BaseModel):
    source: str
    statement: str


class Conflict(BaseModel):
    id: str
    incident_id: str
    topic: str
    statements: list[ConflictStatement]
    involved_sources: list[str]
    status: ConflictStatus = ConflictStatus.unresolved
    detected_at: datetime


class IncidentState(BaseModel):
    """Full snapshot of an incident's current structured picture."""

    incident: Incident
    facts: list[Fact] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    unresolved_questions: list[UnresolvedQuestion] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)


# --- LLM structured-output schema ----------------------------------------------
# What the extraction model must return for a single conversation turn. Kept
# separate from the domain records above: these are proposals with no id/incident
# yet — the intelligence service turns them into real records (or merges them
# into existing ones).


class ExtractedFact(BaseModel):
    statement: str
    confidence: str = "reported"


class ExtractedHypothesis(BaseModel):
    statement: str


class ExtractedDecision(BaseModel):
    decision: str
    decided_by: str | None = None


class ExtractedAction(BaseModel):
    description: str
    owner: str | None = None


class ExtractedQuestion(BaseModel):
    question: str


class ExtractedConflictStatement(BaseModel):
    source: str
    statement: str


class ExtractedConflict(BaseModel):
    topic: str
    statements: list[ExtractedConflictStatement]


class ExtractedTimelineEvent(BaseModel):
    event: str


class ConversationAnalysis(BaseModel):
    """Structured output the extraction LLM must return for one conversation turn."""

    facts: list[ExtractedFact] = Field(default_factory=list)
    hypotheses: list[ExtractedHypothesis] = Field(default_factory=list)
    decisions: list[ExtractedDecision] = Field(default_factory=list)
    actions: list[ExtractedAction] = Field(default_factory=list)
    unresolved_questions: list[ExtractedQuestion] = Field(default_factory=list)
    conflicts: list[ExtractedConflict] = Field(default_factory=list)
    timeline_events: list[ExtractedTimelineEvent] = Field(default_factory=list)


# --- API request/response schemas -----------------------------------------------


class CreateIncidentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class UpdateIncidentStatusRequest(BaseModel):
    status: IncidentStatus


class ConversationTurnRequest(BaseModel):
    speaker: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)
    # Preserve the original conversation timestamp when the caller has one
    # (e.g. an Agora transcript event); otherwise the server time is used.
    occurred_at: datetime | None = None


class ConversationChanges(BaseModel):
    facts: list[Fact] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    actions_created: list[Action] = Field(default_factory=list)
    actions_updated: list[Action] = Field(default_factory=list)
    unresolved_questions: list[UnresolvedQuestion] = Field(default_factory=list)
    conflicts_created: list[Conflict] = Field(default_factory=list)
    conflicts_updated: list[Conflict] = Field(default_factory=list)
    timeline_events: list[TimelineEvent] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(
            [
                self.facts,
                self.hypotheses,
                self.decisions,
                self.actions_created,
                self.actions_updated,
                self.unresolved_questions,
                self.conflicts_created,
                self.conflicts_updated,
                self.timeline_events,
            ]
        )


class ConversationTurnResponse(BaseModel):
    changes: ConversationChanges
    state: IncidentState
