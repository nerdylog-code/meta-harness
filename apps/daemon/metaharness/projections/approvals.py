"""Approvals as a projection of the log.

An approval is a canonical fact: `approval.requested` creates it, `approval.granted`,
`approval.denied`, `approval.expired` and `approval.consumed` decide its fate, and the projection
is the only place those facts become a row. A UI modal that "remembers" an approval is not an
approval, and this table is why.

What this projection deliberately does not do is decide anything. Whether a payload matches, whether
the TTL passed, whether an R4 approval has a named human and whether an approval was already used
are rules -- they live in `metaharness_contracts.approval` and in the API that calls it. A
projection that also enforced policy would be a second source of truth for the rules.
"""

from __future__ import annotations

import json
import sqlite3

from metaharness_contracts import CanonicalEvent

REQUESTED = "approval.requested"
GRANTED = "approval.granted"
DENIED = "approval.denied"
EXPIRED = "approval.expired"
CONSUMED = "approval.consumed"

KINDS: frozenset[str] = frozenset({REQUESTED, GRANTED, DENIED, EXPIRED, CONSUMED})

#: The state each decision event moves an approval to. `pending` is the state of a fresh request.
STATE_OF: dict[str, str] = {
    GRANTED: "granted",
    DENIED: "denied",
    EXPIRED: "expired",
    CONSUMED: "consumed",
}


class ApprovalsProjection:
    name = "approvals"
    tables: tuple[str, ...] = ("approvals",)

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if event.kind not in KINDS:
            return False
        body = event.payload_body
        approval_id = body.get("approval_id") or body.get("id")
        if not approval_id:
            raise ValueError(f"{event.kind} must identify its approval")

        if event.kind == REQUESTED:
            conn.execute(
                """
                INSERT INTO approvals (
                    id, action_type, action_payload, action_payload_hash, risk_level, human_summary,
                    reversibility, requested_by, requested_ts, expires_at, state, last_seq,
                    mission_id, task_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    str(approval_id),
                    str(body.get("action_type") or ""),
                    json.dumps(body.get("action_payload") or {}, sort_keys=True),
                    str(body.get("action_payload_hash") or ""),
                    str(body.get("risk_level") or ""),
                    str(body.get("human_summary") or ""),
                    str(body.get("reversibility") or "reversible"),
                    str(body.get("requested_by") or ""),
                    event.ts,
                    body.get("expires_at"),
                    event.seq,
                    # The scope comes from the canonical envelope, not from parsing the action
                    # payload: the log already says which mission and task this approval is about.
                    event.mission_id,
                    event.task_id,
                ),
            )
            return True

        state = STATE_OF[event.kind]
        assignments = ["state = ?", "last_seq = ?"]
        params: list[object] = [state, event.seq]
        if event.kind == GRANTED:
            assignments += ["granted_by = ?", "granted_ts = ?"]
            params += [body.get("by"), event.ts]
        if event.kind == DENIED:
            assignments.append("decided_reason = ?")
            params.append(body.get("reason"))
        if event.kind == CONSUMED:
            assignments.append("consumed_ts = ?")
            params.append(event.ts)
        params.append(str(approval_id))
        cursor = conn.execute(
            f"UPDATE approvals SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608 - literals above
            tuple(params),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"{event.kind} names approval {approval_id}, which the log never created")
        return True
