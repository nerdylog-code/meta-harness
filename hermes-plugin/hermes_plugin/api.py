"""FastAPI router for the dashboard plugin surface.

Mounted at ``/api/plugins/meta-harness/`` by the dashboard web server.

Endpoints are deliberately small. Every handler that returns internal state
goes through the reducer + redaction layers so a misbehaving renderer cannot
trick the backend into leaking provider keys.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from . import characters, engines, paths, plugin_lab, store, topology
from . import capabilities as caps

logger = logging.getLogger("meta-harness.api")

router = APIRouter()
_DATA_DIR: Path | None = None


def register_router(data_dir: Path) -> None:
    """Record the data dir for handlers that need filesystem access.

    The dashboard mounts the router unconditionally; handlers check
    ``_DATA_DIR`` before touching disk.
    """
    global _DATA_DIR
    _DATA_DIR = data_dir


def _data_dir() -> Path:
    if _DATA_DIR is None:
        return paths.data_dir()
    return _DATA_DIR


# ---------------------------------------------------------------------------
# Status / capabilities / plugins
# ---------------------------------------------------------------------------


@router.get("/status")
def get_status() -> dict:
    """Compact health + summary used by the status bar chip and the
    Command Center header."""
    hermes = engines.get("hermes")
    pi = engines.get("pi")
    return {
        "name": "meta-harness",
        "version": "0.1.0",
        "build": int(time.time()),
        "engines": {
            "hermes": {"available": hermes.available() if hermes else False,
                       "default_model": hermes.default_model() if hermes else None},
            "pi": {"available": pi.available() if pi else False,
                   "default_model": pi.default_model() if pi else None},
        },
        "active_pack": characters.active_id(),
        "data_dir": str(_data_dir()),
        "topologies": [t["id"] for t in topology.list_topologies()],
        "character_packs": [p["id"] for p in characters.list_packs()],
    }


@router.get("/capabilities")
def get_capabilities() -> dict:
    return {"capabilities": caps.list_capabilities(),
            "engines": engines.list_engines()}


@router.get("/plugins")
def get_plugins() -> dict:
    return {"plugins": plugin_lab.list_plugins()}


@router.get("/plugins/{plugin_id}")
def get_plugin(plugin_id: str) -> dict:
    plugin = plugin_lab.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(404, detail=f"plugin {plugin_id} not found")
    return {"plugin": plugin}


@router.post("/plugins/{plugin_id}/lab")
def post_plugin_lab(plugin_id: str, body: dict) -> dict:
    """Convenience endpoint equivalent to ``harness_plugin_lab`` tool."""
    body = dict(body)
    body["plugin_id"] = plugin_id
    from . import runtime
    return runtime.tool_harness_plugin_lab(body)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.post("/runs")
def post_runs(body: dict) -> dict:
    from . import runtime
    return runtime.tool_harness_run(body)


@router.get("/runs")
def get_runs(status: Optional[str] = Query(None), limit: int = 50) -> dict:
    return {"runs": store.list_runs(limit=limit, status=status)}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, detail=f"run {run_id} not found")
    return {"run": run, "agents": store.list_agents(run_id)}


@router.get("/runs/{run_id}/agents")
def get_run_agents(run_id: str) -> dict:
    return {"agents": store.list_agents(run_id)}


@router.get("/runs/{run_id}/events")
def get_run_events(run_id: str, since_id: int = 0,
                   limit: int = 200) -> dict:
    return {"events": store.list_events(run_id, since_id, limit)}


@router.get("/runs/{run_id}/graph")
def get_run_graph(run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, detail=f"run {run_id} not found")
    top = topology.get(run["topology"])
    if not top:
        return {"nodes": [], "edges": []}
    agents = {a["id"]: a for a in store.list_agents(run_id)}
    nodes = []
    for n in top.nodes:
        # Match the topology node id to one of the worker ids created for
        # the run. There may be multiple workers per node if retries ran.
        matches = [a for a in agents.values() if a["role"] == (n.role or "builder")]
        chosen = matches[0] if matches else None
        nodes.append({
            "id": n.id,
            "type": n.type,
            "role": n.role,
            "engine": chosen["engine"] if chosen else n.engine,
            "state": chosen["state"] if chosen else "pending",
        })
    edges = [{"from": s, "to": t} for s, t in top.edges]
    return {"nodes": nodes, "edges": edges, "topology": top.id, "kind": top.kind}


@router.post("/runs/{run_id}/cancel")
def post_run_cancel(run_id: str) -> dict:
    from . import runtime
    return runtime.tool_harness_cancel({"run_id": run_id})


@router.post("/runs/{run_id}/pause")
def post_run_pause(run_id: str) -> dict:
    """Pause is intentionally identical to cancel-with-recoverable for MVP.

    The harness keeps the run state; the operator can resume by re-running
    the same task under the same topology. This avoids implementing partial
    topology checkpointing before it is proven necessary.
    """
    store.update_run(run_id, status="paused")
    store.append_event({"event": "run.paused", "run_id": run_id})
    return {"run_id": run_id, "status": "paused"}


@router.post("/runs/{run_id}/resume")
def post_run_resume(run_id: str, body: dict | None = None) -> dict:
    """Resume a paused run by re-issuing it under the same topology."""
    body = body or {}
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, detail=f"run {run_id} not found")
    body.setdefault("task", run["task"])
    body.setdefault("topology", run["topology"])
    body.setdefault("engine", run.get("engine"))
    body.setdefault("model", run.get("model"))
    from . import runtime
    return runtime.tool_harness_run(body)


# ---------------------------------------------------------------------------
# Topologies
# ---------------------------------------------------------------------------


@router.get("/topologies")
def get_topologies() -> dict:
    return {"topologies": topology.list_topologies()}


@router.get("/topologies/{topology_id}")
def get_topology(topology_id: str) -> dict:
    t = topology.get(topology_id)
    if not t:
        raise HTTPException(404, detail=f"topology {topology_id} not found")
    return {"topology": {
        "id": t.id, "version": t.version, "kind": t.kind,
        "nodes": [{"id": n.id, "type": n.type, "role": n.role,
                   "engine": n.engine, "model": n.model,
                   "capabilities": n.capabilities}
                  for n in t.nodes],
        "edges": [{"from": s, "to": t_} for s, t_ in t.edges],
        "failure": t.failure, "limits": t.limits,
    }}


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------


@router.get("/character-packs")
def get_character_packs() -> dict:
    return {"packs": characters.list_packs(), "active": characters.active_id()}


@router.get("/character-packs/{pack_id}")
def get_character_pack(pack_id: str) -> dict:
    pack = characters.get_pack_with_assets(pack_id)
    if not pack:
        raise HTTPException(404, detail=f"pack {pack_id} not found")
    return {"pack": pack}


@router.post("/character-packs/reload")
def post_character_packs_reload() -> dict:
    characters.seed_builtins()
    loaded = characters.scan(user_dir=paths.character_packs_dir())
    return {"loaded": loaded, "packs": characters.list_packs()}


@router.post("/character-packs/{pack_id}/activate")
def post_activate_pack(pack_id: str) -> dict:
    if not characters.set_active(pack_id):
        raise HTTPException(404, detail=f"pack {pack_id} not found")
    return {"active": pack_id}


@router.get("/character-packs/{pack_id}/assets/{name}")
def get_character_asset(pack_id: str, name: str):
    """Serve one asset file. Path traversal is rejected explicitly."""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, detail="invalid asset name")
    pack_dir = paths.character_packs_dir() / pack_id
    target = (pack_dir / "assets" / name).resolve()
    if not str(target).startswith(str(pack_dir.resolve())):
        raise HTTPException(400, detail="path escape")
    if not target.exists():
        raise HTTPException(404, detail="asset not found")
    return FileResponse(str(target))


@router.post("/character-assignments")
def post_character_assignment(body: dict) -> dict:
    agent_id = body.get("agent_id")
    pack = body.get("pack")
    character = body.get("character")
    if not (agent_id and pack and character):
        raise HTTPException(400, detail="agent_id, pack, character required")
    if not characters.get(pack):
        raise HTTPException(404, detail=f"pack {pack} not found")
    store.assign_character(agent_id, pack, character)
    return {"agent_id": agent_id, "pack": pack, "character": character}


@router.get("/character-assignments")
def get_character_assignments() -> dict:
    return {"assignments": store.list_assignments()}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> dict:
    art = store.get_artifact(artifact_id)
    if not art:
        raise HTTPException(404, detail="artifact not found")
    return {"artifact": art}


@router.get("/artifacts/{artifact_id}/content")
def get_artifact_content(artifact_id: str):
    art = store.get_artifact(artifact_id)
    if not art or not art.get("path"):
        raise HTTPException(404, detail="artifact not found")
    target = Path(art["path"])
    if not target.exists():
        raise HTTPException(404, detail="artifact file missing")
    return FileResponse(str(target))


# ---------------------------------------------------------------------------
# Live events — WebSocket with polling fallback
# ---------------------------------------------------------------------------


@router.websocket("/runs/{run_id}/events/ws")
async def events_ws(ws: WebSocket, run_id: str):
    """Live event stream for one run.

    Polling fallback is always available via GET /runs/{run_id}/events; the
    WebSocket is just an accelerator. A WebSocket failure (e.g. remote/OAuth
    gateway that does not allow WS upgrades) must never break the rest of
    the dashboard.
    """
    await ws.accept()
    try:
        last_id = 0
        while True:
            events = store.list_events(run_id, since_id=last_id, limit=200)
            for evt in events:
                await ws.send_json(evt)
                last_id = max(last_id, evt["id"])
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.warning("events ws closed (%s): %s", run_id, exc)
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Plugin Lab (extra HTTP convenience)
# ---------------------------------------------------------------------------


@router.post("/plugin-lab/create")
def post_plugin_lab_create(body: dict) -> dict:
    from . import runtime
    return runtime.tool_harness_plugin_lab({"action": "create", **body})


@router.post("/plugin-lab/validate")
def post_plugin_lab_validate(body: dict) -> dict:
    from . import runtime
    return runtime.tool_harness_plugin_lab({"action": "validate", **body})


@router.post("/plugin-lab/activate")
def post_plugin_lab_activate(body: dict) -> dict:
    from . import runtime
    return runtime.tool_harness_plugin_lab({"action": "activate", **body})


@router.post("/plugin-lab/rollback")
def post_plugin_lab_rollback(body: dict) -> dict:
    from . import runtime
    return runtime.tool_harness_plugin_lab({"action": "rollback", **body})