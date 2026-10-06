/**
 * Typed client for the daemon API. No domain state lives here: the daemon is the system of
 * record (PROJECT_BOOK §5.5) and this module is a projection of it, nothing more.
 *
 * Every type below mirrors a response the daemon actually returns today. Nothing here
 * models a feature the daemon does not have -- a hopeful shape in the client is how a UI
 * starts lying about what the system can do.
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
  store: {
    path: string | null;
    schema_version: number | null;
    events: number | null;
    journal_mode: string | null;
  };
  reconcile: ReconcileReport | null;
}

export interface ReconcileReport {
  runs_examined: number;
  orphans_marked: number;
  leases_released: number;
  artifacts_preserved: number;
  artifacts_missing: string[];
  events_emitted: string[];
  skipped_alive: string[];
}

export interface VersionInfo {
  name: string;
  version: string;
  git_sha: string;
  git_branch: string;
  python: string;
  platform: string;
  data_root: string;
  api_version: string;
}

/** The frozen envelope (ADR-0017): 14 keys, no more, no less. */
export interface EventEnvelope {
  id: string;
  seq: number;
  ts: number;
  kind: string;
  mission_id: string | null;
  task_id: string | null;
  run_id: string | null;
  agent_id: string | null;
  session_id: string | null;
  runtime_id: string | null;
  correlation_id: string | null;
  causation_id: string | null;
  payload: Record<string, unknown>;
  provenance: { method: string; origin?: string };
}

export const ENVELOPE_KEYS = [
  "id",
  "seq",
  "ts",
  "kind",
  "mission_id",
  "task_id",
  "run_id",
  "agent_id",
  "session_id",
  "runtime_id",
  "correlation_id",
  "causation_id",
  "payload",
  "provenance",
] as const;

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
  const body = await getJson<{ events: EventEnvelope[]; last_seq: number }>(
    `/v1/events?limit=${limit}`,
  );
  return body.events;
}

export function eventStreamUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/v1/events/ws`;
}

/** The provenance label, rendered verbatim. `unknown` is never shown as `0` or blank. */
export function provenanceLabel(event: EventEnvelope): string {
  return event.provenance?.method ?? "unknown";
}

export function shortId(value: string | null | undefined, length = 10): string {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

export function formatTimestamp(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toISOString().replace("T", " ").replace("Z", "");
}

export function namespaceOf(kind: string): string {
  const [namespace] = kind.split(".");
  return namespace || "unknown";
}
