"use client";

import type {
  ConnectionState,
  IAgoraRTCClient,
  IMicrophoneAudioTrack,
} from "agora-rtc-sdk-ng";
import type { RTMClient } from "agora-rtm-sdk";
import { useCallback, useRef, useState } from "react";
import { fetchRtcToken, startAgoraAgent, stopAgoraAgent } from "./agora-api";
import { parseTranscriptPayload, TranscriptDeduper } from "./liveTranscript";

export interface RemoteParticipant {
  uid: number | string;
  hasAudio: boolean;
}

export interface LiveHumanUtterance {
  id: string;
  uid: number | string;
  text: string;
}

export type AgentStatus = "idle" | "starting" | "running" | "stopping" | "error";

// M6.2: must match IncidentDashboard.tsx's LAST_INCIDENT_KEY - this hook and
// the dashboard are sibling components with no shared state, so the current
// incident id is threaded through via the same localStorage key the
// dashboard already persists it to, rather than lifting state up or
// inventing a second channel just for this.
const LAST_INCIDENT_KEY = "echoward:lastIncidentId";

function randomUid(): number {
  // Agora RTC uids are 32-bit unsigned ints; keep well within range and non-zero.
  return Math.floor(Math.random() * 900_000) + 100_000;
}

export function useAgoraRoom() {
  const clientRef = useRef<IAgoraRTCClient | null>(null);
  const localAudioTrackRef = useRef<IMicrophoneAudioTrack | null>(null);
  const rtmClientRef = useRef<RTMClient | null>(null);

  const [connectionState, setConnectionState] = useState<ConnectionState>("DISCONNECTED");
  const [channel, setChannel] = useState<string | null>(null);
  const [localUid, setLocalUid] = useState<number | null>(null);
  const [remoteUsers, setRemoteUsers] = useState<RemoteParticipant[]>([]);
  const [isMuted, setIsMuted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [agentId, setAgentId] = useState<string | null>(null);
  const [agentUid, setAgentUid] = useState<number | null>(null);
  const [agentStatus, setAgentStatus] = useState<AgentStatus>("idle");

  // M6.1: agentUidRef mirrors agentUid so the RTM message listener set up in
  // setupLiveTranscript below (registered once per join()) always reads the
  // current value instead of closing over a stale one.
  const agentUidRef = useRef<number | null>(null);
  const transcriptDeduperRef = useRef(new TranscriptDeduper());
  const [lastHumanUtterance, setLastHumanUtterance] = useState<LiveHumanUtterance | null>(null);

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

  // M6.1: live transcript ingestion via Signaling (RTM) - see
  // ./liveTranscript.ts's module docstring for why this isn't the RTC
  // `stream-message` channel. Entirely best-effort: any failure here is
  // caught and logged, never rethrown, so it can't block or break the RTC
  // room itself. `agentUidRef`/`transcriptDeduperRef` are shared with the
  // rest of the hook so classification/dedup stay consistent regardless of
  // which transport delivered a given segment.
  const setupLiveTranscript = useCallback(
    async (appId: string, uid: number, token: string, channelName: string) => {
      try {
        const { default: AgoraRTM } = await import("agora-rtm-sdk");
        const { RTM } = AgoraRTM;
        const rtm = new RTM(appId, String(uid));
        rtmClientRef.current = rtm;

        rtm.addEventListener("linkState", (event) => {
          console.log("[agora-rtm] linkState ->", event.currentState);
        });

        // Listeners must be registered before subscribe(), per Agora's own
        // RTM docs, to avoid missing early events.
        //
        // UNVERIFIED (remaining live-verification point): assumes the
        // Conversational AI Engine publishes transcripts to an RTM channel
        // with the same name as the RTC room - matching the pattern in
        // Agora's own toolkit reference (subscribeMessage(channelName)) but
        // not confirmed against a live project.
        // Diagnostic logging below is deliberately layered so a console log
        // alone (no breakpoints/devtools stepping needed) tells you exactly
        // where a missing utterance is being lost: RTM reception (this
        // handler firing at all), parsing (parse:<stage> on a drop),
        // finalization (interim vs. duplicate-final skip), or - one layer up,
        // in IncidentDashboard.tsx - API ingestion.
        rtm.addEventListener("message", (event) => {
          try {
            console.debug("[agora-rtm] message received", {
              channelType: event.channelType,
              publisher: event.publisher,
              messageType: event.messageType,
            });
            const segment = parseTranscriptPayload(event.message, agentUidRef.current, (stage, info) => {
              console.debug(`[agora-rtm] parse:${stage}`, info);
            });
            if (!segment) return;
            if (segment.isAgent) {
              console.debug("[agora-rtm] skipping agent speech segment", { turnId: segment.turnId });
              return;
            }
            if (!segment.isFinal) {
              console.debug("[agora-rtm] interim (non-final) human segment - waiting for final", {
                turnId: segment.turnId,
                preview: segment.text.slice(0, 60),
              });
              return;
            }
            if (!transcriptDeduperRef.current.shouldSubmit(segment)) {
              console.debug("[agora-rtm] final segment already submitted - skipping duplicate", {
                turnId: segment.turnId,
              });
              return;
            }
            console.log("[agora-rtm] finalized human utterance ready for ingestion", {
              turnId: segment.turnId,
              uid: segment.uid,
              text: segment.text,
            });
            setLastHumanUtterance({
              id: `${segment.turnId ?? segment.uid}-${Date.now()}`,
              uid: segment.uid,
              text: segment.text,
            });
          } catch (err) {
            // A malformed/unexpected transcript payload must never break
            // the room - log and move on.
            console.warn("[agora-rtm] failed to process transcript message", err);
          }
        });

        await rtm.login({ token });
        await rtm.subscribe(channelName);
        console.log("[agora-rtm] subscribed to live transcript channel", channelName);
      } catch (err) {
        // Live transcript ingestion is best-effort - the room, EchoWard's
        // audio, and everything else must keep working even if this fails
        // (e.g. Signaling isn't enabled for the project yet).
        console.error("[agora-rtm] live transcript setup failed - continuing without it", err);
      }
    },
    []
  );

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

        // Set up after the RTC join/publish succeeds - Agora's RTM docs
        // recommend staggering RTM login and RTC join rather than doing them
        // at the same time, and setupLiveTranscript never throws, so a
        // Signaling failure here can't undo the successful room join above.
        await setupLiveTranscript(app_id, uid, token, channelName);
      } catch (err) {
        console.error("[agora] failed to join channel", err);
        const message = err instanceof Error ? err.message : "Failed to join the room";
        setError(message);
        throw err;
      }
    },
    [getClient, setupLiveTranscript]
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
    }
    try {
      await rtmClientRef.current?.logout();
      console.log("[agora-rtm] logged out");
    } catch (err) {
      console.error("[agora-rtm] error while logging out", err);
    } finally {
      rtmClientRef.current = null;
      setChannel(null);
      setLocalUid(null);
      setRemoteUsers([]);
      setAgentId(null);
      setAgentUid(null);
      agentUidRef.current = null;
      transcriptDeduperRef.current.reset();
      setLastHumanUtterance(null);
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
      const incidentId = typeof window !== "undefined" ? localStorage.getItem(LAST_INCIDENT_KEY) : null;
      console.log("[agora] starting EchoWard agent in channel", channel, "incident", incidentId);
      const res = await startAgoraAgent(channel, incidentId);
      setAgentId(res.agent_id);
      setAgentUid(res.agent_uid);
      agentUidRef.current = res.agent_uid;
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
      agentUidRef.current = null;
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
    lastHumanUtterance,
    join,
    leave,
    toggleMute,
    startEchoWard,
    stopEchoWard,
  };
}
