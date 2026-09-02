"use client";

import { useState } from "react";
import IncidentDashboard from "@/components/IncidentDashboard";
import { createIncident } from "@/lib/incidents-api";
import { clearLastIncidentId, persistLastIncidentId, useLastIncidentId } from "@/lib/incidentSession";
import { useAgoraRoom } from "@/lib/useAgoraRoom";

const DEFAULT_NAME = "Responder";

export default function Home() {
  const room = useAgoraRoom();
  const storedIncidentId = useLastIncidentId();
  // undefined = "no explicit choice this session yet, defer to storedIncidentId";
  // null = "explicitly left" (Change incident), overriding storage.
  const [sessionIncidentId, setSessionIncidentId] = useState<string | null | undefined>(undefined);
  const incidentId = sessionIncidentId !== undefined ? sessionIncidentId : storedIncidentId;

  const [displayName, setDisplayName] = useState(DEFAULT_NAME);
  const [starting, setStarting] = useState(false);
  const [joining, setJoining] = useState(false);
  const [flowError, setFlowError] = useState<string | null>(null);

  async function handleStart(title: string, name: string) {
    setFlowError(null);
    setStarting(true);
    setDisplayName(name);
    try {
      const incident = await createIncident(title);
      persistLastIncidentId(incident.id);
      setSessionIncidentId(incident.id);
      try {
        await room.join(incident.id);
      } catch {
        // Incident still starts even if the voice room fails to join - the
        // dashboard's "Join voice" control lets them retry; room.error
        // already carries the failure message for display.
      }
    } catch (err) {
      setFlowError(err instanceof Error ? err.message : "Failed to start incident");
    } finally {
      setStarting(false);
    }
  }

  async function handleJoinExisting(code: string, name: string, withVoice: boolean) {
    setFlowError(null);
    setJoining(true);
    setDisplayName(name);
    try {
      persistLastIncidentId(code);
      setSessionIncidentId(code);
      if (withVoice) {
        try {
          await room.join(code);
        } catch {
          // Same as above - the incident view still opens; retry via "Join voice".
        }
      }
    } finally {
      setJoining(false);
    }
  }

  async function handleChangeIncident() {
    if (room.channel) {
      try {
        await room.leave();
      } catch {
        // best-effort cleanup, the user is leaving this incident regardless
      }
    }
    clearLastIncidentId();
    setSessionIncidentId(null);
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-6 px-6 py-10">
      <header>
        <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">EchoWard</h1>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">Voice-native AI Incident Commander</p>
      </header>

      {!incidentId ? (
        <StartIncidentScreen
          onStart={handleStart}
          onJoinExisting={handleJoinExisting}
          starting={starting}
          joining={joining}
          error={flowError ?? room.error}
        />
      ) : (
        <IncidentDashboard
          incidentId={incidentId}
          room={room}
          displayName={displayName}
          onChangeIncident={handleChangeIncident}
          onJoinVoice={() => room.join(incidentId)}
        />
      )}
    </main>
  );
}

function StartIncidentScreen({
  onStart,
  onJoinExisting,
  starting,
  joining,
  error,
}: {
  onStart: (title: string, name: string) => void;
  onJoinExisting: (code: string, name: string, withVoice: boolean) => void;
  starting: boolean;
  joining: boolean;
  error: string | null;
}) {
  const [title, setTitle] = useState("");
  const [name, setName] = useState(DEFAULT_NAME);
  const [showJoin, setShowJoin] = useState(false);
  const [code, setCode] = useState("");

  function handleStartSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    onStart(title.trim(), name.trim() || DEFAULT_NAME);
  }

  function submitJoin(withVoice: boolean) {
    const trimmed = code.trim();
    if (!trimmed) return;
    onJoinExisting(trimmed, name.trim() || DEFAULT_NAME, withVoice);
  }

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
      <div>
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Start an incident</h2>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          EchoWard joins the call, listens, and keeps a shared incident picture live for the team.
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      <form onSubmit={handleStartSubmit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-zinc-600 dark:text-zinc-400">Incident title</span>
          <input
            className="rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Payment service outage"
            required
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-zinc-600 dark:text-zinc-400">Your name</span>
          <input
            className="rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={DEFAULT_NAME}
          />
        </label>
        <button
          type="submit"
          disabled={starting}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {starting ? "Starting incident…" : "Start Incident"}
        </button>
      </form>

      <div className="border-t border-zinc-100 pt-3 dark:border-zinc-800">
        <button
          type="button"
          onClick={() => setShowJoin((v) => !v)}
          className="text-xs text-zinc-400 underline dark:text-zinc-500"
        >
          {showJoin ? "Hide" : "Join an existing incident"}
        </button>
        {showJoin && (
          <div className="mt-2 flex flex-col gap-2">
            <input
              className="rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Incident code"
            />
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => submitJoin(true)}
                disabled={joining}
                className="rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
              >
                {joining ? "Joining…" : "Join with voice"}
              </button>
              <button
                type="button"
                onClick={() => submitJoin(false)}
                disabled={joining}
                className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300"
              >
                Watch only
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
