"""Engine adapters — normalized worker abstraction.

Each engine implements:

  async start(spec) -> worker_id
  async send(worker_id, message) -> None
  async stream(worker_id) -> AsyncIterator[event]
  async cancel(worker_id) -> None
  async close(worker_id) -> None

Events follow the Meta-Harness normalized schema (see ``EVENT_SCHEMA`` in
``runtime.py``).
"""

from .base import Engine, WorkerHandle, EVENT_SCHEMA  # re-export
from .hermes import HermesEngine
from .pi import PiEngine

_REGISTRY: dict[str, Engine] = {}


def register(engine: Engine) -> None:
    _REGISTRY[engine.name] = engine


def get(name: str) -> Engine | None:
    return _REGISTRY.get(name)


def list_engines() -> list[dict]:
    return [
        {"name": e.name, "description": e.description,
         "available": e.available(), "default_model": e.default_model()}
        for e in _REGISTRY.values()
    ]


def reset() -> None:
    _REGISTRY.clear()


__all__ = [
    "Engine", "WorkerHandle", "EVENT_SCHEMA",
    "HermesEngine", "PiEngine",
    "register", "get", "list_engines", "reset",
]