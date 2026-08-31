import { API_URL } from "./api";

export type IncidentStatus = "investigating" | "identified" | "mitigating" | "resolved";

export const INCIDENT_STATUSES: IncidentStatus[] = [
  "investigating",
  "identified",
  "mitigating",
  "resolved",
];

export interface Incident {
  id: string;
  title: string;
  status: IncidentStatus;
  created_at: string;
  updated_at: string;
}

export interface Fact {
  id: string;
  statement: string;
  source: string;
  timestamp: string;
  confidence: string;
}

export interface Hypothesis {
  id: string;
  statement: string;
  source: string;
  timestamp: string;
  status: string;
}

export interface Decision {
  id: string;
  decision: string;
  decided_by: string | null;
  timestamp: string;
}

export interface Action {
  id: string;
  description: string;
  owner: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface TimelineEvent {
  id: string;
  timestamp: string;
  event: string;
  source: string;
}

export interface UnresolvedQuestion {
  id: string;
  question: string;
  status: string;
  created_at: string;
}

export interface ConflictStatement {
  source: string;
  statement: string;
}

export interface Conflict {
  id: string;
  topic: string;
  statements: ConflictStatement[];
  involved_sources: string[];
  status: string;
  detected_at: string;
}

export interface IncidentState {
  incident: Incident;
  facts: Fact[];
  hypotheses: Hypothesis[];
  decisions: Decision[];
  actions: Action[];
  timeline: TimelineEvent[];
  unresolved_questions: UnresolvedQuestion[];
  conflicts: Conflict[];
}

export interface ConversationTurnResponse {
  changes: {
    facts: Fact[];
    hypotheses: Hypothesis[];
    decisions: Decision[];
    actions_created: Action[];
    actions_updated: Action[];
    unresolved_questions: UnresolvedQuestion[];
    conflicts_created: Conflict[];
    conflicts_updated: Conflict[];
    timeline_events: TimelineEvent[];
  };
  state: IncidentState;
}

async function errorDetail(res: Response): Promise<string> {
  const payload = await res.json().catch(() => null);
  const detail = payload?.detail ?? `request failed with status ${res.status}`;
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

async function sendJson<T>(method: "POST" | "PATCH", path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  return sendJson<T>("POST", path, body);
}

export function createIncident(title: string): Promise<Incident> {
  return postJson<Incident>("/api/incidents", { title });
}

export function getIncident(incidentId: string): Promise<Incident> {
  return getJson<Incident>(`/api/incidents/${incidentId}`);
}

export function getIncidentState(incidentId: string): Promise<IncidentState> {
  return getJson<IncidentState>(`/api/incidents/${incidentId}/state`);
}

export function updateIncidentStatus(incidentId: string, status: IncidentStatus): Promise<IncidentState> {
  return sendJson<IncidentState>("PATCH", `/api/incidents/${incidentId}/status`, { status });
}

export function sendConversationTurn(
  incidentId: string,
  speaker: string,
  text: string
): Promise<ConversationTurnResponse> {
  return postJson<ConversationTurnResponse>(`/api/incidents/${incidentId}/conversation`, {
    speaker,
    text,
  });
}
