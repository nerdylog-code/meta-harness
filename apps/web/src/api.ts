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

// --------------------------------------------------------------- control plane

export interface MissionRow {
  id: string;
  title: string;
  objective: string;
  owner: string | null;
  status: string;
  created_ts: number;
}

export interface AgentVersionRow {
  id: string;
  agent_id: string;
  version: number;
  runtime_preferred: string | null;
  model_primary: string | null;
}

export interface AgentRow {
  id: string;
  display_name: string;
  role: string;
  created_ts: number;
  versions: AgentVersionRow[];
}

export interface SessionRow {
  id: string;
  agent_id: string | null;
  mission_id: string | null;
  runtime_id: string | null;
  provider: string | null;
  model: string | null;
  state: string;
  reason: string | null;
  created_ts: number;
}

export interface RuntimeStatus {
  runtime: {
    runtime_id: string;
    name: string;
    version: string | null;
    available: boolean;
    detail: string | null;
    protocol: string | null;
    capabilities: { capabilities: Record<string, { supported: boolean; note: string | null }> };
  };
  sessions: Array<{
    session_id: string;
    agent_id: string | null;
    streaming: boolean;
    settled: boolean;
    dropped_events: number;
    provider: string | null;
    model: string | null;
  }>;
  protocol_errors: string[];
}

export const fetchMissions = () => getJson<{ missions: MissionRow[]; count: number }>("/v1/missions");
export const fetchAgents = () => getJson<{ agents: AgentRow[]; count: number }>("/v1/agents");
export const fetchSessions = (agentId?: string) =>
  getJson<{ sessions: SessionRow[]; count: number }>(
    agentId ? `/v1/sessions?agent_id=${encodeURIComponent(agentId)}` : "/v1/sessions",
  );
export const fetchRuntime = () => getJson<RuntimeStatus>("/v1/runtime");

export async function fetchSessionEvents(sessionId: string, limit = 300): Promise<EventEnvelope[]> {
  const body = await getJson<{ events: EventEnvelope[]; count: number }>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/events?limit=${limit}`,
  );
  return body.events;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${path} answered ${response.status}: ${detail.slice(0, 300)}`);
  }
  return (await response.json()) as T;
}

export const createMission = (title: string, objective = "") =>
  postJson<{ mission_id: string }>("/v1/missions", { title, objective });

export const createAgent = (displayName: string, modelPrimary: string | null) =>
  postJson<{ agent_id: string }>("/v1/agents", {
    display_name: displayName,
    role: "builder",
    model_primary: modelPrimary,
  });

export const createSession = (agentId: string, missionId: string | null, tools: string[]) =>
  postJson<{ session_id: string; run_id: string; runtime_id: string; detail: Record<string, unknown>; pid: number }>(
    "/v1/sessions",
    { agent_id: agentId, mission_id: missionId, tools },
  );

export const sendMessage = (sessionId: string, text: string) =>
  postJson<{ accepted: boolean }>(`/v1/sessions/${encodeURIComponent(sessionId)}/messages`, { text });

export interface MigrationRow {
  id: string;
  agent_id: string | null;
  mission_id: string | null;
  from_runtime: string | null;
  to_runtime: string | null;
  from_session: string | null;
  to_session: string | null;
  capsule_id: string | null;
  digest: string | null;
  state: string;
  failed_stage: string | null;
  reason: string | null;
  created_ts: number;
}

export interface CapsuleRow {
  id: string;
  agent_id: string | null;
  mission_id: string | null;
  session_id: string | null;
  runtime_id: string | null;
  phase: string;
  objective: string;
  sha256: string;
  size: number;
  verified: boolean;
  created_ts: number;
}

export interface RuntimesPayload {
  runtimes: Array<{
    runtime_id: string;
    is_default: boolean;
    available: boolean;
    name?: string | null;
    version?: string | null;
    protocol?: string | null;
    detail?: string | null;
  }>;
}

export const fetchRuntimes = () => getJson<RuntimesPayload>("/v1/runtimes");
export const fetchMigrations = (agentId?: string) =>
  getJson<{ migrations: MigrationRow[]; count: number }>(
    agentId ? `/v1/migrations?agent_id=${encodeURIComponent(agentId)}` : "/v1/migrations",
  );
export const fetchCapsules = (agentId?: string) =>
  getJson<{ capsules: CapsuleRow[]; count: number }>(
    agentId ? `/v1/capsules?agent_id=${encodeURIComponent(agentId)}` : "/v1/capsules",
  );

export const migrateAgent = (agentId: string, toRuntime: string, model: string | null = null) =>
  postJson<{
    migration_id: string;
    agent_id: string;
    version: number;
    from_runtime: string | null;
    to_runtime: string;
    from_session: string | null;
    to_session: string;
    state: Record<string, boolean>;
  }>(`/v1/agents/${encodeURIComponent(agentId)}/migrate`, {
    to_runtime: toRuntime,
    model,
    tools: ["read"],
  });

export const cancelSession = (sessionId: string) =>
  postJson<{ cancelled: boolean; orphans_left: boolean | null }>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/cancel`,
    {},
  );
