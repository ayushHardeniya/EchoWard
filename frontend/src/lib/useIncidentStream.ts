"use client";

import { useEffect, useRef, useState } from "react";
import { API_URL } from "./api";
import { getIncidentState, type IncidentState } from "./incidents-api";

export type StreamStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

function streamUrl(incidentId: string): string {
  const base = API_URL.replace(/^http/, "ws");
  return `${base}/api/incidents/${incidentId}/stream`;
}

/**
 * Loads an incident's state via REST, then keeps it live via the
 * /api/incidents/{id}/stream WebSocket. Reconnects with a small backoff if the
 * socket drops, and refetches state over REST once reconnected as a
 * belt-and-braces measure in case any update was missed while disconnected.
 */
export function useIncidentStream(incidentId: string) {
  const [state, setState] = useState<IncidentState | null>(null);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [error, setError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;
    attemptRef.current = 0;

    // Show something immediately, don't wait on the socket handshake.
    getIncidentState(incidentId)
      .then(setState)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load incident"));

    function connect() {
      if (unmountedRef.current) return;
      const socket = new WebSocket(streamUrl(incidentId));
      socketRef.current = socket;

      socket.onopen = () => {
        attemptRef.current = 0;
        setStatus("connected");
        setError(null);
        getIncidentState(incidentId).then(setState).catch(() => undefined);
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message?.type === "incident.updated" && message.state) {
            setState(message.state as IncidentState);
          }
        } catch {
          // ignore malformed frame
        }
      };

      socket.onclose = () => {
        if (unmountedRef.current) return;
        setStatus((prev) => (prev === "connecting" ? "disconnected" : "reconnecting"));
        const delay = Math.min(1000 * 2 ** attemptRef.current, 10000);
        attemptRef.current += 1;
        reconnectTimerRef.current = setTimeout(connect, delay);
      };

      socket.onerror = () => {
        socket.close();
      };
    }

    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [incidentId]);

  return { state, status, error };
}
