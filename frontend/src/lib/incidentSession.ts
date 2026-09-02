"use client";

import { useSyncExternalStore } from "react";

// Shared by page.tsx (to decide the active Agora channel / voice join target)
// and IncidentDashboard.tsx (to load/stream incident state) - the current
// incident id is the one thing that ties "the voice room", "the incident",
// and "EchoWard" together, so it lives in one place instead of being
// duplicated per component.
const LAST_INCIDENT_KEY = "echoward:lastIncidentId";

// useSyncExternalStore (rather than reading localStorage in a lazy useState
// initializer or a mount effect) is what keeps the very first client render
// identical to the server-rendered HTML - React deliberately renders
// getServerSnapshot() during hydration, then re-syncs to the real client
// snapshot right after, so there's no SSR/CSR mismatch and no need to
// setState from inside an effect just to mirror an external source.
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): string | null {
  return localStorage.getItem(LAST_INCIDENT_KEY);
}

function getServerSnapshot(): string | null {
  return null;
}

export function useLastIncidentId(): string | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function persistLastIncidentId(id: string) {
  localStorage.setItem(LAST_INCIDENT_KEY, id);
  listeners.forEach((listener) => listener());
}

export function clearLastIncidentId() {
  localStorage.removeItem(LAST_INCIDENT_KEY);
  listeners.forEach((listener) => listener());
}
