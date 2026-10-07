// GENERATED FILE — DO NOT EDIT BY HAND.
//
// Source of truth: packages/contracts/metaharness_contracts/*.py
// Regenerate:      python packages/contracts/scripts/generate.py
// Parity is enforced by tests/contracts/test_parity.py (byte comparison).
//
// TypeScript has no runtime validation here on purpose: these are the *types*
// the UI consumes, and the validation lives on the Python side where the data
// enters the system.

export const CONTRACT_VERSION = 1;

export type CapsulePhase = "NORMAL" | "NOTICE" | "PREPARE" | "FORCED" | "COMPACTING" | "RESUMED";
export type Enforcement = "weak" | "moderate" | "strong";
export type PluginKind = "RuntimeAdapter" | "ToolProvider" | "ContextEngine" | "MemoryProvider" | "RagProvider" | "SandboxProvider" | "WorkspaceProvider" | "ChannelProvider" | "VoiceProvider" | "WorkflowProvider" | "RendererProvider" | "MetricsExporter" | "SkillProvider" | "ModelProvider";
export type Provenance = "provider_reported" | "runtime_reported" | "measured" | "estimated" | "unknown";
export type RiskLevel = "R0" | "R1" | "R2" | "R3" | "R4";
export type TaskState = "draft" | "ready" | "claimed" | "running" | "waiting" | "review" | "blocked" | "failed" | "done" | "cancelled";
export type TrustLevel = "core" | "builtin" | "signed" | "trusted-user" | "user" | "generated" | "experimental" | "quarantined";

/** How a task proves it is done. A model's sentence is not a gate. */
export interface AcceptanceGate {
  command?: string | null;
  criteria?: string[];
  requires_artifact?: boolean;
}

/** A proposal. It authorises nothing; it is the input to an approval. */
export interface ActionProposal {
  action: string;
  action_payload?: Record<string, unknown>;
  evidence?: string[];
  impact: string;
  required_approval: boolean;
  reversible: boolean;
  risk: RiskLevel;
}

export interface ActiveFile {
  path: string;
  reason: string;
  state?: string;
}

/** The identity of an agent, independent of any runtime. */
export interface AgentSpec {
  character?: CharacterBinding;
  context_policy?: string;
  display_name: string;
  heartbeat_policy?: string;
  id: string;
  memory_policy?: string;
  metadata?: Record<string, unknown>;
  model_policy: ModelPolicy;
  permissions?: Permission[];
  purpose?: string | null;
  role: string;
  runtime_policy: RuntimePolicy;
  skills?: string[];
  voice?: string | null;
  workspace_policy?: WorkspacePolicy;
}

/** A frozen configuration of an agent. Immutable by construction. */
export interface AgentVersion {
  agent_id: string;
  created_at: number;
  notes?: string | null;
  spec: AgentSpec;
  trust?: TrustLevel;
  version: number;
}

/** An authorisation bound to one payload hash. */
export interface ApprovalRequest {
  action_payload?: Record<string, unknown>;
  action_payload_hash: string;
  action_type: string;
  expires_at?: number | null;
  granted?: boolean;
  granted_at?: number | null;
  granted_by?: string | null;
  human_summary: string;
  id: string;
  requested_by: string;
  reversibility: string;
  risk_level: RiskLevel;
}

/** A durable payload. The bytes live on the filesystem, never in the DB. */
export interface ArtifactRef {
  id: string;
  metadata?: Record<string, unknown>;
  mime: string;
  origin?: Record<string, unknown>;
  path: string;
  sha256: string;
  size: number;
}

/** A pointer to an artifact, by exact id. Never the artifact's content. */
export interface ArtifactRefLite {
  artifact_id: string;
  purpose: string;
}

export interface Blocker {
  description: string;
  kind?: string;
  needs?: string | null;
}

/** A budget is a limit, not a hope (BOOK §12). */
export interface Budget {
  attempts?: number | null;
  money?: number | null;
  tokens?: number | null;
  wall_time_s?: number | null;
}

/** One thing that happened, canonical and vendor-neutral. Nothing here branches on a vendor: an executing runtime is an id string, and the same envelope carries any of them. Keeping the schema free of vendor names is a tested property, not a style preference (tests/contracts/test_contract_hygiene.py). */
export interface CanonicalEvent {
  agent_id?: string | null;
  causation_id?: string | null;
  correlation_id?: string | null;
  id: string;
  kind: string;
  mission_id?: string | null;
  payload?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
  run_id?: string | null;
  runtime_id?: string | null;
  seq: number;
  session_id?: string | null;
  task_id?: string | null;
  ts: number;
  [key: string]: unknown;
}

/** One capability, with the metadata its provider chooses to attach. */
export interface CapabilityInfo {
  metadata?: Record<string, unknown>;
  note?: string | null;
  supported: boolean;
  version?: number;
}

/** A runtime's declared capabilities. This is a mapping of capability id -> :class:`CapabilityInfo`, not a wall of booleans spread across the adapter contract. It says nothing about *which* runtime it came from, and it is the only thing the UI may consult before offering a feature. */
export interface CapabilitySet {
  capabilities?: Record<string, CapabilityInfo>;
}

export interface CharacterBinding {
  id?: string;
  pack?: string;
}

export interface CompletedItem {
  proof?: string[];
  result: string;
  task_id?: string | null;
}

/** The 16 required fields of WP-003 decision 6 — no more, no fewer. */
export interface ContextCapsule {
  active_files?: ActiveFile[];
  artifacts?: ArtifactRefLite[];
  blockers?: Blocker[];
  completed?: CompletedItem[];
  current_phase: CapsulePhase;
  decisions?: Decision[];
  invariants?: string[];
  memory_candidates?: string[];
  next_actions?: NextAction[];
  objective: string;
  open_questions?: OpenQuestion[];
  resume_instruction: string;
  self_assessment?: SelfAssessment;
  tests?: TestRecord[];
  workspace_state?: Record<string, unknown>;
}

export interface Decision {
  alternatives?: string[];
  choice: string;
  id: string;
  rationale: string;
}

export interface Message {
  attachments?: string[];
  metadata?: Record<string, unknown>;
  role?: string;
  text: string;
}

/** A single measurement with the provenance of *that* value. ``value`` is None exactly when the metric is unknown — never 0. The type is intentionally **not** parametrized by int/float: JSON has one number type, so a `Metric[int]` in Python would promise a distinction the wire cannot carry and the TypeScript mirror could not express. Count fields (tokens, retries) are documented as counts and validated as such by their producers. */
export interface Metric {
  note?: string | null;
  provenance?: Provenance;
  source?: string | null;
  unit?: string | null;
  value?: number | null;
}

export interface MissionSpec {
  created_at: number;
  id: string;
  metadata?: Record<string, unknown>;
  objective: string;
  owner?: string | null;
  status?: string;
  title: string;
}

export interface ModelInfo {
  context_limit?: number | null;
  id: string;
  notes?: Record<string, unknown>;
  provider?: string | null;
  [key: string]: unknown;
}

export interface ModelPolicy {
  escalation?: string | null;
  primary: string;
  rationale?: string | null;
}

export interface NetworkPolicy {
  allow?: string[];
  deny?: string[];
  mode?: string;
}

export interface NextAction {
  depends_on?: string[];
  exact_action: string;
  task_id?: string | null;
}

export interface OpenQuestion {
  blocking?: boolean;
  question: string;
}

/** A named permission with a risk level. R4 cannot be auto-granted. */
export interface Permission {
  granted?: boolean;
  name: string;
  risk?: RiskLevel;
  scope?: string[];
}

/** The minimum a plugin must declare to be loadable. */
export interface PluginManifest {
  config_schema?: Record<string, unknown>;
  entrypoint: string;
  events_consumed?: string[];
  events_produced?: string[];
  health_check?: string | null;
  id: string;
  kind: PluginKind;
  model_visible_surfaces?: string[];
  permissions?: string[];
  provides?: string[];
  requires?: string[];
  unload_semantics?: string;
  version: string;
}

/** What `probe()` reports. Vendor identity is data, not a schema branch. */
export interface RuntimeInfo {
  available: boolean;
  capabilities?: CapabilitySet;
  detail?: string | null;
  name: string;
  protocol?: string | null;
  runtime_id: string;
  version?: string | null;
  [key: string]: unknown;
}

export interface RuntimePolicy {
  fallbacks?: string[];
  preferred: string;
}

export interface RuntimeSession {
  created_at: number;
  detail?: Record<string, unknown>;
  runtime_id: string;
  session_id: string;
  [key: string]: unknown;
}

/** Which secret scopes a task may resolve, by *name*, never by value. A policy refers to secrets; it never carries one. Values are resolved by the secrets broker at use time (BOOK §45). */
export interface SecretPolicy {
  allowed?: string[];
  denied?: string[];
  inject_as_env?: boolean;
}

export interface SelfAssessment {
  concerns?: string[];
  confidence?: number | null;
  not_verified?: string[];
}

/** Everything a runtime needs to create a session. `allowed_tools` is deliberately **required**: a session created without an explicit tool restriction silently inherits the runtime's full tool set, which is how a test turn once wrote real memory (see the Phase-0 evidence recorded in V1_CONFLICTS §C12). */
export interface SessionSpec {
  agent_id: string;
  allowed_tools: string[];
  metadata?: Record<string, unknown>;
  model?: string | null;
  runtime_id: string;
  system_prompt?: string | null;
  workspace?: string | null;
}

/** A unit of work, per BOOK §7. */
export interface TaskSpec {
  acceptance_gate: AcceptanceGate;
  artifacts?: string[];
  budget?: Budget;
  completed_at?: number | null;
  created_at?: number;
  deadline?: number | null;
  dependencies?: string[];
  description?: string;
  id: string;
  mission_id: string;
  owner_agent?: string | null;
  parent_id?: string | null;
  proof?: string[];
  retries?: number;
  run_id?: string | null;
  state?: TaskState;
  title: string;
  workspace_policy?: WorkspacePolicy;
  workspace_scope?: string | null;
}

export interface TestRecord {
  command: string;
  last_result: string;
  passed?: boolean | null;
}

/** The universal usage record (BOOK §17/§49). Every numeric field is a :class:`Metric`, so `estimated` can never be mistaken for `provider_reported` and a missing value can never read as zero. */
export interface UsageSample {
  agent?: string | null;
  cache_read_tokens?: Metric;
  cache_write_tokens?: Metric;
  compaction_input?: Metric;
  compaction_output?: Metric;
  context_limit?: Metric;
  context_tokens?: Metric;
  duration_ms?: Metric;
  estimated_cost?: Metric;
  input_tokens?: Metric;
  model?: string | null;
  output_tokens?: Metric;
  provider?: string | null;
  provider_cost?: Metric;
  reasoning_tokens?: Metric;
  retries?: Metric;
  retrieval_tokens?: Metric;
  run?: string | null;
  runtime?: string | null;
  session?: string | null;
  system_tokens?: Metric;
  task?: string | null;
  tool_duration_ms?: Metric;
  tool_result_tokens?: Metric;
  tool_schema_tokens?: Metric;
  ttft_ms?: Metric;
}

export interface WorkspacePolicy {
  deny?: string[];
  enforcement?: Enforcement | null;
  enforcement_detail?: string | null;
  include?: string[];
  network?: NetworkPolicy;
  readonly?: string[];
  secrets?: SecretPolicy;
  write?: string[];
}

/** The union of every wire contract, for discriminated handling in the UI. */
export type WireContract =
  | Metric
  | UsageSample
  | CanonicalEvent
  | CapabilityInfo
  | CapabilitySet
  | RuntimeInfo
  | ModelInfo
  | Message
  | SessionSpec
  | RuntimeSession
  | NetworkPolicy
  | SecretPolicy
  | WorkspacePolicy
  | Budget
  | AcceptanceGate
  | MissionSpec
  | TaskSpec
  | ArtifactRef
  | ApprovalRequest
  | ActionProposal
  | ContextCapsule
  | PluginManifest
  | AgentSpec
  | AgentVersion;
