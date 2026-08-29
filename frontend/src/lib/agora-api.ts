import { API_URL } from "./api";

export interface RtcTokenResponse {
  app_id: string;
  channel: string;
  uid: number;
  token: string;
  expires_in: number;
}

export interface StartAgentResponse {
  agent_id: string;
  channel: string;
  agent_uid: number;
  status: string;
}

export interface StopAgentResponse {
  agent_id: string;
  status: string;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail ?? `request failed with status ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

export function fetchRtcToken(
  channel: string,
  uid: number,
  role: "publisher" | "subscriber" = "publisher"
): Promise<RtcTokenResponse> {
  return postJson<RtcTokenResponse>("/api/agora/token", { channel, uid, role });
}

export function startAgoraAgent(channel: string): Promise<StartAgentResponse> {
  return postJson<StartAgentResponse>("/api/agora/agent/start", { channel });
}

export function stopAgoraAgent(agentId: string): Promise<StopAgentResponse> {
  return postJson<StopAgentResponse>("/api/agora/agent/stop", { agent_id: agentId });
}
