"""Runtime contracts: what a runtime is, what it can do, and how it is driven.

WP-003 decisions 3 and 4. The adapter interface is the load-bearing part of the
whole product: everything above it (missions, tasks, context, UI) talks to this
and never to a vendor SDK.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import CAP_SESSION_STREAMING, KNOWN_CAPABILITIES, UnsupportedCapability
from .events import CanonicalEvent
from .ids import IdKind, is_valid_id
from .usage import UsageSample

#: A dotted, versionable capability id. Unknown ids are allowed on purpose:
#: a newer runtime may advertise something this build has never heard of, and
#: refusing it would make every adapter version-locked.
_CAPABILITY_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"


class CapabilityInfo(BaseModel):
    """One capability, with the metadata its provider chooses to attach."""

    model_config = ConfigDict(extra="forbid")

    supported: bool
    version: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class CapabilitySet(BaseModel):
    """A runtime's declared capabilities.

    This is a mapping of capability id -> :class:`CapabilityInfo`, not a wall of
    booleans spread across the adapter contract. It says nothing about *which*
    runtime it came from, and it is the only thing the UI may consult before
    offering a feature.
    """

    model_config = ConfigDict(extra="forbid")

    capabilities: dict[str, CapabilityInfo] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def _validate_keys(cls, value: dict[str, CapabilityInfo]) -> dict[str, CapabilityInfo]:
        import re

        pattern = re.compile(_CAPABILITY_PATTERN)
        for key in value:
            if not pattern.match(key):
                raise ValueError(f"capability id must be dotted lowercase, got {key!r}")
        return value

    @classmethod
    def of(cls, **flags: bool) -> "CapabilitySet":
        """`CapabilitySet.of(**{"session.streaming": True})` for tests/fakes."""
        return cls(capabilities={key: CapabilityInfo(supported=bool(flag)) for key, flag in flags.items()})

    @classmethod
    def none(cls) -> "CapabilitySet":
        return cls(capabilities={})

    @classmethod
    def all_known(cls, *, version: int = 1) -> "CapabilitySet":
        return cls(
            capabilities={key: CapabilityInfo(supported=True, version=version) for key in KNOWN_CAPABILITIES}
        )

    def supports(self, capability: str) -> bool:
        info = self.capabilities.get(capability)
        return bool(info and info.supported)

    def require(self, capability: str, *, runtime: str | None = None) -> CapabilityInfo:
        if not self.supports(capability):
            raise UnsupportedCapability(capability, runtime)
        return self.capabilities[capability]

    def unsupported(self) -> list[str]:
        return sorted(key for key, info in self.capabilities.items() if not info.supported)

    def supported(self) -> list[str]:
        return sorted(key for key, info in self.capabilities.items() if info.supported)

    def merge(self, other: "CapabilitySet") -> "CapabilitySet":
        merged = dict(self.capabilities)
        for key, info in other.capabilities.items():
            merged[key] = info
        return CapabilitySet(capabilities=merged)

    def has_streaming(self) -> bool:
        return self.supports(CAP_SESSION_STREAMING)


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    provider: str | None = None
    context_limit: int | None = None
    notes: dict[str, Any] = Field(default_factory=dict)


class RuntimeInfo(BaseModel):
    """What `probe()` reports. Vendor identity is data, not a schema branch."""

    model_config = ConfigDict(extra="allow")

    runtime_id: str
    name: str
    version: str | None = None
    available: bool
    detail: str | None = None
    protocol: str | None = None
    capabilities: CapabilitySet = Field(default_factory=CapabilitySet.none)

    @field_validator("runtime_id")
    @classmethod
    def _validate_runtime_id(cls, value: str) -> str:
        if not value.startswith("rt_") or not is_valid_id(value, IdKind.RUNTIME):
            raise ValueError(f"runtime_id must be an rt_ id, got {value!r}")
        return value


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = "user"
    text: str
    attachments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionSpec(BaseModel):
    """Everything a runtime needs to create a session.

    `allowed_tools` is deliberately **required**: a session created without an
    explicit tool restriction silently inherits the runtime's full tool set,
    which is how a test turn once wrote real memory (see the Phase-0 evidence
    recorded in V1_CONFLICTS §C12).
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    runtime_id: str
    workspace: str | None = None
    model: str | None = None
    allowed_tools: list[str]
    system_prompt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_tools")
    @classmethod
    def _validate_tools(cls, value: list[str]) -> list[str]:
        if value is None:
            raise ValueError(
                "allowed_tools is required: an empty list means 'no tools', an omitted list "
                "means 'inherit everything' and is refused by contract"
            )
        for tool in value:
            if not isinstance(tool, str) or not tool.strip():
                raise ValueError(f"tool names must be non-empty strings, got {tool!r}")
        return value


class RuntimeSession(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str
    runtime_id: str
    created_at: float
    detail: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class RuntimeAdapter(Protocol):
    """The stable async interface (WP-003 decision 3).

    A capability that is absent raises :class:`UnsupportedCapability` — never a
    fabricated success, an empty list that looks like "nothing happened", or a
    silently ignored call.
    """

    runtime_id: str

    async def probe(self) -> RuntimeInfo: ...

    async def capabilities(self) -> CapabilitySet: ...

    async def models(self) -> list[ModelInfo]: ...

    async def create_session(
        self, spec: SessionSpec, environment: Any | None = None
    ) -> RuntimeSession:
        """`environment` is an `ExecutionEnvironment` plan (M3, ADR-0019).

        The adapter asks the environment for how to run -- argv prefix, working directory, HOME --
        and never learns what a container or a mount namespace is. Optional on purpose: an adapter
        with no environment behaves exactly as it did before M3.
        """
        ...

    async def send(self, session_id: str, message: Message) -> None: ...

    async def steer(self, session_id: str, instruction: str) -> None: ...

    async def follow_up(self, session_id: str, message: Message) -> None: ...

    def events(self, session_id: str) -> AsyncIterator[CanonicalEvent]: ...

    async def interrupt(self, session_id: str) -> None: ...

    async def cancel(self, session_id: str) -> None: ...

    async def usage(self, session_id: str) -> UsageSample: ...

    async def artifacts(self, session_id: str) -> list[str]: ...

    async def close(self, session_id: str) -> None: ...


ADAPTER_METHODS: tuple[str, ...] = (
    "probe",
    "capabilities",
    "models",
    "create_session",
    "send",
    "steer",
    "follow_up",
    "events",
    "interrupt",
    "cancel",
    "usage",
    "artifacts",
    "close",
)
