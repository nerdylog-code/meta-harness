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

export interface BudgetRecordRow {
  kind: string;
  requested: number | null;
  observed: number | null;
  enforcement_mode: string;
  enforcement: string;
  limit_reached: boolean;
  action: string | null;
  provenance: string;
  note: string | null;
}

export interface PolicyEvidence {
  provider: string | null;
  filesystem: string;
  network: string;
  wall_time: string;
  tool_calls: string;
  tokens: string;
  cost: string;
  child_processes: string;
  isolation?: string;
  checks: Array<{ name: string; ok: boolean; detail: string | null }>;
  note: string | null;
  as_badges?: Record<string, string>;
}

export interface SessionPolicy {
  session_id: string;
  requested: Record<string, unknown> | null;
  effective: { workspace?: string; filesystem_mode?: string; sandbox_provider?: string | null; budgets?: BudgetRecordRow[] } | null;
  evidence: PolicyEvidence | null;
  sandbox: Record<string, unknown> | null;
  isolation: string | null;
  budgets: BudgetRecordRow[] | null;
  exceeded: Record<string, unknown>;
  live: boolean;
}

export interface SandboxProviders {
  providers: Array<{ provider: string; available: boolean; detail: string }>;
  prefer: string | null;
}

export const fetchSessionPolicy = (sessionId: string) =>
  getJson<SessionPolicy>(`/v1/sessions/${encodeURIComponent(sessionId)}/policy`);

export const fetchSandboxProviders = () => getJson<SandboxProviders>("/v1/sandbox");

export const cancelSession = (sessionId: string) =>
  postJson<{ cancelled: boolean; orphans_left: boolean | null }>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/cancel`,
    {},
  );

// ------------------------------------------------------------------ work graph

/**
 * A task in a mission's dependency graph. The daemon is canonical: `ready` and `state` are
 * computed server-side and never inferred here, and `dependencies`/`dependents` carry task
 * ids that this module does not resolve -- the UI resolves them to titles for display.
 */
export interface MissionTask {
  id: string;
  mission_id: string;
  title: string;
  /** Canonical wire name. The UI may label it "Objective"; the field stays `description`. */
  description: string;
  /** Canonical wire name: `state`, never `status`. */
  state: string;
  /** Canonical wire name: `owner_agent`, never `assigned_agent_id`. */
  owner_agent: string | null;
  run_id: string | null;
  workspace_scope: string | null;
  dependencies: string[];
  dependents: string[];
  ready: boolean;
  created_at: number;
  completed_at: number | null;
  proof: string;
  artifacts: string;
  acceptance: string;
}

export interface MissionTasksPayload {
  mission_id: string;
  tasks: MissionTask[];
  ready: string[];
  blocked: string[];
  order: string[];
}

/**
 * A refusal from the daemon that carries a machine-readable status and the daemon's own detail
 * string. A 409 (a cycle, an unmet dependency, an illegal transition) is a decision the server
 * made, not a transport failure, and the UI must show the detail rather than swallow it.
 */
export class ApiRefusal extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(path: string, status: number, detail: string) {
    super(`${path} refused ${status}: ${detail}`);
    this.name = "ApiRefusal";
    this.status = status;
    this.detail = detail;
  }
}

/** POST/DELETE with the same shape as postJson, but refusals keep their status and detail. */
async function sendJson<T>(method: "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers:
      body === undefined
        ? { accept: "application/json" }
        : { "content-type": "application/json", accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed && typeof parsed === "object" && "detail" in parsed) {
        const raw = (parsed as { detail: unknown }).detail;
        if (typeof raw === "string") detail = raw;
      }
    } catch {
      // not JSON: the raw body text is the detail
    }
    throw new ApiRefusal(path, response.status, detail.slice(0, 500));
  }
  return (await response.json()) as T;
}

export const fetchMissionTasks = (missionId: string) =>
  getJson<MissionTasksPayload>(`/v1/missions/${encodeURIComponent(missionId)}/tasks`);

// ---------------------------------------------------------------------- workboard

export interface WorkboardTask {
  task_id: string;
  title: string;
  state: string;
  lane: string;
  agent: { id: string } | null;
  dependencies: string[];
  blocked: { waiting_on: string[]; dead: string[] };
  run: {
    run_id: string;
    state: string | null;
    runtime_id?: string | null;
    agent_id?: string | null;
    session_id?: string | null;
    provider?: string | null;
    model?: string | null;
    reason?: string | null;
    note?: string | null;
  } | null;
  workspace: {
    provider: string;
    locator: string;
    state: string;
    dirty: boolean | null;
    measured: boolean;
    note: string | null;
  } | null;
  writer: {
    run_id: string;
    generation: number;
    expires_at: number;
    state: string;
    active: boolean;
    expired: boolean;
  } | null;
  approvals: {
    pending: number;
    by_state: Record<string, number>;
    requires_attention: boolean;
    latest: { id: string } | null;
    expired_pending: string[];
  };
  artifacts: { count: number; recorded: number; proof: number; ids: string[] };
  usage: { sample: Record<string, unknown>; sampled_at: number } | null;
  enforcement: {
    write_isolation: string;
    write_isolation_scope: string;
    write_isolation_detail: string;
    filesystem_isolation: string;
    filesystem_isolation_scope: string;
    filesystem_isolation_detail: string;
    note: string;
  };
  reasons: string[];
  wave: number | null;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
}

export interface WorkboardPayload {
  mission: { id: string; title: string; objective: string | null; state: string | null };
  columns: Array<{ lane: string; count: number; tasks: WorkboardTask[] }>;
  waves: Array<{ wave: number; tasks: string[] }>;
  counts: Record<string, number>;
  total: number;
  filters: {
    agents: string[];
    runtimes: string[];
    states: string[];
    lanes: string[];
    needs_approval: string[];
    has_workspace: string[];
    has_active_lease: string[];
    blocked: string[];
  };
  degraded: string[];
  composed_at: number;
}

export const fetchMissionBoard = (missionId: string) =>
  getJson<WorkboardPayload>(`/v1/missions/${encodeURIComponent(missionId)}/board`);

export const requestTaskReview = (taskId: string) =>
  sendJson<MissionTask>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/review`, {});

export const fetchTask = (taskId: string) =>
  getJson<MissionTask>(`/v1/tasks/${encodeURIComponent(taskId)}`);

export const createTask = (
  missionId: string,
  input: {
    title: string;
    /** The wire field is `description`; the form may label it "Objective". */
    description: string;
    dependencies: string[];
    requiresArtifact: boolean;
    ownerAgent: string | null;
  },
) =>
  sendJson<MissionTask>("POST", `/v1/missions/${encodeURIComponent(missionId)}/tasks`, {
    title: input.title,
    description: input.description,
    dependencies: input.dependencies,
    requires_artifact: input.requiresArtifact,
    owner_agent: input.ownerAgent,
  });

export const startTask = (
  taskId: string,
  runId: string | null = null,
  agentId: string | null = null,
) =>
  sendJson<MissionTask>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/start`, {
    run_id: runId,
    agent_id: agentId,
  });

export const completeTask = (
  taskId: string,
  proof: string[],
  runId: string | null = null,
  artifacts: string | null = null,
) =>
  sendJson<MissionTask>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/complete`, {
    proof,
    run_id: runId,
    artifacts,
  });

export const failTask = (taskId: string, reason: string | null = null) =>
  sendJson<MissionTask>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/fail`, { reason });

export const cancelTask = (taskId: string, reason: string | null = null) =>
  sendJson<MissionTask>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/cancel`, { reason });

export const assignTask = (taskId: string, agentId: string) =>
  sendJson<MissionTask>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/assign`, {
    agent_id: agentId,
  });

export const addTaskDependency = (taskId: string, dependsOn: string) =>
  sendJson<MissionTask>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/dependencies`, {
    depends_on: dependsOn,
  });

export const removeTaskDependency = (taskId: string, dependencyId: string) =>
  sendJson<MissionTask>(
    "DELETE",
    `/v1/tasks/${encodeURIComponent(taskId)}/dependencies/${encodeURIComponent(dependencyId)}`,
  );

// ------------------------------------------------------------------- approvals

export type ApprovalRisk = "R0" | "R1" | "R2" | "R3" | "R4";
export type ApprovalState = "pending" | "granted" | "denied" | "expired" | "consumed";

export interface Approval {
  id: string;
  action_type: string;
  action_payload: Record<string, unknown>;
  action_payload_hash: string;
  risk_level: ApprovalRisk;
  requires_human: boolean;
  human_summary: string;
  reversibility: "reversible" | "irreversible";
  requested_by: string;
  requested_ts: number;
  expires_at: number | null;
  state: ApprovalState;
  granted_by: string | null;
  granted_ts: number | null;
  consumed_ts: number | null;
  reason: string | null;
  risk_floor: string;
}

export interface ApprovalList {
  approvals: Approval[];
  pending: string[];
  counts: Record<string, number>;
}

export interface ApprovalConsumeResult {
  authorised: boolean;
  approval_id: string;
  action_payload_hash: string;
  approval: Approval;
}

export const fetchApprovals = () => getJson<ApprovalList>("/v1/approvals?state=pending");
export const fetchApproval = (id: string) =>
  getJson<Approval>(`/v1/approvals/${encodeURIComponent(id)}`);
export const grantApproval = (id: string, by: string) =>
  sendJson<Approval>("POST", `/v1/approvals/${encodeURIComponent(id)}/grant`, { by });
export const denyApproval = (id: string, by: string, reason?: string) =>
  sendJson<Approval>("POST", `/v1/approvals/${encodeURIComponent(id)}/deny`, {
    by,
    ...(reason ? { reason } : {}),
  });
export const consumeApproval = (
  id: string,
  input: { action_type: string; action_payload: Record<string, unknown>; by?: string },
) => sendJson<ApprovalConsumeResult>("POST", `/v1/approvals/${encodeURIComponent(id)}/consume`, input);

// ------------------------------------------------------------------ artifacts

export type ArtifactIntegrity = "ok" | "missing" | "mismatch" | "unchecked";
export type ArtifactPreviewKind = "text" | "json" | "image" | "binary";

export interface ArtifactOrigin {
  mission_id?: string | null;
  task_id?: string | null;
  run_id?: string | null;
  agent_id?: string | null;
}

export interface ArtifactRecord {
  id: string;
  sha256: string;
  size: number;
  mime: string;
  locator: string;
  preview_kind: ArtifactPreviewKind;
  preview_available: boolean;
  preview_reason: string | null;
  integrity: ArtifactIntegrity;
  integrity_detail: string | null;
  verified: boolean;
  origin: ArtifactOrigin;
  metadata: Record<string, unknown>;
  provenance: Record<string, unknown>;
  created_at?: number;
  seq?: number;
}

export interface ArtifactListPayload {
  artifacts: ArtifactRecord[];
  count: number;
}

export interface ArtifactPreview {
  id: string;
  mime: string;
  kind: ArtifactPreviewKind;
  size: number;
  start: number;
  limit: number;
  truncated: boolean;
  text: string | null;
  reason: string | null;
  integrity?: ArtifactIntegrity;
  content_url?: string;
}

export interface ArtifactListFilters {
  mission_id?: string;
  task_id?: string;
  run_id?: string;
  agent_id?: string;
}

async function getArtifactJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed && typeof parsed === "object" && "detail" in parsed) {
        const raw = (parsed as { detail: unknown }).detail;
        if (typeof raw === "string") detail = raw;
      }
    } catch {
      // Keep the daemon's plain-text refusal detail.
    }
    throw new ApiRefusal(path, response.status, detail.slice(0, 500));
  }
  return (await response.json()) as T;
}

export const fetchArtifacts = (filters: ArtifactListFilters = {}) => {
  const query = new URLSearchParams();
  for (const key of ["mission_id", "task_id", "run_id", "agent_id"] as const) {
    const value = filters[key];
    if (value) query.set(key, value);
  }
  const suffix = query.size ? `?${query.toString()}` : "";
  return getArtifactJson<ArtifactListPayload>(`/v1/artifacts${suffix}`);
};

export const fetchArtifact = (artifactId: string) =>
  getArtifactJson<ArtifactRecord>(`/v1/artifacts/${encodeURIComponent(artifactId)}`);

export const fetchArtifactPreview = (artifactId: string, start = 0, limit = 4096) => {
  const query = new URLSearchParams({ start: String(start), limit: String(limit) });
  return getArtifactJson<ArtifactPreview>(
    `/v1/artifacts/${encodeURIComponent(artifactId)}/preview?${query.toString()}`,
  );
};

export async function fetchArtifactContent(
  artifactId: string,
  range: { start?: number; end?: number } = {},
): Promise<Response> {
  const query = new URLSearchParams();
  if (range.start !== undefined) query.set("start", String(range.start));
  if (range.end !== undefined) query.set("end", String(range.end));
  const suffix = query.size ? `?${query.toString()}` : "";
  const path = `/v1/artifacts/${encodeURIComponent(artifactId)}/content${suffix}`;
  const response = await fetch(path);
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed && typeof parsed === "object" && "detail" in parsed) {
        const raw = (parsed as { detail: unknown }).detail;
        if (typeof raw === "string") detail = raw;
      }
    } catch {
      // Keep the daemon's plain-text refusal detail.
    }
    throw new ApiRefusal(path, response.status, detail.slice(0, 500));
  }
  return response;
}

// -------------------------------------------------------------- task workspaces

export interface WorkspaceWriter {
  run_id: string;
  generation: number;
  acquired_at: number;
  heartbeat_at: number;
  expires_at: number;
  state: string;
  expired: boolean;
  active: boolean;
}

export interface WorkspaceEnforcement {
  write_isolation: string;
  write_isolation_scope: string;
  write_isolation_detail: string | null;
  filesystem_isolation: string;
  filesystem_isolation_scope: string;
  note: string | null;
}

export interface Workspace {
  task_id: string;
  mission_id: string;
  provider: string;
  repository: { root: string; common_dir: string };
  base_ref: string;
  base_commit: string;
  branch: string;
  locator: string;
  state: "ready" | "allocating" | "missing" | "conflict" | "removed" | "failed" | string;
  recorded_state: string;
  dirty: boolean | null;
  measured: boolean;
  note: string | null;
  created_at: number;
  writer: WorkspaceWriter | null;
  enforcement: WorkspaceEnforcement;
}

export interface WorkspaceLeaseResult {
  verdict: "grant" | "renew";
  reason: string;
  writer: WorkspaceWriter;
}

export interface WorkspaceLeaseReleaseResult {
  released: boolean;
  writer: WorkspaceWriter | null;
}

export interface WorkspaceLeaseRenewResult {
  renewed: boolean;
  writer: WorkspaceWriter;
}

export interface WorkspaceRemoveResult {
  removed: boolean;
  task_id: string;
  branch: string;
  branch_kept: boolean;
}

export const fetchTaskWorkspace = async (taskId: string): Promise<Workspace | null> => {
  const path = `/v1/tasks/${encodeURIComponent(taskId)}/workspace`;
  try {
    return await getArtifactJson<Workspace>(path);
  } catch (error) {
    if (error instanceof ApiRefusal && error.status === 404) return null;
    throw error;
  }
};

export const fetchWorkspaces = () =>
  getArtifactJson<{ workspaces: Workspace[]; count: number }>("/v1/workspaces");

export const allocateWorkspace = (taskId: string, repository: string, base_ref = "HEAD") =>
  sendJson<Workspace>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/workspace/allocate`, {
    repository,
    base_ref,
  });

export const acquireWorkspaceLease = (taskId: string, run_id: string, ttl_s?: number) =>
  sendJson<WorkspaceLeaseResult>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/workspace/lease/acquire`, {
    run_id,
    ...(ttl_s === undefined ? {} : { ttl_s }),
  });

export const releaseWorkspaceLease = (taskId: string, run_id: string, generation: number) =>
  sendJson<WorkspaceLeaseReleaseResult>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/workspace/lease/release`, {
    run_id,
    generation,
  });

export const renewWorkspaceLease = (taskId: string, run_id: string, generation: number) =>
  sendJson<WorkspaceLeaseRenewResult>("POST", `/v1/tasks/${encodeURIComponent(taskId)}/workspace/lease/renew`, {
    run_id,
    generation,
  });

export const removeTaskWorkspace = (taskId: string) =>
  sendJson<WorkspaceRemoveResult>("DELETE", `/v1/tasks/${encodeURIComponent(taskId)}/workspace`);

// ----------------------------------------------------------------------- canvas

export type CanvasEntityType = "task" | "agent" | "run" | "session" | "workspace" | "approval" | "artifact";

export interface CanvasNode {
  key: string;
  entity_type: CanvasEntityType;
  entity_id: string;
  label: string;
  state: string | null;
  summary: string;
  metadata: Record<string, unknown>;
}

export interface CanvasEdge {
  key: string;
  kind: string;
  source: string;
  target: string;
  label: string;
  mutable: boolean;
}

export interface MissionCanvas {
  mission_id: string;
  mission: { id: string; title: string; objective: string | null };
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  waves: Array<{ wave: number; tasks: string[] }>;
  node_types: CanvasEntityType[];
  counts: { nodes: number; edges: number; by_type: Partial<Record<CanvasEntityType, number>> };
  degraded: string[];
  composed_at: number;
}

export const fetchMissionCanvas = (missionId: string) =>
  getArtifactJson<MissionCanvas>(`/v1/missions/${encodeURIComponent(missionId)}/canvas`);
