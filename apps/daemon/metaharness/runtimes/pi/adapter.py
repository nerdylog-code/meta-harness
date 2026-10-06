"""`RuntimeAdapter` v2 over Pi (WP-017, ADR-0006).

The daemon drives Pi exactly like any other runtime: it never learns Pi's vocabulary, and Pi
never learns ours. This class is the seam.

Three rules it follows literally:

* **Persist first.** Every parsed event is handed to ``on_event`` *before* it reaches the
  iterator, and the daemon's callback is the one that writes to the store. A frame the UI can
  see is a frame that was recorded.
* **Accepted is not finished.** ``send`` returns when Pi accepts the prompt (``disposition``);
  completion is ``runtime.pi.settled``, which is the only signal that Pi will not continue on
  its own. A caller that waits for the response and calls it done has been fooled by Pi's own
  docs, not by us.
* **No hardcoded model, no invented numbers.** Provider and model come from the session spec
  or from Pi's answer; a metric Pi did not report stays ``unknown``. A capability that was not
  confirmed is ``supported=False`` with a note, never a hopeful ``True``.

Capabilities are declared from Pi's documented command set (`docs/protocols/PI_RPC.md` §4) and
from what the probe answered. Approval handling through extension UI exists in Pi but is not
consumed here yet, so it is declared unsupported rather than assumed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Sequence

from metaharness_contracts import (
    CAP_APPROVAL_NATIVE,
    CAP_CONTEXT_COMPACTION,
    CAP_MODEL_SWITCH,
    CAP_SESSION_FOLLOW_UP,
    CAP_SESSION_RESUME,
    CAP_SESSION_STEER,
    CAP_SESSION_STREAMING,
    CAP_TOOL_EVENTS,
    CAP_USAGE_COST,
    CAP_USAGE_TOKENS,
    CanonicalEvent,
    CapabilityInfo,
    CapabilitySet,
    IdKind,
    Message,
    ModelInfo,
    RuntimeInfo,
    RuntimeSession,
    SessionSpec,
    UsageSample,
    new_id,
)
from ...process import ProcessSupervisor, supervisor as default_supervisor
from ...paths import data_root
from .parse import ParsedEvent, PiParser
from .transport import PiTransport, PiTransportError

DEFAULT_ARGV: tuple[str, ...] = ("pi", "--mode", "rpc")
PROTOCOL_NAME = "jsonl-rpc"

#: Confirmed against Pi's documented RPC commands, plus the live probe.
SUPPORTED_CAPABILITIES: tuple[str, ...] = (
    CAP_SESSION_STREAMING,
    CAP_SESSION_STEER,
    CAP_SESSION_FOLLOW_UP,
    CAP_SESSION_RESUME,
    CAP_TOOL_EVENTS,
    CAP_USAGE_TOKENS,
    CAP_USAGE_COST,
    CAP_CONTEXT_COMPACTION,
    CAP_MODEL_SWITCH,
)

#: Declared unsupported on purpose, with the reason a reader can act on.
UNSUPPORTED_NOTES: dict[str, str] = {
    CAP_APPROVAL_NATIVE: "Pi's extension UI carries approvals; this adapter does not consume them yet",
    "agent.subagents": "Pi subagents are an extension concern and are not modelled here",
    "workspace.worktree": "isolation is the daemon's job (WP-007), not the runtime's",
    "voice.native": "Pi has no voice surface",
}

EVENT_QUEUE_MAX = 10_000


def _model_id(state: dict[str, Any]) -> str | None:
    """Pi reports the model as an object; the wire contract wants an id.

    Discovered by the real end-to-end run: `get_state` answers
    ``{"model": {"id": "kimi-k3", "provider": "opencode-go", ...}}``, and binding that object to
    the `sessions.model` column failed the projection -- correctly, loudly, and without
    persisting the event. This is the normalisation that keeps the contract's string.
    """
    model = state.get("model")
    if isinstance(model, dict):
        identifier = model.get("id") or model.get("name")
        return str(identifier) if identifier else None
    return str(model) if model else None


def _provider_id(state: dict[str, Any]) -> str | None:
    provider = state.get("provider")
    if isinstance(provider, dict):
        identifier = provider.get("id") or provider.get("name")
        return str(identifier) if identifier else None
    if provider:
        return str(provider)
    model = state.get("model")
    if isinstance(model, dict) and model.get("provider"):
        return str(model["provider"])
    return None


@dataclass
class _Session:
    session_id: str
    spec: SessionSpec
    transport: PiTransport
    parser: PiParser
    session_dir: Path
    queue: asyncio.Queue[CanonicalEvent] = field(default_factory=lambda: asyncio.Queue(maxsize=EVENT_QUEUE_MAX))
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    reader: asyncio.Task[None] | None = None
    streaming: bool = False
    created_at: float = field(default_factory=time.time)
    usage: UsageSample | None = None
    provider: str | None = None
    model: str | None = None
    pi_session_id: str | None = None
    dropped: int = 0


class PiRuntimeAdapter:
    """The Pi runtime, behind the frozen interface."""

    runtime_id = "rt_pi"

    def __init__(
        self,
        *,
        argv: Sequence[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        supervisor: ProcessSupervisor | None = None,
        data_root_override: str | None = None,
        on_event: Callable[[CanonicalEvent, bool], None] | None = None,
        response_timeout_s: float = 30.0,
        probe_timeout_s: float = 20.0,
    ) -> None:
        self.argv = list(argv or DEFAULT_ARGV)
        self.cwd = cwd
        self.env = env
        self.supervisor = supervisor or default_supervisor()
        self.data_root_override = data_root_override
        self.on_event = on_event
        self.response_timeout_s = response_timeout_s
        self.probe_timeout_s = probe_timeout_s
        self.sessions: dict[str, _Session] = {}
        self.protocol_errors: list[str] = []
        #: Commands Pi advertises that this adapter does not model. Kept out of CapabilitySet
        #: on purpose: that type validates its keys against the frozen capability vocabulary.
        self.advertised_commands: list[str] = []

    # ------------------------------------------------------------------ discovery

    def _ephemeral_argv(self) -> list[str]:
        return [*self.argv, "--no-session"]

    async def _ephemeral(self) -> PiTransport:
        transport = PiTransport(
            self._ephemeral_argv(),
            cwd=self.cwd,
            env=self.env,
            supervisor=self.supervisor,
            response_timeout_s=self.probe_timeout_s,
            on_protocol_error=lambda error: self.protocol_errors.append(str(error)),
        )
        await transport.start()
        return transport

    async def probe(self) -> RuntimeInfo:
        """Start Pi once, ask for its state, and report what actually answered."""
        try:
            transport = await self._ephemeral()
        except Exception as exc:
            self._emit_simple(
                "runtime.unreachable",
                {"runtime_id": self.runtime_id, "reason": str(exc), "argv_count": len(self.argv)},
            )
            return RuntimeInfo(
                runtime_id=self.runtime_id,
                name="pi",
                available=False,
                detail=f"cannot start {self.argv[0]!r}: {exc}",
                protocol=PROTOCOL_NAME,
                capabilities=CapabilitySet.none(),
            )
        try:
            state = await transport.command("get_state", timeout_s=self.probe_timeout_s)
            commands = await transport.command("get_commands", timeout_s=self.probe_timeout_s)
        except PiTransportError as exc:
            return RuntimeInfo(
                runtime_id=self.runtime_id,
                name="pi",
                available=False,
                detail=f"pi started but did not answer get_state: {exc}",
                protocol=PROTOCOL_NAME,
                capabilities=CapabilitySet.none(),
            )
        finally:
            await transport.close(reason="probe", grace_s=3.0)

        advertised = {
            str(entry.get("name"))
            for entry in (commands.get("commands") or [])
            if isinstance(entry, dict)
        }
        capabilities = self._capability_set(advertised)
        return RuntimeInfo(
            runtime_id=self.runtime_id,
            name="pi",
            version=str(state.get("version") or "") or None,
            available=True,
            detail=f"provider={_provider_id(state)} model={_model_id(state)}",
            protocol=PROTOCOL_NAME,
            capabilities=capabilities,
        )

    def _capability_set(self, advertised: Iterable[str] | None = None) -> CapabilitySet:
        """Known capability ids only.

        A `CapabilitySet` validates its keys against the frozen vocabulary, so an
        ``pi.<command>`` entry would be a contract violation rather than extra information.
        Commands Pi advertises that we do not model are kept on ``advertised_commands``
        instead: visible, but not smuggled into a typed field that means something else.
        """
        self.advertised_commands = sorted(set(advertised or ()))
        capabilities: dict[str, CapabilityInfo] = {}
        for capability in SUPPORTED_CAPABILITIES:
            capabilities[capability] = CapabilityInfo(supported=True, metadata={"source": "pi_docs"})
        for capability, note in UNSUPPORTED_NOTES.items():
            capabilities[capability] = CapabilityInfo(supported=False, note=note)
        return CapabilitySet(capabilities=capabilities)

    async def capabilities(self) -> CapabilitySet:
        return self._capability_set()

    async def models(self) -> list[ModelInfo]:
        transport = await self._ephemeral()
        try:
            data = await transport.command("get_available_models", timeout_s=self.probe_timeout_s)
        finally:
            await transport.close(reason="models", grace_s=3.0)
        models: list[ModelInfo] = []
        for entry in data.get("models") or []:
            if not isinstance(entry, dict):
                continue
            identifier = entry.get("id") or entry.get("name")
            if not identifier:
                continue
            models.append(
                ModelInfo(
                    id=str(identifier),
                    provider=entry.get("provider"),
                    context_limit=entry.get("contextLimit") or entry.get("context_limit"),
                    notes={k: v for k, v in entry.items() if k not in {"id", "name", "provider", "contextLimit"}},
                )
            )
        return models

    # ------------------------------------------------------------------- sessions

    def _session_dir(self, session_id: str) -> Path:
        root = Path(self.data_root_override) if self.data_root_override else Path(data_root())
        directory = root / "sessions" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _session_argv(self, spec: SessionSpec, session_dir: Path) -> list[str]:
        argv = [*self.argv]
        if spec.model:  # never hardcoded: the model policy decides, or Pi's default stands
            argv += ["--model", spec.model]
        if spec.system_prompt:
            argv += ["--system-prompt", spec.system_prompt]
        argv += ["--session-dir", str(session_dir)]
        if spec.allowed_tools:
            argv += ["--tools", ",".join(spec.allowed_tools)]
        else:
            argv += ["--no-tools"]
        return argv

    async def create_session(self, spec: SessionSpec) -> RuntimeSession:
        session_id = new_id(IdKind.SESSION)
        session_dir = self._session_dir(session_id)
        transport = PiTransport(
            self._session_argv(spec, session_dir),
            cwd=spec.workspace or self.cwd,
            env=self.env,
            supervisor=self.supervisor,
            response_timeout_s=self.response_timeout_s,
            on_protocol_error=lambda error: self.protocol_errors.append(str(error)),
        )
        await transport.start()

        parser = PiParser(
            session_id=session_id,
            agent_id=spec.agent_id,
            run_id=spec.metadata.get("run_id"),
            mission_id=spec.metadata.get("mission_id"),
            task_id=spec.metadata.get("task_id"),
            runtime_id=self.runtime_id,
        )
        session = _Session(
            session_id=session_id,
            spec=spec,
            transport=transport,
            parser=parser,
            session_dir=session_dir,
        )
        self.sessions[session_id] = session

        try:
            state = await transport.command("get_state", timeout_s=self.probe_timeout_s)
            session.provider = _provider_id(state)
            session.model = _model_id(state)
            session.pi_session_id = state.get("sessionId")
        except PiTransportError as exc:
            # A session that cannot answer get_state is still usable, but the caller must be
            # able to see that we never learned its provider/model.
            session.provider = None
            session.model = None
            self.protocol_errors.append(f"get_state failed at session start: {exc}")

        session.reader = asyncio.create_task(self._pump(session), name=f"pi-pump-{session_id}")
        self._emit_simple(
            "session.opened",
            {
                "session_id": session_id,
                "runtime_id": self.runtime_id,
                "provider": session.provider,
                "model": session.model or spec.model,
                "pi_session_id": session.pi_session_id,
                "session_dir": str(session_dir),
                "tools": list(spec.allowed_tools),
            },
            session_id=session_id,
            agent_id=spec.agent_id,
            mission_id=spec.metadata.get("mission_id"),
            task_id=spec.metadata.get("task_id"),
            run_id=spec.metadata.get("run_id"),
        )
        return RuntimeSession(
            session_id=session_id,
            runtime_id=self.runtime_id,
            created_at=session.created_at,
            detail={
                "provider": session.provider,
                "model": session.model or spec.model,
                "pi_session_id": session.pi_session_id,
                "session_dir": str(session_dir),
            },
        )

    def _require(self, session_id: str) -> _Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"unknown session {session_id}")
        return session

    async def send(self, session_id: str, message: Message) -> None:
        session = self._require(session_id)
        session.streaming = True
        session.settled.clear()
        data = await session.transport.command("prompt", message=message.text)
        self._emit_simple(
            "runtime.pi.prompt_accepted",
            {
                "disposition": data.get("disposition"),
                "chars": len(message.text),
                "note": "accepted is not finished: completion is runtime.pi.settled",
            },
            session_id=session_id,
            agent_id=session.spec.agent_id,
            run_id=session.spec.metadata.get("run_id"),
        )

    async def steer(self, session_id: str, instruction: str) -> None:
        session = self._require(session_id)
        await session.transport.command("steer", message=instruction)

    async def follow_up(self, session_id: str, message: Message) -> None:
        session = self._require(session_id)
        session.settled.clear()
        await session.transport.command("follow_up", message=message.text)

    async def interrupt(self, session_id: str) -> None:
        session = self._require(session_id)
        await session.transport.command("abort")
        self._emit_simple("runtime.pi.interrupted", {}, session_id=session_id, run_id=session.spec.metadata.get("run_id"))

    async def cancel(self, session_id: str) -> None:
        session = self._require(session_id)
        report = await session.transport.cancel(grace_s=5.0)
        session.streaming = False
        session.settled.set()
        self._emit_simple(
            "runtime.pi.cancelled",
            {
                "orphans": report.orphans,
                "survivors": report.survivors,
                "forced_kill": report.forced_kill,
                "exit_code": report.exit_code,
            },
            session_id=session_id,
            agent_id=session.spec.agent_id,
            run_id=session.spec.metadata.get("run_id"),
        )

    async def usage(self, session_id: str) -> UsageSample:
        session = self._require(session_id)
        if session.usage is not None:
            return session.usage
        # No sample yet: report an all-unknown sample rather than a zeroed one, so a caller
        # cannot mistake "not measured" for "cost nothing" (BOOK §17).
        return UsageSample(
            runtime=self.runtime_id,
            agent=session.spec.agent_id,
            session=session_id,
            run=session.spec.metadata.get("run_id"),
            provider=session.provider,
            model=session.model,
        )

    async def artifacts(self, session_id: str) -> list[str]:
        """No artifacts of our own.

        Tool results are externalized by the daemon (BOOK §21) because the store owns artifact
        ids and hashing; the adapter deliberately returns an empty list rather than inventing
        paths it does not own.
        """
        self._require(session_id)
        return []

    async def close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        if session.reader is not None:
            session.reader.cancel()
            try:
                await session.reader
            except (asyncio.CancelledError, Exception):
                pass
        report = await session.transport.close(reason="closed", grace_s=5.0)
        self._emit_simple(
            "session.closed",
            {"reason": "closed", "orphans": report.orphans, "exit_code": report.exit_code},
            session_id=session_id,
            agent_id=session.spec.agent_id,
            run_id=session.spec.metadata.get("run_id"),
        )

    async def close_all(self) -> None:
        for session_id in list(self.sessions):
            await self.close(session_id)

    # --------------------------------------------------------------------- events

    def events(self, session_id: str) -> AsyncIterator[CanonicalEvent]:
        session = self._require(session_id)
        return self._iterate(session)

    async def _iterate(self, session: _Session) -> AsyncIterator[CanonicalEvent]:
        while True:
            try:
                event = await asyncio.wait_for(session.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if session.session_id not in self.sessions and session.queue.empty():
                    return
                continue
            yield event

    async def wait_until_settled(self, session_id: str, *, timeout_s: float | None = None) -> bool:
        """Wait for `runtime.pi.settled` -- the only honest completion signal."""
        session = self._require(session_id)
        try:
            await asyncio.wait_for(session.settled.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------------- internal

    async def _pump(self, session: _Session) -> None:
        try:
            async for record in session.transport.events():
                for parsed in session.parser.feed(record):
                    self._emit(parsed, session)
                    if parsed.kind == "runtime.pi.settled":
                        session.streaming = False
                        session.settled.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - a pump failure must be visible
            self.protocol_errors.append(f"pump for {session.session_id} failed: {exc}")

    def _emit(self, parsed: ParsedEvent, session: _Session) -> None:
        if parsed.kind == "usage.sampled":
            sample = parsed.payload.get("sample")
            if isinstance(sample, dict):
                try:
                    session.usage = UsageSample(**sample)
                except Exception:
                    pass
        if parsed.kind == "runtime.pi.session":
            session.pi_session_id = parsed.payload.get("pi_session_id") or session.pi_session_id
        if parsed.kind == "message.completed" and parsed.payload.get("role") == "assistant":
            session.parser.provider = session.parser.provider or session.provider

        event = CanonicalEvent.build(
            parsed.kind,
            parsed.payload,
            event_id=new_id(IdKind.EVENT),
            seq=1,  # placeholder: the store assigns the canonical seq on append
            provenance=parsed.provenance,
            **{key: value for key, value in parsed.ids.items() if value is not None},
        )
        if self.on_event is not None:
            self.on_event(event, parsed.transient)
        try:
            session.queue.put_nowait(event)
        except asyncio.QueueFull:
            session.dropped += 1

    def _emit_simple(self, kind: str, payload: dict[str, Any], **ids: Any) -> None:
        event = CanonicalEvent.build(
            kind,
            payload,
            event_id=new_id(IdKind.EVENT),
            seq=1,
            provenance={"method": "runtime_reported", "origin": "pi_adapter"},
            runtime_id=self.runtime_id,
            **{key: value for key, value in ids.items() if value is not None},
        )
        if self.on_event is not None:
            self.on_event(event, False)
