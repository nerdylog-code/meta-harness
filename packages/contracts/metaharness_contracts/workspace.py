"""Workspace policy — intent separated from enforcement.

WP-003 decision 7. The policy states what the agent *wants* (globs to include,
write, read-only, deny; network and secret rules), and the runtime/provider
reports the enforcement it actually **reached**. The two are never conflated:
an agent running in a git worktree does not get to call that a sandbox.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from enum import Enum

from .enums import ContractError, Enforcement

NETWORK_MODES: tuple[str, ...] = ("deny", "allowlist", "unrestricted")


class NetworkPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "allowlist"
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        if value not in NETWORK_MODES:
            raise ContractError(f"network mode must be one of {NETWORK_MODES}, got {value!r}")
        return value


class SecretPolicy(BaseModel):
    """Which secret scopes a task may resolve, by *name*, never by value.

    A policy refers to secrets; it never carries one. Values are resolved by the
    secrets broker at use time (BOOK §45).
    """

    model_config = ConfigDict(extra="forbid")

    allowed: list[str] = Field(default_factory=list)
    denied: list[str] = Field(default_factory=list)
    inject_as_env: bool = True


class WorkspacePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)
    readonly: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    secrets: SecretPolicy = Field(default_factory=SecretPolicy)

    #: What the provider actually achieves. Never set by the policy author.
    enforcement: Enforcement | None = None
    enforcement_detail: str | None = None

    @model_validator(mode="after")
    def _validate_consistency(self) -> "WorkspacePolicy":
        overlap = set(self.write) & set(self.readonly)
        if overlap:
            raise ContractError(f"paths cannot be both writable and read-only: {sorted(overlap)}")
        both = set(self.write) & set(self.deny)
        if both:
            raise ContractError(f"paths cannot be both writable and denied: {sorted(both)}")
        return self

    def allows_write(self, path: str) -> bool:
        if self._matches(path, self.deny):
            return False
        if self._matches(path, self.readonly):
            return False
        if self.write:
            return self._matches(path, self.write)
        return self._matches(path, self.include) if self.include else True

    def allows_read(self, path: str) -> bool:
        if self._matches(path, self.deny):
            return False
        if not self.include:
            return True
        return self._matches(path, self.include) or self._matches(path, self.readonly)

    @staticmethod
    def _matches(path: str, patterns: list[str]) -> bool:
        candidate = path.replace("\\", "/")
        return any(fnmatch.fnmatch(candidate, pattern) for pattern in patterns)

    def with_enforcement(self, enforcement: Enforcement, *, detail: str | None = None) -> "WorkspacePolicy":
        """Attach what the provider really achieved."""
        return self.model_copy(update={"enforcement": enforcement, "enforcement_detail": detail})

    @property
    def declares_isolation(self) -> bool:
        return self.enforcement in {Enforcement.MODERATE, Enforcement.STRONG}


DEFAULT_ISOLATING_POLICY = WorkspacePolicy(
    include=["src/**"],
    write=["src/**"],
    readonly=["packages/**"],
    deny=[".env", "secrets/**", "infra/**"],
    network=NetworkPolicy(mode="allowlist", allow=["127.0.0.1"]),
)


def enforcement_is_honest(policy: WorkspacePolicy) -> dict[str, Any]:
    """Diagnostic used by the UI and by tests: is the claim justified?

    A worktree is not a security boundary (BOOK §26), so a policy that denies
    paths but declares `strong` without a container must be flagged rather than
    displayed as protected.
    """
    return {
        "requested_denies": len(policy.deny),
        "enforcement": policy.enforcement.value if policy.enforcement else None,
        "declared_by_policy_author": False,
        "detail": policy.enforcement_detail,
    }


# --------------------------------------------------------------------------------- allocations
#
# A worktree is concurrency and write isolation, not a security boundary (BOOK §26). Nothing here
# may be read as "the host filesystem is contained": that is the sandbox's dimension (S1), measured
# separately. The two are composed, never conflated:
#
#     Task -> Worktree -> Sandbox -> Runtime
#
# Identity is `(task_id, repository)`. No new public id prefix is introduced for an allocation or a
# lease: the existing namespace is frozen, and a lease is identified by its allocation plus a
# monotonically increasing generation.

LEASE_TTL_S = 300.0

#: How strongly a worktree isolates *concurrent repository writes*. It is never stronger than this,
#: and it says nothing about what the runtime can read elsewhere.
WORKTREE_ENFORCEMENT: Enforcement = Enforcement.MODERATE


class WorkspaceState(str, Enum):
    ALLOCATING = "allocating"
    READY = "ready"
    MISSING = "missing"  # the record says it exists; the directory does not
    CONFLICT = "conflict"  # something else is where the worktree should be
    REMOVED = "removed"
    FAILED = "failed"


class LeaseState(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class RepositoryIdentity(BaseModel):
    """Which repository an allocation belongs to. Answered by Git, never by the caller.

    `rev-parse --show-toplevel` is the answer to "is this a repository", and `--git-common-dir` is
    the answer to "which one" -- two worktrees of the same repository share a common dir, and two
    clones of the same URL do not.
    """

    model_config = ConfigDict(extra="forbid")

    root: str
    common_dir: str
    head: str | None = None


class WorkspaceAllocation(BaseModel):
    """One task's isolated working copy of one repository."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    mission_id: str | None = None
    repository: RepositoryIdentity
    base_ref: str
    base_commit: str
    branch: str
    #: Stable and platform-neutral, POSIX separators everywhere: `workspaces/<task_id>`.
    locator: str
    #: Where it really is. Internal: the daemon knows it, a client never receives it.
    host_path: str
    created_at: float
    state: WorkspaceState = WorkspaceState.READY
    #: `None` means "not measured", which is not the same as clean.
    dirty: bool | None = None

    @field_validator("locator")
    @classmethod
    def _wire_locator(cls, value: str) -> str:
        if "\\" in value or value.startswith("/"):
            raise ContractError(
                f"the wire locator is relative and POSIX on every platform, got {value!r}"
            )
        return value

    @field_validator("task_id")
    @classmethod
    def _validate_task(cls, value: str) -> str:
        from .ids import IdKind, is_valid_id

        if not is_valid_id(value, IdKind.TASK):
            raise ContractError(f"an allocation belongs to a tsk_ task, got {value!r}")
        return value

    @property
    def identity(self) -> tuple[str, str]:
        """`(task_id, repository common dir)` -- the allocation's canonical identity."""
        return (self.task_id, self.repository.common_dir)


class WriterLease(BaseModel):
    """Who may write to one allocation. One workspace, at most one active writer."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    run_id: str
    generation: int
    acquired_at: float
    heartbeat_at: float
    expires_at: float
    state: LeaseState = LeaseState.ACTIVE

    def is_expired(self, *, now: float | None = None) -> bool:
        import time

        return (now if now is not None else time.time()) > self.expires_at

    def is_active(self, *, now: float | None = None) -> bool:
        return self.state is LeaseState.ACTIVE and not self.is_expired(now=now)


def acquire_verdict(
    lease: WriterLease | None, *, run_id: str, now: float | None = None
) -> tuple[str, str]:
    """Can this run become the writer? Returns ``(verdict, reason)``.

    ``grant`` -- nobody holds a live lease; ``renew`` -- this run already does; ``refuse`` -- another
    run does and its lease has not expired. Expiry is what makes a lease bounded rather than an
    immortal lock.
    """
    if lease is None or lease.state is not LeaseState.ACTIVE:
        return "grant", "no active writer"
    if lease.is_expired(now=now):
        return "grant", f"the lease held by {lease.run_id} expired at {lease.expires_at}"
    if lease.run_id == run_id:
        return "renew", "this run is already the writer"
    return (
        "refuse",
        f"run {lease.run_id} holds generation {lease.generation} until {lease.expires_at}",
    )


def release_verdict(
    lease: WriterLease | None, *, run_id: str, generation: int, now: float | None = None
) -> tuple[bool, str]:
    """May this run release the lease? A stale holder may not release a newer lease."""
    if lease is None:
        return False, "there is no lease to release"
    if lease.generation != generation:
        return (
            False,
            f"generation {generation} is not current (the lease is at generation {lease.generation})",
        )
    if lease.run_id != run_id:
        return False, f"only the writer ({lease.run_id}) may release this lease"
    if lease.state is not LeaseState.ACTIVE:
        return False, f"the lease is already {lease.state.value}"
    return True, ""


def enforcement_summary(
    allocation: WorkspaceAllocation | None, *, sandbox_filesystem: str | None = None
) -> dict[str, Any]:
    """The two dimensions, stated separately, because they are separate.

    A worktree gives *moderate* isolation of concurrent repository writes and nothing else. Host
    filesystem isolation comes from the sandbox (S1) and is reported as measured, or as unknown --
    never upgraded by the existence of a worktree.
    """
    return {
        "write_isolation": WORKTREE_ENFORCEMENT.value if allocation else "weak",
        "write_isolation_scope": "concurrent repository mutation",
        "filesystem_isolation": sandbox_filesystem or "unknown",
        "filesystem_isolation_scope": "what the runtime can read and reach",
        "note": (
            "a worktree is not a sandbox: it isolates concurrent writes to one repository, not what "
            "the runtime can see (BOOK §26)"
        ),
    }
