"""`RuntimeAdapter` v2 over Hermes' ACP surface (M2, ADR-0016).

The same seam the Pi adapter is, for a different protocol. What differs is what the protocol
actually does, and the adapter is where those differences are allowed to show instead of being
smoothed over:

* **ACP answers once, at the end of a turn.** Pi accepts a prompt and settles later; here the
  `session/prompt` response *is* the completion. So `send()` writes the request and returns, a
  background task awaits the answer, and completion is still an event (`runtime.hermes.settled`)
  rather than a return value — otherwise the HTTP call would block for minutes and the API's
  shape would depend on which runtime answered.
* **The turn result carries the usage.** `{inputTokens, outputTokens, cachedReadTokens,
  thoughtTokens, totalTokens}`, provider-reported. There is **no cost** in ACP's usage, so cost
  stays unknown rather than estimated from a catalogue we did not read.
* **`usage_update` is context pressure, not usage.** `{size, used}` is emitted as
  `runtime.hermes.context_pressure`.
* **Tool allowlists are not a thing ACP offers.** No parameter restricts which tools the agent may
  call; the permission model is a *mode* (`default` asks before edits, `accept_edits`, `dont_ask`).
  A session therefore records `tools_enforced: false` with that reason instead of pretending the
  requested list was applied.
* **Steering and follow-ups were never observed**, so `steer`/`follow_up` raise
  `UnsupportedCapability` rather than being invented from the specification.

Everything declared in the capability set is either observed in a capture or explicitly marked
unsupported with a note. See `docs/protocols/HERMES_ACP.md`, including its "what was not
established" section.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Sequence

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
    UnsupportedCapability,
    UsageSample,
    new_id,
)
from ...process import ProcessSupervisor, supervisor as default_supervisor
from .parse import AcpParser, ParsedEvent
from .transport import AcpTransport, AcpTransportError

DEFAULT_ARGV: tuple[str, ...] = ("hermes", "acp")
PROTOCOL_NAME = "acp-jsonrpc"

#: Capabilities the captures established.
SUPPORTED_CAPABILITIES: tuple[str, ...] = (
    CAP_SESSION_STREAMING,  # agent_message_chunk / agent_thought_chunk
    CAP_TOOL_EVENTS,  # tool_call + tool_call_update
    CAP_USAGE_TOKENS,  # usage in the session/prompt response
)

#: Declared unsupported on purpose, each with a reason a reader can act on.
UNSUPPORTED_NOTES: dict[str, str] = {
    CAP_APPROVAL_NATIVE: "no approval request was observed; Hermes advertises permission modes, not per-tool approvals",
    CAP_USAGE_COST: "ACP's usage carries tokens only -- no cost field was observed, so cost stays unknown",
    CAP_SESSION_STEER: "no steer method was observed in the ACP surface",
    CAP_SESSION_FOLLOW_UP: "no follow-up method was observed; a second session/prompt is the only path seen",
    CAP_SESSION_RESUME: "advertised by the handshake (loadSession) but never exercised by this adapter",
    CAP_CONTEXT_COMPACTION: "the agent reports its own compressionDepth, but no command to trigger compaction was observed",
    CAP_MODEL_SWITCH: "session/new lists the available models; switching one mid-session was not observed",
    "agent.subagents": "not part of the observed ACP surface",
    "workspace.worktree": "isolation is the daemon's job, not the runtime's",
    "voice.native": "no voice surface was observed",
}

EVENT_QUEUE_MAX = 10_000


@dataclass
class _Session:
    session_id: str
    spec: SessionSpec
    transport: AcpTransport
    parser: AcpParser
    queue: asyncio.Queue[CanonicalEvent] = field(default_factory=lambda: asyncio.Queue(maxsize=EVENT_QUEUE_MAX))
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    reader: asyncio.Task[None] | None = None
    turn: asyncio.Task[None] | None = None
    streaming: bool = False
    created_at: float = field(default_factory=time.time)
    usage: UsageSample | None = None
    provider: str | None = None
    model: str | None = None
    acp_session_id: str | None = None
    modes: list[str] = field(default_factory=list)
    dropped: int = 0


class HermesRuntimeAdapter:
    """Hermes over ACP, behind the frozen interface."""

    runtime_id = "rt_hermes"

    def __init__(
        self,
        *,
        argv: Sequence[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        supervisor: ProcessSupervisor | None = None,
        on_event: Callable[[CanonicalEvent, bool], None] | None = None,
        # A turn's duration belongs to the runtime, not to us: a real Hermes turn that consumed a
        # capsule made 23 tool calls and was still working at 120s, which is when the first
        # end-to-end run killed it with this timeout. This is a safety net against a hung process,
        # not a policy about how long thinking may take.
        response_timeout_s: float = 900.0,
        probe_timeout_s: float = 60.0,
    ) -> None:
        self.argv = list(argv or DEFAULT_ARGV)
        self.cwd = cwd
        self.env = env
        self.supervisor = supervisor or default_supervisor()
        self.on_event = on_event
        self.response_timeout_s = response_timeout_s
        self.probe_timeout_s = probe_timeout_s
        self.sessions: dict[str, _Session] = {}
        #: The execution environment of the session currently being created (M3). The adapter only
        #: forwards it; it never inspects the sandbox.
        self.environment: Any | None = None
        self.protocol_errors: list[str] = []
        self.advertised_commands: list[str] = []
        #: The handshake's own account of itself, kept verbatim so `probe()` can report it.
        self.handshake: dict[str, Any] = {}

    # ------------------------------------------------------------------ discovery

    async def _open(self, environment: Any | None = None) -> AcpTransport:
        """The environment wraps the argv; the adapter never learns what wrapped it."""
        argv = list(self.argv)
        cwd = self.cwd
        env = self.env
        if environment is not None:
            argv = environment.wrap(argv)
            cwd = environment.plan.cwd
            env = {**(self.env or {}), **environment.plan.env}
        transport = AcpTransport(
            argv,
            cwd=cwd,
            env=env,
            supervisor=self.supervisor,
            response_timeout_s=self.response_timeout_s,
            on_protocol_error=lambda error: self.protocol_errors.append(str(error)),
        )
        await transport.start()
        return transport

    async def _initialize(self, transport: AcpTransport) -> dict[str, Any]:
        result = await transport.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
            },
            timeout_s=self.probe_timeout_s,
        )
        self.handshake = result
        return result

    async def probe(self) -> RuntimeInfo:
        """Start Hermes, ask `initialize`, and report what answered — nothing else."""
        try:
            transport = await self._open()
        except Exception as exc:
            self._emit_simple(
                "runtime.unreachable",
                {"runtime_id": self.runtime_id, "reason": str(exc), "argv_count": len(self.argv)},
            )
            return RuntimeInfo(
                runtime_id=self.runtime_id,
                name="hermes",
                available=False,
                detail=f"cannot start {self.argv[0]!r}: {exc}",
                protocol=PROTOCOL_NAME,
                capabilities=CapabilitySet.none(),
            )
        try:
            result = await self._initialize(transport)
        except AcpTransportError as exc:
            return RuntimeInfo(
                runtime_id=self.runtime_id,
                name="hermes",
                available=False,
                detail=f"hermes started but did not answer initialize: {exc}",
                protocol=PROTOCOL_NAME,
                capabilities=CapabilitySet.none(),
            )
        finally:
            await transport.close(reason="probe", grace_s=3.0)

        info = result.get("agentInfo")
        agent = info if isinstance(info, dict) else {}
        return RuntimeInfo(
            runtime_id=self.runtime_id,
            name=str(agent.get("name") or "hermes"),
            version=str(agent.get("version") or "") or None,
            available=True,
            detail=f"protocol={result.get('protocolVersion')} agent={agent.get('name')}",
            protocol=PROTOCOL_NAME,
            capabilities=self._capability_set(),
        )

    def _capability_set(self) -> CapabilitySet:
        capabilities: dict[str, CapabilityInfo] = {}
        for capability in SUPPORTED_CAPABILITIES:
            capabilities[capability] = CapabilityInfo(supported=True, metadata={"source": "acp_capture"})
        for capability, note in UNSUPPORTED_NOTES.items():
            capabilities[capability] = CapabilityInfo(supported=False, note=note)
        return CapabilitySet(capabilities=capabilities)

    async def capabilities(self) -> CapabilitySet:
        return self._capability_set()

    async def models(self) -> list[ModelInfo]:
        """ACP lists models in the `session/new` result, so a session has to exist to ask."""
        transport = await self._open()
        try:
            await self._initialize(transport)
            result = await transport.request(
                "session/new", {"cwd": self.cwd or ".", "mcpServers": []}, timeout_s=self.probe_timeout_s
            )
        except AcpTransportError as exc:
            self.protocol_errors.append(f"models() could not open a session: {exc}")
            return []
        finally:
            await transport.close(reason="models", grace_s=3.0)

        raw_models = result.get("models")
        models_block = raw_models if isinstance(raw_models, dict) else {}
        entries = models_block.get("availableModels") or []
        models: list[ModelInfo] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identifier = entry.get("modelId") or entry.get("id")
            if not identifier:
                continue
            models.append(
                ModelInfo(
                    id=str(identifier),
                    provider=entry.get("provider") or _provider_of(entry.get("description")),
                    notes={k: v for k, v in entry.items() if k not in {"modelId", "id", "name", "description"}},
                )
            )
        return models

    # ------------------------------------------------------------------- sessions

    async def create_session(
        self, spec: SessionSpec, environment: Any | None = None
    ) -> RuntimeSession:
        session_id = new_id(IdKind.SESSION)
        self.environment = environment
        transport = await self._open(environment)
        await self._initialize(transport)
        try:
            created = await transport.request(
                "session/new",
                {"cwd": spec.workspace or self.cwd or ".", "mcpServers": []},
                timeout_s=self.probe_timeout_s,
            )
        except AcpTransportError as exc:
            await transport.close(reason="session/new failed", grace_s=3.0)
            raise

        parser = AcpParser(
            session_id=session_id,
            agent_id=spec.agent_id,
            run_id=spec.metadata.get("run_id"),
            mission_id=spec.metadata.get("mission_id"),
            task_id=spec.metadata.get("task_id"),
            runtime_id=self.runtime_id,
        )
        raw_modes = created.get("modes")
        modes_block = raw_modes if isinstance(raw_modes, dict) else {}
        raw_models = created.get("models")
        models_block = raw_models if isinstance(raw_models, dict) else {}
        session = _Session(
            session_id=session_id,
            spec=spec,
            transport=transport,
            parser=parser,
            acp_session_id=str(created.get("sessionId") or "") or None,
            modes=[str(entry.get("id")) for entry in (modes_block.get("availableModes") or []) if isinstance(entry, dict)],
        )
        # `session/new` reports `currentModelId` -- the model the agent is actually on. The
        # session records *that*, not the model the spec asked for: "requested != served" is a
        # rule this project already paid for, and ACP offers no observed way to select a model.
        current = models_block.get("currentModelId")
        served = str(current) if current else None
        session.model = served or spec.model
        entries = [entry for entry in (models_block.get("availableModels") or []) if isinstance(entry, dict)]
        matched = next((entry for entry in entries if entry.get("modelId") == session.model), None)
        session.provider = _provider_of((matched or {}).get("description"))
        mismatch = bool(spec.model and served and spec.model != served)
        parser.model = session.model
        parser.provider = session.provider
        self.sessions[session_id] = session
        session.reader = asyncio.create_task(self._pump(session), name=f"hermes-pump-{session_id}")

        self._emit_simple(
            "session.opened",
            {
                "session_id": session_id,
                "runtime_id": self.runtime_id,
                "provider": session.provider,
                "model": session.model,
                "acp_session_id": session.acp_session_id,
                "modes": session.modes,
                "requested_model": spec.model,
                "model_mismatch": mismatch,
                "model_note": (
                    "the runtime is on a different model than the policy asked for, and no method "
                    "to select one was observed in the ACP surface"
                    if mismatch
                    else None
                ),
                "tools": list(spec.allowed_tools),
                # The honest bit: ACP has no tool allowlist, so the request was NOT enforced.
                "tools_enforced": False,
                "tools_note": "ACP exposes permission modes, not a tool allowlist; the requested list is a record, not a restriction",
                "execution": environment.as_dict() if environment is not None else None,
            },
            session_id=session_id,
            agent_id=spec.agent_id,
            mission_id=spec.metadata.get("mission_id"),
            task_id=spec.metadata.get("task_id"),
            run_id=spec.metadata.get("run_id"),
        )
        detail: dict[str, Any] = {
            "provider": session.provider,
            "model": session.model,
            "acp_session_id": session.acp_session_id,
            "modes": session.modes,
            "tools_enforced": False,
        }
        if environment is not None:
            detail["isolation"] = environment.evidence.isolation.value
            detail["sandbox_provider"] = environment.plan.provider
            detail["filesystem_mode"] = environment.plan.filesystem_mode
        return RuntimeSession(
            session_id=session_id,
            runtime_id=self.runtime_id,
            created_at=session.created_at,
            detail=detail,
        )

    def _require(self, session_id: str) -> _Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"unknown session {session_id}")
        return session

    async def send(self, session_id: str, message: Message) -> None:
        """Write the prompt and return. Completion arrives as `runtime.hermes.settled`.

        The turn runs in the background on purpose: ACP answers at the end of the turn, and a
        `send()` that awaited it would make the HTTP call take minutes -- the API's behaviour must
        not depend on which runtime answered.
        """
        session = self._require(session_id)
        if session.acp_session_id is None:
            raise AcpTransportError(f"session {session_id} has no ACP session id")
        session.streaming = True
        session.settled.clear()
        if session.turn is not None and not session.turn.done():
            raise AcpTransportError("a turn is already running on this session")
        session.turn = asyncio.create_task(self._run_turn(session, message), name=f"hermes-turn-{session_id}")

    async def _run_turn(self, session: _Session, message: Message) -> None:
        try:
            result = await session.transport.request(
                "session/prompt",
                {
                    "sessionId": session.acp_session_id,
                    "prompt": [{"type": "text", "text": message.text}],
                },
                timeout_s=self.response_timeout_s,
            )
        except (AcpTransportError, asyncio.CancelledError) as exc:
            session.streaming = False
            session.settled.set()
            self._emit_simple(
                "runtime.hermes.error",
                {"message": str(exc) or "turn cancelled", "chars": len(message.text)},
                session_id=session.session_id,
                agent_id=session.spec.agent_id,
                run_id=session.spec.metadata.get("run_id"),
            )
            return
        for parsed in session.parser.turn_result(result):
            self._emit(parsed, session)
        session.streaming = False
        session.settled.set()

    async def steer(self, session_id: str, instruction: str) -> None:
        self._require(session_id)
        raise UnsupportedCapability(
            "hermes does not expose steering: no steer method was observed in its ACP surface "
            "(docs/protocols/HERMES_ACP.md)"
        )

    async def follow_up(self, session_id: str, message: Message) -> None:
        self._require(session_id)
        raise UnsupportedCapability(
            "hermes has no observed follow-up method; a second session/prompt is the only path seen"
        )

    async def interrupt(self, session_id: str) -> None:
        session = self._require(session_id)
        await session.transport.request("session/cancel", {"sessionId": session.acp_session_id}, timeout_s=15.0)
        self._emit_simple(
            "runtime.hermes.interrupted",
            {"note": "session/cancel was accepted; the kill and its orphan check still happen in cancel()"},
            session_id=session_id,
            agent_id=session.spec.agent_id,
            run_id=session.spec.metadata.get("run_id"),
        )

    async def cancel(self, session_id: str) -> None:
        session = self._require(session_id)
        if session.turn is not None and not session.turn.done():
            session.turn.cancel()
        report = await session.transport.cancel(grace_s=5.0)
        session.streaming = False
        session.settled.set()
        self._emit_simple(
            "runtime.hermes.cancelled",
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
        return UsageSample(
            runtime=self.runtime_id,
            agent=session.spec.agent_id,
            session=session_id,
            run=session.spec.metadata.get("run_id"),
            provider=session.provider,
            model=session.model,
        )

    async def artifacts(self, session_id: str) -> list[str]:
        self._require(session_id)
        return []

    async def close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        if session.turn is not None and not session.turn.done():
            session.turn.cancel()
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
        session = self._require(session_id)
        try:
            await asyncio.wait_for(session.settled.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------------- internal

    async def _pump(self, session: _Session) -> None:
        try:
            async for frame in session.transport.events():
                for parsed in session.parser.feed(frame):
                    self._emit(parsed, session)
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
                except Exception as exc:
                    # Never silently: a rejected sample is a mapping bug, and swallowing it makes
                    # usage look "not measured" when it was in fact measured and mis-shaped.
                    self.protocol_errors.append(f"usage sample rejected: {exc}")
        if parsed.kind == "runtime.hermes.session_info":
            provenance = parsed.payload.get("session_provenance")
            if isinstance(provenance, dict) and provenance.get("acpSessionId"):
                session.acp_session_id = str(provenance["acpSessionId"])

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
            provenance={"method": "runtime_reported", "origin": "hermes_adapter"},
            runtime_id=self.runtime_id,
            **{key: value for key, value in ids.items() if value is not None},
        )
        if self.on_event is not None:
            self.on_event(event, False)


def _provider_of(text: Any) -> str | None:
    """The captures carry the provider inside a human sentence ("Provider: ChatGPT or Codex
    Subscription"). Reading it is a best effort, and it never invents one: no match means None."""
    if not isinstance(text, str):
        return None
    marker = "Provider:"
    if marker not in text:
        return None
    rest = text.split(marker, 1)[1].strip()
    # The captures use a middle dot for the model and a bullet for the "current" marker:
    # "Provider: OpenCode Go \u2022 current". Both are separators, and neither is part of a name.
    return re.split(r"[\u00b7\u2022]", rest)[0].strip() or None
