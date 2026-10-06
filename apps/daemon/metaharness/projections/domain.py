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
    #: `runtime.hermes.cancelled` is listed for the same reason Pi's is: a cancel on the second
    #: runtime must not leave a row that still reads "open".
    STATE_BY_KIND = {
        "session.opened": "open",
        "session.closed": "closed",
        "session.archived": "archived",
        "runtime.pi.cancelled": "cancelled",
        "runtime.hermes.cancelled": "cancelled",
    }

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if event.kind not in self.STATE_BY_KIND:
            return False
        session_id = event.session_id or event.payload_body.get("session_id")
        if not session_id:
            raise ValueError(f"{event.kind} must identify its session")
        body = event.payload_body
        state = self.STATE_BY_KIND[event.kind]
        if event.kind in {"runtime.pi.cancelled", "runtime.hermes.cancelled"} and not body.get("orphans", True):
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


class CapsulesProjection:
    """Context capsules, indexed by the artifact that holds them (M2).

    The row is written from `capsule.created` and nothing else. `verified` is a recorded outcome,
    not an assumption: a capsule that failed verification still gets a row -- an attempt is a fact
    and the log is append-only -- but the row says it failed, and the migration reads that field.
    """

    name = "capsules"
    tables: tuple[str, ...] = ("capsules",)

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if event.kind != "capsule.created":
            return False
        body = event.payload_body
        capsule_id = event.payload_body.get("capsule_id") or body.get("id")
        if not capsule_id:
            raise ValueError("capsule.created must identify the artifact that holds it")
        verification = body.get("verification")
        conn.execute(
            """
            INSERT INTO capsules (
                id, agent_id, mission_id, session_id, runtime_id, phase, objective,
                sha256, size, verified, verification, created_ts, last_seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                agent_id     = COALESCE(excluded.agent_id, capsules.agent_id),
                mission_id   = COALESCE(excluded.mission_id, capsules.mission_id),
                session_id   = COALESCE(excluded.session_id, capsules.session_id),
                runtime_id   = COALESCE(excluded.runtime_id, capsules.runtime_id),
                phase        = excluded.phase,
                objective    = excluded.objective,
                sha256       = excluded.sha256,
                size         = excluded.size,
                verified     = excluded.verified,
                verification = excluded.verification,
                last_seq     = excluded.last_seq
            """,
            (
                str(capsule_id),
                event.agent_id or body.get("agent_id"),
                event.mission_id or body.get("mission_id"),
                event.session_id or body.get("session_id"),
                event.runtime_id or body.get("runtime_id"),
                str(body.get("phase") or "NORMAL"),
                str(body.get("objective") or ""),
                str(body.get("sha256") or ""),
                int(body.get("size") or 0),
                1 if body.get("verified") else 0,
                json.dumps(verification, ensure_ascii=False) if verification is not None else None,
                event.ts,
                event.seq,
            ),
        )
        return True
