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

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from metaharness.capsule import (
    CapsuleError,
    build_capsule,
    capsule_bytes,
    verify_capsule,
)
from metaharness_contracts.capsule import CapsulePhase
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
    store = _store(request)
    bus = _bus(request)
    rows = store.rows("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    session = rows[0]
    # Resolved by the session's own runtime, not by the default one: a Hermes session is not in the
    # Pi adapter's table, and "no such session" was the wrong answer for it.
    adapter = _adapter(request, session.get("runtime_id"))
    if session_id not in getattr(adapter, "sessions", {}):
        raise HTTPException(
            status_code=409,
            detail=f"session {session_id} is not live on {session.get('runtime_id')} (it may be archived)",
        )
    # The control plane records what it asked for, before it asks. Without this the log holds only
    # the answers, and "what did the user want?" has no answer in the system of record.
    bus.publish(
        "message.submitted",
        {"role": "user", "text": payload.text, "chars": len(payload.text), "session_id": session_id},
        method="measured",
        session_id=session_id,
        agent_id=session.get("agent_id"),
        mission_id=session.get("mission_id"),
        run_id=session.get("run_id"),
        runtime_id=session.get("runtime_id"),
    )
    await adapter.send(session_id, Message(text=payload.text))
    return {"session_id": session_id, "accepted": True, "chars": len(payload.text)}


@router.post("/v1/sessions/{session_id}/cancel")
async def cancel_session(session_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    bus = _bus(request)
    rows = store.rows("SELECT * FROM sessions WHERE id = ?", (session_id,))
    adapter = _adapter(request, rows[0].get("runtime_id") if rows else None)
    session = getattr(adapter, "sessions", {}).get(session_id)
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


# ------------------------------------------------------------- agent versioning


class AgentVersionIn(BaseModel):
    runtime_preferred: str | None = None
    model_primary: str | None = None
    reason: str | None = None


@router.post("/v1/agents/{agent_id}/versions")
def create_agent_version(agent_id: str, payload: AgentVersionIn, request: Request) -> dict[str, Any]:
    """R1 — a new configuration for the *same* identity (ADR-0002).

    Changing the runtime an agent prefers is a version, never a new agent. An unknown runtime id is
    a 404: a policy pointing at a runtime that is not configured would look fine in the UI and fail
    at the first session.
    """
    store = _store(request)
    bus = _bus(request)
    if not store.rows("SELECT 1 FROM agents WHERE id = ?", (agent_id,)):
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id}")
    if payload.runtime_preferred is not None:
        adapters = getattr(request.app.state, "adapters", None) or {}
        if payload.runtime_preferred not in adapters:
            raise HTTPException(status_code=404, detail=f"unknown runtime {payload.runtime_preferred}")
    current = store.rows("SELECT COALESCE(MAX(version), 0) AS v FROM agent_versions WHERE agent_id = ?", (agent_id,))
    version = int(current[0]["v"] if current else 0) + 1
    bus.publish(
        "agent.version.created",
        {
            "agent_id": agent_id,
            "version": version,
            "runtime_preferred": payload.runtime_preferred,
            "model_primary": payload.model_primary,
            "reason": payload.reason,
        },
        method="measured",
        agent_id=agent_id,
    )
    return {
        "agent_id": agent_id,
        "version": version,
        "runtime_preferred": payload.runtime_preferred,
        "model_primary": payload.model_primary,
    }


# ------------------------------------------------------------------- archival


@router.post("/v1/sessions/{session_id}/archive")
async def archive_session(session_id: str, request: Request) -> dict[str, Any]:
    """R1 — archival is a record, never a deletion.

    The session stops being live, its runtime process is released, and every event stays exactly
    where it was: `session.archived` is appended, so the history is what the next runtime can be
    pointed at. Nothing is removed from an append-only log, and nothing pretends to be.
    """
    store = _store(request)
    bus = _bus(request)
    rows = store.rows("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    session = rows[0]
    adapter = _adapter(request, session.get("runtime_id"))
    closed = False
    if session_id in getattr(adapter, "sessions", {}):
        try:
            await adapter.close(session_id)
            closed = True
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"could not close the runtime session: {exc}") from exc
    bus.publish(
        "session.archived",
        {
            "reason": "archived by request",
            "runtime_closed": closed,
            "note": "the events stay in the log: archival marks the session, it does not delete it",
        },
        method="measured",
        session_id=session_id,
        agent_id=session.get("agent_id"),
        mission_id=session.get("mission_id"),
        runtime_id=session.get("runtime_id"),
    )
    return {"session_id": session_id, "archived": True, "runtime_closed": closed}


# -------------------------------------------------------------------- capsules


class CapsuleIn(BaseModel):
    agent_id: str
    session_id: str | None = None
    mission_id: str | None = None
    phase: str | None = None


def _store_capsule(store, bus, draft, *, agent_id: str, session_id: str | None, mission_id: str | None, runtime_id: str | None) -> dict[str, Any]:
    """Verify, persist and record. The digest check happens against the bytes actually stored."""
    record = store.put_artifact(
        data=capsule_bytes(draft.capsule),
        mime="application/json",
        origin={"kind": "context_capsule", "agent_id": agent_id, "session_id": session_id},
        metadata={"phase": draft.capsule.current_phase.value, "objective": draft.capsule.objective},
        agent_id=agent_id,
        session_id=session_id,
        mission_id=mission_id,
    )
    report = verify_capsule(store, draft.capsule, expected_agent_id=agent_id, stored_digest=record.sha256)
    bus.publish(
        "capsule.created",
        {
            "capsule_id": record.id,
            "sha256": record.sha256,
            "size": record.size,
            "agent_id": agent_id,
            "session_id": session_id,
            "mission_id": mission_id,
            "runtime_id": runtime_id,
            "phase": draft.capsule.current_phase.value,
            "objective": draft.capsule.objective,
            "verified": report.ok,
            "verification": report.as_dict(),
            "evidence": draft.evidence,
        },
        method="measured",
        agent_id=agent_id,
        session_id=session_id,
        mission_id=mission_id,
        runtime_id=runtime_id,
    )
    return {
        "capsule_id": record.id,
        "sha256": record.sha256,
        "size": record.size,
        "verified": report.ok,
        "verification": report.as_dict(),
        "objective": draft.capsule.objective,
        "evidence": draft.evidence,
        "resume_instruction": draft.capsule.resume_instruction,
    }


@router.post("/v1/capsules")
def create_capsule(payload: CapsuleIn, request: Request) -> dict[str, Any]:
    """R1 — build a capsule from the log, verify it, and store it as a content-addressed artifact.

    A capsule that fails verification is still recorded: the attempt is a fact and the log is
    append-only. What it cannot do is claim success -- the response and the event both carry
    `verified: false` and the failing checks.
    """
    store = _store(request)
    bus = _bus(request)
    if not store.rows("SELECT 1 FROM agents WHERE id = ?", (payload.agent_id,)):
        raise HTTPException(status_code=404, detail=f"unknown agent {payload.agent_id}")
    try:
        phase = CapsulePhase(payload.phase) if payload.phase else CapsulePhase.NORMAL
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unknown capsule phase {payload.phase!r}") from exc
    session_id = payload.session_id
    runtime_id = None
    if session_id:
        rows = store.rows("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not rows:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        runtime_id = rows[0].get("runtime_id")
    try:
        draft = build_capsule(
            store, agent_id=payload.agent_id, session_id=session_id, mission_id=payload.mission_id, phase=phase
        )
    except CapsuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _store_capsule(
        store, bus, draft, agent_id=payload.agent_id, session_id=session_id, mission_id=payload.mission_id, runtime_id=runtime_id
    )


@router.get("/v1/capsules")
def list_capsules(request: Request, agent_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    store = _store(request)
    sql = "SELECT * FROM capsules"
    where, params = [], []
    if agent_id:
        where.append("agent_id = ?")
        params.append(agent_id)
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_ts DESC"
    rows = store.rows(sql, params)
    for row in rows:
        row["verified"] = bool(row.get("verified"))
        if row.get("verification"):
            row["verification"] = json.loads(row["verification"])
    return {"capsules": rows, "count": len(rows)}


@router.get("/v1/capsules/{capsule_id}")
def get_capsule(capsule_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    rows = store.rows("SELECT * FROM capsules WHERE id = ?", (capsule_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown capsule {capsule_id}")
    row = rows[0]
    row["verified"] = bool(row.get("verified"))
    # Deliberately unguarded: if the artifact that holds this capsule cannot be read, that is a
    # real error and it should be loud. An earlier version caught everything here and turned a
    # missing import into `body: null`, which reads as "the capsule has no content".
    body = json.loads(store.artifact_bytes(capsule_id).decode("utf-8"))
    return {"capsule": row, "body": body}


# ------------------------------------------------------------------- migration


class MigrateIn(BaseModel):
    to_runtime: str
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    mission_id: str | None = None
    reason: str | None = None


@router.post("/v1/agents/{agent_id}/migrate")
async def migrate_agent(agent_id: str, payload: MigrateIn, request: Request) -> dict[str, Any]:
    """R2 — move an agent to another runtime, carrying a verified capsule and nothing else.

    The order is the one the Architect approved, and it is not arbitrary:

    1. build the capsule from the log and **verify** it — an unverified capsule stops here, with a
       409, before anything has moved;
    2. archive the session being left (its process is released; its events stay);
    3. publish a new **version** of the same agent pointing at the new runtime;
    4. open a session on the target runtime and inject the capsule's resume instruction as its
       first message.

    The agent id never changes. Chat history is not copied: the transfer object is the capsule, and
    the old session remains visible as history.
    """
    store = _store(request)
    bus = _bus(request)
    if not store.rows("SELECT 1 FROM agents WHERE id = ?", (agent_id,)):
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id}")
    target = _adapter(request, payload.to_runtime)

    candidates = store.rows(
        "SELECT * FROM sessions WHERE agent_id = ? AND state != 'archived' ORDER BY created_ts DESC LIMIT 1",
        (agent_id,),
    )
    leaving = candidates[0] if candidates else None
    mission_id = payload.mission_id or (leaving or {}).get("mission_id")

    try:
        draft = build_capsule(
            store,
            agent_id=agent_id,
            session_id=leaving["id"] if leaving else None,
            mission_id=mission_id,
        )
    except CapsuleError as exc:
        raise HTTPException(status_code=409, detail={"error": "no_material_for_capsule", "detail": str(exc)}) from exc

    report = verify_capsule(store, draft.capsule, expected_agent_id=agent_id)
    if not report.ok:
        # Fail closed. A migration that travels on an unverified handoff is the failure mode this
        # whole work package exists to prevent.
        raise HTTPException(
            status_code=409,
            detail={"error": "capsule_unverified", "verification": report.as_dict()},
        )

    from_runtime = (leaving or {}).get("runtime_id")
    stored = _store_capsule(
        store, bus, draft, agent_id=agent_id, session_id=leaving["id"] if leaving else None,
        mission_id=mission_id, runtime_id=from_runtime,
    )
    if not stored["verified"]:
        raise HTTPException(
            status_code=409,
            detail={"error": "capsule_unverified_after_store", "verification": stored["verification"]},
        )

    # 2. archive what is being left
    archived = None
    if leaving:
        archived = await archive_session(leaving["id"], request)

    # 3. the same agent, a new version
    current = store.rows("SELECT COALESCE(MAX(version), 0) AS v FROM agent_versions WHERE agent_id = ?", (agent_id,))
    version = int(current[0]["v"] if current else 0) + 1
    bus.publish(
        "agent.version.created",
        {
            "agent_id": agent_id,
            "version": version,
            "runtime_preferred": payload.to_runtime,
            "model_primary": payload.model,
            "reason": payload.reason or f"runtime migration {from_runtime} -> {payload.to_runtime}",
            "capsule_id": stored["capsule_id"],
        },
        method="measured",
        agent_id=agent_id,
    )

    # 4. the new session, with the capsule injected as its first message
    run_id = new_id(IdKind.RUN)
    spec = SessionSpec(
        agent_id=agent_id,
        runtime_id=payload.to_runtime,
        model=payload.model,
        allowed_tools=list(payload.tools),
        metadata={
            "run_id": run_id,
            "mission_id": mission_id,
            "capsule_id": stored["capsule_id"],
            "migrated_from": leaving["id"] if leaving else None,
        },
    )
    try:
        session = await target.create_session(spec)
        bus.publish(
            "message.submitted",
            {
                "role": "user",
                "text": draft.capsule.resume_instruction,
                "chars": len(draft.capsule.resume_instruction),
                "session_id": session.session_id,
                "injected": "context_capsule",
                "capsule_id": stored["capsule_id"],
            },
            method="measured",
            session_id=session.session_id,
            agent_id=agent_id,
            mission_id=mission_id,
            runtime_id=payload.to_runtime,
        )
        await target.send(session.session_id, Message(role="user", text=draft.capsule.resume_instruction))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"the target runtime refused the session: {exc}") from exc

    bus.publish(
        "runtime.migrated",
        {
            "agent_id": agent_id,
            "from_runtime": from_runtime,
            "to_runtime": payload.to_runtime,
            "from_session": leaving["id"] if leaving else None,
            "to_session": session.session_id,
            "capsule_id": stored["capsule_id"],
            "capsule_verified": True,
            "version": version,
            "injected": "the capsule's resume instruction was sent as the first message",
        },
        method="measured",
        agent_id=agent_id,
        mission_id=mission_id,
        session_id=session.session_id,
        runtime_id=payload.to_runtime,
    )
    return {
        "agent_id": agent_id,
        "version": version,
        "from_runtime": from_runtime,
        "to_runtime": payload.to_runtime,
        "from_session": leaving["id"] if leaving else None,
        "to_session": session.session_id,
        "archived": archived,
        "capsule": stored,
        "runtime_session": session.model_dump(mode="json"),
    }
