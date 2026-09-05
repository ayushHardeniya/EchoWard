"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import IncidentDashboard from "@/components/IncidentDashboard";
import { API_URL, fetchHealth } from "@/lib/api";
import { createIncident } from "@/lib/incidents-api";
import { clearLastIncidentId, persistLastIncidentId, useLastIncidentId } from "@/lib/incidentSession";
import { useAgoraRoom } from "@/lib/useAgoraRoom";

const DEFAULT_NAME = "Responder";

// Render's free tier suspends an idle backend; the next request wakes it, but the
// cold start can take about a minute. /health is safe to retry (unlike POST
// /api/incidents, which must never fire more than once per click).
const HEALTH_CHECK_TIMEOUT_MS = 6_000;
const WAKE_RETRY_SECONDS = 8;
const WAKE_TIMEOUT_MS = 80_000;

function delay(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

async function pingHealth(): Promise<boolean> {
  try {
    await fetchHealth(AbortSignal.timeout(HEALTH_CHECK_TIMEOUT_MS));
    return true;
  } catch {
    return false;
  }
}

/** Small ambient "API · Online/Waking" indicator - independent of the Start
 * Incident flow's own (fresh, on-click) health gate below. */
function useApiStatus(): "online" | "waking" {
  const [online, setOnline] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function check() {
      const healthy = await pingHealth();
      if (cancelled) return;
      setOnline(healthy);
      if (!healthy) {
        timer = setTimeout(check, WAKE_RETRY_SECONDS * 1000);
      }
    }

    check();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return online ? "online" : "waking";
}

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
  const [wakeState, setWakeState] = useState<"idle" | "waking" | "timeout">("idle");
  const [retryIn, setRetryIn] = useState(WAKE_RETRY_SECONDS);

  const mountedRef = useRef(true);
  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );

  async function waitForBackendWake(): Promise<boolean> {
    setWakeState("waking");
    const deadline = Date.now() + WAKE_TIMEOUT_MS;
    while (Date.now() < deadline) {
      for (let s = WAKE_RETRY_SECONDS; s > 0; s--) {
        setRetryIn(s);
        await delay(1000);
        if (!mountedRef.current) return false;
      }
      if (await pingHealth()) return true;
    }
    return false;
  }

  async function handleStart(title: string, name: string) {
    setFlowError(null);
    setWakeState("idle");
    setStarting(true);
    setDisplayName(name);

    // /health is safe to retry - unlike the create-incident call below, which
    // must only ever fire once per click to avoid creating duplicate incidents.
    if (!(await pingHealth())) {
      const awake = await waitForBackendWake();
      if (!mountedRef.current) return;
      if (!awake) {
        setWakeState("timeout");
        setStarting(false);
        return;
      }
      setWakeState("idle");
    }

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
      <main className="relative mx-auto flex min-h-screen max-w-5xl flex-col gap-10 px-6 py-10 sm:py-14">
        <LandingScreen
          onStart={handleStart}
          onJoinExisting={handleJoinExisting}
          starting={starting}
          joining={joining}
          error={flowError ?? room.error}
          wakeState={wakeState}
          retryIn={retryIn}
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
  wakeState,
  retryIn,
}: {
  onStart: (title: string, name: string) => void;
  onJoinExisting: (code: string, name: string, withVoice: boolean) => void;
  starting: boolean;
  joining: boolean;
  error: string | null;
  wakeState: "idle" | "waking" | "timeout";
  retryIn: number;
}) {
  const apiStatus = useApiStatus();

  return (
    <>
      <div className="absolute right-0 top-3 flex items-center gap-4">
        <a
          href="https://echoward-backend.onrender.com/docs"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Open EchoWard API documentation"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-200 transition-colors hover:text-white"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            className="h-4 w-4"
            aria-hidden="true"
          >
            <path
              d="M6 3.75h9.5L19 7.25V20.25H6V3.75Z"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinejoin="round"
            />
            <path
              d="M15 3.75v3.5h4M9 11h6M9 14.5h6M9 18h4"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span>API Docs</span>
          <span aria-hidden="true">↗</span>
        </a>

        <a
          href="https://github.com/ayushHardeniya/EchoWard/"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Open EchoWard on GitHub"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-200 transition-colors hover:text-white"
        >
          <svg
            viewBox="0 0 24 24"
            fill="currentColor"
            className="h-4 w-4 shrink-0"
            aria-hidden="true"
          >
            <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.084-.73.084-.73 1.205.084 1.838 1.237 1.838 1.237 1.07 1.834 2.809 1.304 3.495.997.108-.775.418-1.305.762-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.957-.266 1.983-.399 3.005-.404 1.02.005 2.047.138 3.006.404 2.292-1.552 3.296-1.23 3.296-1.23.647 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.43.372.823 1.103.823 2.222 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.572C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
          </svg>
          <span>GitHub</span>
          <span aria-hidden="true">↗</span>
        </a>
      </div>
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
          wakeState={wakeState}
          retryIn={retryIn}
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

        <div className="flex flex-col items-start gap-1.5 sm:items-end">
          <span className="text-sm font-semibold tracking-wide text-zinc-700 dark:text-zinc-300">
            Built by Team ZenYukti
          </span>
          <ApiStatusBadge status={apiStatus} />
        </div>
      </section>
    </>
  );
}

function ApiStatusBadge({ status }: { status: "online" | "waking" }) {
  return (
    <a
      href={`${API_URL}/health`}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300"
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${status === "online" ? "bg-emerald-500" : "animate-pulse bg-amber-500"}`}
      />
      API · {status === "online" ? "Online" : "Waking"}
    </a>
  );
}

function StartIncidentCard({
  onStart,
  onJoinExisting,
  starting,
  joining,
  error,
  wakeState,
  retryIn,
}: {
  onStart: (title: string, name: string) => void;
  onJoinExisting: (code: string, name: string, withVoice: boolean) => void;
  starting: boolean;
  joining: boolean;
  error: string | null;
  wakeState: "idle" | "waking" | "timeout";
  retryIn: number;
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

      {wakeState === "waking" && (
        <div className="flex items-start gap-3 rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2.5 dark:border-zinc-800 dark:bg-zinc-950">
          <span className="mt-1 h-2 w-2 shrink-0 animate-pulse rounded-full bg-amber-500" />
          <div className="flex flex-col gap-0.5">
            <p className="text-sm font-medium text-zinc-700 dark:text-zinc-200">Backend is waking up</p>
            <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
              EchoWard&apos;s backend is starting. This can take about a minute on the free deployment.
            </p>
            <p className="text-[11px] text-zinc-400 dark:text-zinc-500">Checking again in {retryIn}s…</p>
          </div>
        </div>
      )}

      {wakeState === "timeout" && (
        <div className="flex flex-col gap-2 rounded-md border border-red-300 bg-red-50 px-3 py-2.5 dark:border-red-900 dark:bg-red-950">
          <p className="text-sm text-red-700 dark:text-red-300">
            The backend is taking longer than expected to wake up.
          </p>
          <button
            type="button"
            onClick={() => onStart(title.trim(), name.trim() || DEFAULT_NAME)}
            className="self-start rounded-md border border-red-300 px-3 py-1 text-xs font-medium text-red-700 dark:border-red-800 dark:text-red-300"
          >
            Try again
          </button>
        </div>
      )}

      {wakeState === "idle" && error && (
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
