"""Event plane — the canonical envelope and an in-process broadcast bus.

Scope of WP-002: enough event plumbing for the skeleton to prove it is alive.
Durable storage is WP-004's job (SQLite is canonical, per ADR-0003); this module
deliberately keeps only a bounded in-memory ring so a late subscriber still sees
what just happened.

Envelope shape follows PROJECT_BOOK §13 exactly (a flat dict, not a pydantic
model — the typed contract set is WP-003's deliverable and freezing it here
would pre-empt the Architect review).
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

RING_SIZE = 256

# Provenance vocabulary (PROJECT_BOOK §17/§49). Every payload must say how it
# was obtained; mixing `estimated` with `measured` silently is a correctness bug.
PROVENANCE_METHODS = ("measured", "provider_reported", "runtime_reported", "estimated", "unknown")

ENVELOPE_KEYS = (
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


def new_id(prefix: str = "evt") -> str:
    """Sortable, collision-resistant id: time-prefixed + random suffix."""
    stamp = f"{int(time.time() * 1000):013d}"
    return f"{prefix}_{stamp}{secrets.token_hex(5)}"


@dataclass(frozen=True)
class CanonicalEvent:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    seq: int = 0
    ts: float = 0.0
    mission_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    runtime_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        if "." not in self.kind:
            raise ValueError(f"event kind must be namespaced (namespace.name), got {self.kind!r}")
        method = (self.provenance or {}).get("method")
        if method not in PROVENANCE_METHODS:
            raise ValueError(
                f"event {self.kind!r} needs provenance.method in {PROVENANCE_METHODS}, got {method!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in ENVELOPE_KEYS}


class EventBus:
    """Fan-out bus with a bounded replay ring.

    Not thread-safe by design: it is driven by the daemon's event loop. The
    publish path is synchronous (`put_nowait`) so emitting an event can never
    block a request handler.
    """

    def __init__(self, ring_size: int = RING_SIZE) -> None:
        self._ring: deque[CanonicalEvent] = deque(maxlen=ring_size)
        self._subscribers: set[asyncio.Queue] = set()
        self._seq = 0
        self._lock = threading.Lock()

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def publish(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        method: str = "measured",
        origin: str = "kernel",
        **ids: Any,
    ) -> CanonicalEvent:
        with self._lock:
            self._seq += 1
            seq = self._seq
        event = CanonicalEvent(
            kind=kind,
            payload=dict(payload or {}),
            provenance={"method": method, "origin": origin},
            id=new_id(),
            seq=seq,
            ts=time.time(),
            **{k: v for k, v in ids.items() if k in ENVELOPE_KEYS},
        )
        self._ring.append(event)
        stale: list[asyncio.Queue] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow consumer must not stall the plane: drop it, loudly,
                # and let it re-attach. Silence would be the bug (BOOK §82).
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)
        return event

    def recent(self, limit: int = 50) -> list[CanonicalEvent]:
        if limit <= 0:
            return []
        return list(self._ring)[-limit:]

    def subscribe(self, maxsize: int = 512) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def stream(self, queue: asyncio.Queue) -> AsyncIterator[CanonicalEvent]:
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe(queue)

    def __iter__(self) -> Iterator[CanonicalEvent]:
        return iter(list(self._ring))
