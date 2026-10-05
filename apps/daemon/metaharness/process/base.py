"""Child-process supervision — the one component every runtime adapter needs.

`ProcessSupervisor` spawns, streams, interrupts, terminates and **kills a whole
process tree without leaving orphans**, identically on Windows and Linux
(BOOK §10.3/§79). One interface, two OS implementations; callers never branch on
platform.

Rules this module enforces, because a runtime that cannot be cancelled poisons
everything above it:

  * no ``shell=True`` anywhere — argv lists only (BOOK §10.2);
  * cancellation is **verified**, not assumed: after the escalation window the
    tree is re-walked and survivors are reported in the kill report;
  * stream output is bounded and marked when truncated, and drains concurrently
    so a chatty child cannot deadlock the supervisor;
  * a descendant that escaped the process group (``setsid`` / detached) is
    reported as escaped — never silently claimed as killed (BOOK §82).

This module does not import the app or the event bus: an optional ``on_event``
callback lets the daemon route `system.process.*` events without coupling the
kernel to a transport.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Sequence

import psutil

DEFAULT_GRACE_S = 5.0
DEFAULT_WALL_TIMEOUT_S = 900.0
STREAM_BUFFER_CHUNKS = 4096
PREVIEW_CHARS = 4000

EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class ProcessHandle:
    pid: int
    argv: list[str]
    cwd: str | None
    started_at: float = field(default_factory=time.time)
    process: Any = None  # asyncio.subprocess.Process


@dataclass
class StreamChunk:
    stream: str  # "stdout" | "stderr"
    text: str
    truncated: bool = False


@dataclass
class ProcessOutcome:
    pid: int
    returncode: int | None
    signal: int | None
    duration_ms: int
    requested: bool  # did *we* end it?
    reason: str | None = None  # "exit" | "interrupt" | "terminate" | "kill_tree" | "wall_timeout"


@dataclass
class KillReport:
    root_pid: int
    killed: list[int]
    escalated: bool
    survivors: list[int]
    escaped: list[int]

    @property
    def orphan_check(self) -> bool:
        """True only when nothing survived. This is the BOOK §79 gate."""
        return not self.survivors


class ProcessError(RuntimeError):
    pass


class _Stream:
    """Bounded, non-blocking capture for one pipe."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.queue: asyncio.Queue[StreamChunk | None] = asyncio.Queue(maxsize=256)
        self.chunks: deque[str] = deque(maxlen=STREAM_BUFFER_CHUNKS)
        self.bytes = 0
        self.truncated = False
        self.eof = False

    @property
    def text(self) -> str:
        return "".join(self.chunks)

    @property
    def preview(self) -> str:
        return self.text[-PREVIEW_CHARS:]


class ProcessSupervisor:
    """Base implementation; OS subclasses supply spawn flags and signals."""

    platform: str = "unknown"

    def __init__(
        self,
        *,
        grace_s: float = DEFAULT_GRACE_S,
        wall_timeout_s: float = DEFAULT_WALL_TIMEOUT_S,
        on_event: EventCallback | None = None,
        max_stream_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.grace_s = grace_s
        self.wall_timeout_s = wall_timeout_s
        self.on_event = on_event
        self.max_stream_bytes = max_stream_bytes
        self._streams: dict[int, dict[str, _Stream]] = {}
        self._tasks: dict[int, list[asyncio.Task]] = {}
        self._requested: dict[int, str] = {}
        # Every descendant we have ever seen for a root pid. Without this, a
        # child that survives its parent (or left the session) becomes
        # invisible the moment the parent dies, and "zero orphans" would be a
        # claim made by an amnesiac supervisor.
        self._known: dict[int, set[int]] = {}

    # -- OS hooks ----------------------------------------------------------
    def _spawn_kwargs(self) -> dict[str, Any]:
        raise NotImplementedError

    def _signal_group(self, handle: ProcessHandle, sig: str, known: Sequence[int] = ()) -> bool:
        """Send a logical signal ("int"|"term"|"kill") to the whole tree.

        ``known`` carries the descendants discovered **before** signalling —
        the tree must be enumerated while it is still alive, because once the
        root dies its children are reparented and can no longer be found by
        walking down from it.

        Returns True when at least one process was signalled.
        """
        raise NotImplementedError

    # -- lifecycle ---------------------------------------------------------
    async def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: str | os.PathLike | None = None,
        env: dict[str, str] | None = None,
        capture: bool = True,
    ) -> ProcessHandle:
        if not argv:
            raise ProcessError("argv must not be empty")
        if isinstance(argv, str):  # a string is a shell habit; refuse it loudly
            raise ProcessError("argv must be a sequence of arguments, not a command string")
        kwargs = self._spawn_kwargs()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd) if cwd else None,
            env={**os.environ, **(env or {})} if env else None,
            stdin=asyncio.subprocess.PIPE if capture else None,
            stdout=asyncio.subprocess.PIPE if capture else None,
            stderr=asyncio.subprocess.PIPE if capture else None,
            **kwargs,
        )
        handle = ProcessHandle(pid=process.pid, argv=list(argv), cwd=str(cwd) if cwd else None, process=process)
        if capture:
            self._streams[process.pid] = {"stdout": _Stream("stdout"), "stderr": _Stream("stderr")}
            self._tasks[process.pid] = [
                asyncio.create_task(self._drain(process.stdout, self._streams[process.pid]["stdout"])),
                asyncio.create_task(self._drain(process.stderr, self._streams[process.pid]["stderr"])),
            ]
        self._emit("system.process.spawned", {"pid": process.pid, "cwd": handle.cwd, "argv_count": len(argv)})
        return handle

    async def _drain(self, reader: asyncio.StreamReader | None, sink: _Stream) -> None:
        if reader is None:
            sink.eof = True
            await sink.queue.put(None)
            return
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                sink.bytes += len(data)
                text = data.decode("utf-8", errors="replace")
                truncated = False
                if sink.bytes > self.max_stream_bytes:
                    truncated = True
                    if not sink.truncated:
                        sink.truncated = True
                        text = text + "\n[stream truncated by supervisor]"
                    else:
                        text = ""
                if text:
                    sink.chunks.append(text)
                    if not sink.queue.full():
                        sink.queue.put_nowait(StreamChunk(sink.name, text, truncated))
        finally:
            sink.eof = True
            if not sink.queue.full():
                sink.queue.put_nowait(None)

    async def write_stdin(self, handle: ProcessHandle, data: bytes) -> None:
        process = handle.process
        if process is None or process.stdin is None:
            raise ProcessError("process was spawned without a stdin pipe")
        process.stdin.write(data)
        try:
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ProcessError(f"stdin closed: {exc}") from exc

    async def close_stdin(self, handle: ProcessHandle) -> None:
        process = handle.process
        if process is not None and process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()

    async def stream(self, handle: ProcessHandle, which: str = "stdout", timeout_s: float | None = None) -> AsyncIterator[StreamChunk]:
        sink = self._streams.get(handle.pid, {}).get(which)
        if sink is None:
            raise ProcessError(f"no {which} capture for pid {handle.pid}")
        while True:
            try:
                item = await asyncio.wait_for(sink.queue.get(), timeout=timeout_s) if timeout_s else await sink.queue.get()
            except asyncio.TimeoutError:
                return
            if item is None:
                return
            yield item

    def capture(self, handle: ProcessHandle, which: str = "stdout") -> str:
        sink = self._streams.get(handle.pid, {}).get(which)
        return sink.text if sink else ""

    def preview(self, handle: ProcessHandle, which: str = "stdout") -> str:
        sink = self._streams.get(handle.pid, {}).get(which)
        return sink.preview if sink else ""

    async def wait(self, handle: ProcessHandle, timeout_s: float | None = None) -> ProcessOutcome:
        process = handle.process
        if process is None:
            raise ProcessError("handle has no process")
        budget = self.wall_timeout_s if timeout_s is None else timeout_s
        requested = self._requested.get(handle.pid)
        try:
            await asyncio.wait_for(process.wait(), timeout=budget)
        except asyncio.TimeoutError:
            self._requested[handle.pid] = "wall_timeout"
            report = await self.kill_tree(handle)
            requested = "wall_timeout"
            return ProcessOutcome(
                pid=handle.pid,
                returncode=process.returncode,
                signal=_signal_of(process.returncode),
                duration_ms=int((time.time() - handle.started_at) * 1000),
                requested=True,
                reason=f"wall_timeout+{'killed' if report.orphan_check else 'survivors'}",
            )
        await self._finish_streams(handle.pid)
        return ProcessOutcome(
            pid=handle.pid,
            returncode=process.returncode,
            signal=_signal_of(process.returncode),
            duration_ms=int((time.time() - handle.started_at) * 1000),
            requested=requested is not None,
            reason=requested or "exit",
        )

    async def _finish_streams(self, pid: int) -> None:
        for task in self._tasks.get(pid, []):
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                task.cancel()

    # -- tree control ------------------------------------------------------
    def descendants(self, pid: int) -> list[int]:
        try:
            parent = psutil.Process(pid)
        except psutil.Error:
            return []
        out: list[int] = []
        for child in parent.children(recursive=True):
            try:
                out.append(child.pid)
            except psutil.Error:  # pragma: no cover - raced exit
                pass
        return out

    def group_members(self, pid: int) -> list[int]:
        """Descendants that are still inside our process group/session."""
        return self.descendants(pid)

    def _snapshot(self, handle: ProcessHandle) -> list[int]:
        """Enumerate the live tree now, and remember it for later verification."""
        found = self.descendants(handle.pid)
        bucket = self._known.setdefault(handle.pid, set())
        bucket.update(found)
        bucket.discard(handle.pid)
        return found

    def _escaped(self, handle: ProcessHandle, snapshot: Sequence[int]) -> list[int]:
        """Processes that had already left our session before cancellation.

        A group signal alone would miss them; they are killed by pid and
        reported here instead of being quietly counted as handled.
        """
        return sorted(pid for pid in snapshot if not _in_group(pid, handle.pid))

    async def interrupt(self, handle: ProcessHandle) -> bool:
        self._requested[handle.pid] = "interrupt"
        sent = self._signal_group(handle, "int", self._snapshot(handle))
        self._emit("system.process.interrupt", {"pid": handle.pid, "sent": sent})
        return sent

    async def terminate(self, handle: ProcessHandle, grace_s: float | None = None) -> KillReport:
        """Graceful stop with escalation. Always ends with a verified tree walk."""
        self._requested[handle.pid] = "terminate"
        grace = self.grace_s if grace_s is None else grace_s
        snapshot = self._snapshot(handle)
        escaped = self._escaped(handle, snapshot)
        sent = self._signal_group(handle, "term", snapshot)
        escalated = False
        if sent:
            survived = await self._wait_gone(handle.pid, grace, snapshot)
            if survived:
                escalated = True
                self._signal_group(handle, "kill", self._snapshot(handle))
        report = await self._verify_dead(handle, escalated=escalated, escaped=escaped)
        self._emit(
            "system.process.kill_tree",
            {
                "root_pid": report.root_pid,
                "killed": report.killed,
                "escalated": report.escalated,
                "survivors": report.survivors,
                "escaped": report.escaped,
                "orphan_check": report.orphan_check,
            },
        )
        return report

    async def kill_tree(self, handle: ProcessHandle) -> KillReport:
        """Hard stop: kill the whole tree, then prove nothing survived."""
        self._requested.setdefault(handle.pid, "kill_tree")
        snapshot = self._snapshot(handle)
        escaped = self._escaped(handle, snapshot)
        self._signal_group(handle, "kill", snapshot)
        report = await self._verify_dead(handle, escalated=True, escaped=escaped)
        self._emit(
            "system.process.kill_tree",
            {
                "root_pid": report.root_pid,
                "killed": report.killed,
                "escalated": report.escalated,
                "survivors": report.survivors,
                "escaped": report.escaped,
                "orphan_check": report.orphan_check,
            },
        )
        return report

    async def _wait_gone(self, pid: int, grace_s: float, snapshot: Sequence[int] = ()) -> bool:
        watched = set(snapshot) | self._known.get(pid, set())
        deadline = time.time() + grace_s
        while time.time() < deadline:
            if not self.descendants(pid) and not _alive(pid) and not any(_alive(p) for p in watched):
                return False
            await asyncio.sleep(0.1)
        return bool(self.descendants(pid)) or _alive(pid) or any(_alive(p) for p in watched)

    async def _verify_dead(self, handle: ProcessHandle, *, escalated: bool, escaped: Sequence[int] = ()) -> KillReport:
        """Wait for the tree to disappear, then report what is still alive.

        Verification is deliberately paranoid: it checks both the currently
        reachable descendants *and* every pid ever observed for this root, so a
        reparented grandchild cannot hide behind its dead parent.
        """
        watched = set(self._known.get(handle.pid, set()))
        gone_deadline = time.time() + max(2.0, self.grace_s)
        while time.time() < gone_deadline:
            live = set(self.descendants(handle.pid))
            self._known.setdefault(handle.pid, set()).update(live)
            watched |= live
            if not any(_alive(pid) for pid in watched):
                break
            await asyncio.sleep(0.1)
        live_descendants = set(self.descendants(handle.pid))
        watched |= live_descendants
        survivors = sorted(pid for pid in watched if _alive(pid))
        return KillReport(
            root_pid=handle.pid,
            killed=sorted({handle.pid, *watched}),
            escalated=escalated,
            survivors=survivors,
            escaped=sorted(escaped),
        )

    async def close(self, handle: ProcessHandle) -> None:
        await self._finish_streams(handle.pid)
        await self.close_stdin(handle)
        self._streams.pop(handle.pid, None)
        self._tasks.pop(handle.pid, None)
        self._requested.pop(handle.pid, None)
        self._known.pop(handle.pid, None)

    def health(self, handle: ProcessHandle) -> dict[str, Any]:
        process = handle.process
        returncode = process.returncode if process is not None else None
        return {
            "pid": handle.pid,
            "alive": returncode is None and _alive(handle.pid),
            "returncode": returncode,
            "descendants": len(self.descendants(handle.pid)),
            "stdout_preview": len(self.preview(handle, "stdout")),
            "stderr_preview": len(self.preview(handle, "stderr")),
        }

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, payload)
            except Exception:  # pragma: no cover - a bad callback must not kill supervision
                pass


def _signal_of(returncode: int | None) -> int | None:
    if returncode is None:
        return None
    if returncode < 0:  # POSIX: terminated by signal N
        return -returncode
    if returncode > 128:  # some shells/OSes map it this way
        return returncode - 128
    return None


def _alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _in_group(pid: int, root_pid: int) -> bool:
    """True when the process still belongs to the root's session/group.

    A child that called setsid() leaves the group; on POSIX a group signal can
    no longer reach it, so it must be reported as escaped rather than as killed.
    """
    if not hasattr(os, "getsid"):
        return True  # Windows has no sessions; psutil's tree walk is the guarantee
    try:
        return os.getsid(pid) == os.getsid(root_pid)
    except (ProcessLookupError, PermissionError, OSError):
        return False
