"""Pi RPC transport: JSONL commands out, JSONL records in (WP-015).

The protocol is documented in `docs/protocols/PI_RPC.md`, observed from Pi 0.99.2's own
`docs/rpc.md` and two live probes. What this module owns:

* **framing** — records are split on ``LF`` only. Pi's documentation warns explicitly that a
  generic line reader (Node's ``readline``) also splits on ``U+2028``/``U+2029``, which are
  legal inside JSON strings; splitting on the byte ``\\n`` is the only correct rule, so the
  reader is hand-written rather than delegated.
* **correlation** — every command carries an ``id`` and command handling is asynchronous, so
  responses are matched by id and never by arrival order.
* **honesty** — a line that is not a JSON object is a protocol error that names the line. It is
  never skipped silently: a dropped record is a hole in the audit trail.
* **lifecycle** — the process is spawned, cancelled and killed through `ProcessSupervisor`, so
  cancellation ends with an orphan check instead of a hopeful signal (BOOK §79).

stdout belongs to this class (``drain=False`` on the supervisor), stderr stays with the
supervisor for diagnostics. One reader per pipe, always.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Sequence

from ...process import ProcessHandle, ProcessSupervisor, supervisor as default_supervisor

MAX_LINE_BYTES = 4 * 1024 * 1024
EVENT_QUEUE_MAX = 10_000
DEFAULT_RESPONSE_TIMEOUT_S = 30.0
READ_CHUNK = 65_536


class PiTransportError(RuntimeError):
    """Base class for transport failures."""


class PiCommandError(PiTransportError):
    """Pi answered ``success: false`` (or did not answer in time)."""

    def __init__(self, command: str, message: str, *, response: dict[str, Any] | None = None) -> None:
        super().__init__(f"pi command {command!r} failed: {message}")
        self.command = command
        self.response = response or {}


class PiProtocolError(PiTransportError):
    """A stdout line was not a JSON object, or a response had no matching request."""


@dataclass
class TransportStats:
    records: int = 0
    responses: int = 0
    events: int = 0
    protocol_errors: int = 0
    dropped_events: int = 0
    bytes_read: int = 0


@dataclass
class CloseReport:
    reason: str
    exit_code: int | None
    orphans: bool
    survivors: list[int] = field(default_factory=list)
    forced_kill: bool = False
    stderr_preview: str = ""


class PiTransport:
    """A long-lived ``pi --mode rpc`` subprocess."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        supervisor: ProcessSupervisor | None = None,
        response_timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
        on_protocol_error: Callable[[PiProtocolError], None] | None = None,
        event_queue_max: int = EVENT_QUEUE_MAX,
    ) -> None:
        if not argv:
            raise PiTransportError("argv must not be empty")
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.supervisor = supervisor or default_supervisor()
        self.response_timeout_s = response_timeout_s
        self.on_protocol_error = on_protocol_error
        self.handle: ProcessHandle | None = None
        self.stats = TransportStats()
        self.protocol_errors: list[str] = []
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=event_queue_max)
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._counter = 0
        self._closed = False
        self.started_at = 0.0

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> "PiTransport":
        if self.handle is not None:
            raise PiTransportError("transport already started")
        handle = await self.supervisor.spawn(
            self.argv, cwd=self.cwd, env=self.env, capture=True, drain=False
        )
        self.handle = handle
        self.started_at = time.time()
        self._reader = asyncio.create_task(self._read_loop(), name=f"pi-reader-{handle.pid}")
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
            return CloseReport(reason=reason, exit_code=None, orphans=False)
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
        await self._fail_pending("transport closed")
        return CloseReport(
            reason=reason,
            exit_code=process.returncode if process is not None else None,
            orphans=report.orphan_check,
            survivors=list(report.survivors),
            forced_kill=forced,
            stderr_preview=self.supervisor.preview(self.handle, "stderr")[:2000],
        )

    async def cancel(self, *, grace_s: float = 5.0) -> CloseReport:
        """Ask Pi to stop, then verify the process is gone (never a bare signal)."""
        if self.handle is None:
            return CloseReport(reason="cancel", exit_code=None, orphans=False)
        for command in ("abort", "clear_queue"):
            try:
                await self.command(command, timeout_s=min(grace_s, 5.0))
            except PiTransportError:
                break
        process = self.handle.process
        if process is not None:
            try:
                await asyncio.wait_for(process.wait(), timeout=grace_s)
            except asyncio.TimeoutError:
                pass
        return await self.close(reason="cancelled", grace_s=1.0)

    # -------------------------------------------------------------------- commands

    def _next_id(self) -> str:
        self._counter += 1
        return f"req-{self._counter}"

    async def command(
        self, command: str, *, timeout_s: float | None = None, **fields: Any
    ) -> dict[str, Any]:
        """Send one command and return its ``data`` (raising on ``success: false``)."""
        if self.handle is None or self.handle.process is None:
            raise PiTransportError("transport is not running")
        if self._closed:
            raise PiTransportError("transport is closed")
        request_id = self._next_id()
        record = {"id": request_id, "type": command, **fields}
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            await self.supervisor.write_stdin(self.handle, payload)
        except Exception as exc:
            self._pending.pop(request_id, None)
            raise PiTransportError(f"cannot write {command!r} to pi: {exc}") from exc
        try:
            response = await asyncio.wait_for(future, timeout=timeout_s or self.response_timeout_s)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise PiCommandError(command, f"no response within {timeout_s or self.response_timeout_s}s") from exc
        if not response.get("success", False):
            raise PiCommandError(
                command, str(response.get("error") or "unspecified failure"), response=response
            )
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------------- events

    def try_next_event(self) -> dict[str, Any] | None:
        try:
            return self._events.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def next_event(self, *, timeout_s: float | None = None) -> dict[str, Any]:
        if timeout_s is None:
            return await self._events.get()
        return await asyncio.wait_for(self._events.get(), timeout=timeout_s)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield session events until the peer goes away."""
        while True:
            if self._closed and self._events.empty():
                return
            try:
                record = await asyncio.wait_for(self._events.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if self._reader is not None and self._reader.done():
                    return
                continue
            yield record

    # ---------------------------------------------------------------------- reading

    async def _read_loop(self) -> None:
        assert self.handle is not None and self.handle.process is not None
        stream = self.handle.process.stdout
        if stream is None:  # pragma: no cover - capture=True guarantees a pipe
            return
        buffer = b""
        while True:
            chunk = await stream.read(READ_CHUNK)
            if not chunk:
                break
            self.stats.bytes_read += len(chunk)
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                if raw.endswith(b"\r"):
                    raw = raw[:-1]
                self._handle_line(raw)
            if len(buffer) > MAX_LINE_BYTES:
                self._protocol_error(
                    PiProtocolError(f"record exceeds {MAX_LINE_BYTES} bytes without a newline")
                )
                buffer = b""
        if buffer.strip():
            self._handle_line(buffer)
        await self._fail_pending("pi stdout closed")

    def _handle_line(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            self._protocol_error(
                PiProtocolError(f"stdout line is not JSON: {text[:200]!r}")
            )
            return
        if not isinstance(record, dict):
            self._protocol_error(PiProtocolError(f"stdout record is not an object: {text[:200]!r}"))
            return
        self.stats.records += 1
        if record.get("type") == "response":
            self._handle_response(record)
        else:
            self._handle_event(record)

    def _handle_response(self, record: dict[str, Any]) -> None:
        request_id = record.get("id")
        future = self._pending.pop(str(request_id), None) if request_id is not None else None
        if future is None:
            self._protocol_error(
                PiProtocolError(f"response for unknown request id {request_id!r}")
            )
            return
        self.stats.responses += 1
        if not future.done():
            future.set_result(record)

    def _handle_event(self, record: dict[str, Any]) -> None:
        self.stats.events += 1
        try:
            self._events.put_nowait(record)
        except asyncio.QueueFull:
            self.stats.dropped_events += 1
            self._protocol_error(
                PiProtocolError(
                    f"event queue overflow: dropped {record.get('type')!r} "
                    f"(the consumer is not keeping up)"
                )
            )

    def _protocol_error(self, error: PiProtocolError) -> None:
        self.stats.protocol_errors += 1
        self.protocol_errors.append(str(error))
        if self.on_protocol_error is not None:
            try:
                self.on_protocol_error(error)
            except Exception:  # pragma: no cover - a bad callback must not kill the reader
                pass

    async def _fail_pending(self, reason: str) -> None:
        for request_id, future in list(self._pending.items()):
            self._pending.pop(request_id, None)
            if not future.done():
                future.set_exception(PiTransportError(f"{reason} before response to {request_id}"))
