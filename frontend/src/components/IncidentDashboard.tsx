"use client";

import { useEffect, useState } from "react";
import {
  type Action,
  confirmAction,
  type Conflict,
  type CoordinationFinding,
  type CoordinationSeverity,
  type Decision,
  type Fact,
  type Hypothesis,
  INCIDENT_STATUSES,
  type IncidentStatus,
  prepareAction,
  resolveConflict,
  sendConversationTurn,
  type TimelineEvent,
  type UnresolvedQuestion,
  updateIncidentStatus,
  type VoiceIntervention,
} from "@/lib/incidents-api";
import type { AgentStatus, useAgoraRoom } from "@/lib/useAgoraRoom";
import { type StreamStatus, useIncidentStream } from "@/lib/useIncidentStream";

type Room = ReturnType<typeof useAgoraRoom>;

const STATUS_STYLE: Record<IncidentStatus, string> = {
  investigating: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  identified: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  mitigating: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  resolved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
};

const ACTION_STATUS_STYLE: Record<string, string> = {
  pending: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  in_progress: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  awaiting_confirmation: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  confirmed: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  executing: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  completed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  blocked: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

// M5's one allowlisted demo action (see backend/app/tools.py's ALLOWED_ACTIONS) -
// the dashboard only ever offers to prepare this, never arbitrary text.
const ROLLBACK_ACTION_TYPE = "rollback_payment_service";
const ROLLBACK_TARGET = "payment-service";

const COORDINATION_SEVERITY_BADGE: Record<CoordinationSeverity, string> = {
  high: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  medium: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  low: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  info: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
};

const COORDINATION_SEVERITY_BORDER: Record<CoordinationSeverity, string> = {
  high: "border-red-300 dark:border-red-900",
  medium: "border-amber-300 dark:border-amber-900",
  low: "border-zinc-200 dark:border-zinc-800",
  info: "border-zinc-200 dark:border-zinc-800",
};

const STREAM_STYLE: Record<StreamStatus, { label: string; dot: string }> = {
  connecting: { label: "Connecting…", dot: "bg-zinc-400" },
  connected: { label: "Live", dot: "bg-emerald-500" },
  reconnecting: { label: "Reconnecting…", dot: "bg-amber-500 animate-pulse" },
  disconnected: { label: "Disconnected", dot: "bg-red-500" },
};

const VOICE_CONNECTION_LABEL: Record<string, string> = {
  DISCONNECTED: "Not joined",
  CONNECTING: "Connecting…",
  CONNECTED: "Joined",
  RECONNECTING: "Reconnecting…",
  DISCONNECTING: "Leaving…",
};

const ECHOWARD_BADGE: Record<AgentStatus, { label: string; className: string }> = {
  idle: { label: "EchoWard idle", className: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300" },
  starting: {
    label: "EchoWard starting…",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  running: {
    label: "EchoWard listening",
    className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  },
  stopping: {
    label: "EchoWard stopping…",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  error: { label: "EchoWard error", className: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" },
};

// Timeline events are written by the backend as short, prefixed sentences
// (see backend/app/intelligence.py's _log_event calls) - recognizing the
// known prefixes lets the dashboard show a compact type tag instead of the
// raw "Fact reported: ..." wording. Anything that doesn't match a known
// prefix (e.g. an owner-assignment note) just falls back to plain text.
const TIMELINE_TAGS: { prefix: string; label: string; className: string }[] = [
  { prefix: "Fact reported: ", label: "FACT", className: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300" },
  {
    prefix: "Hypothesis proposed: ",
    label: "HYPOTHESIS",
    className: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  },
  { prefix: "Decision: ", label: "DECISION", className: "bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200" },
  { prefix: "Action: ", label: "ACTION", className: "bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200" },
  {
    prefix: "Conflicting information: ",
    label: "CONFLICT",
    className: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  },
  {
    prefix: "Open question: ",
    label: "QUESTION",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
];

function formatTimelineEvent(event: string): { tag: string; className: string; text: string } | null {
  const match = TIMELINE_TAGS.find((t) => event.startsWith(t.prefix));
  if (!match) return null;
  return { tag: match.label, className: match.className, text: event.slice(match.prefix.length) };
}

function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatRelative(iso: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

function isWithin(iso: string, windowMs: number): boolean {
  return Date.now() - new Date(iso).getTime() < windowMs;
}

function useTick(intervalMs: number) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}

export default function IncidentDashboard({
  incidentId,
  room,
  displayName,
  onChangeIncident,
  onJoinVoice,
}: {
  incidentId: string;
  room: Room;
  displayName: string;
  onChangeIncident: () => void;
  onJoinVoice: () => void;
}) {
  const { state, status, error } = useIncidentStream(incidentId);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [liveTranscriptError, setLiveTranscriptError] = useState<string | null>(null);
  const [codeCopied, setCodeCopied] = useState(false);
  useTick(5000); // refresh "just spoke Xm ago" / connection-relative text periodically

  const liveUtterance = room.lastHumanUtterance;

  // M6.1: submit each finalized human utterance from the live Agora room to
  // the same conversation endpoint the incident intelligence pipeline uses.
  // A submission failure is logged and surfaced quietly here - it must never
  // interrupt the Agora room itself (see frontend/src/lib/useAgoraRoom.ts,
  // liveTranscript.ts).
  useEffect(() => {
    if (!liveUtterance) return;
    sendConversationTurn(incidentId, "Participant", liveUtterance.text)
      .then(() => setLiveTranscriptError(null))
      .catch((err) => {
        const message = err instanceof Error ? err.message : "Failed to submit live transcript";
        console.error("[live-transcript] failed to submit utterance to incident intelligence", err);
        setLiveTranscriptError(message);
      });
  }, [liveUtterance, incidentId]);

  async function handleStatusChange(next: IncidentStatus) {
    setStatusError(null);
    try {
      await updateIncidentStatus(incidentId, next);
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : "Failed to update status");
    }
  }

  async function handleCopyCode() {
    try {
      await navigator.clipboard.writeText(incidentId);
      setCodeCopied(true);
      setTimeout(() => setCodeCopied(false), 2000);
    } catch {
      // Clipboard access can fail (permissions, insecure context) - not
      // worth surfacing as an error, the user can still share manually.
    }
  }

  if (error && !state) {
    return (
      <section className="rounded-lg border border-red-300 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
        Could not load this incident: {error}
        <button onClick={onChangeIncident} className="ml-2 underline">
          Start over
        </button>
      </section>
    );
  }

  if (!state) {
    return (
      <section className="rounded-lg border border-zinc-200 bg-white p-6 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
        Loading incident…
      </section>
    );
  }

  const { incident } = state;
  const streamStyle = STREAM_STYLE[status];
  const echoward = ECHOWARD_BADGE[room.agentStatus];
  const voiceJoined = room.channel !== null;

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
      {/* Header: incident identity + live/EchoWard/status state */}
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-zinc-200 pb-4 dark:border-zinc-800">
        <div>
          <p className="text-xs font-semibold tracking-wide text-zinc-400 dark:text-zinc-500">
            ECHOWARD · AI INCIDENT COMMANDER
          </p>
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">{incident.title}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-100 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
              <span className={`h-1.5 w-1.5 rounded-full ${streamStyle.dot}`} />
              {streamStyle.label}
            </span>
            <span
              className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${echoward.className}`}
            >
              {echoward.label}
            </span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <select
            value={incident.status}
            onChange={(e) => handleStatusChange(e.target.value as IncidentStatus)}
            className={`rounded-full border-0 px-3 py-1 text-xs font-semibold uppercase tracking-wide ${STATUS_STYLE[incident.status]}`}
          >
            {INCIDENT_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-2 text-xs text-zinc-400 dark:text-zinc-500">
            <button onClick={handleCopyCode} className="underline">
              {codeCopied ? "Code copied" : "Copy incident code"}
            </button>
            <span>·</span>
            <button onClick={onChangeIncident} className="underline">
              Change incident
            </button>
          </div>
        </div>
      </div>

      {(error || statusError || liveTranscriptError) && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          {error ?? statusError ?? liveTranscriptError}
        </div>
      )}

      {/* Voice room: who's on the call and the mic/EchoWard controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-zinc-50 px-3 py-2 text-sm dark:bg-zinc-800/40">
        <div>
          {voiceJoined ? (
            <ParticipantsSummary room={room} displayName={displayName} />
          ) : (
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              Voice room {(VOICE_CONNECTION_LABEL[room.connectionState] ?? "not joined").toLowerCase()}
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {voiceJoined ? (
            <>
              <SmallButton onClick={() => room.toggleMute()}>{room.isMuted ? "Unmute" : "Mute"}</SmallButton>
              {room.agentStatus === "idle" || room.agentStatus === "error" ? (
                <SmallButton emphasis="positive" onClick={() => room.startEchoWard()}>
                  Start EchoWard
                </SmallButton>
              ) : (
                <SmallButton
                  emphasis="negative"
                  onClick={() => room.stopEchoWard()}
                  disabled={room.agentStatus === "stopping" || room.agentStatus === "starting"}
                >
                  Stop EchoWard
                </SmallButton>
              )}
              <SmallButton onClick={() => room.leave()}>Leave voice</SmallButton>
            </>
          ) : (
            <SmallButton emphasis="positive" onClick={onJoinVoice}>
              Join voice
            </SmallButton>
          )}
        </div>
      </div>

      {/* EchoWard's last proactive voice intervention (M6.2) — a lightweight
          "it just spoke" indicator, not a transcript/history panel. */}
      <VoiceInterventionBanner intervention={state.last_voice_intervention} />

      {/* Coordination — what the team needs to pay attention to next (M4) */}
      <CoordinationPanel findings={state.coordination_findings} />

      {/* Facts / Hypotheses */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Panel title="Facts" badge="CONFIRMED" badgeClass="bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
          {state.facts.length === 0 && <Empty text="No confirmed facts yet." />}
          <ul className="flex flex-col gap-2">
            {state.facts.map((f: Fact) => (
              <li key={f.id} className="rounded-md bg-blue-50 px-3 py-2 text-sm dark:bg-blue-950/40">
                <p className="text-zinc-900 dark:text-zinc-100">{f.statement}</p>
                <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                  {f.source} · {formatClock(f.timestamp)}
                </p>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel
          title="Hypotheses"
          badge="UNCONFIRMED"
          badgeClass="bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300"
        >
          {state.hypotheses.length === 0 && <Empty text="No hypotheses proposed yet." />}
          <ul className="flex flex-col gap-2">
            {state.hypotheses.map((h: Hypothesis) => (
              <li key={h.id} className="rounded-md bg-violet-50 px-3 py-2 text-sm dark:bg-violet-950/40">
                <p className="text-zinc-900 dark:text-zinc-100">{h.statement}</p>
                <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                  possible explanation — not confirmed · {h.source} · {formatClock(h.timestamp)}
                </p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      {/* Conflicts — full width, deliberately prominent */}
      <Panel title="Conflicts" badge="UNRESOLVED" badgeClass="bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300">
        {state.conflicts.length === 0 && <Empty text="No conflicting information reported." />}
        <ul className="flex flex-col gap-2">
          {state.conflicts.map((c: Conflict) => (
            <ConflictRow key={c.id} incidentId={incidentId} conflict={c} />
          ))}
        </ul>
      </Panel>

      {/* Actions / Decisions */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Panel title="Actions">
          {state.actions.length === 0 && <Empty text="No action items yet." />}
          <ul className="flex flex-col gap-2">
            {state.actions.map((a: Action) => (
              <ActionRow key={a.id} incidentId={incidentId} action={a} />
            ))}
          </ul>
        </Panel>

        <Panel title="Decisions">
          {state.decisions.length === 0 && <Empty text="No decisions recorded yet." />}
          <ul className="flex flex-col gap-2">
            {state.decisions.map((d: Decision) => (
              <li key={d.id} className="rounded-md bg-zinc-50 px-3 py-2 text-sm dark:bg-zinc-800/60">
                <p className="text-zinc-900 dark:text-zinc-100">{d.decision}</p>
                <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                  {d.decided_by ?? "unattributed"} · {formatClock(d.timestamp)}
                </p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      {/* Timeline */}
      <Panel title="Timeline">
        {state.timeline.length === 0 && <Empty text="Nothing logged yet." />}
        <ul className="flex flex-col gap-1.5">
          {[...state.timeline]
            .reverse()
            .map((t: TimelineEvent) => {
              const tag = formatTimelineEvent(t.event);
              return (
                <li key={t.id} className="flex items-start gap-2 text-sm">
                  <span className="mt-0.5 w-14 shrink-0 font-mono text-xs text-zinc-400 dark:text-zinc-500">
                    {formatClock(t.timestamp)}
                  </span>
                  {tag && (
                    <span
                      className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${tag.className}`}
                    >
                      {tag.tag}
                    </span>
                  )}
                  <span className="text-zinc-800 dark:text-zinc-200">{tag ? tag.text : t.event}</span>
                  <span className="text-xs text-zinc-400 dark:text-zinc-500">— {t.source}</span>
                </li>
              );
            })}
        </ul>
      </Panel>

      {/* Unresolved questions */}
      <Panel title="Unresolved Questions">
        {state.unresolved_questions.length === 0 && <Empty text="No open questions." />}
        <ul className="flex flex-col gap-1">
          {state.unresolved_questions.map((q: UnresolvedQuestion) => (
            <li key={q.id} className="text-sm text-zinc-700 dark:text-zinc-300">
              {q.question}
            </li>
          ))}
        </ul>
      </Panel>
    </section>
  );
}

/** Compact "who's on the call" summary - never a raw Agora uid, only
 * display names / generic ordinal labels for participants Agora gives no
 * identity for (see backend CLAUDE.md: "Participant names are UI-only"). */
function ParticipantsSummary({ room, displayName }: { room: Room; displayName: string }) {
  const humanRemotes = room.remoteUsers.filter((u) => u.uid !== room.agentUid);
  const labels = [
    `${displayName || "You"}${room.isMuted ? " · muted" : ""}`,
    ...humanRemotes.map((_, i) => `Participant ${i + 2}`),
  ];
  return (
    <span className="text-xs text-zinc-600 dark:text-zinc-300">
      <span className="font-medium text-zinc-800 dark:text-zinc-100">
        {labels.length} in room
      </span>{" "}
      · {labels.join(" · ")}
    </span>
  );
}

function SmallButton({
  onClick,
  disabled,
  emphasis,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  emphasis?: "positive" | "negative";
  children: React.ReactNode;
}) {
  const base = "rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-50";
  const style =
    emphasis === "positive"
      ? "bg-emerald-600 text-white"
      : emphasis === "negative"
        ? "bg-red-600 text-white"
        : "border border-zinc-300 text-zinc-700 dark:border-zinc-700 dark:text-zinc-300";
  return (
    <button onClick={onClick} disabled={disabled} className={`${base} ${style}`}>
      {children}
    </button>
  );
}

const VOICE_INTERVENTION_RECENCY_MS = 10 * 60 * 1000; // 10 minutes

/** Lightweight "EchoWard just spoke" indicator (M6.2) — hidden once the
 * last proactive intervention is no longer recent, and hidden entirely for a
 * fresh incident with no voice history yet. */
function VoiceInterventionBanner({ intervention }: { intervention: VoiceIntervention | null }) {
  if (!intervention) return null;
  if (!isWithin(intervention.spoken_at, VOICE_INTERVENTION_RECENCY_MS)) return null;

  return (
    <div className="rounded-md border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm dark:border-indigo-900 dark:bg-indigo-950/40">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-indigo-500 dark:text-indigo-400">
        EchoWard · Just spoke{" "}
        <span className="font-normal normal-case text-indigo-400 dark:text-indigo-500">
          · {formatRelative(intervention.spoken_at)}
        </span>
      </p>
      <p className="mt-0.5 text-indigo-900 dark:text-indigo-200">&ldquo;{intervention.message}&rdquo;</p>
    </div>
  );
}

/** "What needs attention next" panel — a compact "All clear" state when
 * there's nothing to flag, and the most severe open finding shown larger
 * than the rest when there is. */
function CoordinationPanel({ findings }: { findings: CoordinationFinding[] }) {
  const summary = findings.find((f) => f.type === "situational_summary");
  const attention = findings.filter((f) => f.type !== "situational_summary");
  const [topFinding, ...restFindings] = attention;

  return (
    <Panel
      title="Coordination"
      badge={attention.length > 0 ? `ATTENTION REQUIRED · ${attention.length}` : "ALL CLEAR"}
      badgeClass={
        attention.length > 0
          ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
          : "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
      }
    >
      {summary && <p className="mb-2 text-xs text-zinc-500 dark:text-zinc-400">{summary.description}</p>}
      {attention.length === 0 ? (
        <Empty text="Nothing needs attention right now — EchoWard is watching." />
      ) : (
        <div className="flex flex-col gap-2">
          <FindingCard finding={topFinding} prominent />
          {restFindings.length > 0 && (
            <ul className="flex flex-col gap-1.5">
              {restFindings.map((f) => (
                <li key={f.id}>
                  <FindingCard finding={f} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}

function FindingCard({ finding, prominent = false }: { finding: CoordinationFinding; prominent?: boolean }) {
  return (
    <div
      className={`rounded-md border px-3 text-sm ${prominent ? "py-3" : "py-2"} ${COORDINATION_SEVERITY_BORDER[finding.severity]}`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${COORDINATION_SEVERITY_BADGE[finding.severity]}`}
        >
          {finding.severity}
        </span>
        <p className={`font-medium text-zinc-900 dark:text-zinc-100 ${prominent ? "text-base" : "text-sm"}`}>
          {finding.title}
        </p>
      </div>
      <p className="mt-1 text-zinc-600 dark:text-zinc-300">{finding.description}</p>
    </div>
  );
}

/**
 * One conflict's card, including the explicit human "Resolve" control. Only a
 * human clicking this ever moves a conflict out of `unresolved` - EchoWard
 * itself never picks a side (see CLAUDE.md's product principle), so the UI
 * makes the distinction explicit in the button/resolved-state copy below.
 */
function ConflictRow({ incidentId, conflict }: { incidentId: string; conflict: Conflict }) {
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);
  const unresolved = conflict.status === "unresolved";

  async function handleResolve() {
    setBusy(true);
    setRowError(null);
    try {
      await resolveConflict(incidentId, conflict.id);
    } catch (err) {
      setRowError(err instanceof Error ? err.message : "Failed to resolve conflict");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm dark:border-red-900 dark:bg-red-950/40">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium text-red-900 dark:text-red-200">{conflict.topic}</p>
        <span className="shrink-0 text-xs font-semibold uppercase text-red-700 dark:text-red-400">
          {conflict.status}
        </span>
      </div>
      <ul className="mt-1.5 flex flex-col gap-1">
        {conflict.statements.map((s, i) => (
          <li key={i} className="rounded bg-white/60 px-2 py-1 text-red-800 dark:bg-red-950/30 dark:text-red-300">
            <span className="mr-1 font-medium">{s.source}:</span>
            <span className="line-clamp-2" title={s.statement}>
              &ldquo;{s.statement}&rdquo;
            </span>
          </li>
        ))}
      </ul>

      {unresolved ? (
        <div className="mt-2 flex items-center justify-between gap-2 border-t border-red-200 pt-2 dark:border-red-900">
          <p className="text-xs font-medium text-red-600 dark:text-red-400">
            Human resolution required — EchoWard has not decided who is right.
          </p>
          <button
            onClick={handleResolve}
            disabled={busy}
            className="shrink-0 rounded-md bg-red-600 px-3 py-1 text-xs font-semibold text-white disabled:opacity-50"
          >
            {busy ? "Resolving…" : "Mark resolved"}
          </button>
        </div>
      ) : (
        <p className="mt-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">
          Resolved by a human — EchoWard did not decide this.
        </p>
      )}

      {rowError && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{rowError}</p>}
    </li>
  );
}

/**
 * One action's card, including M5's prepare/confirm controls. The server
 * enforces the actual safety boundary (see app/actions.py) - this component
 * only ever offers the buttons that are valid for the action's current
 * status, never lets the frontend "execute" anything directly.
 */
function ActionRow({ incidentId, action }: { incidentId: string; action: Action }) {
  const [reason, setReason] = useState(action.description);
  const [confirmedBy, setConfirmedBy] = useState("");
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);

  async function handlePrepare() {
    setBusy(true);
    setRowError(null);
    try {
      await prepareAction(incidentId, action.id, ROLLBACK_ACTION_TYPE, ROLLBACK_TARGET, reason.trim() || action.description);
    } catch (err) {
      setRowError(err instanceof Error ? err.message : "Failed to prepare action");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm() {
    setBusy(true);
    setRowError(null);
    try {
      await confirmAction(incidentId, action.id, confirmedBy.trim() || undefined);
    } catch (err) {
      setRowError(err instanceof Error ? err.message : "Failed to confirm action");
    } finally {
      setBusy(false);
    }
  }

  const canPrepare = action.status === "pending" || action.status === "in_progress";
  const awaitingConfirmation = action.status === "awaiting_confirmation";
  const terminal = action.status === "completed" || action.status === "failed";

  return (
    <li className="rounded-md border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800">
      <p className="text-zinc-900 dark:text-zinc-100">{action.description}</p>
      <div className="mt-1 flex items-center justify-between text-xs">
        <span className={action.owner ? "text-zinc-600 dark:text-zinc-300" : "italic text-zinc-400"}>
          Owner: {action.owner ?? "Unassigned"}
        </span>
        <span className={`rounded-full px-2 py-0.5 font-medium ${ACTION_STATUS_STYLE[action.status]}`}>
          {action.status.replace(/_/g, " ")}
        </span>
      </div>

      {canPrepare && (
        <div className="mt-2 flex flex-col gap-1.5 border-t border-zinc-100 pt-2 dark:border-zinc-800">
          <input
            className="rounded-md border border-zinc-300 bg-transparent px-2 py-1 text-xs text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason for rollback"
          />
          <button
            onClick={handlePrepare}
            disabled={busy}
            className="self-start rounded-md bg-zinc-200 px-3 py-1 text-xs font-medium text-zinc-800 disabled:opacity-50 dark:bg-zinc-700 dark:text-zinc-100"
          >
            Prepare: Roll back payment service
          </button>
        </div>
      )}

      {awaitingConfirmation && (
        <div className="mt-2 flex flex-col gap-1.5 border-t border-amber-200 pt-2 dark:border-amber-900">
          <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">Human confirmation required</p>
          <p className="text-xs text-amber-700 dark:text-amber-400">
            {action.action_type} → {action.target}
            {action.reason ? ` — ${action.reason}` : ""}
          </p>
          <div className="flex gap-2">
            <input
              className="flex-1 rounded-md border border-zinc-300 bg-transparent px-2 py-1 text-xs text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
              value={confirmedBy}
              onChange={(e) => setConfirmedBy(e.target.value)}
              placeholder="Your name (optional)"
            />
            <button
              onClick={handleConfirm}
              disabled={busy}
              className="rounded-md bg-amber-600 px-3 py-1 text-xs font-semibold text-white disabled:opacity-50"
            >
              Confirm &amp; Execute
            </button>
          </div>
        </div>
      )}

      {terminal && action.tool_result && (
        <p
          className={`mt-2 border-t pt-2 text-xs ${
            action.tool_result.success
              ? "border-emerald-100 text-emerald-700 dark:border-emerald-900 dark:text-emerald-400"
              : "border-red-100 text-red-700 dark:border-red-900 dark:text-red-400"
          }`}
        >
          {action.tool_result.message}
        </p>
      )}

      {rowError && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{rowError}</p>}
    </li>
  );
}

function Panel({
  title,
  badge,
  badgeClass,
  children,
}: {
  title: string;
  badge?: string;
  badgeClass?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">{title}</h3>
        {badge && (
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${badgeClass}`}>{badge}</span>
        )}
      </div>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-sm text-zinc-400 dark:text-zinc-500">{text}</p>;
}
