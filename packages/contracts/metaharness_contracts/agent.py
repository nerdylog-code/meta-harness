"""Agent identity, versions and their policies.

The agent is the durable thing (BOOK §5.1): its identity survives runtime and
model changes, and `AgentVersion` records which configuration a mission actually
ran with, so an A/B comparison is possible later (BOOK §38).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import ContractError, RiskLevel, TrustLevel
from .ids import IdKind, is_valid_id
from .workspace import WorkspacePolicy


class RuntimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred: str
    fallbacks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "RuntimePolicy":
        chain = [self.preferred, *self.fallbacks]
        if len(set(chain)) != len(chain):
            raise ContractError(f"runtime policy repeats a runtime: {chain}")
        return self

    @property
    def chain(self) -> list[str]:
        return [self.preferred, *self.fallbacks]


class ModelPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str
    escalation: str | None = None
    rationale: str | None = None


class Permission(BaseModel):
    """A named permission with a risk level. R4 cannot be auto-granted."""

    model_config = ConfigDict(extra="forbid")

    name: str
    risk: RiskLevel = RiskLevel.R0
    granted: bool = False
    scope: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _r4_needs_approval(self) -> "Permission":
        if self.risk is RiskLevel.R4 and self.granted:
            raise ContractError(
                f"permission {self.name!r} cannot be pre-granted at R4; it requires an explicit "
                "approval per action (BOOK §40/§44)"
            )
        return self


class CharacterBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack: str = "default"
    id: str = "builder"


class AgentSpec(BaseModel):
    """The identity of an agent, independent of any runtime."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    role: str
    purpose: str | None = None
    runtime_policy: RuntimePolicy
    model_policy: ModelPolicy
    skills: list[str] = Field(default_factory=list)
    memory_policy: str = "project"
    context_policy: str = "capsule-v1"
    workspace_policy: WorkspacePolicy = Field(default_factory=WorkspacePolicy)
    heartbeat_policy: str = "standard"
    permissions: list[Permission] = Field(default_factory=list)
    character: CharacterBinding = Field(default_factory=CharacterBinding)
    voice: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not is_valid_id(value, IdKind.AGENT):
            raise ContractError(f"agent id must be agt_-prefixed, got {value!r}")
        return value

    @field_validator("display_name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ContractError("display_name must not be empty")
        return value


class AgentVersion(BaseModel):
    """A frozen configuration of an agent. Immutable by construction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    version: int
    spec: AgentSpec
    created_at: float
    trust: TrustLevel = TrustLevel.USER
    notes: str | None = None

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: int) -> int:
        if value < 1:
            raise ContractError("agent version numbers start at 1")
        return value

    @model_validator(mode="after")
    def _ids_agree(self) -> "AgentVersion":
        if self.agent_id != self.spec.id:
            raise ContractError(
                f"agent_id {self.agent_id!r} does not match spec.id {self.spec.id!r}"
            )
        return self

    @property
    def ref(self) -> str:
        """`agt_…:v3` — the form missions record so a run is reproducible."""
        return f"{self.agent_id}:v{self.version}"
