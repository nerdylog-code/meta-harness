"""Mission, task and artifact contracts.

A mission is the largest durable unit (BOOK §5.2); a task is the unit of work a
runtime actually executes, with its own budgets and acceptance gate.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import ContractError, TERMINAL_TASK_STATES, TaskState
from .ids import IdKind, is_valid_id
from .workspace import WorkspacePolicy


class Budget(BaseModel):
    """A budget is a limit, not a hope (BOOK §12)."""

    model_config = ConfigDict(extra="forbid")

    tokens: int | None = None
    money: float | None = None
    wall_time_s: float | None = None
    attempts: int | None = None

    @field_validator("tokens", "attempts")
    @classmethod
    def _positive_int(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ContractError(f"budget values must be positive, got {value}")
        return value

    @field_validator("money", "wall_time_s")
    @classmethod
    def _positive_float(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ContractError(f"budget values must be positive, got {value}")
        return value

    @property
    def is_bounded(self) -> bool:
        return any(value is not None for value in (self.tokens, self.money, self.wall_time_s, self.attempts))


class AcceptanceGate(BaseModel):
    """How a task proves it is done. A model's sentence is not a gate."""

    model_config = ConfigDict(extra="forbid")

    command: str | None = None
    criteria: list[str] = Field(default_factory=list)
    requires_artifact: bool = False

    @model_validator(mode="after")
    def _gate_must_be_checkable(self) -> "AcceptanceGate":
        if not self.command and not self.criteria:
            raise ContractError(
                "an acceptance gate needs a command or explicit criteria; 'the model said it is "
                "done' is not a gate (BOOK §3.15/§81)"
            )
        return self


class MissionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    objective: str
    created_at: float
    owner: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not is_valid_id(value, IdKind.MISSION):
            raise ContractError(f"mission id must be mis_-prefixed, got {value!r}")
        return value

    @field_validator("title", "objective")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ContractError("title and objective must not be empty")
        return value


class TaskSpec(BaseModel):
    """A unit of work, per BOOK §7."""

    model_config = ConfigDict(extra="forbid")

    id: str
    mission_id: str
    title: str
    description: str = ""
    owner_agent: str | None = None
    state: TaskState = TaskState.DRAFT
    dependencies: list[str] = Field(default_factory=list)
    workspace_scope: str | None = None
    workspace_policy: WorkspacePolicy = Field(default_factory=WorkspacePolicy)
    acceptance_gate: AcceptanceGate
    budget: Budget = Field(default_factory=Budget)
    deadline: float | None = None
    artifacts: list[str] = Field(default_factory=list)
    proof: list[str] = Field(default_factory=list)
    retries: int = 0
    parent_id: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not is_valid_id(value, IdKind.TASK):
            raise ContractError(f"task id must be tsk_-prefixed, got {value!r}")
        return value

    @field_validator("mission_id")
    @classmethod
    def _validate_mission(cls, value: str) -> str:
        if not is_valid_id(value, IdKind.MISSION):
            raise ContractError(f"mission_id must be mis_-prefixed, got {value!r}")
        return value

    @field_validator("dependencies")
    @classmethod
    def _validate_dependencies(cls, values: list[str]) -> list[str]:
        for value in values:
            if not is_valid_id(value, IdKind.TASK):
                raise ContractError(f"dependency must be a tsk_ id, got {value!r}")
        return values

    @model_validator(mode="after")
    def _validate_task(self) -> "TaskSpec":
        if self.id in self.dependencies:
            raise ContractError("a task cannot depend on itself")
        if self.state in TERMINAL_TASK_STATES and not self.proof and self.acceptance_gate.requires_artifact:
            raise ContractError(
                f"task {self.id} is {self.state.value} and its gate requires an artifact, but no "
                "proof was recorded"
            )
        return self

    @property
    def blocked(self) -> bool:
        return bool(self.dependencies) and self.state is TaskState.BLOCKED


class ArtifactRef(BaseModel):
    """A durable payload. The bytes live on the filesystem, never in the DB."""

    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    sha256: str
    mime: str
    size: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    origin: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not is_valid_id(value, IdKind.ARTIFACT):
            raise ContractError(f"artifact id must be art_-prefixed, got {value!r}")
        return value

    @field_validator("sha256")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ContractError(f"sha256 must be 64 lowercase hex chars, got {value!r}")
        return value

    @field_validator("size")
    @classmethod
    def _validate_size(cls, value: int) -> int:
        if value < 0:
            raise ContractError("size must not be negative")
        return value

    @field_validator("path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        if not value.strip():
            raise ContractError("path must not be empty")
        # Normalized on construction so Windows and Linux produce the same
        # logical path; the raw form is preserved in metadata by the producer.
        return value.replace("\\", "/")
