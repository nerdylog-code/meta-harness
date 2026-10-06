"""The control-plane API: missions, agents, sessions, messages (WP-019).

Every mutation here is an event. The endpoints do not write projection rows -- the store does
that inside the same transaction -- which is what makes a restart reproducible: the roster and
the session history come back from the log, not from anything the API remembered.

Risk classification (BOOK §44), stated because the endpoints are mutations:

* ``POST /v1/missions``, ``POST /v1/agents`` -- **R1**, local and reversible: they record an
  intention.
* ``POST /v1/sessions`` -- **R2**, it starts a real process on a real runtime.
* ``POST /v1/sessions/{id}/messages`` -- **R2**, it spends provider credits. The model comes
  from the session's spec; nothing is hardcoded here.
* ``POST /v1/sessions/{id}/cancel`` -- **R2**, it ends work and is verified by an orphan check.

Nothing in this module pushes, deletes or escalates privileges, and none of it is reachable
from off-host (the loopback guard is the enforced property until the per-launch secret lands).
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from metaharness_contracts import IdKind, Message, SessionSpec, new_id

router = APIRouter()


class MissionIn(BaseModel):
    title: str
    objective: str = ""
    owner: str | None = None


class AgentIn(BaseModel):
    display_name: str
    role: str = "builder"
    runtime_preferred: str = "rt_pi"
    model_primary: str | None = None


class SessionIn(BaseModel):
    runtime_id: str | None = None  # which runtime serves it; default is the configured one
    agent_id: str
    mission_id: str | None = None
    task_id: str | None = None
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    system_prompt: str | None = None


class MessageIn(BaseModel):
    text: str


def _bus(request: Request):  # noqa: ANN202 - FastAPI dependency style
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        raise HTTPException(status_code=503, detail="the event plane is not running")
    return bus


def _store(request: Request):  # noqa: ANN202
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="the store is not open")
    return store


def _adapter(request: Request, runtime_id: str | None = None):  # noqa: ANN202
    """The adapter for a runtime id, or the default one when none was asked for.

    Runtime selection is explicit: an unknown id is a 404, never a silent fallback to another
    runtime, because "Nova is on Hermes now" must not be true only in the UI.
    """
    adapters = getattr(request.app.state, "adapters", None) or {}
    if runtime_id:
        chosen = adapters.get(runtime_id)
        if chosen is None:
            raise HTTPException(status_code=404, detail=f"unknown runtime {runtime_id}")
        return chosen
    adapter = getattr(request.app.state, "adapter", None) or next(iter(adapters.values()), None)
    if adapter is None:
        raise HTTPException(status_code=503, detail="no runtime adapter is configured")
    return adapter


# ------------------------------------------------------------------------- missions


@router.post("/v1/missions")
def create_mission(payload: MissionIn, request: Request) -> dict[str, Any]:
    mission_id = new_id(IdKind.MISSION)
    event = _bus(request).publish(
        "mission.created",
        {
            "title": payload.title,
            "objective": payload.objective,
            "owner": payload.owner,
            "status": "active",
        },
        method="measured",
        mission_id=mission_id,
    )
    return {"mission_id": mission_id, "seq": event.seq, "title": payload.title}


@router.get("/v1/missions")
def list_missions(request: Request) -> dict[str, Any]:
    rows = _store(request).rows("SELECT * FROM missions ORDER BY created_ts DESC")
    return {"missions": rows, "count": len(rows)}


# --------------------------------------------------------------------------- agents


@router.post("/v1/agents")
def create_agent(payload: AgentIn, request: Request) -> dict[str, Any]:
    bus = _bus(request)
    agent_id = new_id(IdKind.AGENT)
    created = bus.publish(
        "agent.created",
        {"display_name": payload.display_name, "role": payload.role},
        method="measured",
        agent_id=agent_id,
    )
    # Version 1 is the configuration this agent starts life with. Changing the runtime later
    # creates version 2 -- it never creates a second agent (ADR-0002).
    version = bus.publish(
        "agent.version.created",
        {
            "version": 1,
            "runtime_preferred": payload.runtime_preferred,
            "model_primary": payload.model_primary,
        },
        method="measured",
        agent_id=agent_id,
    )
    return {
        "agent_id": agent_id,
        "display_name": payload.display_name,
        "role": payload.role,
        "version": 1,
        "seq": {"created": created.seq, "version": version.seq},
    }


@router.get("/v1/agents")
def list_agents(request: Request) -> dict[str, Any]:
    store = _store(request)
    agents = store.rows("SELECT * FROM agents ORDER BY created_ts DESC")
    versions = store.rows("SELECT * FROM agent_versions ORDER BY agent_id, version")
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for row in versions:
        by_agent.setdefault(str(row["agent_id"]), []).append(row)
    for agent in agents:
        agent["versions"] = by_agent.get(str(agent["id"]), [])
    return {"agents": agents, "count": len(agents)}


# ------------------------------------------------------------------------- sessions


@router.post("/v1/sessions")
async def create_session(payload: SessionIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    bus = _bus(request)
    adapter = _adapter(request, payload.runtime_id)

    agent = store.rows("SELECT * FROM agents WHERE id = ?", (payload.agent_id,))
    if not agent:
        raise HTTPException(status_code=404, detail=f"unknown agent {payload.agent_id}")

    run_id = new_id(IdKind.RUN)
    spec = SessionSpec(
        agent_id=payload.agent_id,
        runtime_id=adapter.runtime_id,
        model=payload.model,
        allowed_tools=list(payload.tools),
        system_prompt=payload.system_prompt,
        metadata={
            "run_id": run_id,
            "mission_id": payload.mission_id,
            "task_id": payload.task_id,
        },
    )
    try:
        session = await adapter.create_session(spec)
    except Exception as exc:  # a runtime that cannot start is reported, never hidden
        bus.publish(
            "runtime.unreachable",
            {"runtime_id": adapter.runtime_id, "reason": str(exc), "agent_id": payload.agent_id},
            method="measured",
            agent_id=payload.agent_id,
            run_id=run_id,
        )
        raise HTTPException(status_code=502, detail=f"runtime {adapter.runtime_id} failed: {exc}") from exc

    pid = adapter.sessions[session.session_id].transport.pid
    bus.publish(
        "run.created",
        {"title": f"session {session.session_id}", "session_id": session.session_id},
        method="measured",
        run_id=run_id,
        agent_id=payload.agent_id,
        mission_id=payload.mission_id,
        task_id=payload.task_id,
        session_id=session.session_id,
        runtime_id=adapter.runtime_id,
    )
    bus.publish(
        "run.started",
        {"pid": pid, "runtime": adapter.runtime_id},
        method="measured",
        run_id=run_id,
        agent_id=payload.agent_id,
        mission_id=payload.mission_id,
        session_id=session.session_id,
        runtime_id=adapter.runtime_id,
    )
    return {
        "session_id": session.session_id,
        "run_id": run_id,
        "runtime_id": session.runtime_id,
        "detail": session.detail,
        "pid": pid,
    }


@router.get("/v1/sessions")
def list_sessions(request: Request, agent_id: str | None = None) -> dict[str, Any]:
    store = _store(request)
    if agent_id:
        rows = store.rows("SELECT * FROM sessions WHERE agent_id = ? ORDER BY created_ts DESC", (agent_id,))
    else:
        rows = store.rows("SELECT * FROM sessions ORDER BY created_ts DESC")
    return {"sessions": rows, "count": len(rows)}


@router.post("/v1/sessions/{session_id}/messages")
async def send_message(session_id: str, payload: MessageIn, request: Request) -> dict[str, Any]:
    adapter = _adapter(request)
    if session_id not in adapter.sessions:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    await adapter.send(session_id, Message(text=payload.text))
    return {"session_id": session_id, "accepted": True, "chars": len(payload.text)}


@router.post("/v1/sessions/{session_id}/cancel")
async def cancel_session(session_id: str, request: Request) -> dict[str, Any]:
    adapter = _adapter(request)
    bus = _bus(request)
    session = adapter.sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    await adapter.cancel(session_id)
    cancelled = [
        event
        for event in bus.recent(200)
        if event.kind == "runtime.pi.cancelled" and event.session_id == session_id
    ]
    # `KillReport.orphan_check` is True when the kill was *verified* to leave no survivors, so
    # the two readings must not be conflated: `orphan_check` is the proof, `orphans_left` is the
    # alarm. (The first real run showed the inverted name reporting "orphans left" for a clean
    # kill, which is exactly the kind of lie a field name can tell.)
    body = cancelled[-1].payload_body if cancelled else {}
    orphan_check = bool(body.get("orphans", True))
    bus.publish(
        "run.interrupted",
        {
            "reason": "cancelled by request",
            "orphaned": False,
            "orphan_check": orphan_check,
            "survivors": list(body.get("survivors") or []),
        },
        method="measured",
        run_id=session.spec.metadata.get("run_id"),
        agent_id=session.spec.agent_id,
        mission_id=session.spec.metadata.get("mission_id"),
        session_id=session_id,
        runtime_id=adapter.runtime_id,
    )
    return {
        "session_id": session_id,
        "cancelled": True,
        "orphan_check": orphan_check,
        "orphans_left": not orphan_check,
        "survivors": list(body.get("survivors") or []),
    }


@router.get("/v1/sessions/{session_id}/events")
def session_events(session_id: str, request: Request, limit: int = 200) -> dict[str, Any]:
    """The session's history, read from the log -- which is what survives a restart."""
    store = _store(request)
    limit = max(1, min(limit, 1000))
    events = store.events(session_id=session_id, limit=limit)
    return {
        "session_id": session_id,
        "events": [event.model_dump(mode="json") for event in events],
        "count": len(events),
    }


# -------------------------------------------------------------------------- runtime


@router.get("/v1/runtime")
async def runtime_status(request: Request) -> dict[str, Any]:
    adapter = _adapter(request)
    info = await adapter.probe()
    return {
        "runtime": info.model_dump(mode="json"),
        "advertised_commands": list(getattr(adapter, "advertised_commands", [])),
        "sessions": [
            {
                "session_id": session.session_id,
                "agent_id": session.spec.agent_id,
                "streaming": session.streaming,
                "settled": session.settled.is_set(),
                "dropped_events": session.dropped,
                "provider": session.provider,
                "model": session.model,
            }
            for session in adapter.sessions.values()
        ],
        "protocol_errors": adapter.protocol_errors[-10:],
        "checked_at": time.time(),
    }


# ------------------------------------------------------------------------ runtimes


@router.get("/v1/runtimes")
async def list_runtimes(request: Request) -> dict[str, Any]:
    """Every configured runtime, probed, with what it says it can do.

    A runtime that is not installed answers `available: false` with the reason: the UI must be
    able to say "Hermes is not here" instead of offering a button that cannot work.
    """
    adapters = getattr(request.app.state, "adapters", None) or {}
    default = getattr(request.app.state, "adapter", None)
    rows = []
    for runtime_id, adapter in adapters.items():
        try:
            info = await adapter.probe()
            rows.append(
                {
                    "runtime_id": runtime_id,
                    "is_default": adapter is default,
                    "available": info.available,
                    "name": info.name,
                    "version": info.version,
                    "protocol": info.protocol,
                    "detail": info.detail,
                    "capabilities": info.capabilities.model_dump(mode="json"),
                }
            )
        except Exception as exc:  # a probe that explodes is still an answer
            rows.append({"runtime_id": runtime_id, "is_default": adapter is default, "available": False, "detail": str(exc)})
    return {"runtimes": rows, "count": len(rows)}
