"""The artifact index projection (migration 0002).

Artifacts reach the database the same way every other row does: through an event. The
bytes are written to the filesystem first (content-addressed by sha256, so re-exporting
the same payload costs nothing), then ``artifact.created`` carries the index into the
log, and this projection writes the row inside the same transaction. A failed
transaction therefore leaves at most a harmless unreferenced file, never a row pointing
at nothing.

The artifact id travels in the payload rather than the envelope: the envelope's id fields
are the 14 frozen keys of ADR-0017 and do not include an artifact slot. That is a
deliberate property of the contract, not an omission to fix here.
"""

from __future__ import annotations

import json
import sqlite3

from metaharness_contracts import CanonicalEvent

REQUIRED_FIELDS = ("artifact_id", "path", "sha256", "mime", "size")


class ArtifactsProjection:
    name = "artifacts"
    tables: tuple[str, ...] = ("artifacts",)

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        if event.kind != "artifact.created":
            return False
        body = event.payload_body
        missing = [field for field in REQUIRED_FIELDS if body.get(field) in (None, "")]
        if missing:
            raise ValueError(f"artifact.created is missing {', '.join(missing)}")

        conn.execute(
            """
            INSERT INTO artifacts (
                id, path, sha256, mime, size, metadata, origin, created_ts, seq, mission_id, task_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                path = excluded.path,
                sha256 = excluded.sha256,
                mime = excluded.mime,
                size = excluded.size
            """,
            (
                body["artifact_id"],
                body["path"],
                body["sha256"],
                body["mime"],
                int(body["size"]),
                json.dumps(body.get("metadata") or {}, sort_keys=True, ensure_ascii=False),
                json.dumps(body.get("origin") or {}, sort_keys=True, ensure_ascii=False),
                event.ts,
                event.seq,
                # From the canonical envelope: the log already says which task produced this.
                event.mission_id,
                event.task_id,
            ),
        )
        return True
