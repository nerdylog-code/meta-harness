"""Meta-Harness v2 shared contracts (WP-003).

One logical specification, consumed by Python directly and mirrored to
TypeScript by generation (`packages/contracts/scripts/generate.py`), with parity
enforced by a test rather than by discipline.

Import order matters for readers, not for the interpreter:

    ids, enums      what values exist
    serialization   how they cross the wire
    usage           measurements and their provenance
    events          what happened
    domain          missions, tasks, artifacts
    workspace       where work may happen
    approval        who authorised what
    capsule         how work continues
    runtime         how runtimes are driven
    plugin          how capabilities are added
    fake, conformance   proof that the contract is satisfiable
"""

from __future__ import annotations

from .agent import AgentSpec, AgentVersion, CharacterBinding, ModelPolicy, Permission, RuntimePolicy
from .approval import (
    NON_DELEGABLE_RISK,
    ActionProposal,
    ApprovalRequest,
    payload_hash,
    requires_human,
)
from .capsule import (
    FORBIDDEN_FIELD_NAMES,
    MAX_CAPSULE_BYTES,
    MAX_RESUME_INSTRUCTION_CHARS,
    ActiveFile,
    ArtifactRefLite,
    Blocker,
    CapsulePhase,
    CompletedItem,
    ContextCapsule,
    Decision,
    NextAction,
    OpenQuestion,
    SelfAssessment,
    TestRecord,
    assert_no_transcript_field,
)
from .conformance import (
    Check,
    ConformanceReport,
    adapter_is_async,
    check_protocol_shape,
    run_conformance,
)
from .domain import AcceptanceGate, ArtifactRef, Budget, MissionSpec, TaskSpec
from .enums import (
    CAP_AGENT_SUBAGENTS,
    CAP_APPROVAL_NATIVE,
    CAP_CONTEXT_COMPACTION,
    CAP_MODEL_SWITCH,
    CAP_SESSION_FOLLOW_UP,
    CAP_SESSION_RESUME,
    CAP_SESSION_STEER,
    CAP_SESSION_STREAMING,
    CAP_TOOL_EVENTS,
    CAP_USAGE_COST,
    CAP_USAGE_TOKENS,
    CAP_VOICE_NATIVE,
    CAP_WORKSPACE_WORKTREE,
    KNOWN_CAPABILITIES,
    TERMINAL_TASK_STATES,
    ContractError,
    Enforcement,
    Provenance,
    RiskLevel,
    TaskState,
    TrustLevel,
    UnsupportedCapability,
)
from .events import ENVELOPE_KEYS, ID_FIELDS, KNOWN_NAMESPACES, CanonicalEvent, is_namespaced
from .fake import DEFAULT_FAKE_RUNTIME_ID, FAKE_RUNTIME_ID, FakeRuntimeAdapter
from .ids import (
    ID_PREFIXES,
    PREFIXES,
    IdKind,
    InvalidId,
    is_valid_id,
    new_id,
    opaque_part,
    validate_id,
)
from .policy import (
    ENFORCEMENT_MODES,
    MODE_CEILING,
    BudgetKind,
    BudgetRecord,
    BudgetRequest,
    EffectivePolicy,
    EnforcementEvidence,
    EvidenceCheck,
    ExecutionPolicy,
    RequestedPolicy,
)
from .plugin import UNLOAD_SEMANTICS, PluginKind, PluginManifest
from .runtime import (
    ADAPTER_METHODS,
    CapabilityInfo,
    CapabilitySet,
    Message,
    ModelInfo,
    RuntimeAdapter,
    RuntimeInfo,
    RuntimeSession,
    SessionSpec,
)
from .serialization import NotJsonSafe, dumps, external_envelope, json_safe, round_trip
from .taskgraph import (
    DEAD_STATES,
    LANES,
    LANE_OF_STATE,
    SATISFIED_STATES,
    STARTABLE_STATES,
    TERMINAL_LANES,
    TaskGraphError,
    blocked_ids,
    board_lane,
    dependency_blockers,
    effective_lane,
    find_cycle,
    is_ready,
    missing_dependencies,
    ready_ids,
    waves,
)
from .usage import Metric, UsageSample
from .workspace import (
    DEFAULT_ISOLATING_POLICY,
    LEASE_TTL_S,
    NETWORK_MODES,
    LeaseState,
    NetworkPolicy,
    RepositoryIdentity,
    SecretPolicy,
    WorkspaceAllocation,
    WorkspacePolicy,
    WorkspaceState,
    WriterLease,
    acquire_verdict,
    enforcement_is_honest,
    enforcement_summary,
    release_verdict,
)

#: Every contract that crosses the wire, in the order the generator emits them.
WIRE_CONTRACTS: tuple[str, ...] = (
    "Metric",
    "UsageSample",
    "CanonicalEvent",
    "CapabilityInfo",
    "CapabilitySet",
    "RuntimeInfo",
    "ModelInfo",
    "Message",
    "SessionSpec",
    "RuntimeSession",
    "NetworkPolicy",
    "SecretPolicy",
    "WorkspacePolicy",
    "Budget",
    "AcceptanceGate",
    "MissionSpec",
    "TaskSpec",
    "ArtifactRef",
    "ApprovalRequest",
    "ActionProposal",
    "ContextCapsule",
    "PluginManifest",
    "AgentSpec",
    "AgentVersion",
)

_CONTRACTS: dict[str, type] = {
    "Metric": Metric,
    "UsageSample": UsageSample,
    "CanonicalEvent": CanonicalEvent,
    "CapabilityInfo": CapabilityInfo,
    "CapabilitySet": CapabilitySet,
    "RuntimeInfo": RuntimeInfo,
    "ModelInfo": ModelInfo,
    "Message": Message,
    "SessionSpec": SessionSpec,
    "RuntimeSession": RuntimeSession,
    "NetworkPolicy": NetworkPolicy,
    "SecretPolicy": SecretPolicy,
    "WorkspacePolicy": WorkspacePolicy,
    "Budget": Budget,
    "AcceptanceGate": AcceptanceGate,
    "MissionSpec": MissionSpec,
    "TaskSpec": TaskSpec,
    "ArtifactRef": ArtifactRef,
    "ApprovalRequest": ApprovalRequest,
    "ActionProposal": ActionProposal,
    "ContextCapsule": ContextCapsule,
    "PluginManifest": PluginManifest,
    "AgentSpec": AgentSpec,
    "AgentVersion": AgentVersion,
}


def contract_models() -> dict[str, type]:
    """The models the generator and the parity test operate on."""
    missing = [name for name in WIRE_CONTRACTS if name not in _CONTRACTS]
    if missing:
        raise RuntimeError(f"WIRE_CONTRACTS names models that do not exist: {missing}")
    return {name: _CONTRACTS[name] for name in WIRE_CONTRACTS}


__all__ = [name for name in dir() if not name.startswith("_")]
