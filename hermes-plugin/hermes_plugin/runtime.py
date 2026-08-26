"""Bootstrap, hook adapters, and model-facing tool handlers.

This module wires everything together:

  * opens the SQLite store
  * registers engines, capabilities, character packs, topologies
  * maps host hook events to normalized Meta-Harness events
  * implements the model-facing tools (harness_inspect, harness_run, ...)

The host's ``ctx`` object is opaque to us; we read config via
``ctx.get_config`` and call ``ctx.register_hook`` / ``ctx.register_tool``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import (
    capabilities as caps,
    characters as chars,
    engines,
    paths,
    plugin_lab,
    redaction,
    store,
    topology,
)

logger = logging.getLogger("meta-harness.runtime")

_BOOTSTRAPPED = False
_LOCK = threading.Lock()
_ACTIVE_RUNS: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def bootstrap(*, data_dir: Path, ctx: Any = None) -> None:
    """Initialise the runtime. Idempotent."""
    global _BOOTSTRAPPED
    with _LOCK:
        if _BOOTSTRAPPED:
            return
        _BOOTSTRAPPED = True

    data_dir.mkdir(parents=True, exist_ok=True)
    store.init(data_dir / "harness.db")

    # Engines first (capabilities depend on them).
    engines.reset()
    engines.register(engines.HermesEngine())
    engines.register(engines.PiEngine())

    # Capabilities — register defaults. Real provider registration is
    # deterministic: which engine handles which capability is fixed for
    # the MVP. Advanced routing can come later.
    caps.reset()
    caps.register("engine.hermes", "hermes-native",
                  description="Hermes-native agent via host gateway",
                  trust="trusted-installed", available=True)
    caps.register("engine.pi", "pi-rpc",
                  description="Pi Coding Agent via JSON-RPC stdio",
                  trust="trusted-installed",
                  available=engines.get("pi").available())

    # Character packs.
    chars.reset()
    chars.seed_builtins()
    chars.scan(user_dir=paths.character_packs_dir(),
               builtin_dir=Path(__file__).resolve().parents[3] / "character-packs")

    # Topologies.
    topology.reset()
    topology.ensure_builtin(paths.topologies_dir())
    topology.load_directory(paths.topologies_dir())

    # Bind a sensible default capability set for run/agent validation.
    caps.register("validation.test", "local-subprocess",
                  description="Run a local subprocess and inspect exit code + output",
                  trust="system", available=True)
    caps.register("validation.command", "local-subprocess",
                  description="Run a shell command as a validation gate",
                  trust="system", available=True)
    caps.register("filesystem.read", "host-fs",
                  description="Read host filesystem files",
                  trust="system", available=True)
    caps.register("filesystem.write", "host-fs",
                  description="Write host filesystem files",
                  trust="system", available=True)
    caps.register("terminal.execute", "host-terminal",
                  description="Execute host terminal commands",
                  trust="system", available=True)
    caps.register("git.status", "host-git",
                  description="git status / diff / worktree",
                  trust="system", available=True)

    logger.info("meta-harness bootstrapped (data_dir=%s)", data_dir)


# ---------------------------------------------------------------------------
# Host hook adapters — translate host events into normalized Meta-Harness events
# ---------------------------------------------------------------------------


def hook_pre_tool_call(*args, **kwargs) -> None:
    """Mirror host tool calls into the event store.

    The host's hook signature is stable across versions; we accept both
    positional and keyword forms and pull whichever fields are present.
    """
    info = _extract(*args, **kwargs)
    store.append_event({
        "event": "tool.started",
        "run_id": info.get("session_id"),
        "agent_id": info.get("task_id"),
        "tool": info.get("tool_name"),
        "args": redaction.redact(info.get("args")),
    })


def hook_post_tool_call(*args, **kwargs) -> None:
    info = _extract(*args, **kwargs)
    store.append_event({
        "event": "tool.completed",
        "run_id": info.get("session_id"),
        "agent_id": info.get("task_id"),
        "tool": info.get("tool_name"),
        "duration_ms": info.get("duration_ms"),
        "ok": True,
    })


def hook_pre_api_request(*args, **kwargs) -> None:
    info = _extract(*args, **kwargs)
    store.append_event({
        "event": "model.request.started",
        "run_id": info.get("session_id"),
        "agent_id": info.get("task_id"),
        "model": info.get("model"),
    })


def hook_post_api_request(*args, **kwargs) -> None:
    info = _extract(*args, **kwargs)
    store.append_event({
        "event": "model.request.completed",
        "run_id": info.get("session_id"),
        "agent_id": info.get("task_id"),
        "model": info.get("model"),
        "ok": True,
    })


def hook_session_start(*args, **kwargs) -> None:
    info = _extract(*args, **kwargs)
    store.append_event({
        "event": "session.started",
        "run_id": info.get("session_id"),
        "data": info,
    })


def hook_session_end(*args, **kwargs) -> None:
    info = _extract(*args, **kwargs)
    store.append_event({
        "event": "session.ended",
        "run_id": info.get("session_id"),
        "data": info,
    })


def _extract(*args, **kwargs) -> dict:
    """Pull a dict from whatever shape the host's hook call uses."""
    if args and isinstance(args[0], dict):
        return args[0]
    if kwargs:
        return kwargs
    return {}


# ---------------------------------------------------------------------------
# Model-facing tools
# ---------------------------------------------------------------------------


def tool_harness_inspect(params: dict | None = None) -> dict:
    params = params or {}
    topic = params.get("topic", "capabilities")
    if topic == "capabilities":
        return {"capabilities": caps.list_capabilities(),
                "engines": engines.list_engines()}
    if topic == "engines":
        return {"engines": engines.list_engines()}
    if topic == "topologies":
        items = topology.list_topologies()
        if params.get("id"):
            t = topology.get(params["id"])
            return {"topology": t}
        return {"topologies": items}
    if topic == "runs":
        items = store.list_runs(limit=50)
        if params.get("id"):
            return {"run": store.get_run(params["id"])}
        return {"runs": items}
    if topic == "plugins":
        return {"plugins": plugin_lab.list_plugins()}
    if topic == "characters":
        return {"packs": chars.list_packs(),
                "active": chars.active_id()}
    return {"error": f"unknown topic: {topic}"}


def tool_harness_status(params: dict) -> dict:
    rid = params["run_id"]
    run = store.get_run(rid)
    if not run:
        return {"error": f"run {rid} not found"}
    agents = store.list_agents(rid)
    recent = store.list_events(rid, limit=20)
    return {"run": run, "agents": agents, "recent_events": recent}


def tool_harness_capabilities(params: dict) -> dict:
    return {"capabilities": caps.list_capabilities(),
            "engines": engines.list_engines()}


def tool_harness_plugins(params: dict) -> dict:
    return {"plugins": plugin_lab.list_plugins()}


def tool_harness_topology_list(params: dict) -> dict:
    return {"topologies": topology.list_topologies()}


def tool_harness_artifact_get(params: dict) -> dict:
    art = store.get_artifact(params["artifact_id"])
    if not art:
        return {"error": "not found"}
    return {"artifact": art}


def tool_harness_run(params: dict) -> dict:
    """Start a run. Returns the run id immediately; the topology runs in
    background. Use ``harness_status`` to poll progress."""
    task = params["task"]
    topology_id = params.get("topology") or "solo"
    engine_hint = params.get("engine")
    model = params.get("model")
    role = params.get("role")
    context = dict(params.get("context") or {})

    top = topology.get(topology_id)
    if not top:
        return {"error": f"unknown topology: {topology_id}"}

    rid = store.create_run(
        topology=topology_id,
        engine=engine_hint or "hermes",
        model=model,
        task=task,
        context={"role": role} | context,
    )
    store.append_event({
        "event": "run.created", "run_id": rid,
        "topology": topology_id, "task": task[:512],
    })

    # Override the entry node's role if requested.
    if role and top.nodes:
        top.nodes[0].role = role

    run_ctx = topology.RunContext(
        run_id=rid, task=task, context=context, topology=top,
        engine_hint=engine_hint, model_hint=model,
    )
    task_obj = asyncio.create_task(_safe_execute(run_ctx))
    _ACTIVE_RUNS[rid] = task_obj
    task_obj.add_done_callback(lambda _t, r=rid: _ACTIVE_RUNS.pop(r, None))
    return {"run_id": rid, "topology": topology_id, "status": "started"}


async def _safe_execute(run_ctx: topology.RunContext) -> dict:
    try:
        return await topology.execute(run_ctx)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # already reported inside execute()
        logger.warning("run %s crashed: %s", run_ctx.run_id, exc)
        return {"ok": False, "error": str(exc)}


def tool_harness_cancel(params: dict) -> dict:
    rid = params["run_id"]
    task_obj = _ACTIVE_RUNS.get(rid)
    if not task_obj:
        store.update_run(rid, status="cancelled")
        store.append_event({"event": "run.cancelled", "run_id": rid})
        return {"run_id": rid, "status": "cancelled", "note": "no live task"}
    task_obj.cancel()
    return {"run_id": rid, "status": "cancelling"}


def tool_harness_plugin_lab(params: dict) -> dict:
    action = params["action"]
    plugin_id = params.get("plugin_id") or ""
    version = params.get("version") or ""
    if action == "list":
        return {"plugins": plugin_lab.list_plugins()}
    if action == "inspect":
        return {"plugin": plugin_lab.get_plugin(plugin_id)}
    if action == "create":
        manifest = params.get("manifest") or {}
        code = params.get("code")
        return plugin_lab.create_version(plugin_id, manifest, code)
    if action == "validate":
        return plugin_lab.validate_version(plugin_id, version)
    if action == "activate":
        return plugin_lab.activate_experimental(plugin_id, version)
    if action == "rollback":
        return plugin_lab.rollback(plugin_id, version or None)
    return {"error": f"unknown action: {action}"}