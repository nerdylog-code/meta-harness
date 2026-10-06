"""Missions, agents and sessions as projections of the log (WP-019).

Same discipline as `runs` and `artifacts`: these tables are written only by `apply_event`,
inside the append transaction, and replay rebuilds them. The API never writes a row directly,
which is what makes "restart and the agent is still there" a property of the log rather than a
hope.

Two things worth stating because they are easy to get wrong:

* **identity ≠ configuration.** `agent.created` makes an agent; `agent.version.created` adds a
  configuration to it. Changing the runtime creates a version (ADR-0002), never a new agent.
* **requested ≠ served.** A session records the provider and model the runtime actually
  reported, so "Nova runs on Pi" is a claim with evidence behind it.
"""

from __future__ import annotations

import json
import sqlite3

from metaharness_contracts import CanonicalEvent


class MissionsProjection:
    name = "missions"
    tables: tuple[str, ...] = ("missions",)

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if event.kind != "mission.created":
            return False
        body = event.payload_body
        mission_id = event.mission_id or body.get("mission_id")
        if not mission_id:
            raise ValueError("mission.created must identify its mission")
        conn.execute(
            """
            INSERT INTO missions (id, title, objective, owner, status, created_ts, updated_ts, last_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                objective = excluded.objective,
                owner = COALESCE(excluded.owner, missions.owner),
                updated_ts = excluded.updated_ts,
                last_seq = excluded.last_seq
            """,
            (
                mission_id,
                str(body.get("title") or "untitled"),
                str(body.get("objective") or ""),
                body.get("owner"),
                str(body.get("status") or "active"),
                event.ts,
                event.ts,
                event.seq,
            ),
        )
        return True


class AgentsProjection:
    name = "agents"
    tables: tuple[str, ...] = ("agents", "agent_versions")

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if event.kind == "agent.created":
            body = event.payload_body
            agent_id = event.agent_id or body.get("agent_id")
            if not agent_id:
                raise ValueError("agent.created must identify its agent")
            conn.execute(
                """
                INSERT INTO agents (id, display_name, role, created_ts, updated_ts, last_seq)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    role = excluded.role,
                    updated_ts = excluded.updated_ts,
                    last_seq = excluded.last_seq
                """,
                (
                    agent_id,
                    str(body.get("display_name") or agent_id),
                    str(body.get("role") or "builder"),
                    event.ts,
                    event.ts,
                    event.seq,
                ),
            )
            return True

        if event.kind == "agent.version.created":
            body = event.payload_body
            agent_id = event.agent_id or body.get("agent_id")
            version = body.get("version")
            if not agent_id or not isinstance(version, int):
                raise ValueError("agent.version.created needs agent_id and an integer version")
            conn.execute(
                """
                INSERT INTO agent_versions (id, agent_id, version, runtime_preferred, model_primary, created_ts, last_seq)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, version) DO UPDATE SET
                    runtime_preferred = excluded.runtime_preferred,
                    model_primary = excluded.model_primary,
                    last_seq = excluded.last_seq
                """,
                (
                    f"{agent_id}:v{version}",
                    agent_id,
                    version,
                    body.get("runtime_preferred"),
                    body.get("model_primary"),
                    event.ts,
                    event.seq,
                ),
            )
            conn.execute(
                "UPDATE agents SET updated_ts = ?, last_seq = ? WHERE id = ?",
                (event.ts, event.seq, agent_id),
            )
            return True
        return False


class SessionsProjection:
    name = "sessions"
    tables: tuple[str, ...] = ("sessions",)

    #: state <- event kind. A session that was cancelled says so; it never looks merely closed.
    STATE_BY_KIND = {
        "session.opened": "open",
        "session.closed": "closed",
        "runtime.pi.cancelled": "cancelled",
    }

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if event.kind not in self.STATE_BY_KIND:
            return False
        session_id = event.session_id or event.payload_body.get("session_id")
        if not session_id:
            raise ValueError(f"{event.kind} must identify its session")
        body = event.payload_body
        state = self.STATE_BY_KIND[event.kind]
        if event.kind == "runtime.pi.cancelled" and not body.get("orphans", True):
            # A cancel that left survivors is not a clean cancel, and the row must not imply it.
            state = "cancelled-with-survivors"
        reason = body.get("reason") or ("orphaned process" if state.endswith("survivors") else None)

        conn.execute(
            """
            INSERT INTO sessions (
                id, agent_id, mission_id, task_id, run_id, runtime_id, provider, model,
                state, reason, created_ts, updated_ts, last_seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                agent_id   = COALESCE(excluded.agent_id, sessions.agent_id),
                mission_id = COALESCE(excluded.mission_id, sessions.mission_id),
                task_id    = COALESCE(excluded.task_id, sessions.task_id),
                run_id     = COALESCE(excluded.run_id, sessions.run_id),
                runtime_id = COALESCE(excluded.runtime_id, sessions.runtime_id),
                provider   = COALESCE(excluded.provider, sessions.provider),
                model      = COALESCE(excluded.model, sessions.model),
                state      = excluded.state,
                reason     = COALESCE(excluded.reason, sessions.reason),
                updated_ts = excluded.updated_ts,
                last_seq   = excluded.last_seq
            """,
            (
                session_id,
                event.agent_id,
                event.mission_id,
                event.task_id,
                event.run_id,
                event.runtime_id,
                body.get("provider"),
                body.get("model"),
                state,
                reason,
                event.ts,
                event.ts,
                event.seq,
            ),
        )
        return True
