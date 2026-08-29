"use client";

import { useState } from "react";
import { type AgentStatus, useAgoraRoom } from "@/lib/useAgoraRoom";

const CONNECTION_LABEL: Record<string, string> = {
  DISCONNECTED: "Disconnected",
  CONNECTING: "Connecting…",
  CONNECTED: "Connected",
  RECONNECTING: "Reconnecting…",
  DISCONNECTING: "Disconnecting…",
};

const AGENT_LABEL: Record<AgentStatus, string> = {
  idle: "Not started",
  starting: "Starting…",
  running: "Live",
  stopping: "Stopping…",
  error: "Error",
};

export default function Home() {
  const room = useAgoraRoom();
  const [channelInput, setChannelInput] = useState("incident-room");
  const [displayName, setDisplayName] = useState("Engineer");
  const [joining, setJoining] = useState(false);

  const isJoined = room.channel !== null;

  async function handleJoin(e: React.FormEvent) {
    e.preventDefault();
    if (!channelInput.trim()) return;
    setJoining(true);
    try {
      await room.join(channelInput.trim());
    } catch {
      // error already captured in room.error
    } finally {
      setJoining(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 px-6 py-10">
      <header>
        <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">EchoWard</h1>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Voice-native AI Incident Commander — voice room MVP
        </p>
      </header>

      {room.error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {room.error}
        </div>
      )}

      {!isJoined ? (
        <form
          onSubmit={handleJoin}
          className="flex flex-col gap-4 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900"
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-zinc-600 dark:text-zinc-400">Incident room / channel name</span>
            <input
              className="rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
              value={channelInput}
              onChange={(e) => setChannelInput(e.target.value)}
              placeholder="incident-room"
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-zinc-600 dark:text-zinc-400">Your name (display only)</span>
            <input
              className="rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Engineer"
            />
          </label>
          <button
            type="submit"
            disabled={joining}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
          >
            {joining ? "Joining…" : "Join Room"}
          </button>
        </form>
      ) : (
        <div className="flex flex-col gap-6 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-zinc-500 dark:text-zinc-400">Room</p>
              <p className="font-mono text-lg text-zinc-900 dark:text-zinc-100">{room.channel}</p>
            </div>
            <span className="rounded-full bg-zinc-100 px-3 py-1 text-xs font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
              {CONNECTION_LABEL[room.connectionState] ?? room.connectionState}
            </span>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => room.leave()}
              className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 dark:border-zinc-700 dark:text-zinc-300"
            >
              Leave Room
            </button>
            <button
              onClick={() => room.toggleMute()}
              className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 dark:border-zinc-700 dark:text-zinc-300"
            >
              {room.isMuted ? "Unmute" : "Mute"}
            </button>
            {room.agentStatus === "idle" || room.agentStatus === "error" ? (
              <button
                onClick={() => room.startEchoWard()}
                className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white"
              >
                Start EchoWard
              </button>
            ) : (
              <button
                onClick={() => room.stopEchoWard()}
                disabled={room.agentStatus === "stopping" || room.agentStatus === "starting"}
                className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                Stop EchoWard
              </button>
            )}
          </div>

          <div>
            <p className="mb-2 text-sm text-zinc-500 dark:text-zinc-400">
              EchoWard: {AGENT_LABEL[room.agentStatus]}
            </p>
            <p className="mb-2 text-sm text-zinc-500 dark:text-zinc-400">Participants</p>
            <ul className="flex flex-col gap-1 text-sm">
              <li className="flex items-center justify-between rounded-md bg-zinc-50 px-3 py-2 dark:bg-zinc-800/60">
                <span>
                  {displayName || "You"} (uid {room.localUid}){room.isMuted ? " · muted" : ""}
                </span>
                <span className="text-xs text-zinc-500 dark:text-zinc-400">local</span>
              </li>
              {room.remoteUsers.map((user) => (
                <li
                  key={user.uid}
                  className="flex items-center justify-between rounded-md bg-zinc-50 px-3 py-2 dark:bg-zinc-800/60"
                >
                  <span>
                    {user.uid === room.agentUid ? "EchoWard" : `Participant (uid ${user.uid})`}
                  </span>
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">
                    {user.hasAudio ? "speaking-capable" : "no audio"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </main>
  );
}
