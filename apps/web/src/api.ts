/**
 * Thin typed client for the daemon API. No domain state lives here: the daemon
 * is the system of record (PROJECT_BOOK §5.5), the UI is a projection.
 */

export interface Health {
  status: string;
  service: string;
  version: string;
  git_sha: string;
  uptime_s: number;
  data_root: string;
  web_bundle: string | null;
  events: { last_seq: number; subscribers: number };
}

export interface VersionInfo {
  name: string;
  version: string;
  schema_version: number;
  git_sha: string;
  git_branch: string;
  python: string;
  platform: string;
  data_root: string;
  api_version: string;
}

export interface EventEnvelope {
  id: string;
  seq: number;
  ts: number;
  kind: string;
  payload: Record<string, unknown>;
  provenance: { method: string; origin: string };
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`${path} answered ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export const fetchHealth = () => getJson<Health>("/health");
export const fetchVersion = () => getJson<VersionInfo>("/version");

export async function fetchRecentEvents(limit = 20): Promise<EventEnvelope[]> {
  const body = await getJson<{ events: EventEnvelope[] }>(`/v1/events?limit=${limit}`);
  return body.events;
}

/**
 * Live event stream. Returns a disposer. Falls back to nothing on failure so the
 * caller can keep polling — a silent dead socket is the bug this avoids.
 */
export function openEventStream(onEvent: (event: EventEnvelope) => void): () => void {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  let socket: WebSocket | null = null;
  try {
    socket = new WebSocket(`${scheme}//${window.location.host}/v1/events/ws`);
  } catch {
    return () => undefined;
  }
  socket.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data as string) as EventEnvelope);
    } catch {
      // A malformed frame must not break the stream.
    }
  };
  return () => {
    socket?.close();
  };
}
