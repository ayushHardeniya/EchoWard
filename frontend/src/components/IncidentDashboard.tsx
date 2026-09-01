"use client";

import { useEffect, useState } from "react";
import {
  type Action,
  type Conflict,
  type CoordinationFinding,
  type CoordinationSeverity,
  createIncident,
  type Decision,
  type Fact,
  type Hypothesis,
  INCIDENT_STATUSES,
  type IncidentStatus,
  sendConversationTurn,
  type TimelineEvent,
  type UnresolvedQuestion,
  updateIncidentStatus,
} from "@/lib/incidents-api";
import { type StreamStatus, useIncidentStream } from "@/lib/useIncidentStream";

const LAST_INCIDENT_KEY = "echoward:lastIncidentId";

const STATUS_STYLE: Record<IncidentStatus, string> = {
  investigating: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  identified: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  mitigating: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  resolved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
};

const ACTION_STATUS_STYLE: Record<string, string> = {
  pending: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  in_progress: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  completed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  blocked: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

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

function useTick(intervalMs: number) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}

export default function IncidentDashboard({ titleHint }: { titleHint?: string }) {
  const [incidentId, setIncidentId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return localStorage.getItem(LAST_INCIDENT_KEY);
  });
  const [openIdInput, setOpenIdInput] = useState("");
  const [titleInput, setTitleInput] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    if (incidentId) localStorage.setItem(LAST_INCIDENT_KEY, incidentId);
  }, [incidentId]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      const incident = await createIncident(titleInput.trim() || titleHint || "Untitled incident");
      setIncidentId(incident.id);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create incident");
    } finally {
      setCreating(false);
    }
  }

  function handleOpen(e: React.FormEvent) {
    e.preventDefault();
    if (openIdInput.trim()) setIncidentId(openIdInput.trim());
  }

  if (!incidentId) {
    return (
      <section className="flex flex-col gap-4 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
        <div>
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Incident Command</h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Create a new incident, or open one that&rsquo;s already running (paste its id — useful for
            watching the same incident update live from a second browser tab).
          </p>
        </div>

        {createError && (
          <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {createError}
          </div>
        )}

        <form onSubmit={handleCreate} className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
            value={titleInput}
            onChange={(e) => setTitleInput(e.target.value)}
            placeholder={titleHint ? `Incident title (e.g. ${titleHint})` : "Incident title"}
          />
          <button
            type="submit"
            disabled={creating}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
          >
            Create Incident
          </button>
        </form>

        <form onSubmit={handleOpen} className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-zinc-300 bg-transparent px-3 py-2 font-mono text-sm text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
            value={openIdInput}
            onChange={(e) => setOpenIdInput(e.target.value)}
            placeholder="Or paste an existing incident id"
          />
          <button
            type="submit"
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 dark:border-zinc-700 dark:text-zinc-300"
          >
            Open
          </button>
        </form>
      </section>
    );
  }

  return <DashboardView incidentId={incidentId} onChangeIncident={() => setIncidentId(null)} />;
}

function DashboardView({
  incidentId,
  onChangeIncident,
}: {
  incidentId: string;
  onChangeIncident: () => void;
}) {
  const { state, status, error } = useIncidentStream(incidentId);
  const [statusError, setStatusError] = useState<string | null>(null);
  useTick(5000); // refresh "last updated" / connection-relative text periodically

  async function handleStatusChange(next: IncidentStatus) {
    setStatusError(null);
    try {
      await updateIncidentStatus(incidentId, next);
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : "Failed to update status");
    }
  }

  if (error && !state) {
    return (
      <section className="rounded-lg border border-red-300 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
        Could not load incident {incidentId}: {error}
        <button onClick={onChangeIncident} className="ml-2 underline">
          Choose a different incident
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

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
      {/* Header / overview */}
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-zinc-200 pb-4 dark:border-zinc-800">
        <div>
          <p className="text-xs font-semibold tracking-wide text-zinc-400 dark:text-zinc-500">
            ECHOWARD · AI INCIDENT COMMANDER
          </p>
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">{incident.title}</h2>
          <p className="mt-1 font-mono text-xs text-zinc-400 dark:text-zinc-500">#{incident.id}</p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${streamStyle.dot}`} />
            <span className="text-xs text-zinc-500 dark:text-zinc-400">{streamStyle.label}</span>
          </div>
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
          <p className="text-xs text-zinc-400 dark:text-zinc-500">
            Updated {formatRelative(incident.updated_at)}
          </p>
          <button onClick={onChangeIncident} className="text-xs text-zinc-400 underline dark:text-zinc-500">
            Change incident
          </button>
        </div>
      </div>

      {(error || statusError) && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          {error ?? statusError}
        </div>
      )}

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
      <Panel
        title="Conflicts"
        badge="UNRESOLVED"
        badgeClass="bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
      >
        {state.conflicts.length === 0 && <Empty text="No conflicting information reported." />}
        <ul className="flex flex-col gap-2">
          {state.conflicts.map((c: Conflict) => (
            <li
              key={c.id}
              className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm dark:border-red-900 dark:bg-red-950/40"
            >
              <div className="flex items-center justify-between">
                <p className="font-medium text-red-900 dark:text-red-200">! {c.topic}</p>
                <span className="text-xs uppercase text-red-700 dark:text-red-400">{c.status}</span>
              </div>
              <ul className="mt-1 flex flex-col gap-1">
                {c.statements.map((s, i) => (
                  <li key={i} className="text-red-800 dark:text-red-300">
                    <span className="font-medium">{s.source}:</span> &ldquo;{s.statement}&rdquo;
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">
                Surfaced for humans to resolve — EchoWard does not decide who is right.
              </p>
            </li>
          ))}
        </ul>
      </Panel>

      {/* Actions / Decisions */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Panel title="Actions">
          {state.actions.length === 0 && <Empty text="No action items yet." />}
          <ul className="flex flex-col gap-2">
            {state.actions.map((a: Action) => (
              <li
                key={a.id}
                className="rounded-md border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800"
              >
                <p className="text-zinc-900 dark:text-zinc-100">{a.description}</p>
                <div className="mt-1 flex items-center justify-between text-xs">
                  <span className={a.owner ? "text-zinc-600 dark:text-zinc-300" : "italic text-zinc-400"}>
                    Owner: {a.owner ?? "Unassigned"}
                  </span>
                  <span className={`rounded-full px-2 py-0.5 font-medium ${ACTION_STATUS_STYLE[a.status]}`}>
                    {a.status.replace("_", " ")}
                  </span>
                </div>
              </li>
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
        <ul className="flex flex-col gap-1">
          {[...state.timeline]
            .reverse()
            .map((t: TimelineEvent) => (
              <li key={t.id} className="flex gap-3 text-sm">
                <span className="w-14 shrink-0 font-mono text-xs text-zinc-400 dark:text-zinc-500">
                  {formatClock(t.timestamp)}
                </span>
                <span className="text-zinc-800 dark:text-zinc-200">{t.event}</span>
                <span className="text-xs text-zinc-400 dark:text-zinc-500">— {t.source}</span>
              </li>
            ))}
        </ul>
      </Panel>

      {/* Unresolved questions */}
      <Panel title="Unresolved Questions">
        {state.unresolved_questions.length === 0 && <Empty text="No open questions." />}
        <ul className="flex flex-col gap-1">
          {state.unresolved_questions.map((q: UnresolvedQuestion) => (
            <li key={q.id} className="text-sm text-zinc-700 dark:text-zinc-300">
              ? {q.question}
            </li>
          ))}
        </ul>
      </Panel>

      <DevConversationControl incidentId={incidentId} />
    </section>
  );
}

/** Compact "what needs attention next" panel — findings, not raw AI text. */
function CoordinationPanel({ findings }: { findings: CoordinationFinding[] }) {
  const summary = findings.find((f) => f.type === "situational_summary");
  const attention = findings.filter((f) => f.type !== "situational_summary");

  return (
    <Panel title="Coordination" badge="ATTENTION REQUIRED" badgeClass="bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200">
      {summary && (
        <p className="mb-2 text-xs text-zinc-500 dark:text-zinc-400">{summary.description}</p>
      )}
      {attention.length === 0 ? (
        <Empty text="No coordination issues detected." />
      ) : (
        <ul className="flex flex-col gap-2">
          {attention.map((f) => (
            <li
              key={f.id}
              className={`rounded-md border px-3 py-2 text-sm ${COORDINATION_SEVERITY_BORDER[f.severity]}`}
            >
              <div className="flex items-center gap-2">
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${COORDINATION_SEVERITY_BADGE[f.severity]}`}
                >
                  {f.severity}
                </span>
                <p className="font-medium text-zinc-900 dark:text-zinc-100">{f.title}</p>
              </div>
              <p className="mt-1 text-zinc-600 dark:text-zinc-300">{f.description}</p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
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
        <h3 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {title}
        </h3>
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

/** Small dev-only control for exercising the pipeline without a live Agora session. */
function DevConversationControl({ incidentId }: { incidentId: string }) {
  const [speaker, setSpeaker] = useState("Alice");
  const [text, setText] = useState("Payments are failing for about 30% of users.");
  const [busy, setBusy] = useState(false);
  const [devError, setDevError] = useState<string | null>(null);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!speaker.trim() || !text.trim()) return;
    setBusy(true);
    setDevError(null);
    try {
      await sendConversationTurn(incidentId, speaker.trim(), text.trim());
    } catch (err) {
      setDevError(err instanceof Error ? err.message : "Failed to send statement");
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="mt-2 border-t border-zinc-200 pt-3 text-sm dark:border-zinc-800">
      <summary className="cursor-pointer text-xs text-zinc-400 dark:text-zinc-500">
        Dev: send a test conversation statement
      </summary>
      <form onSubmit={handleSend} className="mt-2 flex flex-col gap-2 sm:flex-row">
        <input
          className="w-28 rounded-md border border-zinc-300 bg-transparent px-2 py-1.5 text-xs text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
          value={speaker}
          onChange={(e) => setSpeaker(e.target.value)}
          placeholder="Speaker"
        />
        <input
          className="flex-1 rounded-md border border-zinc-300 bg-transparent px-2 py-1.5 text-xs text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="What did they say?"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-zinc-200 px-3 py-1.5 text-xs font-medium text-zinc-800 disabled:opacity-50 dark:bg-zinc-700 dark:text-zinc-100"
        >
          Send
        </button>
      </form>
      {devError && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{devError}</p>}
    </details>
  );
}
