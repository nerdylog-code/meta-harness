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
