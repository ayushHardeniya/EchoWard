"use client";

import { useEffect, useState } from "react";
import { fetchHealth, type HealthResponse } from "@/lib/api";

type Status = { state: "loading" } | { state: "ok"; data: HealthResponse } | { state: "error"; message: string };

export default function Home() {
  const [status, setStatus] = useState<Status>({ state: "loading" });

  useEffect(() => {
    fetchHealth()
      .then((data) => setStatus({ state: "ok", data }))
      .catch((err: Error) => setStatus({ state: "error", message: err.message }));
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-zinc-50 px-6 text-center dark:bg-black">
      <h1 className="text-3xl font-semibold text-zinc-900 dark:text-zinc-50">EchoWard</h1>
      <p className="max-w-md text-zinc-600 dark:text-zinc-400">
        Voice-native AI Incident Commander — foundation build.
      </p>

      <div className="rounded-lg border border-zinc-200 bg-white px-6 py-4 text-left dark:border-zinc-800 dark:bg-zinc-900">
        {status.state === "loading" && (
          <p className="text-zinc-500">Checking backend connection…</p>
        )}
        {status.state === "ok" && (
          <dl className="space-y-1 text-sm">
            <Row label="Backend status" value={status.data.status} />
            <Row label="Service" value={status.data.service} />
            <Row label="Environment" value={status.data.environment} />
            <Row
              label="Database connected"
              value={status.data.database_connected ? "yes" : "no"}
            />
          </dl>
        )}
        {status.state === "error" && (
          <p className="max-w-sm text-sm text-red-600 dark:text-red-400">
            Could not reach backend: {status.message}
            <br />
            Make sure the FastAPI server is running on the URL set in NEXT_PUBLIC_API_URL.
          </p>
        )}
      </div>
    </main>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-8">
      <dt className="text-zinc-500">{label}</dt>
      <dd className="font-mono text-zinc-900 dark:text-zinc-100">{value}</dd>
    </div>
  );
}
