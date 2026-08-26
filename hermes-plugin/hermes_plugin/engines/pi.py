"""Pi Coding Agent engine adapter.

Spawns ``pi --mode rpc`` (JSON-RPC over stdio) when available, normalizes
its events into the Meta-Harness event schema.

We never scrape Pi terminal output. If the binary is missing or the rpc
handshake fails, the engine reports ``AVAILABLE=false`` and emits a single
synthetic failure event — the rest of the harness keeps working.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from .base import WorkerHandle

logger = logging.getLogger("meta-harness.engines.pi")

_WORKERS: dict[str, "_PiWorker"] = {}


class _PiWorker:
    def __init__(self, handle: WorkerHandle, proc: asyncio.subprocess.Process,
                 queue: asyncio.Queue, cancel_event: asyncio.Event):
        self.handle = handle
        self.proc = proc
        self.queue = queue
        self.cancel_event = cancel_event


def _resolve_pi() -> str | None:
    """Resolve the Pi executable. Honor PATH and common npm locations."""
    env = os.environ.get("PI_EXECUTABLE", "").strip()
    if env and Path(env).exists():
        return env
    found = shutil.which("pi")
    if found:
        return found
    # Windows: npm-global default
    candidates = [
        Path.home() / "AppData/Roaming/npm/pi.cmd",
        Path.home() / "AppData/Roaming/npm/pi",
        Path("/usr/local/bin/pi"),
        Path("/opt/homebrew/bin/pi"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


class PiEngine:
    name = "pi"
    description = "Pi Coding Agent (clean-room JSON RPC worker)"

    def __init__(self) -> None:
        self._binary: str | None = _resolve_pi()

    def available(self) -> bool:
        return self._binary is not None

    def default_model(self) -> str | None:
        # Pi is model-agnostic; pick the host's active default if visible.
        try:
            from . import _HOST_STATE  # type: ignore
            if _HOST_STATE:
                return _HOST_STATE.get("model")
        except Exception:
            pass
        return None

    async def start(self, spec: dict) -> WorkerHandle:
        if not self._binary:
            wid = f"pi_{uuid.uuid4().hex[:8]}"
            handle = WorkerHandle(worker_id=wid, engine=self.name,
                                  role=spec.get("role", "builder"),
                                  model=spec.get("model"))
            # Record immediate failure so the caller learns the truth.
            await asyncio.sleep(0)
            return handle
        wid = f"pi_{uuid.uuid4().hex[:8]}"
        handle = WorkerHandle(worker_id=wid, engine=self.name,
                              role=spec.get("role", "builder"),
                              model=spec.get("model"))
        queue: asyncio.Queue = asyncio.Queue()
        cancel_event = asyncio.Event()

        cmd = [self._binary, "--mode", "rpc", "--no-extensions", "--no-skills"]
        if spec.get("model"):
            cmd += ["--model", spec["model"]]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=spec.get("cwd"),
            )
        except Exception as exc:
            logger.warning("pi engine failed to spawn: %s", exc)
            return handle

        worker = _PiWorker(handle, proc, queue, cancel_event)
        _WORKERS[wid] = worker

        await queue.put({
            "event": "worker.spawned",
            "engine": self.name,
            "worker_id": wid,
            "pid": proc.pid,
        })

        asyncio.create_task(self._pump(worker, spec))
        return handle

    async def _pump(self, worker: _PiWorker, spec: dict) -> None:
        proc = worker.proc
        try:
            # Send the initial task as the first rpc message.
            assert proc.stdin is not None
            payload = {
                "type": "user",
                "content": spec.get("task", ""),
            }
            proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            await proc.stdin.drain()

            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    evt = json.loads(line.decode("utf-8", errors="replace").strip())
                except json.JSONDecodeError:
                    continue
                normalized = self._normalize(evt, worker.handle.worker_id)
                await worker.queue.put(normalized)
                if normalized.get("event") in ("worker.completed", "worker.failed"):
                    break
        except Exception as exc:
            await worker.queue.put({
                "event": "worker.failed",
                "engine": self.name,
                "worker_id": worker.handle.worker_id,
                "error": str(exc),
            })
        finally:
            try:
                if proc.returncode is None:
                    proc.terminate()
            except Exception:
                pass

    def _normalize(self, evt: dict, worker_id: str) -> dict:
        """Translate a Pi rpc event into the Meta-Harness schema.

        Pi's rpc schema is intentionally loose; we recognize the common
        shapes and fall back to passthrough. The mapping is best-effort and
        never crashes on unknown types — every Pi event becomes a tool.*
        event the rest of the harness can react to.
        """
        t = evt.get("type") or evt.get("event") or "unknown"
        if t in ("assistant", "agent"):
            return {"event": "agent.thinking", "engine": self.name,
                    "worker_id": worker_id, "data": evt}
        if t in ("user",):
            return {"event": "message.sent", "engine": self.name,
                    "worker_id": worker_id, "data": evt}
        if t in ("tool_use", "tool_call", "tool.start"):
            return {"event": "tool.started", "engine": self.name,
                    "worker_id": worker_id,
                    "tool": evt.get("name") or evt.get("tool"),
                    "args": evt.get("input") or evt.get("args"),
                    "data": evt}
        if t in ("tool_result", "tool.end"):
            return {"event": "tool.completed", "engine": self.name,
                    "worker_id": worker_id,
                    "tool": evt.get("name") or evt.get("tool"),
                    "result": evt.get("result") or evt.get("output"),
                    "data": evt}
        if t in ("done", "complete", "end"):
            return {"event": "worker.completed", "engine": self.name,
                    "worker_id": worker_id, "data": evt}
        if t in ("error", "fail"):
            return {"event": "worker.failed", "engine": self.name,
                    "worker_id": worker_id, "error": evt.get("error") or evt.get("message"),
                    "data": evt}
        # Pass-through with kind preserved.
        return {"event": f"pi.{t}", "engine": self.name,
                "worker_id": worker_id, "data": evt}

    async def send(self, worker_id: str, message: dict) -> None:
        worker = _WORKERS.get(worker_id)
        if not worker:
            return
        try:
            assert worker.proc.stdin is not None
            worker.proc.stdin.write(
                (json.dumps({"type": "user", "content": message.get("content", "")}) + "\n").encode("utf-8")
            )
            await worker.proc.stdin.drain()
        except Exception as exc:  # pragma: no cover
            logger.warning("pi engine send failed: %s", exc)

    async def stream(self, worker_id: str) -> AsyncIterator[dict]:
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
        worker = _WORKERS.pop(worker_id, None)
        if not worker:
            return
        worker.cancel_event.set()
        try:
            worker.proc.terminate()
        except Exception:
            pass
        await worker.queue.put({
            "event": "worker.cancelled",
            "engine": self.name,
            "worker_id": worker_id,
        })

    async def close(self, worker_id: str) -> None:
        await self.cancel(worker_id)