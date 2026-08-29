"use client";

import type {
  ConnectionState,
  IAgoraRTCClient,
  IMicrophoneAudioTrack,
} from "agora-rtc-sdk-ng";
import { useCallback, useRef, useState } from "react";
import { fetchRtcToken, startAgoraAgent, stopAgoraAgent } from "./agora-api";

export interface RemoteParticipant {
  uid: number | string;
  hasAudio: boolean;
}

export type AgentStatus = "idle" | "starting" | "running" | "stopping" | "error";

function randomUid(): number {
  // Agora RTC uids are 32-bit unsigned ints; keep well within range and non-zero.
  return Math.floor(Math.random() * 900_000) + 100_000;
}

export function useAgoraRoom() {
  const clientRef = useRef<IAgoraRTCClient | null>(null);
  const localAudioTrackRef = useRef<IMicrophoneAudioTrack | null>(null);

  const [connectionState, setConnectionState] = useState<ConnectionState>("DISCONNECTED");
  const [channel, setChannel] = useState<string | null>(null);
  const [localUid, setLocalUid] = useState<number | null>(null);
  const [remoteUsers, setRemoteUsers] = useState<RemoteParticipant[]>([]);
  const [isMuted, setIsMuted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [agentId, setAgentId] = useState<string | null>(null);
  const [agentUid, setAgentUid] = useState<number | null>(null);
  const [agentStatus, setAgentStatus] = useState<AgentStatus>("idle");

  const getClient = useCallback(async (): Promise<IAgoraRTCClient> => {
    if (clientRef.current) return clientRef.current;

    const { default: AgoraRTC } = await import("agora-rtc-sdk-ng");
    const client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });

    client.on("connection-state-change", (curState) => {
      console.log("[agora] connection-state-change ->", curState);
      setConnectionState(curState);
    });

    client.on("user-published", async (user, mediaType) => {
      try {
        await client.subscribe(user, mediaType);
        if (mediaType === "audio") {
          user.audioTrack?.play();
        }
      } catch (err) {
        console.error("[agora] failed to subscribe to remote user", user.uid, err);
      }
      setRemoteUsers((prev) => {
        const others = prev.filter((u) => u.uid !== user.uid);
        return [...others, { uid: user.uid, hasAudio: mediaType === "audio" }];
      });
    });

    client.on("user-unpublished", (user, mediaType) => {
      if (mediaType !== "audio") return;
      setRemoteUsers((prev) =>
        prev.map((u) => (u.uid === user.uid ? { ...u, hasAudio: false } : u))
      );
    });

    client.on("user-left", (user) => {
      console.log("[agora] user-left ->", user.uid);
      setRemoteUsers((prev) => prev.filter((u) => u.uid !== user.uid));
    });

    clientRef.current = client;
    return client;
  }, []);

  const join = useCallback(
    async (channelName: string) => {
      setError(null);
      try {
        const client = await getClient();
        const uid = randomUid();

        console.log("[agora] requesting token for channel", channelName, "uid", uid);
        const { app_id, token } = await fetchRtcToken(channelName, uid, "publisher");

        await client.join(app_id, channelName, token, uid);
        console.log("[agora] joined channel", channelName, "as uid", uid);

        const { default: AgoraRTC } = await import("agora-rtc-sdk-ng");
        const micTrack = await AgoraRTC.createMicrophoneAudioTrack();
        localAudioTrackRef.current = micTrack;
        await client.publish([micTrack]);

        setChannel(channelName);
        setLocalUid(uid);
        setIsMuted(false);
      } catch (err) {
        console.error("[agora] failed to join channel", err);
        const message = err instanceof Error ? err.message : "Failed to join the room";
        setError(message);
        throw err;
      }
    },
    [getClient]
  );

  const leave = useCallback(async () => {
    if (agentId) {
      try {
        await stopAgoraAgent(agentId);
      } catch (err) {
        console.error("[agora] failed to stop agent while leaving", err);
      }
    }
    try {
      localAudioTrackRef.current?.close();
      localAudioTrackRef.current = null;
      await clientRef.current?.leave();
      console.log("[agora] left channel");
    } catch (err) {
      console.error("[agora] error while leaving channel", err);
    } finally {
      setChannel(null);
      setLocalUid(null);
      setRemoteUsers([]);
      setAgentId(null);
      setAgentUid(null);
      setAgentStatus("idle");
      setIsMuted(false);
      setConnectionState("DISCONNECTED");
    }
  }, [agentId]);

  const toggleMute = useCallback(async () => {
    const track = localAudioTrackRef.current;
    if (!track) return;
    const nextMuted = !isMuted;
    await track.setEnabled(!nextMuted);
    setIsMuted(nextMuted);
  }, [isMuted]);

  const startEchoWard = useCallback(async () => {
    if (!channel) return;
    setAgentStatus("starting");
    setError(null);
    try {
      console.log("[agora] starting EchoWard agent in channel", channel);
      const res = await startAgoraAgent(channel);
      setAgentId(res.agent_id);
      setAgentUid(res.agent_uid);
      setAgentStatus("running");
      console.log("[agora] EchoWard agent running", res.agent_id);
    } catch (err) {
      console.error("[agora] failed to start EchoWard agent", err);
      setError(err instanceof Error ? err.message : "Failed to start EchoWard");
      setAgentStatus("error");
    }
  }, [channel]);

  const stopEchoWard = useCallback(async () => {
    if (!agentId) return;
    setAgentStatus("stopping");
    try {
      await stopAgoraAgent(agentId);
      console.log("[agora] EchoWard agent stopped", agentId);
    } catch (err) {
      console.error("[agora] failed to stop EchoWard agent", err);
      setError(err instanceof Error ? err.message : "Failed to stop EchoWard");
    } finally {
      setAgentId(null);
      setAgentUid(null);
      setAgentStatus("idle");
    }
  }, [agentId]);

  return {
    connectionState,
    channel,
    localUid,
    remoteUsers,
    isMuted,
    error,
    agentId,
    agentUid,
    agentStatus,
    join,
    leave,
    toggleMute,
    startEchoWard,
    stopEchoWard,
  };
}
