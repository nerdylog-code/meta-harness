"""The canonical event envelope.

WP-003 decision 2 fixes the envelope; PROJECT_BOOK §13 fixes *which* fields and
in what set, and `tests/contracts/test_events.py` asserts that agreement so a
future edit cannot quietly widen it.

Two decisions worth stating out loud:

* **`seq` is monotonic in the canonical store.** The envelope carries it; the
  store guarantees it (ADR-0003, WP-004). Nothing here assigns it.
* **`payload` is versioned per event, with no universal payload schema.** Each
  payload must carry an integer `"v"`; everything else in it belongs to that
  event kind. A mega-schema for all payloads is exactly what the Book forbids.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import ContractError
from .ids import IdKind, is_valid_id

ENVELOPE_KEYS: tuple[str, ...] = (
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
)

#: The id-bearing envelope fields and the kind each must be.
ID_FIELDS: dict[str, IdKind] = {
    "mission_id": IdKind.MISSION,
    "task_id": IdKind.TASK,
    "run_id": IdKind.RUN,
    "agent_id": IdKind.AGENT,
    "session_id": IdKind.SESSION,
}

PAYLOAD_VERSION_KEY = "v"

# Namespaces are open (a plugin may add one), but a kind must be namespaced and
# must not be empty on either side of the dot.
KNOWN_NAMESPACES: tuple[str, ...] = (
    "system",
    "runtime",
    "mission",
    "agent",
    "task",
    "run",
    "session",
    "message",
    "tool",
    "approval",
    "workspace",
    "artifact",
    "context",
    "memory",
    "rag",
    "heartbeat",
    "usage",
    "plugin",
    "channel",
    "voice",
    "eval",
    "benchmark",
)


class CanonicalEvent(BaseModel):
    """One thing that happened, canonical and vendor-neutral.

    Nothing here branches on a vendor: an executing runtime is an id string, and
    the same envelope carries any of them. Keeping the schema free of vendor
    names is a tested property, not a style preference
    (tests/contracts/test_contract_hygiene.py).
    """

    # extra="allow": an *external* producer may attach metadata we do not know
    # yet (WP-003 decision 12). Unknown keys round-trip instead of crashing the
    # runtime, and are never interpreted here.
    model_config = ConfigDict(extra="allow")

    id: str
    seq: int
    ts: float
    kind: str
    mission_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    runtime_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "CanonicalEvent":
        if not is_valid_id(self.id, IdKind.EVENT):
            raise ContractError(f"event id must be evt_-prefixed, got {self.id!r}")
        for field, kind in ID_FIELDS.items():
            value = getattr(self, field)
            if value is not None and not is_valid_id(value, kind):
                raise ContractError(f"{field} must be a {kind.value} id, got {value!r}")
        if self.runtime_id is not None and not self.runtime_id.startswith("rt_"):
            raise ContractError(f"runtime_id must be rt_-prefixed, got {self.runtime_id!r}")
        if self.seq < 1:
            raise ContractError("seq is monotonic and starts at 1")
        if self.ts <= 0:
            raise ContractError("ts must be a positive unix timestamp")
        # The namespace is NOT validated against KNOWN_NAMESPACES on purpose:
        # a plugin may introduce `vendor.thing`, and refusing it would make the
        # envelope version-locked. The known list exists for documentation and
        # drift detection (tests/contracts/test_events.py).
        return self

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        namespace, separator, name = value.partition(".")
        if not separator or not namespace.strip() or not name.strip():
            raise ContractError(f"kind must be 'namespace.name', got {value!r}")
        if value != value.strip() or " " in value:
            raise ContractError(f"kind must not contain whitespace, got {value!r}")
        return value

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        raw = value.get(PAYLOAD_VERSION_KEY)
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
            raise ContractError(
                f"payload must declare its own version via {PAYLOAD_VERSION_KEY!r} (int >= 1); "
                "there is no universal payload schema (WP-003 decision 2)"
            )
        return value

    @property
    def namespace(self) -> str:
        return self.kind.partition(".")[0]

    @property
    def payload_version(self) -> int:
        return int(self.payload[PAYLOAD_VERSION_KEY])

    @property
    def payload_body(self) -> dict[str, Any]:
        """The payload without its version marker."""
        return {key: value for key, value in self.payload.items() if key != PAYLOAD_VERSION_KEY}

    def with_payload(self, **body: Any) -> "CanonicalEvent":
        """Replace the payload body, keeping the declared payload version."""
        return self.model_copy(update={"payload": {"v": self.payload_version, **body}})

    @classmethod
    def build(
        cls,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        event_id: str,
        seq: int,
        ts: float | None = None,
        provenance: dict[str, Any] | None = None,
        **ids: Any,
    ) -> "CanonicalEvent":
        body = dict(payload or {})
        body.setdefault(PAYLOAD_VERSION_KEY, 1)
        return cls(
            id=event_id,
            seq=seq,
            ts=ts if ts is not None else time.time(),
            kind=kind,
            payload=body,
            provenance=dict(provenance or {"method": "measured", "origin": "kernel"}),
            **ids,
        )


def envelope_keys() -> tuple[str, ...]:
    return ENVELOPE_KEYS


def is_namespaced(kind: str) -> bool:
    namespace, separator, name = kind.partition(".")
    return bool(separator and namespace and name)
