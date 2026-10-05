"""Enumerations whose exact spelling is part of the contract.

Everything here is wire-visible: the strings are persisted, compared and shown
in the UI, so they are frozen by tests rather than left to a comment.
"""

from __future__ import annotations

from enum import Enum


class Provenance(str, Enum):
    """How a value was obtained. Never invent a sixth option silently."""

    PROVIDER_REPORTED = "provider_reported"
    RUNTIME_REPORTED = "runtime_reported"
    MEASURED = "measured"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """BOOK §40/§44. R4 approval is mandatory and no plugin may disable it."""

    R0 = "R0"  # read-only
    R1 = "R1"  # reversible local state
    R2 = "R2"  # workspace mutation
    R3 = "R3"  # external side effect
    R4 = "R4"  # destructive / credential / production / financial


class Enforcement(str, Enum):
    """How strongly a policy is actually enforced (WP-003 decision 7).

    A runtime reports what it *reaches*; the policy asks for what it *wants*.
    The gap is displayed, never smoothed over.
    """

    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class TrustLevel(str, Enum):
    """BOOK §10. Promotions move up this ladder; nothing starts high."""

    CORE = "core"
    BUILTIN = "builtin"
    SIGNED = "signed"
    TRUSTED_USER = "trusted-user"
    USER = "user"
    GENERATED = "generated"
    EXPERIMENTAL = "experimental"
    QUARANTINED = "quarantined"


class TaskState(str, Enum):
    """BOOK §7."""

    DRAFT = "draft"
    READY = "ready"
    CLAIMED = "claimed"
    RUNNING = "running"
    WAITING = "waiting"
    REVIEW = "review"
    BLOCKED = "blocked"
    FAILED = "failed"
    DONE = "done"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATES: frozenset[TaskState] = frozenset(
    {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}
)

# ---- capability ids (WP-003 decision 4) ------------------------------------
# Structured, dotted, versionable. A CapabilitySet is a mapping of these ids to
# CapabilityInfo; it is not a wall of booleans on the adapter contract, and a
# runtime may advertise ids that this list does not know yet (forward compat).

CAP_SESSION_STREAMING = "session.streaming"
CAP_SESSION_STEER = "session.steer"
CAP_SESSION_FOLLOW_UP = "session.follow_up"
CAP_SESSION_RESUME = "session.resume"
CAP_TOOL_EVENTS = "tool.events"
CAP_USAGE_TOKENS = "usage.tokens"
CAP_USAGE_COST = "usage.cost"
CAP_APPROVAL_NATIVE = "approval.native"
CAP_CONTEXT_COMPACTION = "context.compaction"
CAP_MODEL_SWITCH = "model.switch"
CAP_AGENT_SUBAGENTS = "agent.subagents"
CAP_WORKSPACE_WORKTREE = "workspace.worktree"
CAP_VOICE_NATIVE = "voice.native"

KNOWN_CAPABILITIES: tuple[str, ...] = (
    CAP_SESSION_STREAMING,
    CAP_SESSION_STEER,
    CAP_SESSION_FOLLOW_UP,
    CAP_SESSION_RESUME,
    CAP_TOOL_EVENTS,
    CAP_USAGE_TOKENS,
    CAP_USAGE_COST,
    CAP_APPROVAL_NATIVE,
    CAP_CONTEXT_COMPACTION,
    CAP_MODEL_SWITCH,
    CAP_AGENT_SUBAGENTS,
    CAP_WORKSPACE_WORKTREE,
    CAP_VOICE_NATIVE,
)


class UnsupportedCapability(Exception):
    """A runtime does not implement what the caller asked for.

    The contract requires this to be raised (or returned as an explicit
    failure) — never replaced by a fabricated success or an empty result.
    """

    def __init__(self, capability: str, runtime: str | None = None, detail: str = "") -> None:
        self.capability = capability
        self.runtime = runtime
        self.detail = detail
        where = f" by runtime {runtime!r}" if runtime else ""
        extra = f": {detail}" if detail else ""
        super().__init__(f"capability {capability!r} is not supported{where}{extra}")


class ContractError(ValueError):
    """A payload violates the contract."""
