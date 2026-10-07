"""The Workboard: the operational view of the canonical work graph.

This is a **view**, composed on read from projections that already exist. There is no `board_items`
table and no second source of truth: a card is assembled from the task, its dependencies, its run,
its workspace and lease, its approvals, and its own recorded artifact and proof references. The
canonical columns, waves and blocker explanations come from `metaharness_contracts.taskgraph`, so the
daemon, the replay and the UI cannot disagree about where a task belongs.

Two rules are load-bearing here:

* **unknown is not zero.** Usage is reported only when a real `usage.sampled` event exists for the
  run; otherwise the field is absent and the card says it was not measured. The same goes for
  workspace dirtiness, which the board reads as the last recorded measurement rather than running
  `git status` for every card on every render.
* **a blocker explanation is derived, never invented.** The only explanation this endpoint produces
  from the graph is the dependency one -- which dependencies are still unsatisfied and which failed
  -- and it says which is which, because "waiting for B" and "C failed" are different sentences.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from metaharness_contracts import (
    LANES,
    TERMINAL_LANES,
    TaskGraphError,
    TaskState,
    dependency_blockers,
    effective_lane,
    waves,
)

router = APIRouter(tags=["board"])

#: Lanes in the order a board draws them: the flow first, then the terminal states that must not be
#: hidden behind `DONE`.
BOARD_LANES: tuple[str, ...] = (*LANES, *TERMINAL_LANES)


def _store(request: Request):
    return request.app.state.store


def _usage(store, run_id: str | None) -> dict[str, Any] | None:
    """The last measured usage for a run, or ``None`` when nothing was ever measured.

    ``None`` is the point: an unmeasured run has no cost, and reporting `0` would turn "we do not
    know" into "it was free".
    """
    if not run_id:
        return None
    rows = store.rows(
        "SELECT payload, ts FROM events WHERE kind = 'usage.sampled' AND run_id = ? "
        "ORDER BY seq DESC LIMIT 1",
        (run_id,),
    )
    if not rows:
        return None
    try:
        body = json.loads(rows[0]["payload"])
    except (TypeError, ValueError):
        return None
    # A usage sample is what the runtime reported (`sample`); the other keys are accepted so an
    # older shape is still readable rather than silently reported as no usage at all.
    sample = body.get("sample") or body.get("metrics") or body.get("usage") or {}
    if not isinstance(sample, dict) or not sample:
        return None
    return {"sample": sample, "sampled_at": rows[0]["ts"]}


def _run(store, task: dict[str, Any]) -> dict[str, Any] | None:
    run_id = task.get("run_id")
    if not run_id:
        return None
    rows = store.rows("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not rows:
        return {
            "run_id": run_id,
            "state": None,
            "note": "the task names a run that is not in the log",
        }
    row = rows[0]
    provider = model = None
    if row["session_id"]:
        session = store.rows(
            "SELECT provider, model FROM sessions WHERE id = ?", (row["session_id"],)
        )
        if session:
            provider, model = session[0]["provider"], session[0]["model"]
    return {
        "run_id": row["id"],
        "state": row["state"],
        "runtime_id": row["runtime_id"],
        "agent_id": row["agent_id"],
        "session_id": row["session_id"],
        "provider": provider,
        "model": model,
        "reason": row["reason"],
    }


def _workspace(store, task_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The recorded workspace and its writer lease. No Git call: the board reads projections."""
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
        active = lease["state"] == "active" and float(lease["expires_at"]) > now
        writer = {
            "run_id": lease["run_id"],
            "generation": int(lease["generation"]),
            "expires_at": float(lease["expires_at"]),
            "state": lease["state"],
            "active": active,
            "expired": float(lease["expires_at"]) <= now,
        }
    return (
        {
            "provider": "git-worktree",
            "locator": row["locator"],
            "state": row["state"],
            "dirty": row["dirty"],
            "measured": False,
            "note": (
                "the board reads the recorded workspace state; open the workspace to measure the "
                "working copy again"
            ),
        },
        writer,
    )


def _approvals(store, task_id: str) -> dict[str, Any]:
    pending = store.rows(
        "SELECT id, action_type, risk_level, human_summary, expires_at, requested_by "
        "FROM approvals WHERE task_id = ? AND state = 'pending' ORDER BY requested_ts",
        (task_id,),
    )
    by_state = {
        row["state"]: int(row["n"])
        for row in store.rows(
            "SELECT state, COUNT(*) AS n FROM approvals WHERE task_id = ? GROUP BY state", (task_id,)
        )
    }
    now = time.time()
    return {
        "pending": len(pending),
        "by_state": by_state,
        "requires_attention": bool(pending),
        "latest": pending[0] if pending else None,
        "expired_pending": [row["id"] for row in pending if row["expires_at"] and float(row["expires_at"]) < now],
    }


def enforcement_for(store, workspace_state: str | None, session_id: str | None) -> dict[str, Any]:
    """The two enforcement dimensions, reported separately and never merged.

    A worktree isolates concurrent repository writes -- moderate at best -- and says nothing about what
    the runtime can read. That second dimension belongs to the sandbox, and it is read from the
    evidence recorded for this task's own run. With no such evidence it reads `unknown`, because a
    worktree must never upgrade a claim it cannot support.
    """
    ready = workspace_state == "ready"
    result: dict[str, Any] = {
        "write_isolation": "moderate" if ready else "weak",
        "write_isolation_scope": "concurrent repository mutation",
        "write_isolation_detail": (
            "one isolated worktree per task; concurrent writers cannot collide"
            if ready
            else "no isolated working copy is ready"
        ),
        "filesystem_isolation": "unknown",
        "filesystem_isolation_scope": "what the runtime can read",
        "filesystem_isolation_detail": "no sandbox evidence was recorded for this task's run",
        "note": "a worktree isolates concurrent writes; it is not a security boundary",
    }
    if not session_id:
        return result
    rows = store.rows(
        "SELECT payload FROM events WHERE kind = 'session.policy' AND session_id = ? "
        "ORDER BY seq DESC LIMIT 1",
        (session_id,),
    )
    if not rows:
        return result
    try:
        body = json.loads(rows[0]["payload"])
    except (TypeError, ValueError):
        return result
    evidence = body.get("evidence") or {}
    filesystem = evidence.get("filesystem")
    if isinstance(filesystem, str):
        result["filesystem_isolation"] = filesystem
        result["filesystem_isolation_detail"] = (
            f"recorded for this run's session, sandbox provider {evidence.get('provider') or 'unknown'}"
        )
    return result


def _card(store, task: dict[str, Any], deps: dict[str, list[str]], states: dict[str, TaskState]) -> dict[str, Any]:
    task_id = task["id"]
    state = TaskState(task["state"])
    blockers = dependency_blockers(task_id, states, deps)
    workspace, writer = _workspace(store, task_id)
    run = _run(store, task)
    # Artifacts are counted from the projection by scope, so a neighbour's work is never counted
    # here; proof is the task's own recorded references. Both are facts, neither is a join guess.
    artifacts = int(
        store.rows("SELECT COUNT(*) AS n FROM artifacts WHERE task_id = ?", (task_id,))[0]["n"]
    )
    try:
        recorded = len(json.loads(task["artifacts"] or "[]"))
        proof = len(json.loads(task["proof"] or "[]"))
    except (TypeError, ValueError):
        recorded = proof = 0
    enforcement = enforcement_for(store, (workspace or {}).get("state"), (run or {}).get("session_id"))
    return {
        "task_id": task_id,
        "title": task["title"],
        "state": state.value,
        "lane": effective_lane(task_id, state, states, deps),
        "agent": {"id": task["owner_agent"]} if task["owner_agent"] else None,
        "dependencies": sorted(deps.get(task_id, [])),
        "blocked": blockers,
        "run": run,
        "workspace": workspace,
        "writer": writer,
        "approvals": _approvals(store, task_id),
        "artifacts": {
            "count": artifacts,
            "recorded": recorded,
            "proof": proof,
            # The inspector opens an artifact by id, so the card carries the ids it may open --
            # bounded, because a board is not a file listing.
            "ids": [
                row["id"]
                for row in store.rows(
                    "SELECT id FROM artifacts WHERE task_id = ? ORDER BY created_ts LIMIT 20",
                    (task_id,),
                )
            ],
        },
        "usage": _usage(store, task.get("run_id")),
        "enforcement": enforcement,
        "created_at": task["created_ts"],
        "updated_at": task["updated_ts"],
        "completed_at": task["completed_ts"],
    }


def _explain(card: dict[str, Any]) -> list[str]:
    """The sentences a card may say about why it is not moving.

    Only facts that exist: unsatisfied dependencies, failed dependencies, a pending approval, a
    workspace that is not ready, and a lease held by a run. Nothing is inferred about intent.
    """
    reasons: list[str] = []
    waiting = card["blocked"]["waiting_on"]
    dead = card["blocked"]["dead"]
    if waiting:
        reasons.append(f"waiting on {', '.join(waiting)}")
    if dead:
        reasons.append(f"a dependency failed or was cancelled: {', '.join(dead)}")
    if card["approvals"]["pending"]:
        reasons.append(f"approval pending ({card['approvals']['pending']})")
    workspace = card["workspace"]
    if workspace and workspace["state"] != "ready":
        reasons.append(f"workspace is {workspace['state']}")
    if card["writer"] and card["writer"]["active"] and card["lane"] != "RUNNING":
        reasons.append(f"a writer lease is held by {card['writer']['run_id']}")
    return reasons


@router.get("/v1/missions/{mission_id}/board")
def get_board(mission_id: str, request: Request) -> dict[str, Any]:
    """Every task of a mission, placed in a lane, with what is known about it and nothing invented."""
    store = _store(request)
    mission = store.rows("SELECT * FROM missions WHERE id = ?", (mission_id,))
    if not mission:
        raise HTTPException(status_code=404, detail=f"no such mission: {mission_id}")

    tasks = store.rows(
        "SELECT * FROM tasks WHERE mission_id = ? ORDER BY created_ts, id", (mission_id,)
    )
    known = {task["id"] for task in tasks}
    edges: dict[str, list[str]] = {task_id: [] for task_id in known}
    for row in store.rows("SELECT task_id, depends_on FROM task_dependencies ORDER BY task_id, depends_on"):
        if row["task_id"] in known:
            edges[row["task_id"]].append(row["depends_on"])
    states = {task["id"]: TaskState(task["state"]) for task in tasks}
    # A dependency outside this mission is reported on the card but cannot be laid out as a wave.
    inside = {task_id: [dep for dep in deps if dep in known] for task_id, deps in edges.items()}
    try:
        layers = waves(inside)
    except TaskGraphError as exc:
        raise HTTPException(status_code=409, detail=f"the work graph cannot be laid out: {exc}") from exc
    wave_of = {task_id: index for index, layer in enumerate(layers) for task_id in layer}

    cards = [_card(store, task, edges, states) for task in tasks]
    for card in cards:
        card["wave"] = wave_of.get(card["task_id"])
        card["reasons"] = _explain(card)

    by_lane: dict[str, list[dict[str, Any]]] = {lane: [] for lane in BOARD_LANES}
    for card in cards:
        by_lane[card["lane"]].append(card)

    outside = sorted({dep for deps in edges.values() for dep in deps if dep not in known})
    unscoped = int(
        store.rows(
            "SELECT COUNT(*) AS n FROM approvals WHERE state = 'pending' AND task_id IS NULL"
        )[0]["n"]
    )
    degraded: list[str] = []
    if outside:
        degraded.append(f"{len(outside)} dependencies name tasks outside this mission: {', '.join(outside)}")
    if unscoped:
        degraded.append(f"{unscoped} pending approvals have no recorded task scope")
    missing_runs = [card["task_id"] for card in cards if card["run"] and card["run"]["state"] is None]
    if missing_runs:
        degraded.append(f"{len(missing_runs)} tasks name runs the log does not have: {', '.join(missing_runs)}")

    return {
        "mission": {
            "id": mission[0]["id"],
            "title": mission[0]["title"],
            "objective": mission[0].get("objective"),
            "state": mission[0].get("state"),
        },
        "columns": [
            {"lane": lane, "count": len(by_lane[lane]), "tasks": by_lane[lane]} for lane in BOARD_LANES
        ],
        "waves": [{"wave": index, "tasks": layer} for index, layer in enumerate(layers)],
        "counts": {lane: len(by_lane[lane]) for lane in BOARD_LANES},
        "total": len(cards),
        "filters": {
            "agents": sorted({card["agent"]["id"] for card in cards if card["agent"]}),
            "runtimes": sorted(
                {card["run"]["runtime_id"] for card in cards if card["run"] and card["run"].get("runtime_id")}
            ),
            "states": [state.value for state in TaskState],
            "lanes": list(BOARD_LANES),
            "needs_approval": [card["task_id"] for card in cards if card["approvals"]["requires_attention"]],
            "has_workspace": [card["task_id"] for card in cards if card["workspace"]],
            "has_active_lease": [card["task_id"] for card in cards if card["writer"] and card["writer"]["active"]],
            "blocked": [card["task_id"] for card in cards if card["reasons"]],
        },
        "degraded": degraded,
        "composed_at": time.time(),
    }
