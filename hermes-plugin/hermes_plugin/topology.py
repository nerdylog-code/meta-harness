"""Topology executor.

A topology is a small declarative document describing nodes (agents or
process steps) and edges between them. The executor walks the graph and
dispatches each step to the right engine adapter.

Implemented shapes (deliberately small):

  * ``solo``       — one node, no edges
  * ``sequence``   — linear chain
  * ``parallel``   — all nodes run concurrently, results merged
  * ``gate``       — node + validator + retry-with-feedback loop

Topologies are loaded from the topologies/ folder under the data dir; the
package ships built-ins at ``topologies/`` which are seeded into the data
dir on first bootstrap.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import engines, store

logger = logging.getLogger("meta-harness.topology")


@dataclass
class Node:
    id: str
    type: str  # agent | process | gate
    role: str | None = None
    engine: str | None = None
    model: str | None = None
    capabilities: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)


@dataclass
class Topology:
    id: str
    version: str
    kind: str  # solo | sequence | parallel | gate
    nodes: list[Node]
    edges: list[tuple[str, str]]
    failure: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)

    def successors(self, node_id: str) -> list[str]:
        return [tgt for src, tgt in self.edges if src == node_id]


_LOADED: dict[str, Topology] = {}
_LOCK = threading.Lock()


def _coerce_node(raw: dict) -> Node:
    return Node(
        id=raw["id"],
        type=raw.get("type", "agent"),
        role=raw.get("role"),
        engine=raw.get("engine"),
        model=raw.get("model"),
        capabilities=list(raw.get("capabilities") or []),
        config=dict(raw.get("config") or {}),
    )


def load_from_yaml(path: Path) -> Topology:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Accept either ``id:`` or ``name:`` as the topology id (the YAML in
    # topologies/ uses ``name:`` for human readability; the parser needs
    # ``id`` for indexing). Coercion keeps both readable.
    if "id" not in raw and "name" in raw:
        raw["id"] = raw["name"]
    nodes = [_coerce_node(n) for n in raw.get("nodes", [])]
    edges = [(e["from"], e["to"]) for e in raw.get("edges", [])]
    return Topology(
        id=raw["id"],
        version=str(raw.get("version", "1")),
        kind=raw.get("kind", "sequence"),
        nodes=nodes,
        edges=edges,
        failure=dict(raw.get("failure", {})),
        limits=dict(raw.get("limits", {})),
    )


def register(topology: Topology) -> None:
    with _LOCK:
        _LOADED[topology.id] = topology
    store.upsert_topology_state(topology.id, topology.version, "loaded")


def load_directory(directory: Path) -> int:
    """Load every *.yaml topology in a directory. Returns count loaded."""
    if not directory.exists():
        return 0
    n = 0
    for path in sorted(directory.glob("*.y*ml")):
        try:
            register(load_from_yaml(path))
            n += 1
        except Exception as exc:
            logger.warning("topology load failed (%s): %s", path, exc)
    return n


def get(topology_id: str) -> Topology | None:
    with _LOCK:
        return _LOADED.get(topology_id)


def list_topologies() -> list[dict]:
    with _LOCK:
        return [
            {"id": t.id, "version": t.version, "kind": t.kind,
             "nodes": [n.id for n in t.nodes]}
            for t in _LOADED.values()
        ]


def ensure_builtin(topologies_dir: Path) -> None:
    """Seed built-in topologies if they are missing from the data dir."""
    # ``hermes_plugin/topology.py`` lives at:
    #   <repo>/hermes-plugin/hermes_plugin/topology.py
    # parents[0] = hermes_plugin, parents[1] = hermes-plugin, parents[2] = repo.
    package_root = Path(__file__).resolve().parents[2]
    builtin = package_root / "topologies"
    if not builtin.exists():
        return
    topologies_dir.mkdir(parents=True, exist_ok=True)
    for src in builtin.glob("*.yaml"):
        dst = topologies_dir / src.name
        if not dst.exists():
            try:
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    run_id: str
    task: str
    context: dict
    topology: Topology
    engine_hint: str | None = None
    model_hint: str | None = None
    results: dict[str, Any] = field(default_factory=dict)


async def execute(ctx: RunContext) -> dict:
    """Run the topology and return a compact final result."""
    store.update_run(ctx.run_id, status="running", engine=ctx.engine_hint or "")
    store.append_event({
        "event": "run.started", "run_id": ctx.run_id,
        "topology": ctx.topology.id, "kind": ctx.topology.kind,
        "task": ctx.task[:512],
    })
    try:
        if ctx.topology.kind == "solo":
            return await _run_solo(ctx)
        if ctx.topology.kind == "sequence":
            return await _run_sequence(ctx)
        if ctx.topology.kind == "parallel":
            return await _run_parallel(ctx)
        if ctx.topology.kind == "gate":
            return await _run_gate(ctx)
        # Generic DAG fallback: do a topological order pass and sequence it.
        return await _run_sequence(ctx)
    except asyncio.CancelledError:
        store.update_run(ctx.run_id, status="cancelled")
        store.append_event({"event": "run.cancelled", "run_id": ctx.run_id})
        raise
    except Exception as exc:
        store.update_run(ctx.run_id, status="failed", error=str(exc))
        store.append_event({"event": "run.failed", "run_id": ctx.run_id,
                            "error": str(exc)})
        raise


def _engine_for(node: Node, ctx: RunContext) -> str:
    if node.engine:
        return node.engine
    if ctx.engine_hint:
        return ctx.engine_hint
    # Default: prefer hermes when available; pi when explicitly hinted.
    hermes = engines.get("hermes")
    if hermes and hermes.available():
        return "hermes"
    return ctx.engine_hint or "hermes"


async def _start_node(node: Node, ctx: RunContext, *, extra: dict | None = None) -> str:
    engine_name = _engine_for(node, ctx)
    engine = engines.get(engine_name)
    if engine is None:
        raise RuntimeError(f"engine {engine_name!r} not registered")
    if not engine.available():
        # Engine exists but is unavailable (e.g. missing Pi). Fail fast
        # with a meaningful message — the harness does not pretend the
        # work happened.
        store.append_event({
            "event": "agent.failed", "run_id": ctx.run_id,
            "agent_id": f"{ctx.run_id}:{node.id}",
            "engine": engine_name, "error": f"engine {engine_name} unavailable",
        })
        raise RuntimeError(f"engine {engine_name} unavailable")
    model = node.model or ctx.model_hint or engine.default_model()
    spec = {
        "role": node.role or "builder",
        "model": model,
        "task": ctx.task if not extra else (extra.get("task") or ctx.task),
        "system": (extra or {}).get("system") or "",
        "cwd": (extra or {}).get("cwd"),
    }
    handle = await engine.start(spec)
    store.upsert_agent(
        agent_id=handle.worker_id, run_id=ctx.run_id,
        role=handle.role, engine=handle.engine, model=model,
        character=None, state="starting",
    )
    store.append_event({
        "event": "agent.started", "run_id": ctx.run_id,
        "agent_id": handle.worker_id, "node_id": node.id,
        "role": handle.role, "engine": handle.engine, "model": model,
    })
    return handle.worker_id


async def _drain(engine_name: str, worker_id: str, run_id: str) -> dict:
    """Consume the worker stream and emit normalized events."""
    engine = engines.get(engine_name)
    assert engine is not None
    final: dict | None = None
    async for evt in engine.stream(worker_id):
        evt["run_id"] = run_id
        evt["agent_id"] = worker_id
        store.append_event(evt)
        # Update agent state based on event kind
        if evt.get("event") == "agent.thinking":
            store.update_agent(worker_id, state="thinking")
        elif evt.get("event") == "tool.started":
            store.update_agent(worker_id, state="working")
        elif evt.get("event") in ("worker.completed", "worker.failed",
                                   "worker.cancelled"):
            new_state = {
                "worker.completed": "completed",
                "worker.failed": "failed",
                "worker.cancelled": "cancelled",
            }[evt["event"]]
            store.update_agent(worker_id, state=new_state)
            final = evt
            break
    return final or {}


async def _run_solo(ctx: RunContext) -> dict:
    node = ctx.topology.nodes[0]
    wid = await _start_node(node, ctx)
    final = await _drain(_engine_for(node, ctx), wid, ctx.run_id)
    result = {
        "ok": final.get("event") == "worker.completed",
        "final_event": final,
        "node": node.id,
    }
    store.update_run(ctx.run_id, status="completed" if result["ok"] else "failed",
                     result=result)
    store.append_event({"event": "run.completed", "run_id": ctx.run_id,
                        "ok": result["ok"], "node": node.id})
    return result


async def _run_sequence(ctx: RunContext) -> dict:
    by_id = {n.id: n for n in ctx.topology.nodes}
    order = _topo_order(ctx.topology)
    last_result: dict = {"ok": True, "nodes": []}
    for node_id in order:
        node = by_id[node_id]
        # Build the per-node task — feed prior results as context.
        prior = []
        for prev_id, _ in ctx.topology.edges:
            if prev_id == node_id:
                continue
        prior_text = json.dumps(ctx.results, default=str)[:4096]
        task = ctx.task if not prior_text else f"{ctx.task}\n\n[prior]\n{prior_text}"
        wid = await _start_node(node, ctx, extra={"task": task})
        final = await _drain(_engine_for(node, ctx), wid, ctx.run_id)
        ctx.results[node_id] = final
        last_result["nodes"].append({"node": node_id, "final": final})
        if final.get("event") != "worker.completed":
            last_result["ok"] = False
            break
    store.update_run(ctx.run_id, status="completed" if last_result["ok"] else "failed",
                     result=last_result)
    store.append_event({"event": "run.completed", "run_id": ctx.run_id,
                        "ok": last_result["ok"]})
    return last_result


async def _run_parallel(ctx: RunContext) -> dict:
    by_id = {n.id: n for n in ctx.topology.nodes}
    tasks = []
    for node in ctx.topology.nodes:
        tasks.append(_execute_node(node, ctx))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    summary = {"ok": True, "nodes": []}
    for node, result in zip(ctx.topology.nodes, results):
        if isinstance(result, Exception):
            summary["nodes"].append({"node": node.id, "error": str(result)})
            summary["ok"] = False
        else:
            summary["nodes"].append({"node": node.id, "final": result})
            ctx.results[node.id] = result
            if result.get("event") != "worker.completed":
                summary["ok"] = False
    store.update_run(ctx.run_id, status="completed" if summary["ok"] else "failed",
                     result=summary)
    store.append_event({"event": "run.completed", "run_id": ctx.run_id,
                        "ok": summary["ok"]})
    return summary


async def _execute_node(node: Node, ctx: RunContext) -> dict:
    wid = await _start_node(node, ctx)
    return await _drain(_engine_for(node, ctx), wid, ctx.run_id)


async def _run_gate(ctx: RunContext) -> dict:
    """A gate topology has exactly two nodes: a builder and a validator.

    The builder runs. The validator runs against the builder's output.
    If the validator fails, the builder runs again with the failure as
    feedback, up to ``limits.retries`` (default 3).
    """
    if len(ctx.topology.nodes) != 2:
        # Fall back to sequence for malformed gate topologies.
        return await _run_sequence(ctx)
    builder, validator = ctx.topology.nodes
    max_retries = int(ctx.topology.limits.get("retries", 3))
    last: dict = {"ok": False}
    for attempt in range(max_retries + 1):
        feedback = ""
        if attempt > 0 and ctx.results.get(validator.id, {}).get("error"):
            feedback = f"\n\n[validator feedback]\n{ctx.results[validator.id].get('error')}"
        task = f"{ctx.task}{feedback}"
        wid = await _start_node(builder, ctx, extra={"task": task})
        builder_final = await _drain(_engine_for(builder, ctx), wid, ctx.run_id)
        ctx.results[builder.id] = builder_final

        v_wid = await _start_node(validator, ctx, extra={"task": _validator_task(ctx, builder_final)})
        v_final = await _drain(_engine_for(validator, ctx), v_wid, ctx.run_id)
        ctx.results[validator.id] = v_final

        passed = v_final.get("event") == "worker.completed"
        store.append_event({"event": "validation.completed", "run_id": ctx.run_id,
                            "passed": passed, "attempt": attempt,
                            "result": v_final.get("data") or v_final})
        if passed:
            last = {"ok": True, "attempts": attempt + 1, "builder": builder_final,
                    "validator": v_final}
            break
        last = {"ok": False, "attempts": attempt + 1,
                "builder": builder_final, "validator": v_final}
    store.update_run(ctx.run_id, status="completed" if last["ok"] else "failed",
                     result=last)
    store.append_event({"event": "run.completed", "run_id": ctx.run_id,
                        "ok": last["ok"]})
    return last


def _validator_task(ctx: RunContext, builder_final: dict) -> str:
    return (
        "You are the validator. The previous step produced the following "
        "output:\n\n"
        f"{json.dumps(builder_final, default=str)[:6000]}\n\n"
        "Verify it satisfies the original task. Return a JSON object with "
        "fields: pass (bool), reason (string), evidence (string)."
    )


def _topo_order(topology: Topology) -> list[str]:
    """Return a topological order of nodes. Stable across runs."""
    # Kahn's algorithm; we keep insertion order on tie-break.
    indegree = {n.id: 0 for n in topology.nodes}
    for src, tgt in topology.edges:
        if tgt in indegree:
            indegree[tgt] += 1
    queue = [nid for nid, deg in indegree.items() if deg == 0]
    order: list[str] = []
    edges = [e for e in topology.edges]
    while queue:
        n = queue.pop(0)
        order.append(n)
        for src, tgt in edges:
            if src == n:
                indegree[tgt] -= 1
                if indegree[tgt] == 0:
                    queue.append(tgt)
    if len(order) != len(topology.nodes):
        # Cycle or disconnected — best-effort: append remaining in declaration order.
        declared = [n.id for n in topology.nodes]
        for nid in declared:
            if nid not in order:
                order.append(nid)
    return order


def reset() -> None:
    with _LOCK:
        _LOADED.clear()