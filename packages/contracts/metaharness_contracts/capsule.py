"""The Context Capsule — an operational handoff, not a memory.

WP-003 decision 6. The capsule is what lets an agent cross a compaction or a
runtime migration (BOOK §21/§22, M2). Two properties are enforced rather than
promised:

* **Structured, not prose.** Every field has a type, and references use exact
  ids (`task_id`, `artifact_id`, file paths) so a verifier can check them
  against the store instead of guessing.
* **Not a transcript.** Raw history lives in the archive; a capsule that grew
  into a conversation dump would defeat its own purpose, so a size ceiling is
  part of the contract.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import ContractError
from .ids import IdKind, is_valid_id

#: A capsule is a summary. 64 KB is generous for structure and far too small
#: for a transcript, which is exactly the point.
MAX_CAPSULE_BYTES = 64 * 1024
MAX_RESUME_INSTRUCTION_CHARS = 4000
#: Field names that would mean "someone dumped the conversation in here".
FORBIDDEN_FIELD_NAMES = ("transcript", "messages", "history", "raw_log", "conversation", "chat_log")


class CapsulePhase(str, Enum):
    """BOOK §21 pressure phases."""

    NORMAL = "NORMAL"
    NOTICE = "NOTICE"
    PREPARE = "PREPARE"
    FORCED = "FORCED"
    COMPACTING = "COMPACTING"
    RESUMED = "RESUMED"


class CompletedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    result: str
    proof: list[str] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def _validate_task(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_id(value, IdKind.TASK):
            raise ContractError(f"task_id must be a tsk_ id, got {value!r}")
        return value


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    exact_action: str
    depends_on: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    choice: str
    rationale: str
    alternatives: list[str] = Field(default_factory=list)


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    blocking: bool = False


class Blocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    kind: str = "unknown"
    needs: str | None = None


class ActiveFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    reason: str
    state: str = "unknown"


class TestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    last_result: str
    passed: bool | None = None


class ArtifactRefLite(BaseModel):
    """A pointer to an artifact, by exact id. Never the artifact's content."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    purpose: str

    @field_validator("artifact_id")
    @classmethod
    def _validate_artifact(cls, value: str) -> str:
        if not is_valid_id(value, IdKind.ARTIFACT):
            raise ContractError(f"artifact_id must be an art_ id, got {value!r}")
        return value


class SelfAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confidence: float | None = None
    concerns: list[str] = Field(default_factory=list)
    not_verified: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 <= value <= 1.0:
            raise ContractError(f"confidence is a ratio in [0,1], got {value}")
        return value


class ContextCapsule(BaseModel):
    """The 16 required fields of WP-003 decision 6 — no more, no fewer."""

    model_config = ConfigDict(extra="forbid")

    objective: str
    current_phase: CapsulePhase
    completed: list[CompletedItem] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    active_files: list[ActiveFile] = Field(default_factory=list)
    artifacts: list[ArtifactRefLite] = Field(default_factory=list)
    tests: list[TestRecord] = Field(default_factory=list)
    memory_candidates: list[str] = Field(default_factory=list)
    workspace_state: dict[str, Any] = Field(default_factory=dict)
    resume_instruction: str
    self_assessment: SelfAssessment = Field(default_factory=SelfAssessment)

    @field_validator("objective")
    @classmethod
    def _validate_objective(cls, value: str) -> str:
        if not value.strip():
            raise ContractError("objective must not be empty")
        return value

    @field_validator("resume_instruction")
    @classmethod
    def _validate_resume(cls, value: str) -> str:
        if not value.strip():
            raise ContractError("resume_instruction must not be empty")
        if len(value) > MAX_RESUME_INSTRUCTION_CHARS:
            raise ContractError(
                f"resume_instruction exceeds {MAX_RESUME_INSTRUCTION_CHARS} chars: a capsule is a "
                "handoff, not a transcript"
            )
        return value

    @model_validator(mode="after")
    def _validate_capsule(self) -> "ContextCapsule":
        serialized = self.model_dump_json()
        if len(serialized.encode("utf-8")) > MAX_CAPSULE_BYTES:
            raise ContractError(
                f"capsule exceeds {MAX_CAPSULE_BYTES} bytes: raw history belongs in the archive, "
                "not in the handoff"
            )
        for item in self.completed:
            if item.task_id and any(action.task_id == item.task_id for action in self.next_actions):
                raise ContractError(
                    f"task {item.task_id} appears as both completed and a next action"
                )
        return self

    def referenced_ids(self) -> list[str]:
        ids = [item.task_id for item in self.completed if item.task_id]
        ids += [action.task_id for action in self.next_actions if action.task_id]
        ids += [artifact.artifact_id for artifact in self.artifacts]
        return ids


def assert_no_transcript_field() -> None:
    """Guard used by the test suite: the capsule has no dump-a-conversation field."""
    fields = set(ContextCapsule.model_fields)
    offenders = fields & set(FORBIDDEN_FIELD_NAMES)
    if offenders:
        raise ContractError(f"ContextCapsule must not carry raw conversation fields: {sorted(offenders)}")
