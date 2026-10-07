"""Workspace allocations and writer leases as projections of the log.

Two rows, one story: an allocation says *where a task may write*, and a lease says *who is writing
there right now*. Both are folds of `workspace.*` events, so a restart reconstructs them and a replay
reproduces them, and neither is a lock held inside a process.

What this projection deliberately does not do is decide. Whether a repository is real, whether a
destination is free, whether a lease may be granted or released at a given generation, and whether a
worktree is clean enough to remove are rules -- they live in `metaharness_contracts.workspace`, in
the provider, and in the API that calls both.
"""

from __future__ import annotations

import sqlite3

from metaharness_contracts import CanonicalEvent

ALLOCATED = "workspace.allocated"
READY = "workspace.ready"
FAILED = "workspace.failed"
REMOVED = "workspace.removed"
DIRTY = "workspace.dirty"
CLEAN = "workspace.clean"
LEASE_ACQUIRED = "workspace.lease.acquired"
LEASE_RENEWED = "workspace.lease.renewed"
LEASE_RELEASED = "workspace.lease.released"
LEASE_EXPIRED = "workspace.lease.expired"

KINDS: frozenset[str] = frozenset(
    {
        ALLOCATED,
        READY,
        FAILED,
        REMOVED,
        DIRTY,
        CLEAN,
        LEASE_ACQUIRED,
        LEASE_RENEWED,
        LEASE_RELEASED,
        LEASE_EXPIRED,
    }
)

#: The allocation state each lifecycle event moves to. `dirty`/`clean` are measurements, not states.
STATE_OF: dict[str, str] = {
    ALLOCATED: "allocating",
    READY: "ready",
    FAILED: "failed",
    REMOVED: "removed",
}

#: The lease state each event moves to.
LEASE_STATE_OF: dict[str, str] = {
    LEASE_ACQUIRED: "active",
    LEASE_RENEWED: "active",
    LEASE_RELEASED: "released",
    LEASE_EXPIRED: "expired",
}


class WorkspacesProjection:
    name = "workspaces"
    tables: tuple[str, ...] = ("workspaces", "workspace_leases")

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if event.kind not in KINDS:
            return False
        body = event.payload_body
        task_id = body.get("task_id") or event.task_id
        common = body.get("repository_common") or body.get("repository_common_dir")
        if not task_id or not common:
            raise ValueError(f"{event.kind} must identify its task and repository")

        if event.kind == ALLOCATED:
            conn.execute(
                """
                INSERT INTO workspaces (
                    task_id, repository_root, repository_common, mission_id, base_ref, base_commit,
                    branch, locator, host_path, state, dirty, created_ts, updated_ts, last_seq
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'allocating', NULL, ?, ?, ?)
                ON CONFLICT(task_id, repository_common) DO UPDATE SET
                    base_ref = excluded.base_ref,
                    base_commit = excluded.base_commit,
                    branch = excluded.branch,
                    locator = excluded.locator,
                    host_path = excluded.host_path,
                    updated_ts = excluded.updated_ts,
                    last_seq = excluded.last_seq
                """,
                (
                    str(task_id),
                    str(body.get("repository_root") or ""),
                    str(common),
                    body.get("mission_id") or event.mission_id,
                    str(body.get("base_ref") or ""),
                    str(body.get("base_commit") or ""),
                    str(body.get("branch") or ""),
                    str(body.get("locator") or ""),
                    str(body.get("host_path") or ""),
                    event.ts,
                    event.ts,
                    event.seq,
                ),
            )
            return True

        if event.kind in {DIRTY, CLEAN}:
            conn.execute(
                "UPDATE workspaces SET dirty = ?, updated_ts = ?, last_seq = ? "
                "WHERE task_id = ? AND repository_common = ?",
                (1 if event.kind == DIRTY else 0, event.ts, event.seq, str(task_id), str(common)),
            )
            return True

        if event.kind in STATE_OF:
            cursor = conn.execute(
                "UPDATE workspaces SET state = ?, updated_ts = ?, last_seq = ? "
                "WHERE task_id = ? AND repository_common = ?",
                (STATE_OF[event.kind], event.ts, event.seq, str(task_id), str(common)),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"{event.kind} names an allocation the log never created")
            return True

        # Leases: one row per allocation, holding the current writer and its generation.
        if event.kind == LEASE_ACQUIRED:
            conn.execute(
                """
                INSERT INTO workspace_leases (
                    task_id, repository_common, run_id, generation, acquired_ts, heartbeat_ts,
                    expires_at, state, last_seq
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(task_id, repository_common) DO UPDATE SET
                    run_id = excluded.run_id,
                    generation = excluded.generation,
                    acquired_ts = excluded.acquired_ts,
                    heartbeat_ts = excluded.heartbeat_ts,
                    expires_at = excluded.expires_at,
                    state = 'active',
                    last_seq = excluded.last_seq
                """,
                (
                    str(task_id),
                    str(common),
                    str(body.get("run_id") or ""),
                    int(body.get("generation") or 0),
                    event.ts,
                    event.ts,
                    float(body.get("expires_at") or 0.0),
                    event.seq,
                ),
            )
            return True

        assignments = ["state = ?", "last_seq = ?"]
        params: list[object] = [LEASE_STATE_OF[event.kind], event.seq]
        if event.kind == LEASE_RENEWED:
            assignments += ["heartbeat_ts = ?", "expires_at = ?"]
            params += [event.ts, float(body.get("expires_at") or 0.0)]
        params += [str(task_id), str(common)]
        cursor = conn.execute(
            f"UPDATE workspace_leases SET {', '.join(assignments)} "  # noqa: S608 - literals above
            "WHERE task_id = ? AND repository_common = ?",
            tuple(params),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"{event.kind} names a lease the log never acquired")
        return True
