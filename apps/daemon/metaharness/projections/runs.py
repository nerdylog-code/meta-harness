"""The run lifecycle projection (migration 0003).

It exists because the store must be able to answer "was this work actually running when
the daemon died?" -- WP-004 A9 -- and because a projection that is only ever written by
replay is a projection nobody will notice breaking.

The corrective states matter more than the happy path: a run left in ``running`` by a
dead process becomes ``orphaned``, and the reason is recorded on the row *and* as a new
event. Nothing here rewrites history to hide a crash (BOOK 82/83).
"""

from __future__ import annotations

import sqlite3

from metaharness_contracts import CanonicalEvent

#: state <- event kind. ``run.interrupted`` carries the orphan flag because the frozen
#: event catalogue has no ``run.orphaned``: the boot reconciler emits ``run.interrupted``
#: with ``orphaned=true``, and the projected state becomes ``orphaned``. Keeping the
#: event vocabulary frozen is worth one conditional here.
KIND_TO_STATE: dict[str, str] = {
    "run.created": "created",
    "run.started": "running",
    "run.completed": "done",
    "run.failed": "failed",
    "run.interrupted": "interrupted",
}

TERMINAL_STATES = ("done", "failed", "interrupted", "orphaned")


class RunsProjection:
    name = "runs"
    tables: tuple[str, ...] = ("runs",)

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if not event.kind.startswith("run."):
            return False
        run_id = event.run_id or event.payload_body.get("run_id")
        if not run_id:
            raise ValueError("a run.* event must identify its run via run_id")
        if event.kind not in KIND_TO_STATE and event.kind != "run.orphaned":
            # An unknown run.* kind is not ours to guess about: refusing it rolls the
            # append back, which is louder than silently dropping a lifecycle change.
            raise ValueError(f"unknown run lifecycle event kind {event.kind!r}")

        body = event.payload_body
        state = KIND_TO_STATE.get(event.kind, "orphaned")
        if event.kind == "run.interrupted" and body.get("orphaned"):
            state = "orphaned"

        existing = conn.execute("SELECT created_ts FROM runs WHERE id = ?", (run_id,)).fetchone()
        created_ts = existing["created_ts"] if existing else event.ts

        conn.execute(
            """
            INSERT INTO runs (
                id, mission_id, task_id, agent_id, session_id, runtime_id,
                state, reason, pid, created_ts, updated_ts, last_seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                mission_id = COALESCE(excluded.mission_id, runs.mission_id),
                task_id    = COALESCE(excluded.task_id, runs.task_id),
                agent_id   = COALESCE(excluded.agent_id, runs.agent_id),
                session_id = COALESCE(excluded.session_id, runs.session_id),
                runtime_id = COALESCE(excluded.runtime_id, runs.runtime_id),
                state      = excluded.state,
                reason     = COALESCE(excluded.reason, runs.reason),
                pid        = COALESCE(excluded.pid, runs.pid),
                updated_ts = excluded.updated_ts,
                last_seq   = excluded.last_seq
            """,
            (
                run_id,
                event.mission_id,
                event.task_id,
                event.agent_id,
                event.session_id,
                event.runtime_id,
                state,
                body.get("reason"),
                body.get("pid"),
                created_ts,
                event.ts,
                event.seq,
            ),
        )
        return True
