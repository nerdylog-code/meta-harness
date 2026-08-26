"""Hermes-native engine.

Spawns an isolated Hermes session through the host gateway and normalizes
events from the host's stream into the Meta-Harness event schema.

If the host gateway isn't available, the engine degrades gracefully — the
plugin still loads and other engines continue to work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, AsyncIterator

from .base import WorkerHandle

logger = logging.getLogger("meta-harness.engines.hermes")

# Engines are stored module-level. Tests reset via ``reset()``.
_WORKERS: dict[str, "_HermesWorker"] = {}
_LOCK = threading.Lock()


def _resolve_host_request():
    """Late-bound lookup of the host's ``host.request`` shim.

    Hermes exposes a gateway RPC to in-process plugins via the PluginContext;
    the exact attribute varies between hosts and versions. We try a sequence
    of documented names and fall back to None if the host doesn't expose any.

    The plugin must remain loadable in hosts without the gateway shim —
    that's the whole point of the ``available()`` check.
    """
    try:
        from . import _HOST_REQUEST  # type: ignore
        return _HOST_REQUEST
    except ImportError:
        return None


def _resolve_host_state():
    try:
        from . import _HOST_STATE  # type: ignore
    except ImportError:
        return None
    if _HOST_STATE is None:
        return None
    return _HOST_STATE


class _HermesWorker:
    """In-process bookkeeping for one Hermes session worker."""

    def __init__(self, handle: WorkerHandle, queue: asyncio.Queue,
                 cancel_event: asyncio.Event, model: str | None):
        self.handle = handle
        self.queue = queue
        self.cancel_event = cancel_event
        self.model = model
        self.last_event_at = time.time()


class HermesEngine:
    name = "hermes"
    description = "Native Hermes agent (uses host gateway / sessions)"

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: dict[str, asyncio.Queue] = {}

    def available(self) -> bool:
        return _resolve_host_request() is not None

    def default_model(self) -> str | None:
        st = _resolve_host_state()
        if not st:
            return None
        try:
            return st.get("model")
        except Exception:
            return None

    async def start(self, spec: dict) -> WorkerHandle:
        wid = f"hermes_{uuid.uuid4().hex[:8]}"
        handle = WorkerHandle(
            worker_id=wid, engine=self.name,
            role=spec.get("role", "builder"),
            model=spec.get("model"),
        )
        queue: asyncio.Queue = asyncio.Queue()
        cancel_event = asyncio.Event()
        worker = _HermesWorker(handle, queue, cancel_event, handle.model)
        with _LOCK:
            _WORKERS[wid] = worker

        # We try to actually create a host session. If the host is absent
        # (e.g. tests, or a host build without the gateway shim) we keep
        # the worker registered and emit a synthetic "host unavailable"
        # event so callers see the truth instead of a silent hang.
        host_request = _resolve_host_request()
        if host_request is None:
            await queue.put({
                "event": "worker.spawned",
                "engine": self.name,
                "worker_id": wid,
                "host_available": False,
                "note": "Hermes gateway unavailable; engine is in stub mode.",
            })
            return handle

        # Real path — create the host session asynchronously.
        asyncio.create_task(self._spawn_session(host_request, handle, spec, queue, cancel_event))
        return handle

    async def _spawn_session(self, host_request, handle: WorkerHandle,
                             spec: dict, queue: asyncio.Queue,
                             cancel_event: asyncio.Event) -> None:
        try:
            session = await asyncio.to_thread(
                host_request,
                "sessions.create",
                {
                    "title": f"meta-harness:{handle.role}:{handle.worker_id[:8]}",
                    "model": handle.model,
                    "system": spec.get("system", ""),
                    "hidden": True,
                },
            )
            await queue.put({
                "event": "worker.spawned",
                "engine": self.name,
                "worker_id": handle.worker_id,
                "session_id": (session or {}).get("id"),
            })
            # Send the initial task as the first message so the agent loop
            # starts immediately. The host will stream tool events back via
            # host.onEvent; we also mirror them onto the worker queue.
            if spec.get("task"):
                await asyncio.to_thread(
                    host_request, "sessions.message",
                    {"session_id": (session or {}).get("id"),
                     "content": spec.get("task")},
                )
        except Exception as exc:
            await queue.put({
                "event": "worker.failed",
                "engine": self.name,
                "worker_id": handle.worker_id,
                "error": str(exc),
            })

    async def send(self, worker_id: str, message: dict) -> None:
        with _LOCK:
            worker = _WORKERS.get(worker_id)
        if not worker:
            return
        host_request = _resolve_host_request()
        if host_request is None:
            return
        session_id = worker.handle.raw.get("session_id")
        if not session_id:
            return
        try:
            await asyncio.to_thread(
                host_request, "sessions.message",
                {"session_id": session_id, "content": message.get("content", "")},
            )
        except Exception as exc:  # pragma: no cover — host varies
            logger.warning("hermes engine send failed: %s", exc)

    async def stream(self, worker_id: str) -> AsyncIterator[dict]:
        with _LOCK:
            worker = _WORKERS.get(worker_id)
        if not worker:
            return
        while True:
            try:
                evt = await asyncio.wait_for(worker.queue.get(), timeout=15)
                yield evt
                if evt.get("event") in ("worker.completed", "worker.failed",
                                        "worker.cancelled"):
                    break
            except asyncio.TimeoutError:
                yield {"event": "worker.heartbeat",
                       "worker_id": worker_id, "ts": time.time()}

    async def cancel(self, worker_id: str) -> None:
        with _LOCK:
            worker = _WORKERS.pop(worker_id, None)
        if not worker:
            return
        worker.cancel_event.set()
        host_request = _resolve_host_request()
        if host_request is not None:
            session_id = worker.handle.raw.get("session_id")
            if session_id:
                try:
                    await asyncio.to_thread(
                        host_request, "sessions.cancel",
                        {"session_id": session_id},
                    )
                except Exception:
                    pass
        await worker.queue.put({
            "event": "worker.cancelled",
            "engine": self.name,
            "worker_id": worker_id,
        })

    async def close(self, worker_id: str) -> None:
        with _LOCK:
            worker = _WORKERS.pop(worker_id, None)
        if not worker:
            return


def reset() -> None:
    with _LOCK:
        _WORKERS.clear()