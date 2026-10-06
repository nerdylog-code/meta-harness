"""ACP transport: JSON-RPC 2.0 over the child's stdin/stdout (M2, ADR-0016).

Observed against `hermes acp` 0.21.5 — see `docs/protocols/HERMES_ACP.md`. The rules are the
same ones the Pi transport follows, because the lessons were the same:

* **correlation** — every request carries `id`; responses are matched by id, never by arrival
  order, because notifications interleave freely with responses;
* **honesty** — a line that is not a JSON object, or a response with no pending request, is a
  protocol error that names the line. A dropped frame is a hole in the audit trail;
* **lifecycle** — spawn, cancel and kill go through `ProcessSupervisor`, so cancellation ends with
  an orphan check instead of a hopeful signal (BOOK §79).

stdout belongs to this class (`drain=False` on the supervisor); stderr stays with the supervisor
for diagnostics. One reader per pipe, always.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Sequence

from ...process import ProcessHandle, ProcessSupervisor, supervisor as default_supervisor

MAX_LINE_BYTES = 8 * 1024 * 1024
EVENT_QUEUE_MAX = 10_000
DEFAULT_RESPONSE_TIMEOUT_S = 60.0


class AcpProtocolError(RuntimeError):
    """A frame the protocol cannot account for."""


class AcpTransportError(RuntimeError):
    """A request that was refused, or never answered."""


@dataclass
class CloseReport:
    reason: str
    exit_code: int | None
    orphans: bool
    survivors: list[int] = field(default_factory=list)
    forced_kill: bool = False


@dataclass
class AcpStats:
    requests: int = 0
    notifications: int = 0
    protocol_errors: int = 0
    bytes_in: int = 0


class AcpTransport:
    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        supervisor: ProcessSupervisor | None = None,
        response_timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
        on_protocol_error: Callable[[AcpProtocolError], None] | None = None,
        event_queue_max: int = EVENT_QUEUE_MAX,
    ) -> None:
        if not argv:
            raise AcpTransportError("argv must not be empty")
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.supervisor = supervisor or default_supervisor()
        self.response_timeout_s = response_timeout_s
        self.on_protocol_error = on_protocol_error
        self.handle: ProcessHandle | None = None
        self.stats = AcpStats()
        self.protocol_errors: list[str] = []
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=event_queue_max)
        self._reader: asyncio.Task[None] | None = None
        self._counter = 0
        self._closed = False
        self.started_at = 0.0

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> "AcpTransport":
        if self.handle is not None:
            raise AcpTransportError("transport already started")
        handle = await self.supervisor.spawn(
            self.argv, cwd=self.cwd, env=self.env, capture=True, drain=False
        )
        self.handle = handle
        self.started_at = time.time()
        self._reader = asyncio.create_task(self._read_loop(), name=f"acp-reader-{handle.pid}")
        return self

    @property
    def pid(self) -> int | None:
        return self.handle.pid if self.handle else None

    def alive(self) -> bool:
        if self.handle is None or self.handle.process is None:
            return False
        return self.handle.process.returncode is None

    async def close(self, *, reason: str = "closed", grace_s: float = 5.0) -> CloseReport:
        """Close stdin, wait for a clean exit, then kill the tree if it lingers."""
        if self.handle is None:
            return CloseReport(reason=reason, exit_code=None, orphans=True)
        self._closed = True
        try:
            await self.supervisor.close_stdin(self.handle)
        except Exception:  # pragma: no cover - already gone
            pass
        process = self.handle.process
        forced = False
        if process is not None:
            try:
                await asyncio.wait_for(process.wait(), timeout=grace_s)
            except asyncio.TimeoutError:
                forced = True
        report = await self.supervisor.kill_tree(self.handle)
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
        self._fail_pending(f"transport closed ({reason})")
        try:
            self._notifications.put_nowait(None)
        except asyncio.QueueFull:
            pass
        return CloseReport(
            reason=reason,
            exit_code=getattr(process, "returncode", None),
            orphans=report.orphan_check,
            survivors=list(report.survivors),
            forced_kill=forced,
        )

    async def cancel(self, *, grace_s: float = 5.0) -> CloseReport:
        """Cancellation ends with a verified kill, never with a hopeful notification."""
        if self.handle is not None and self.alive():
            try:
                await self.request("session/cancel", {}, timeout_s=10.0)
            except (AcpTransportError, Exception):
                # A refused or unanswered cancel is not a reason to leave a process running.
                pass
        return await self.close(reason="cancelled", grace_s=grace_s)

    # ------------------------------------------------------------------- protocol

    def _fail_pending(self, reason: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(AcpTransportError(reason))
        self._pending.clear()

    async def _write(self, payload: dict[str, Any]) -> None:
        if self.handle is None:
            raise AcpTransportError("transport is not started")
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            await self.supervisor.write_stdin(self.handle, line)
        except Exception as exc:
            raise AcpTransportError(f"cannot write to the agent: {exc}") from exc

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout_s: float | None = None
    ) -> dict[str, Any]:
        """Send a request and wait for *its* response. Errors are raised, never returned."""
        self._counter += 1
        request_id = self._counter
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        self.stats.requests += 1
        try:
            response = await asyncio.wait_for(future, timeout=timeout_s or self.response_timeout_s)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise AcpTransportError(f"{method} was not answered within {timeout_s or self.response_timeout_s}s") from exc
        if "error" in response:
            error = response["error"] or {}
            raise AcpTransportError(
                f"{method} refused with {error.get('code')}: {str(error.get('message'))[:300]}"
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def _read_loop(self) -> None:
        assert self.handle is not None and self.handle.process is not None
        stream = self.handle.process.stdout
        if stream is None:
            self._protocol_error("the agent has no stdout pipe")
            return
        while True:
            try:
                raw = await stream.readline()
            except (asyncio.CancelledError, Exception):
                return
            if not raw:
                # An agent that dies says why somewhere, and it is almost always stderr. Reporting
                # only "closed its stdout" turns a diagnosable failure (a missing script, a sandbox
                # that refused the mount) into a mystery.
                detail = self._death_detail()
                self._fail_pending(f"the agent closed its stdout{detail}")
                try:
                    self._notifications.put_nowait(None)
                except asyncio.QueueFull:
                    pass
                return
            if len(raw) > MAX_LINE_BYTES:
                self._protocol_error(f"line of {len(raw)} bytes exceeds the {MAX_LINE_BYTES} cap")
                continue
            self.stats.bytes_in += len(raw)
            try:
                # Strict on purpose: `errors="replace"` turns an encoding mismatch into a silently
                # mangled string, which is how a cp1252 bullet survived until Windows CI caught it.
                text = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                self._protocol_error(f"line is not valid UTF-8: {raw[:80]!r}")
                continue
            if not text:
                continue
            try:
                frame = json.loads(text)
            except json.JSONDecodeError:
                self._protocol_error(f"line is not JSON: {text[:200]!r}")
                continue
            if not isinstance(frame, dict):
                self._protocol_error(f"line is not a JSON object: {text[:200]!r}")
                continue
            self._dispatch(frame)

    def _death_detail(self) -> str:
        """Exit code and the tail of stderr, when the supervisor has them."""
        if self.handle is None:
            return ""
        parts: list[str] = []
        process = self.handle.process
        code = getattr(process, "returncode", None)
        if code is not None:
            parts.append(f" (exit {code})")
        try:
            captured = (self.supervisor.capture(self.handle, "stderr") or "").strip()
        except Exception:  # pragma: no cover - stream already gone
            captured = ""
        if captured:
            parts.append(": " + captured.splitlines()[-1][:300])
        return "".join(parts)

    def _dispatch(self, frame: dict[str, Any]) -> None:
        if "id" in frame and "method" not in frame:
            future = self._pending.pop(frame["id"], None)
            if future is None:
                self._protocol_error(f"response for unknown request id {frame['id']!r}")
                return
            if not future.done():
                future.set_result(frame)
            return
        if "method" in frame:
            self.stats.notifications += 1
            try:
                self._notifications.put_nowait(frame)
            except asyncio.QueueFull:
                self._protocol_error(f"notification queue full, dropped {frame.get('method')!r}")
            return
        self._protocol_error(f"frame is neither a response nor a notification: {json.dumps(frame)[:200]}")

    def _protocol_error(self, message: str) -> None:
        self.stats.protocol_errors += 1
        self.protocol_errors.append(message)
        if self.on_protocol_error is not None:
            self.on_protocol_error(AcpProtocolError(message))

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Notifications, in arrival order. `None` ends the stream."""
        while True:
            frame = await self._notifications.get()
            if frame is None:
                return
            yield frame
