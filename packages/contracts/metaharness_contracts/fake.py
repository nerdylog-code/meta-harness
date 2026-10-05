"""A fake runtime adapter, for conformance and for the tests above us.

WP-003 decision 14: the contract is only real if something can satisfy it without
a paid API. This adapter is deterministic, offline and scriptable, and it is what
`run_conformance()` is demonstrated against — as well as what later work packages
use to exercise the kernel before a real runtime is attached.
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator

from .enums import (
    CAP_CONTEXT_COMPACTION,
    CAP_SESSION_FOLLOW_UP,
    CAP_SESSION_STEER,
    CAP_SESSION_STREAMING,
    CAP_TOOL_EVENTS,
    CAP_USAGE_TOKENS,
    Provenance,
    UnsupportedCapability,
)
from .events import CanonicalEvent
from .ids import IdKind, new_id
from .runtime import (
    CapabilityInfo,
    CapabilitySet,
    Message,
    ModelInfo,
    RuntimeInfo,
    RuntimeSession,
    SessionSpec,
)
from .usage import Metric, UsageSample

FAKE_RUNTIME_ID = "rt_fake00000000000000"
#: Alias used by tests and by the conformance suite.
DEFAULT_FAKE_RUNTIME_ID = FAKE_RUNTIME_ID
DEFAULT_SUPPORTED = (
    CAP_SESSION_STREAMING,
    CAP_TOOL_EVENTS,
    CAP_USAGE_TOKENS,
    CAP_SESSION_FOLLOW_UP,
)
#: Deliberately NOT supported, so conformance can prove the refusal path is real.
DEFAULT_UNSUPPORTED = (CAP_SESSION_STEER, CAP_CONTEXT_COMPACTION)


class FakeRuntimeAdapter:
    """Implements the `RuntimeAdapter` protocol without any external process."""

    def __init__(
        self,
        *,
        runtime_id: str = FAKE_RUNTIME_ID,
        capabilities: CapabilitySet | None = None,
        available: bool = True,
        usage_sample: UsageSample | None = None,
    ) -> None:
        self.runtime_id = runtime_id
        self.available = available
        self._capabilities = capabilities or CapabilitySet(
            capabilities={
                **{cap: CapabilityInfo(supported=True) for cap in DEFAULT_SUPPORTED},
                **{cap: CapabilityInfo(supported=False, note="fake refuses this on purpose") for cap in DEFAULT_UNSUPPORTED},
            }
        )
        self._usage_sample = usage_sample
        self._sessions: dict[str, dict[str, Any]] = {}
        self._streams: dict[str, list[CanonicalEvent]] = {}
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # -- protocol ----------------------------------------------------------
    async def probe(self) -> RuntimeInfo:
        self._record("probe")
        return RuntimeInfo(
            runtime_id=self.runtime_id,
            name="fake",
            version="0.0.1",
            available=self.available,
            detail="deterministic in-process adapter (no subprocess, no network)",
            protocol="in-process",
            capabilities=self._capabilities,
        )

    async def capabilities(self) -> CapabilitySet:
        self._record("capabilities")
        return self._capabilities

    async def models(self) -> list[ModelInfo]:
        self._record("models")
        return [ModelInfo(id="fake-model-1", provider="fake", context_limit=128000)]

    async def create_session(self, spec: SessionSpec) -> RuntimeSession:
        self._record("create_session", {"agent_id": spec.agent_id, "tools": spec.allowed_tools})
        session_id = new_id(IdKind.SESSION)
        self._sessions[session_id] = {"spec": spec, "messages": [], "cancelled": False, "sequence": 0}
        self._streams[session_id] = []
        self._emit(
            session_id,
            "session.created",
            {"runtime": self.runtime_id, "model": spec.model, "allowed_tools": len(spec.allowed_tools)},
        )
        return RuntimeSession(
            session_id=session_id,
            runtime_id=self.runtime_id,
            created_at=time.time(),
            detail={"workspace": spec.workspace},
        )

    async def send(self, session_id: str, message: Message) -> None:
        self._require_session(session_id)
        self._record("send", {"session_id": session_id, "chars": len(message.text)})
        self._sessions[session_id]["messages"].append(message)
        self._emit(session_id, "message.received", {"role": message.role, "chars": len(message.text)})
        self._emit(session_id, "message.completed", {"text": f"echo:{message.text}"})

    async def steer(self, session_id: str, instruction: str) -> None:
        self._require_session(session_id)
        self._capabilities.require(CAP_SESSION_STEER, runtime=self.runtime_id)
        self._record("steer", {"chars": len(instruction)})
        self._emit(session_id, "session.steered", {"chars": len(instruction)})

    async def follow_up(self, session_id: str, message: Message) -> None:
        self._require_session(session_id)
        self._capabilities.require(CAP_SESSION_FOLLOW_UP, runtime=self.runtime_id)
        self._record("follow_up")
        await self.send(session_id, message)

    async def events(self, session_id: str) -> AsyncIterator[CanonicalEvent]:
        self._require_session(session_id)
        self.calls.append(("events", {"session_id": session_id}))
        for event in list(self._streams.get(session_id, [])):
            yield event

    async def interrupt(self, session_id: str) -> None:
        self._require_session(session_id)
        self._record("interrupt")
        self._emit(session_id, "session.interrupted", {})

    async def cancel(self, session_id: str) -> None:
        self._require_session(session_id)
        self._record("cancel")
        self._sessions[session_id]["cancelled"] = True
        self._emit(session_id, "run.cancelled", {"reason": "requested"})

    async def usage(self, session_id: str) -> UsageSample:
        self._require_session(session_id)
        self._record("usage")
        if self._usage_sample is not None:
            return self._usage_sample
        return UsageSample(
            input_tokens=Metric.reported(1200, provenance=Provenance.PROVIDER_REPORTED),
            output_tokens=Metric.reported(340, provenance=Provenance.PROVIDER_REPORTED),
            cache_read_tokens=Metric.reported(800, provenance=Provenance.PROVIDER_REPORTED),
            duration_ms=Metric.measured(1200.0),
            provider_cost=Metric.unknown(unit="usd", note="the fake has no billing"),
            runtime="fake",
            model="fake-model-1",
            session=session_id,
        )

    async def artifacts(self, session_id: str) -> list[str]:
        self._require_session(session_id)
        self._record("artifacts")
        return [new_id(IdKind.ARTIFACT, salt="fakesession0001")]

    async def close(self, session_id: str) -> None:
        self._require_session(session_id)
        self._record("close")
        self._emit(session_id, "session.closed", {})
        self._sessions.pop(session_id, None)
        self._streams.pop(session_id, None)

    # -- test helpers ------------------------------------------------------
    def _record(self, method: str, payload: dict[str, Any] | None = None) -> None:
        self.calls.append((method, payload or {}))

    def _require_session(self, session_id: str) -> None:
        if session_id not in self._sessions:
            raise KeyError(f"unknown session {session_id!r}")

    def _emit(self, session_id: str, kind: str, body: dict[str, Any]) -> None:
        stream = self._streams.setdefault(session_id, [])
        spec = self._sessions[session_id]["spec"]
        sequence = len(stream) + 1
        stream.append(
            CanonicalEvent.build(
                kind,
                body,
                event_id=new_id(IdKind.EVENT),
                seq=sequence,
                agent_id=spec.agent_id,
                runtime_id=self.runtime_id,
                session_id=session_id,
                provenance={"method": "measured", "origin": "fake-runtime"},
            )
        )
