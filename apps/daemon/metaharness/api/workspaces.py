"""The workspace API: isolated working copies and the single-writer lease.

A worktree is concurrency and write isolation, not a security boundary (BOOK §26). Nothing here
claims host filesystem isolation; `enforcement_summary` reports the two dimensions separately and the
UI is expected to show both.

The rules this module enforces, and why each one is a refusal:

* the repository is validated **by Git**, not by the caller's word, and it may not be the
  Meta-Harness data root by accident;
* the destination is **derived** from the task id under the data root -- a client never chooses where
  a worktree goes, so a client cannot aim one at an arbitrary directory;
* allocation is **idempotent** per `(task, repository)`: a second call reconciles what exists, and if
  something else is at the destination it is refused rather than deleted;
* a lease is granted only when nobody holds a live one, renewed only by its holder, and released only
  by its holder **at the current generation** -- which is what stops a stale owner from releasing a
  newer writer's lease;
* removal requires no active lease, a clean worktree and matching metadata, and never uses `--force`.

The daemon has no actor authentication, so "who is asking" is unverified: the same S2 trust
limitation the rest of the control plane has. The lease is a correctness mechanism for concurrent
writers, not an authorisation mechanism for humans.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from metaharness.workspace import GitWorktreeProvider, WorkspaceError

from metaharness_contracts import (
    LEASE_TTL_S,
    LeaseState,
    WriterLease,
    acquire_verdict,
    enforcement_summary,
    release_verdict,
)

router = APIRouter(tags=["workspaces"])


class AllocateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    base_ref: str = "HEAD"


class LeaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    ttl_s: float | None = None


class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int


def lease_model(row: dict[str, Any]) -> WriterLease:
    """Rebuild the contract object from the log, so the rules are the contract's rules."""
    return WriterLease(
        task_id=row["task_id"],
        run_id=row["run_id"],
        generation=int(row["generation"]),
        acquired_at=float(row["acquired_ts"]),
        heartbeat_at=float(row["heartbeat_ts"]),
        expires_at=float(row["expires_at"]),
        state=LeaseState(row["state"]),
    )


def writer_view(lease: WriterLease | None, row: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if lease is None:
        return None
    return {
        "run_id": lease.run_id,
        "generation": lease.generation,
        "acquired_at": lease.acquired_at,
        "heartbeat_at": lease.heartbeat_at,
        "expires_at": lease.expires_at,
        "state": lease.state.value,
        "expired": lease.is_expired(),
        "active": lease.is_active(),
    }


def workspace_view(state: str, dirty: bool | None, lease: WriterLease | None) -> dict[str, Any]:
    """The two enforcement dimensions, kept apart on purpose.

    A worktree gives moderate isolation of concurrent repository writes. It says nothing about what
    the runtime can read: that is the sandbox's dimension (S1), and this function never upgrades it
    because a worktree exists.
    """
    summary = enforcement_summary(None, sandbox_filesystem=None)
    summary["write_isolation"] = "moderate" if state == "ready" else "weak"
    summary["write_isolation_detail"] = (
        "one isolated worktree per task; concurrent writers cannot collide"
        if state == "ready"
        else f"no isolated working copy is ready (state: {state})"
    )
    summary["writer"] = writer_view(lease)
    return summary


def _store(request: Request):
    return request.app.state.store


def _provider(request: Request) -> GitWorktreeProvider:
    provider = getattr(request.app.state, "workspace_provider", None)
    if provider is None:
        provider = GitWorktreeProvider()
        request.app.state.workspace_provider = provider
    return provider


def _data_root(request: Request) -> Path:
    return Path(request.app.state.data_root)


def _row(store, task_id: str) -> dict[str, Any] | None:
    rows = store.rows("SELECT * FROM workspaces WHERE task_id = ? ORDER BY created_ts LIMIT 1", (task_id,))
    return rows[0] if rows else None


def _lease_row(store, task_id: str, common: str) -> dict[str, Any] | None:
    rows = store.rows(
        "SELECT * FROM workspace_leases WHERE task_id = ? AND repository_common = ?", (task_id, common)
    )
    return rows[0] if rows else None


def _task_or_404(store, task_id: str) -> dict[str, Any]:
    rows = store.rows("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"no such task: {task_id}")
    return rows[0]


def _publish(request: Request, kind: str, payload: dict[str, Any], *, task_id: str, mission_id: str | None = None, run_id: str | None = None) -> None:
    request.app.state.bus.publish(
        kind,
        {"task_id": task_id, **payload},
        method="measured",
        task_id=task_id,
        mission_id=mission_id,
        run_id=run_id,
    )


def _measure(request: Request, row: dict[str, Any]) -> tuple[str, bool | None, str | None]:
    """Measure what is on disk. Returns (state, dirty, note) -- never a guess."""
    provider = _provider(request)
    path = Path(row["host_path"])
    if not path.is_dir():
        return "missing", None, f"the allocation is recorded but {row['locator']} is not on disk"
    try:
        entries = provider.worktrees(row["repository_root"])
    except WorkspaceError as exc:
        return row["state"], None, str(exc)
    paths = {str(Path(entry.path).resolve()) for entry in entries}
    if str(path.resolve()) not in paths:
        return "conflict", None, "the directory exists but git does not list it as a worktree of this repository"
    try:
        dirty, _ = provider.status(str(path))
    except WorkspaceError as exc:
        return row["state"], None, str(exc)
    return "ready", dirty, None


def _view(request: Request, row: dict[str, Any]) -> dict[str, Any]:
    store = _store(request)
    state, dirty, note = _measure(request, row)
    lease_row = _lease_row(store, row["task_id"], row["repository_common"])
    lease = lease_model(lease_row) if lease_row else None
    lease_model_obj = writer_view(lease)
    return {
        "task_id": row["task_id"],
        "mission_id": row["mission_id"],
        "provider": "git-worktree",
        "repository": {
            "root": row["repository_root"],
            "common_dir": row["repository_common"],
        },
        "base_ref": row["base_ref"],
        "base_commit": row["base_commit"],
        "branch": row["branch"],
        "locator": row["locator"],
        "state": state,
        "recorded_state": row["state"],
        "dirty": dirty,
        "measured": True,
        "note": note,
        "created_at": row["created_ts"],
        "writer": lease_model_obj,
        "enforcement": workspace_view(state, dirty, lease),
    }


# --------------------------------------------------------------------------------- endpoints


@router.get("/v1/workspaces")
def list_workspaces(request: Request) -> dict[str, Any]:
    store = _store(request)
    rows = store.rows("SELECT * FROM workspaces ORDER BY created_ts DESC")
    return {"workspaces": [_view(request, row) for row in rows], "count": len(rows)}


@router.get("/v1/tasks/{task_id}/workspace")
def inspect_workspace(task_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    _task_or_404(store, task_id)
    row = _row(store, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} has no workspace allocation")
    return _view(request, row)


@router.post("/v1/tasks/{task_id}/workspace/allocate")
def allocate_workspace(task_id: str, payload: AllocateIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    task = _task_or_404(store, task_id)
    provider = _provider(request)
    data_root = _data_root(request).resolve()

    try:
        identity = provider.validate_repository(payload.repository)
    except WorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    repository_root = Path(identity.root).resolve()
    if repository_root == data_root or data_root in repository_root.parents:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{repository_root} is inside the Meta-Harness data root; refusing to run worktree "
                "commands against the control plane's own storage"
            ),
        )
    existing = _row(store, task_id)
    if existing is not None and existing["repository_common"] != identity.common_dir:
        raise HTTPException(
            status_code=409,
            detail=(
                f"task {task_id} already has an allocation for a different repository "
                f"({existing['repository_common']}); release it before allocating another"
            ),
        )
    # Another task's allocation must never be adopted, even if the path somehow matches.
    clash = store.rows(
        "SELECT task_id FROM workspaces WHERE host_path = ? AND task_id != ?",
        (str(data_root / "workspaces" / task_id), task_id),
    )
    if clash:
        raise HTTPException(
            status_code=409, detail=f"that destination belongs to task {clash[0]['task_id']}"
        )

    host_path = data_root / "workspaces" / task_id
    locator = f"workspaces/{task_id}"
    branch = f"mh/task/{task_id}"
    if existing is not None:
        # Idempotent: reconcile what exists instead of creating a second worktree for one task.
        view = _view(request, existing)
        if view["state"] in {"ready", "missing"}:
            view["reconciled"] = True
            return view
        raise HTTPException(
            status_code=409,
            detail=f"the existing allocation is {view['state']}: {view['note'] or 'cannot be reconciled'}",
        )

    try:
        base_commit = provider.resolve_base(identity.root, payload.base_ref)
    except WorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _publish(
        request,
        "workspace.allocated",
        {
            "repository_root": identity.root,
            "repository_common": identity.common_dir,
            "base_ref": payload.base_ref,
            "base_commit": base_commit,
            "branch": branch,
            "locator": locator,
            "host_path": str(host_path),
            "mission_id": task["mission_id"],
        },
        task_id=task_id,
        mission_id=task["mission_id"],
    )
    try:
        provider.allocate(root=identity.root, branch=branch, base_commit=base_commit, path=str(host_path))
    except WorkspaceError as exc:
        # Partial creation is a fact: the allocation stays `allocating` and says why. Nothing is
        # deleted silently, and `git worktree list` plus the branch remain the recovery surface.
        _publish(
            request,
            "workspace.failed",
            {"repository_common": identity.common_dir, "reason": str(exc)},
            task_id=task_id,
            mission_id=task["mission_id"],
        )
        raise HTTPException(status_code=409, detail=f"the worktree could not be created: {exc}") from exc
    _publish(
        request,
        "workspace.ready",
        {"repository_common": identity.common_dir, "branch": branch, "base_commit": base_commit},
        task_id=task_id,
        mission_id=task["mission_id"],
    )
    return _view(request, _row(store, task_id))  # type: ignore[arg-type]


@router.post("/v1/tasks/{task_id}/workspace/lease/acquire")
def acquire_lease(task_id: str, payload: LeaseIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    _task_or_404(store, task_id)
    row = _row(store, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} has no workspace allocation")
    if not payload.run_id.startswith("run_"):
        raise HTTPException(status_code=422, detail=f"a lease is held by a run, got {payload.run_id!r}")
    current = _lease_row(store, task_id, row["repository_common"])
    lease = lease_model(current) if current else None
    verdict, reason = acquire_verdict(lease, run_id=payload.run_id)
    if verdict == "refuse":
        raise HTTPException(status_code=409, detail=f"workspace is being written by another run: {reason}")
    ttl = payload.ttl_s if payload.ttl_s is not None else LEASE_TTL_S
    now = time.time()
    if verdict == "renew" and lease is not None:
        _publish(
            request,
            "workspace.lease.renewed",
            {
                "repository_common": row["repository_common"],
                "run_id": payload.run_id,
                "generation": lease.generation,
                "expires_at": now + ttl,
            },
            task_id=task_id,
            mission_id=row["mission_id"],
            run_id=payload.run_id,
        )
        return {"verdict": "renew", "reason": reason, "writer": _view(request, row)["writer"]}
    generation = (lease.generation if lease else 0) + 1
    _publish(
        request,
        "workspace.lease.acquired",
        {
            "repository_common": row["repository_common"],
            "run_id": payload.run_id,
            "generation": generation,
            "expires_at": now + ttl,
        },
        task_id=task_id,
        mission_id=row["mission_id"],
        run_id=payload.run_id,
    )
    return {"verdict": "grant", "reason": reason, "writer": _view(request, row)["writer"]}


@router.post("/v1/tasks/{task_id}/workspace/lease/renew")
def renew_lease(task_id: str, payload: ReleaseIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    row = _row(store, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} has no workspace allocation")
    current = _lease_row(store, task_id, row["repository_common"])
    lease = lease_model(current) if current else None
    if lease is None:
        raise HTTPException(status_code=409, detail="there is no lease to renew")
    if lease.generation != payload.generation or lease.run_id != payload.run_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"generation {payload.generation} by {payload.run_id} is not the current writer "
                f"(generation {lease.generation} held by {lease.run_id})"
            ),
        )
    now = time.time()
    _publish(
        request,
        "workspace.lease.renewed",
        {
            "repository_common": row["repository_common"],
            "run_id": payload.run_id,
            "generation": lease.generation,
            "expires_at": now + LEASE_TTL_S,
        },
        task_id=task_id,
        mission_id=row["mission_id"],
        run_id=payload.run_id,
    )
    return {"renewed": True, "writer": _view(request, row)["writer"]}


@router.post("/v1/tasks/{task_id}/workspace/lease/release")
def release_lease(task_id: str, payload: ReleaseIn, request: Request) -> dict[str, Any]:
    store = _store(request)
    row = _row(store, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} has no workspace allocation")
    current = _lease_row(store, task_id, row["repository_common"])
    lease = lease_model(current) if current else None
    allowed, reason = release_verdict(
        lease, run_id=payload.run_id, generation=payload.generation
    )
    if not allowed:
        raise HTTPException(status_code=409, detail=f"refusing to release: {reason}")
    _publish(
        request,
        "workspace.lease.released",
        {
            "repository_common": row["repository_common"],
            "run_id": payload.run_id,
            "generation": payload.generation,
        },
        task_id=task_id,
        mission_id=row["mission_id"],
        run_id=payload.run_id,
    )
    return {"released": True, "writer": _view(request, row)["writer"]}


@router.post("/v1/workspaces/leases/expire")
def expire_leases(request: Request) -> dict[str, Any]:
    """Sweep expired leases and record each expiry. A bounded TTL needs a moment where it is noticed."""
    store = _store(request)
    now = time.time()
    expired: list[str] = []
    for row in store.rows("SELECT * FROM workspace_leases WHERE state = 'active'"):
        if float(row["expires_at"]) < now:
            _publish(
                request,
                "workspace.lease.expired",
                {
                    "repository_common": row["repository_common"],
                    "run_id": row["run_id"],
                    "generation": row["generation"],
                    "reason": "ttl passed",
                },
                task_id=row["task_id"],
                run_id=row["run_id"],
            )
            expired.append(row["task_id"])
    return {"expired": expired, "count": len(expired)}


@router.delete("/v1/tasks/{task_id}/workspace")
def remove_workspace(task_id: str, request: Request) -> dict[str, Any]:
    """Remove a worktree only when it is safe: no active lease, clean, and matching metadata."""
    store = _store(request)
    row = _row(store, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} has no workspace allocation")
    lease_row = _lease_row(store, task_id, row["repository_common"])
    lease = lease_model(lease_row) if lease_row else None
    if lease is not None and lease.is_active():
        raise HTTPException(
            status_code=409,
            detail=f"run {lease.run_id} holds generation {lease.generation}; release the lease first",
        )
    state, dirty, note = _measure(request, row)
    if state == "missing":
        raise HTTPException(status_code=409, detail=f"the worktree is not on disk: {note}")
    if state != "ready":
        raise HTTPException(status_code=409, detail=f"refusing to remove a worktree that is {state}: {note}")
    if dirty:
        raise HTTPException(
            status_code=409,
            detail="the worktree has uncommitted changes; commit or discard them first (no --force here)",
        )
    provider = _provider(request)
    try:
        provider.remove(root=row["repository_root"], path=row["host_path"])
    except WorkspaceError as exc:
        raise HTTPException(status_code=409, detail=f"git refused to remove it: {exc}") from exc
    _publish(
        request,
        "workspace.removed",
        {"repository_common": row["repository_common"], "branch": row["branch"], "locator": row["locator"]},
        task_id=task_id,
        mission_id=row["mission_id"],
    )
    return {"removed": True, "task_id": task_id, "branch": row["branch"], "branch_kept": True}
