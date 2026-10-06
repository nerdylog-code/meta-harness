"""Event plane — the canonical store plus an in-process fan-out bus.

The envelope is the **frozen contract** (ADR-0017, WP-003). This module deliberately does
not define one: WP-002's hand-rolled ``CanonicalEvent`` was a duplicate of the contract, and
a duplicate envelope is a second source of truth in spirit even when it agrees byte for
byte. It is deleted, and two tests keep it deleted.

Two responsibilities, kept apart:

* **durability** belongs to the store (``metaharness.store``). SQLite is canonical; the
  bus never keeps its own copy of the log, because a bounded in-memory ring is a lossy
  second log.
* **delivery** belongs to the bus: who hears about an event *now*, plus a durable backlog
  read so a late subscriber (or a daemon that just restarted) is never left staring at an
  empty stream.

``publish`` is synchronous and therefore blocks its caller for one SQLite commit. That is a
deliberate trade for a local, single-writer daemon: making it async would force ``await``
into code that has no event loop (the process supervisor emits events from sync methods),
and WAL keeps the write small. If profiling ever shows it matters, the fix is a write-behind
queue here -- not a second store.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator

from metaharness_contracts import CanonicalEvent
from metaharness_contracts.events import ENVELOPE_KEYS, PAYLOAD_VERSION_KEY

from .store import Store

__all__ = ["ENVELOPE_KEYS", "EventBus", "PROVENANCE_METHODS", "CanonicalEvent"]

#: Provenance vocabulary (PROJECT_BOOK §17/§49). Every payload must say how it was
#: obtained; mixing `estimated` with `measured` silently is a correctness bug, so an
#: unknown method is refused here even though the contract leaves `provenance` open.
PROVENANCE_METHODS = ("measured", "provider_reported", "runtime_reported", "estimated", "unknown")

DEFAULT_BACKLOG = 50


class EventBus:
    """Persist, then fan out. Subscribers are asyncio queues owned by the daemon loop."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ identity

    @property
    def last_seq(self) -> int:
        """The canonical sequence, owned by the store -- never a local counter."""
        return self.store.latest_seq()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------------- writing

    def publish(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        method: str = "measured",
        origin: str = "kernel",
        **ids: Any,
    ) -> CanonicalEvent:
        """Append the event to the canonical log, then deliver it to subscribers."""
        if method not in PROVENANCE_METHODS:
            raise ValueError(
                f"event {kind!r} needs provenance.method in {PROVENANCE_METHODS}, got {method!r}"
            )
        # The store owns `seq` and stamps the payload version (WP-003 decision 2); it also
        # rejects an event whose projection refuses it, so a delivery can never describe
        # something that was not persisted.
        result = self.store.emit(
            kind, payload, provenance={"method": method, "origin": origin}, **ids
        )
        event = result.event
        self._fanout(event)
        return event

    def publish_event(self, event: CanonicalEvent) -> CanonicalEvent:
        """Persist an event somebody else built (an adapter, the reconciler).

        The store owns ``seq``, so a placeholder is replaced on append; what comes back -- and
        what subscribers see -- is the stored event, never the hopeful one.
        """
        result = self.store.append(event)
        self._fanout(result.event)
        return result.event

    def publish_transient(self, event: CanonicalEvent) -> CanonicalEvent:
        """Fan out without persisting: a stream in progress is not history.

        Used for token deltas, which would otherwise put one row per token in the log. The
        frame is marked ``provenance.persisted = false`` so a client can never mistake a delta
        for something that was recorded (BOOK §21).
        """
        transient = event.model_copy(
            update={"provenance": {**event.provenance, "persisted": False}}
        )
        self._fanout(transient)
        return transient

    def _fanout(self, event: CanonicalEvent) -> None:
        stale: list[asyncio.Queue] = []
        with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow consumer must not stall the plane: drop it, loudly, and let it
                # re-attach. Silence would be the bug (BOOK §82).
                stale.append(queue)
        for queue in stale:
            self.unsubscribe(queue)

    # ------------------------------------------------------------------- reading

    def recent(self, limit: int = DEFAULT_BACKLOG) -> list[CanonicalEvent]:
        """The durable tail of the log, oldest first.

        Read from the store rather than a ring: the backlog a restarted daemon offers is
        then the real history, not whatever happened to be in memory.
        """
        if limit <= 0:
            return []
        latest = self.store.latest_seq()
        if latest == 0:
            return []
        start = max(0, latest - limit)
        return self.store.events(after_seq=start, limit=limit)

    # ------------------------------------------------------------------ delivery

    def subscribe(self, maxsize: int = 512) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    async def stream(self, queue: asyncio.Queue) -> AsyncIterator[CanonicalEvent]:
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe(queue)


def payload_version_key() -> str:
    """Exposed for tests and docs: the payload must declare its own version."""
    return PAYLOAD_VERSION_KEY
