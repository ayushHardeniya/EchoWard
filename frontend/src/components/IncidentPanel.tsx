"use client";

import { useState } from "react";
import {
  createIncident,
  type IncidentState,
  sendConversationTurn,
} from "@/lib/incidents-api";

/**
 * M2 development bridge — proves incident intelligence is wired up end-to-end.
 * Not the real dashboard (that's M3): just enough to create/select an incident,
 * push a test conversation turn through the extraction pipeline, and see the
 * resulting structured state update.
 */
export default function IncidentPanel() {
  const [titleInput, setTitleInput] = useState("Payments incident");
  const [speakerInput, setSpeakerInput] = useState("Alice");
  const [textInput, setTextInput] = useState("Payments are failing for about 30% of users.");
  const [state, setState] = useState<IncidentState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleCreateIncident(e: React.FormEvent) {
    e.preventDefault();
    if (!titleInput.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const incident = await createIncident(titleInput.trim());
      setState({
        incident,
        facts: [],
        hypotheses: [],
        decisions: [],
        actions: [],
        timeline: [],
        unresolved_questions: [],
        conflicts: [],
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create incident");
    } finally {
      setBusy(false);
    }
  }

  async function handleSendTurn(e: React.FormEvent) {
    e.preventDefault();
    if (!state || !speakerInput.trim() || !textInput.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await sendConversationTurn(state.incident.id, speakerInput.trim(), textInput.trim());
      setState(result.state);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to process conversation turn");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
      <div>
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Incident Intelligence</h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          M2 dev bridge — create/select an incident and feed it test statements to see structured
          state update. The real dashboard is a later milestone.
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      {!state ? (
        <form onSubmit={handleCreateIncident} className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
            value={titleInput}
            onChange={(e) => setTitleInput(e.target.value)}
            placeholder="Incident title"
            required
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
          >
            Create Incident
          </button>
        </form>
      ) : (
        <>
          <div className="text-sm">
            <span className="text-zinc-500 dark:text-zinc-400">Incident: </span>
            <span className="font-mono text-zinc-900 dark:text-zinc-100">{state.incident.title}</span>
            <span className="ml-2 rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
              {state.incident.status}
            </span>
          </div>

          <form onSubmit={handleSendTurn} className="flex flex-col gap-2 sm:flex-row">
            <input
              className="w-32 rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
              value={speakerInput}
              onChange={(e) => setSpeakerInput(e.target.value)}
              placeholder="Speaker"
              required
            />
            <input
              className="flex-1 rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              placeholder="What did they say?"
              required
            />
            <button
              type="submit"
              disabled={busy}
              className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              Send test statement
            </button>
          </form>

          <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            <StatTile label="Facts" count={state.facts.length} />
            <StatTile label="Hypotheses" count={state.hypotheses.length} />
            <StatTile label="Decisions" count={state.decisions.length} />
            <StatTile label="Actions" count={state.actions.length} />
            <StatTile label="Conflicts" count={state.conflicts.length} />
            <StatTile label="Open questions" count={state.unresolved_questions.length} />
            <StatTile label="Timeline events" count={state.timeline.length} />
          </dl>

          {state.timeline.length > 0 && (
            <div>
              <p className="mb-1 text-sm text-zinc-500 dark:text-zinc-400">Recent timeline</p>
              <ul className="flex flex-col gap-1 text-sm">
                {state.timeline
                  .slice(-6)
                  .reverse()
                  .map((evt) => (
                    <li key={evt.id} className="rounded-md bg-zinc-50 px-3 py-1.5 dark:bg-zinc-800/60">
                      <span className="text-zinc-500 dark:text-zinc-400">[{evt.source}]</span> {evt.event}
                    </li>
                  ))}
              </ul>
            </div>
          )}

          {state.conflicts.length > 0 && (
            <div>
              <p className="mb-1 text-sm text-zinc-500 dark:text-zinc-400">Unresolved conflicts</p>
              <ul className="flex flex-col gap-2 text-sm">
                {state.conflicts.map((c) => (
                  <li
                    key={c.id}
                    className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 dark:border-amber-900 dark:bg-amber-950"
                  >
                    <p className="font-medium text-amber-900 dark:text-amber-200">{c.topic}</p>
                    <ul className="mt-1 list-disc pl-4 text-amber-800 dark:text-amber-300">
                      {c.statements.map((s, i) => (
                        <li key={i}>
                          {s.source}: {s.statement}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function StatTile({ label, count }: { label: string; count: number }) {
  return (
    <div className="rounded-md bg-zinc-50 px-3 py-2 dark:bg-zinc-800/60">
      <p className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">{count}</p>
      <p className="text-xs text-zinc-500 dark:text-zinc-400">{label}</p>
    </div>
  );
}
