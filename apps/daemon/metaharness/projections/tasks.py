"""The work graph as a projection of the log.

Every state a task can be in is the fold of the `task.*` events that mention it, so a replay
reconstructs the graph exactly -- including the edges, which are their own rows so that readiness
is a join and a cycle check can see them.

Two things this projection deliberately does **not** do:

* It does not decide anything. Whether a dependency exists, whether the graph is a DAG, whether a
  task may start and whether a completion carries the evidence its acceptance gate requires are
  rules, and rules live in `metaharness_contracts.taskgraph` and the API that calls them. A
  projection that also enforced policy would be a second source of truth for the rules.
* It does not invent a state. An event that names a task the projection has never seen is an
  error, not an upsert: a task exists because `task.created` said so.
"""

from __future__ import annotations

import json
import sqlite3

from metaharness_contracts import CanonicalEvent

#: The events this projection folds. Anything else returns False and is ignored.
CREATED = "task.created"
UPDATED = "task.updated"
READY = "task.ready"
ASSIGNED = "task.assigned"
STARTED = "task.started"
BLOCKED = "task.blocked"
REVIEW_REQUESTED = "task.review_requested"
COMPLETED = "task.completed"
FAILED = "task.failed"
CANCELLED = "task.cancelled"
DEPENDENCY_ADDED = "task.dependency_added"
DEPENDENCY_REMOVED = "task.dependency_removed"

KINDS: frozenset[str] = frozenset(
    {
        CREATED,
        UPDATED,
        READY,
        ASSIGNED,
        STARTED,
        BLOCKED,
        REVIEW_REQUESTED,
        COMPLETED,
        FAILED,
        CANCELLED,
        DEPENDENCY_ADDED,
        DEPENDENCY_REMOVED,
    }
)

#: Which event moves a task to which state. The state names come from the Book's `TaskState`
#: vocabulary, so an event cannot introduce a state the contract does not have.
STATE_OF: dict[str, str] = {
    READY: "ready",
    STARTED: "running",
    BLOCKED: "blocked",
    REVIEW_REQUESTED: "review",
    COMPLETED: "done",
    FAILED: "failed",
    CANCELLED: "cancelled",
}


class TasksProjection:
    name = "tasks"
    tables: tuple[str, ...] = ("tasks", "task_dependencies")

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if event.kind not in KINDS:
            return False
        body = event.payload_body
        task_id = event.task_id or body.get("task_id")
        if not task_id:
            raise ValueError(f"{event.kind} must identify its task")

        if event.kind == CREATED:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, title, description, state, owner_agent, run_id,
                    workspace_scope, acceptance, proof, artifacts, created_ts, updated_ts, last_seq
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    str(body.get("mission_id") or event.mission_id or ""),
                    str(body.get("title") or "untitled"),
                    str(body.get("description") or ""),
                    str(body.get("state") or "draft"),
                    body.get("owner_agent"),
                    body.get("run_id"),
                    body.get("workspace_scope"),
                    json.dumps(body.get("acceptance_gate") or {}, sort_keys=True),
                    json.dumps(body.get("proof") or [], sort_keys=True),
                    json.dumps(body.get("artifacts") or [], sort_keys=True),
                    event.ts,
                    event.ts,
                    event.seq,
                ),
            )
            # Dependencies arrive with the task, in the same transaction: a task created without
            # edges is ready the instant it exists, and correcting that afterwards would put a
            # ready -> blocked pair in the log for something that was never actually ready.
            for dep in sorted(set(body.get("dependencies") or [])):
                conn.execute(
                    """
                    INSERT INTO task_dependencies (task_id, depends_on, created_seq)
                    VALUES (?, ?, ?)
                    ON CONFLICT(task_id, depends_on) DO UPDATE SET created_seq = excluded.created_seq
                    """,
                    (task_id, str(dep), event.seq),
                )
            return True

        if event.kind == DEPENDENCY_ADDED:
            depends_on = str(body.get("depends_on") or "")
            if not depends_on:
                raise ValueError("task.dependency_added must name the dependency")
            conn.execute(
                """
                INSERT INTO task_dependencies (task_id, depends_on, created_seq)
                VALUES (?, ?, ?)
                ON CONFLICT(task_id, depends_on) DO UPDATE SET created_seq = excluded.created_seq
                """,
                (task_id, depends_on, event.seq),
            )
            return True

        if event.kind == DEPENDENCY_REMOVED:
            conn.execute(
                "DELETE FROM task_dependencies WHERE task_id = ? AND depends_on = ?",
                (task_id, str(body.get("depends_on") or "")),
            )
            return True

        # Everything else updates the row. A missing row is an error: a task exists because
        # `task.created` said so, and inventing one here would hide a broken log.
        assignments: list[str] = []
        params: list[object] = []
        state = STATE_OF.get(event.kind)
        if state is not None:
            assignments.append("state = ?")
            params.append(state)
        if event.kind == UPDATED:
            for column, key in (("title", "title"), ("description", "description")):
                if key in body:
                    assignments.append(f"{column} = ?")
                    params.append(str(body[key]))
            if "workspace_scope" in body:
                assignments.append("workspace_scope = ?")
                params.append(body["workspace_scope"])
        if event.kind == ASSIGNED:
            assignments.append("owner_agent = ?")
            params.append(body.get("agent_id") or body.get("owner_agent"))
        if event.kind == STARTED and body.get("run_id"):
            assignments.append("run_id = ?")
            params.append(body["run_id"])
        if event.kind == COMPLETED:
            if body.get("proof") is not None:
                assignments.append("proof = ?")
                params.append(json.dumps(body["proof"], sort_keys=True))
            if body.get("artifacts") is not None:
                assignments.append("artifacts = ?")
                params.append(json.dumps(body["artifacts"], sort_keys=True))
            assignments.append("completed_ts = ?")
            params.append(event.ts)
        assignments.append("updated_ts = ?")
        params.append(event.ts)
        assignments.append("last_seq = ?")
        params.append(event.seq)
        params.append(task_id)
        cursor = conn.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608 - column names are literals above
            tuple(params),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"{event.kind} names task {task_id}, which the log never created")
        return True
