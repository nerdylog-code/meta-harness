"""Engine base — shared types and event schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

EVENT_SCHEMA = "meta-harness.event/v1"


@dataclass
class WorkerHandle:
    worker_id: str
    engine: str
    role: str
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Engine(Protocol):
    name: str
    description: str

    def available(self) -> bool: ...
    def default_model(self) -> str | None: ...

    async def start(self, spec: dict) -> WorkerHandle: ...
    async def send(self, worker_id: str, message: dict) -> None: ...
    def stream(self, worker_id: str) -> AsyncIterator[dict]: ...
    async def cancel(self, worker_id: str) -> None: ...
    async def close(self, worker_id: str) -> None: ...