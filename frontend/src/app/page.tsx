"use client";

import Image from "next/image";
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

  if (!incidentId) {
    return (
      <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-10 px-6 py-10 sm:py-14">
        <LandingScreen
          onStart={handleStart}
          onJoinExisting={handleJoinExisting}
          starting={starting}
          joining={joining}
          error={flowError ?? room.error}
        />
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-6 px-6 py-10">
      <header>
        <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">EchoWard</h1>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">Voice-native AI Incident Commander</p>
      </header>

      <IncidentDashboard
        incidentId={incidentId}
        room={room}
        displayName={displayName}
        onChangeIncident={handleChangeIncident}
        onJoinVoice={() => room.join(incidentId)}
      />
    </main>
  );
}

const STEP_ICON_STYLE = {
  blue: "bg-blue-100 text-blue-600 dark:bg-blue-950 dark:text-blue-300",
  violet: "bg-violet-100 text-violet-600 dark:bg-violet-950 dark:text-violet-300",
  amber: "bg-amber-100 text-amber-600 dark:bg-amber-950 dark:text-amber-300",
  emerald: "bg-emerald-100 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-300",
} as const;

function StepIcon({ path, color }: { path: string; color: keyof typeof STEP_ICON_STYLE }) {
  return (
    <div className={`inline-flex h-7 w-7 items-center justify-center rounded-md ${STEP_ICON_STYLE[color]}`}>
      <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4" aria-hidden="true">
        <path d={path} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

const HOW_IT_HELPS = [
  {
    title: "Listen",
    body: "Live incident conversation",
    icon: "M5 10v4M9 7v10M13 4v16M17 8v8M21 11v2",
    color: "blue",
  },
  {
    title: "Structure",
    body: "Facts, hypotheses, decisions, actions",
    icon: "M4 6h16M4 12h10M4 18h13",
    color: "violet",
  },
  {
    title: "Coordinate",
    body: "Conflicts, missing information, ownership",
    icon: "M6 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM6 7v6a4 4 0 0 0 4 4h1M18 7v6a4 4 0 0 1-4 4h-1",
    color: "amber",
  },
  {
    title: "Act safely",
    body: "Human confirmation before critical actions",
    icon: "M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3ZM9.5 12l1.8 1.8L14.5 10",
    color: "emerald",
  },
] as const;

const WHY_ECHOWARD = [
  "Facts ≠ hypotheses — never flattened into one bucket",
  "Conflicting evidence is surfaced, not silently resolved",
  "Incident state stays shared and current for the whole team",
  "Critical actions remain human-controlled, always",
];

function LandingScreen({
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
  return (
    <>
      <section className="grid items-start gap-8 lg:grid-cols-[1.1fr_0.9fr] lg:gap-12">
        <div className="flex flex-col gap-10">
          <Image
            src="/hero-brand-image.png"
            alt="EchoWard"
            width={1030}
            height={579}
            className="h-auto w-64 rounded-xl ring-1 ring-black/10 sm:w-80 dark:ring-white/10"
            priority
          />
          <div className="flex flex-col gap-3">
            <h1 className="text-3xl font-semibold tracking-tight text-balance text-zinc-900 dark:text-zinc-50 sm:text-4xl">
              Voice-native AI Incident Commander
            </h1>
            <p className="max-w-xl text-base leading-relaxed text-zinc-600 dark:text-zinc-400">
              A voice-aware teammate for live technical incidents. EchoWard listens to the room,
              structures what matters, surfaces uncertainty, and helps the team act safely.
            </p>
          </div>
        </div>

        <StartIncidentCard
          onStart={onStart}
          onJoinExisting={onJoinExisting}
          starting={starting}
          joining={joining}
          error={error}
        />
      </section>

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {HOW_IT_HELPS.map((step) => (
          <div
            key={step.title}
            className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
          >
            <StepIcon path={step.icon} color={step.color} />
            <p className="mt-2 text-sm font-semibold text-zinc-900 dark:text-zinc-50">{step.title}</p>
            <p className="mt-0.5 text-xs leading-snug text-zinc-500 dark:text-zinc-400">{step.body}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
        {WHY_ECHOWARD.map((point) => (
          <div key={point} className="flex items-start gap-2 text-sm text-zinc-600 dark:text-zinc-400">
            <svg viewBox="0 0 20 20" fill="none" className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400">
              <path
                d="M4 10.5l3.5 3.5L16 6"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <span>{point}</span>
          </div>
        ))}
      </section>

      <section className="flex flex-col gap-4 border-t border-zinc-100 pt-6 sm:flex-row sm:items-start sm:justify-between dark:border-zinc-800">
        <div className="flex flex-col items-start gap-1.5">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-zinc-200 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
            Powered by
            <Image src="/agora-logo.png" alt="Agora" width={110} height={38} className="h-3 w-auto" />
            Conversational AI
          </span>
          <p className="max-w-2xl text-xs leading-relaxed text-zinc-400 dark:text-zinc-500">
            Agora provides EchoWard&apos;s real-time conversational voice layer, allowing it to
            participate directly in the incident room.
          </p>
        </div>

        <span className="text-sm font-semibold tracking-wide text-zinc-700 dark:text-zinc-300">
          Built by Team ZenYukti
        </span>
      </section>
    </>
  );
}

function StartIncidentCard({
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
    <section className="flex flex-col gap-4 rounded-xl border-2 border-orange-500/40 bg-white p-6 shadow-sm ring-1 ring-zinc-900/5 dark:bg-zinc-900 dark:ring-white/5">
      <div>
        <span className="text-[11px] font-semibold tracking-wide text-zinc-400 dark:text-zinc-500">
          GET STARTED
        </span>
        <h2 className="mt-1 text-lg font-semibold text-zinc-900 dark:text-zinc-50">Start an incident</h2>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
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
