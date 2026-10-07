"""The Canvas projection: canonical state as typed semantic nodes and edges.

This is a **view**, like the Workboard, and it composes projections that already exist. There is no
`canvas_nodes` or `canvas_edges` table: a node is assembled from a task, an agent, a run, a workspace,
an approval or an artifact, and an edge is derived from canonical state -- a dependency row, an
assignment, a lease, a scope column. Nothing is inferred from proximity, and nothing is invented.

Two vocabularies matter here:

* A node **key** is a view identifier, not a public opaque id: `task:tsk_…`, `agent:agt_…`,
  `run:run_…`, `workspace:<task id>` (a workspace has no id of its own; its identity is the task and
  the repository). No new `IdKind` is introduced, and the frozen public namespace stays frozen.
* An edge **kind** says what the relationship is, so the browser can label it and so a mutation can be
  allowed or refused by kind rather than by guessing.

Only relationships the daemon can already change are marked mutable: a task dependency, and an
assignment. Everything operational -- a run, a lease, a workspace, an approval, an artifact -- is
read-only, because there is no canonical command that would create or destroy it from a canvas.

Performance is a design constraint, not an afterthought: this endpoint reads summaries. It does not
hash an artifact, run `git status`, probe a runtime or load a transcript, so a canvas of a hundred
nodes costs a hundred small indexed queries and nothing more.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from metaharness_contracts import TaskGraphError, TaskState, dependency_blockers, effective_lane, waves

from .board import enforcement_for

router = APIRouter(tags=["canvas"])

#: Edge kinds, and whether a canvas may create or remove them through an existing daemon command.
MUTABLE_EDGE_KINDS: frozenset[str] = frozenset({"depends_on", "assigned_to"})

#: What each node type means on screen. The canvas draws these differently, so the vocabulary is
#: fixed here rather than invented per component.
NODE_TYPES: tuple[str, ...] = ("task", "agent", "run", "session", "workspace", "approval", "artifact")


def _store(request: Request):
    return request.app.state.store


def node(
    key: str,
    entity_type: str,
    entity_id: str,
    label: str,
    state: str | None,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "label": label,
        "state": state,
        "summary": summary,
        "metadata": metadata or {},
    }


def edge(kind: str, source: str, target: str, label: str | None = None) -> dict[str, Any]:
    return {
        "key": f"{kind}:{source}->{target}",
        "kind": kind,
        "source": source,
        "target": target,
        "label": label or kind,
        "mutable": kind in MUTABLE_EDGE_KINDS,
    }


def _artifact_integrity(store, artifact_id: str) -> str:
    """Recorded integrity, and honestly `unchecked` when it was never measured.

    The canvas deliberately does not hash: an inspector measures one artifact on request, a canvas of
    a hundred nodes does not read a hundred files to draw itself.
    """
    return "unchecked"


@router.get("/v1/missions/{mission_id}/canvas")
def get_canvas(mission_id: str, request: Request) -> dict[str, Any]:
    """Every canonical entity of a mission, as typed nodes and typed edges."""
    store = _store(request)
    mission_rows = store.rows("SELECT * FROM missions WHERE id = ?", (mission_id,))
    if not mission_rows:
        raise HTTPException(status_code=404, detail=f"no such mission: {mission_id}")
    mission = mission_rows[0]

    tasks = store.rows("SELECT * FROM tasks WHERE mission_id = ? ORDER BY created_ts, id", (mission_id,))
    known = {task["id"] for task in tasks}
    edges: dict[str, list[str]] = {task_id: [] for task_id in known}
    for row in store.rows("SELECT task_id, depends_on FROM task_dependencies ORDER BY task_id, depends_on"):
        if row["task_id"] in known:
            edges[row["task_id"]].append(row["depends_on"])
    states = {task["id"]: TaskState(task["state"]) for task in tasks}
    inside = {task_id: [dep for dep in deps if dep in known] for task_id, deps in edges.items()}
    try:
        layers = waves(inside)
    except TaskGraphError as exc:
        raise HTTPException(status_code=409, detail=f"the work graph cannot be laid out: {exc}") from exc
    wave_of = {task_id: index for index, layer in enumerate(layers) for task_id in layer}

    nodes: list[dict[str, Any]] = []
    canvas_edges: list[dict[str, Any]] = []
    degraded: list[str] = []

    # ------------------------------------------------------------------ tasks
    for task in tasks:
        task_id = task["id"]
        state = TaskState(task["state"])
        lane = effective_lane(task_id, state, states, edges)
        blockers = dependency_blockers(task_id, states, edges)
        try:
            artifact_count = int(
                store.rows("SELECT COUNT(*) AS n FROM artifacts WHERE task_id = ?", (task_id,))[0]["n"]
            )
            proof_count = len(json.loads(task["proof"] or "[]"))
        except (TypeError, ValueError):
            artifact_count = proof_count = 0
        run = _run_of(store, task)
        workspace, writer = _workspace_of(store, task_id)
        pending = store.rows(
            "SELECT id, risk_level, human_summary FROM approvals WHERE task_id = ? AND state = 'pending'",
            (task_id,),
        )
        nodes.append(
            node(
                f"task:{task_id}",
                "task",
                task_id,
                task["title"],
                state.value,
                _task_summary(state, lane, task.get("owner_agent"), run, workspace, writer, len(pending), artifact_count),
                {
                    "lane": lane,
                    "wave": wave_of.get(task_id),
                    "agent_id": task.get("owner_agent"),
                    "run_id": task.get("run_id"),
                    "dependencies": sorted(edges.get(task_id, [])),
                    "waiting_on": blockers["waiting_on"],
                    "dead": blockers["dead"],
                    "approvals_pending": len(pending),
                    "artifacts": artifact_count,
                    "proof": proof_count,
                    "has_workspace": workspace is not None,
                    "writer": writer,
                },
            )
        )
        for dep in sorted(edges.get(task_id, [])):
            if dep in known:
                # One direction, documented: the edge runs from the dependency to the dependent, so
                # an arrow reads "this task is needed by that one".
                canvas_edges.append(edge("depends_on", f"task:{dep}", f"task:{task_id}", "depends on"))

    # ------------------------------------------------------------------ agents
    agent_ids: set[str] = {task["owner_agent"] for task in tasks if task.get("owner_agent")}
    for row in store.rows("SELECT DISTINCT agent_id FROM runs WHERE mission_id = ?", (mission_id,)):
        if row["agent_id"]:
            agent_ids.add(row["agent_id"])
    for agent_id in sorted(agent_ids):
        rows = store.rows("SELECT * FROM agents WHERE id = ?", (agent_id,))
        if not rows:
            degraded.append(f"an assignment names an agent the log does not have: {agent_id}")
            continue
        agent = rows[0]
        assigned = [task["id"] for task in tasks if task.get("owner_agent") == agent_id]
        live = store.rows(
            "SELECT r.id, r.state, s.provider, s.model FROM runs r "
            "LEFT JOIN sessions s ON s.id = r.session_id "
            "WHERE r.agent_id = ? AND r.state = 'running' ORDER BY r.created_ts DESC LIMIT 1",
            (agent_id,),
        )
        current = live[0] if live else None
        nodes.append(
            node(
                f"agent:{agent_id}",
                "agent",
                agent_id,
                agent["display_name"],
                current["state"] if current else None,
                _agent_summary(agent, assigned, current),
                {
                    "role": agent.get("role"),
                    "assigned_tasks": sorted(assigned),
                    "current_run_id": current["id"] if current else None,
                    "provider": current["provider"] if current else None,
                    "model": current["model"] if current else None,
                    # An agent is an identity, not a runtime, a session or a model: those are facts
                    # about what it is doing right now, and they are reported as such.
                    "note": "identity outlives any runtime, session or model",
                },
            )
        )
        for task_id in sorted(assigned):
            canvas_edges.append(edge("assigned_to", f"agent:{agent_id}", f"task:{task_id}", "assigned to"))

    # ------------------------------------------------------------------ runs, sessions, workspaces, approvals, artifacts
    for task in tasks:
        task_id = task["id"]
        run = _run_of(store, task)
        if run:
            nodes.append(
                node(
                    f"run:{run['run_id']}",
                    "run",
                    run["run_id"],
                    run["run_id"],
                    run.get("state"),
                    _run_summary(run),
                    run,
                )
            )
            canvas_edges.append(edge("executed_by", f"task:{task_id}", f"run:{run['run_id']}", "executed by"))
            if run.get("session_id"):
                session = store.rows(
                    "SELECT * FROM sessions WHERE id = ?", (run["session_id"],)
                )
                if session:
                    row = session[0]
                    nodes.append(
                        node(
                            f"session:{row['id']}",
                            "session",
                            row["id"],
                            f"{row.get('provider') or 'runtime'} · {row.get('model') or 'model unknown'}",
                            row.get("state"),
                            f"{row.get('runtime_id') or 'runtime unknown'} · {row.get('provider') or '?'}/{row.get('model') or '?'}",
                            {"run_id": run["run_id"], "runtime_id": row.get("runtime_id")},
                        )
                    )
                    canvas_edges.append(edge("session", f"run:{run['run_id']}", f"session:{row['id']}", "session"))
                else:
                    degraded.append(
                        f"run {run['run_id']} names a session the log does not have: {run['session_id']}"
                    )

        workspace, writer = _workspace_of(store, task_id)
        if workspace:
            key = f"workspace:{task_id}"
            nodes.append(
                node(
                    key,
                    "workspace",
                    task_id,
                    workspace["locator"],
                    workspace["state"],
                    _workspace_summary(workspace, writer),
                    {
                        **workspace,
                        "enforcement": enforcement_for(store, workspace["state"], (run or {}).get("session_id")),
                    },
                )
            )
            canvas_edges.append(edge("runs_in", f"task:{task_id}", key, "runs in"))
            if writer and writer.get("active"):
                canvas_edges.append(edge("writer_lease", key, f"run:{writer['run_id']}", "writer lease"))

        for row in store.rows(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY requested_ts", (task_id,)
        ):
            key = f"approval:{row['id']}"
            nodes.append(
                node(
                    key,
                    "approval",
                    row["id"],
                    row["human_summary"] or row["action_type"],
                    row["state"],
                    f"{row['state']} · {row['risk_level']} · {row['action_type']}",
                    {
                        "risk_level": row["risk_level"],
                        "action_type": row["action_type"],
                        "reversibility": row.get("reversibility"),
                        "expires_at": row.get("expires_at"),
                        "requires_human_attention": row["risk_level"] in {"R3", "R4"} and row["state"] == "pending",
                    },
                )
            )
            canvas_edges.append(edge("requires_approval", f"task:{task_id}", key, "requires approval"))

        for row in store.rows(
            "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_ts LIMIT 50", (task_id,)
        ):
            key = f"artifact:{row['id']}"
            nodes.append(
                node(
                    key,
                    "artifact",
                    row["id"],
                    row["path"].rsplit("/", 1)[-1] if row.get("path") else row["id"],
                    _artifact_integrity(store, row["id"]),
                    f"{row.get('mime') or 'unknown type'} · {_size(row.get('size'))}",
                    {
                        "mime": row.get("mime"),
                        "size": row.get("size"),
                        "integrity": "unchecked",
                        "integrity_note": "the canvas does not hash; open the inspector to measure it",
                    },
                )
            )
            canvas_edges.append(edge("produces", f"task:{task_id}", key, "produces"))

    # ------------------------------------------------------------------ degraded, honestly
    outside = sorted({dep for deps in edges.values() for dep in deps if dep not in known})
    if outside:
        degraded.append(f"{len(outside)} dependencies name tasks outside this mission: {', '.join(outside)}")
    unscoped_approvals = int(
        store.rows("SELECT COUNT(*) AS n FROM approvals WHERE state = 'pending' AND task_id IS NULL")[0]["n"]
    )
    if unscoped_approvals:
        degraded.append(f"{unscoped_approvals} pending approvals have no recorded task scope")
    unscoped_artifacts = int(
        store.rows("SELECT COUNT(*) AS n FROM artifacts WHERE task_id IS NULL")[0]["n"]
    )
    if unscoped_artifacts:
        degraded.append(f"{unscoped_artifacts} artifacts have no recorded task scope")

    return {
        "mission_id": mission_id,
        "mission": {"id": mission["id"], "title": mission["title"], "objective": mission.get("objective")},
        "nodes": nodes,
        "edges": canvas_edges,
        "waves": [{"wave": index, "tasks": layer} for index, layer in enumerate(layers)],
        "node_types": list(NODE_TYPES),
        "counts": {
            "nodes": len(nodes),
            "edges": len(canvas_edges),
            "by_type": {kind: sum(1 for item in nodes if item["entity_type"] == kind) for kind in NODE_TYPES},
        },
        "degraded": degraded,
        "composed_at": time.time(),
    }


# ------------------------------------------------------------------ summaries


def _size(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "size unknown"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value = value / 1024
    return "size unknown"


def _run_of(store, task: dict[str, Any]) -> dict[str, Any] | None:
    run_id = task.get("run_id")
    if not run_id:
        return None
    rows = store.rows("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not rows:
        return {"run_id": run_id, "state": None, "note": "the task names a run that is not in the log"}
    row = rows[0]
    provider = model = None
    if row.get("session_id"):
        session = store.rows("SELECT provider, model FROM sessions WHERE id = ?", (row["session_id"],))
        if session:
            provider, model = session[0]["provider"], session[0]["model"]
    return {
        "run_id": row["id"],
        "state": row["state"],
        "runtime_id": row.get("runtime_id"),
        "agent_id": row.get("agent_id"),
        "session_id": row.get("session_id"),
        "provider": provider,
        "model": model,
    }


def _workspace_of(store, task_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rows = store.rows("SELECT * FROM workspaces WHERE task_id = ? ORDER BY created_ts LIMIT 1", (task_id,))
    if not rows:
        return None, None
    row = rows[0]
    leases = store.rows(
        "SELECT * FROM workspace_leases WHERE task_id = ? AND repository_common = ?",
        (task_id, row["repository_common"]),
    )
    writer = None
    if leases:
        lease = leases[0]
        now = time.time()
        writer = {
            "run_id": lease["run_id"],
            "generation": int(lease["generation"]),
            "expires_at": float(lease["expires_at"]),
            "state": lease["state"],
            "active": lease["state"] == "active" and float(lease["expires_at"]) > now,
            "expired": float(lease["expires_at"]) <= now,
        }
    return (
        {
            "provider": "git-worktree",
            "locator": row["locator"],
            "state": row["state"],
            "dirty": row["dirty"],
            "measured": False,
            "note": "the canvas reads the recorded workspace state; open the workspace to measure it",
        },
        writer,
    )


def _task_summary(state, lane, agent_id, run, workspace, writer, approvals, artifacts) -> str:
    parts = [state.value]
    if lane != state.value.upper():
        parts.append(lane.lower())
    if agent_id:
        parts.append(agent_id)
    if run:
        parts.append(f"run {run['run_id']}")
    if workspace:
        parts.append("workspace")
    if writer and writer.get("active"):
        parts.append(f"writer gen {writer['generation']}")
    if approvals:
        parts.append(f"approval required ({approvals})")
    if artifacts:
        parts.append(f"artifacts {artifacts}")
    return " · ".join(parts)


def _agent_summary(agent, assigned, current) -> str:
    parts = [agent["display_name"]]
    if assigned:
        parts.append(f"{len(assigned)} task{'s' if len(assigned) != 1 else ''}")
    if current:
        parts.append(f"running {current['id']}")
    if not assigned and not current:
        parts.append("idle, nothing assigned")
    return " · ".join(parts)


def _run_summary(run) -> str:
    parts = [run.get("state") or "state unknown"]
    if run.get("runtime_id"):
        parts.append(run["runtime_id"])
    if run.get("provider") or run.get("model"):
        parts.append(f"{run.get('provider') or '?'}/{run.get('model') or '?'}")
    if run.get("note"):
        parts.append(run["note"])
    return " · ".join(parts)


def _workspace_summary(workspace, writer) -> str:
    parts = [workspace["provider"], workspace["state"]]
    parts.append("dirty" if workspace.get("dirty") else "clean" if workspace.get("dirty") is not None else "dirty not measured")
    if writer:
        parts.append(
            f"writer {writer['run_id']} gen {writer['generation']}"
            + (" (expired)" if writer.get("expired") else "")
        )
    else:
        parts.append("no writer")
    return " · ".join(parts)
