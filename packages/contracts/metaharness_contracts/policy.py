"""Execution policy: what was asked, what is in force, and the evidence for both (M3).

WP-003 decision 7 separated *intent* from *enforcement* for a workspace. M3 needs the same
separation for the whole execution: the budgets, the sandbox, the network, and the strength of each
one. They are three objects on purpose, because conflating them is how a product ends up claiming
safety it does not have:

* :class:`RequestedPolicy` — what the caller asked for. A wish until something enforces it.
* :class:`EffectivePolicy` — what is actually in force, and by which mechanism.
* :class:`EnforcementEvidence` — the strength per dimension, plus the checks that were run.

The rule the types enforce: a budget that was reached must say what the control plane *did*, and a
soft limit (one a provider only reports after the fact) must be recorded as ``weak``. A soft limit
labelled hard is a trap, so the model refuses to record one without an action.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import ContractError, Enforcement, Provenance
from .workspace import WorkspacePolicy


class BudgetKind(str, Enum):
    """The five budgets. Each is enforced differently, and the difference is recorded."""

    WALL_TIME = "wall_time"
    TOOL_CALLS = "tool_calls"
    TOKENS = "tokens"
    COST = "cost"
    CHILD_PROCESSES = "child_processes"


#: How a budget is enforced, in the vocabulary the engine uses. `none` is a legal value: a budget
#: that is only observed is a budget that is not enforced, and saying so is the point.
ENFORCEMENT_MODES: tuple[str, ...] = (
    "process_supervisor",  # the supervisor owns the clock and kills the tree: strong
    "event_counter",  # counted from real events, then cancelled: moderate (a call in flight finishes)
    "process_tree_watch",  # descendants counted with psutil, then killed: moderate
    "provider_reported",  # the provider tells us afterwards: weak, and recorded as weak
    "none",
)

#: Which enforcement level each mode can honestly claim.
MODE_CEILING: dict[str, Enforcement] = {
    "process_supervisor": Enforcement.STRONG,
    "event_counter": Enforcement.MODERATE,
    "process_tree_watch": Enforcement.MODERATE,
    "provider_reported": Enforcement.WEAK,
    "none": Enforcement.WEAK,
}


class BudgetRequest(BaseModel):
    """What was asked for. `limit=None` means "no limit requested", which is not the same as zero."""

    model_config = ConfigDict(extra="forbid")

    kind: BudgetKind
    limit: float | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _validate_limit(self) -> "BudgetRequest":
        if self.limit is not None and self.limit < 0:
            raise ContractError(f"{self.kind.value} limit cannot be negative: {self.limit}")
        return self


class BudgetRecord(BaseModel):
    """One budget, with what was asked, what was seen, and how strongly it is enforced."""

    model_config = ConfigDict(extra="forbid")

    kind: BudgetKind
    requested: float | None = None
    observed: float | None = None
    enforcement_mode: str = "none"
    enforcement: Enforcement = Enforcement.WEAK
    limit_reached: bool = False
    #: What the control plane did when the limit was reached. Required once `limit_reached`.
    action: str | None = None
    provenance: Provenance = Provenance.UNKNOWN
    note: str | None = None

    @model_validator(mode="after")
    def _validate_honesty(self) -> "BudgetRecord":
        if self.enforcement_mode not in ENFORCEMENT_MODES:
            raise ContractError(
                f"unknown enforcement mode {self.enforcement_mode!r}; one of {ENFORCEMENT_MODES}"
            )
        ceiling = MODE_CEILING[self.enforcement_mode]
        order = {Enforcement.WEAK: 0, Enforcement.MODERATE: 1, Enforcement.STRONG: 2}
        if order[self.enforcement] > order[ceiling]:
            raise ContractError(
                f"{self.kind.value}: mode {self.enforcement_mode!r} cannot claim "
                f"{self.enforcement.value!r} (ceiling {ceiling.value!r}) -- do not label a soft "
                "limit as hard"
            )
        if self.limit_reached and not self.action:
            raise ContractError(
                f"{self.kind.value} reached its limit but does not say what was done about it"
            )
        return self

    @property
    def ratio(self) -> float | None:
        """observed/requested, for a progress display. None when either side is unknown."""
        if self.requested in (None, 0) or self.observed is None:
            return None
        return float(self.observed) / float(self.requested)


class RequestedPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str | None = None
    #: `auto` lets the daemon pick the strongest provider it can actually use.
    sandbox: str = "auto"
    network: str = "deny"
    workspace_policy: WorkspacePolicy | None = None
    budgets: list[BudgetRequest] = Field(default_factory=list)

    def limit_of(self, kind: BudgetKind) -> float | None:
        for budget in self.budgets:
            if budget.kind == kind:
                return budget.limit
        return None


class EffectivePolicy(BaseModel):
    """What is actually in force. Written by the daemon after the environment is prepared."""

    model_config = ConfigDict(extra="forbid")

    workspace: str
    sandbox_provider: str | None = None
    #: `container_mount` | `namespace_bind` | `cwd_only` -- how the workspace is really confined.
    filesystem_mode: str
    network_mode: str
    workspace_policy: WorkspacePolicy | None = None
    budgets: list[BudgetRecord] = Field(default_factory=list)

    def budget(self, kind: BudgetKind) -> BudgetRecord | None:
        for record in self.budgets:
            if record.kind == kind:
                return record
        return None


class EvidenceCheck(BaseModel):
    """One check that was actually run, and its outcome. The evidence behind a `strong` claim."""

    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    detail: str | None = None


class EnforcementEvidence(BaseModel):
    """The strength of every dimension, plus the checks that justify it."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    filesystem: Enforcement = Enforcement.WEAK
    network: Enforcement = Enforcement.WEAK
    wall_time: Enforcement = Enforcement.WEAK
    tool_calls: Enforcement = Enforcement.WEAK
    tokens: Enforcement = Enforcement.WEAK
    cost: Enforcement = Enforcement.WEAK
    child_processes: Enforcement = Enforcement.WEAK
    checks: list[EvidenceCheck] = Field(default_factory=list)
    note: str | None = None

    def dimension(self, name: str) -> Enforcement:
        return getattr(self, name, Enforcement.WEAK)

    @property
    def isolation(self) -> Enforcement:
        """The weaker of filesystem and network: containment is only as strong as its weak side."""
        order = {Enforcement.WEAK: 0, Enforcement.MODERATE: 1, Enforcement.STRONG: 2}
        weakest = min((self.filesystem, self.network), key=lambda level: order[level])
        return weakest

    def as_badges(self) -> dict[str, str]:
        """What the UI renders. `NOT ENFORCED` is a first-class outcome, not an error state."""
        return {
            "filesystem": self.filesystem.value,
            "network": self.network.value,
            "wall_time": self.wall_time.value,
            "tool_calls": self.tool_calls.value,
            "tokens": self.tokens.value,
            "cost": self.cost.value,
            "child_processes": self.child_processes.value,
            "isolation": self.isolation.value,
        }


class ExecutionPolicy(BaseModel):
    """The three objects together, as one record: what was asked, what is in force, what proves it."""

    model_config = ConfigDict(extra="forbid")

    requested: RequestedPolicy
    effective: EffectivePolicy | None = None
    evidence: EnforcementEvidence | None = None

    @property
    def isolation(self) -> Enforcement:
        return self.evidence.isolation if self.evidence else Enforcement.WEAK

    def summary(self) -> dict[str, Any]:
        return {
            "requested": self.requested.model_dump(mode="json"),
            "effective": self.effective.model_dump(mode="json") if self.effective else None,
            "evidence": self.evidence.model_dump(mode="json") if self.evidence else None,
            "isolation": self.isolation.value,
        }
