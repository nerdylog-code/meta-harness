"""The canonical store: one append-only log, transactional projections, replay.

This is the daemon's storage kernel (WP-004). The invariants it enforces, and where each
one comes from:

* **One source of truth** (BOOK 8, ADR-0003). Every state change enters through an event.
  JSONL is produced *from* here by ``metaharness.export`` and is never read as authority.
* **The log is never rewritten.** Corrections are new events. The schema enforces it with
  triggers, so this is not a promise the code makes to itself.
* **An event and its projections commit together** (constraint 3). ``append`` is one
  ``BEGIN IMMEDIATE`` transaction covering the row and every projection it touches; a
  projection that refuses an event rolls the event back with it (A2).
* **``seq`` is assigned here**, monotonically and without gaps, inside that same
  transaction (constraint 3). Nothing else in the system assigns it.
* **Appends are idempotent by event id.** Adapters reconnect and re-send, so a repeated
  event returns the stored row with ``inserted=False`` and touches nothing -- no second
  projection pass, no seq movement.
* **Large payloads stay out of the database** (constraint 5, migration 0002).

The store deliberately knows nothing about processes, runtimes or leases: it records and
replays the truth. Deciding that a persisted ``running`` row is a lie is the boot
reconciler's job (``metaharness.reconcile``), which asks the outside world and then emits
a *new* event.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from metaharness_contracts import CanonicalEvent, IdKind, new_id

from ..paths import subdir
from ..projections import (
    DEFAULT_PROJECTIONS,
    Projection,
    apply_event,
    canonical_json,
    digest,
    snapshot,
    table_names,
)
from . import migrations as migrations_module
from .artifacts import (
    ArtifactRecord,
    describe,
    hash_file,
    new_artifact_id,
    path_for,
    store_bytes,
    store_file,
)
from .db import connect, integrity_check, journal_mode, transaction
from .errors import AppendOnlyViolation, StoreError

DEFAULT_DB_NAME = "metaharness.sqlite3"

INSERT_EVENT_SQL = """
INSERT INTO events (
    seq, id, ts, kind, namespace, mission_id, task_id, run_id, agent_id, session_id,
    runtime_id, correlation_id, causation_id, payload, provenance
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def default_db_path(root: str | Path | None = None) -> Path:
    """``<data_root>/data/metaharness.sqlite3`` (platformdirs, never hardcoded)."""
    return Path(subdir("data", root)) / DEFAULT_DB_NAME


@dataclass(frozen=True)
class AppendResult:
    event: CanonicalEvent
    seq: int
    inserted: bool
    projections: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayReport:
    events: int
    source_digest: str
    target_digest: str
    equal: bool


@dataclass(frozen=True)
class VerifyReport:
    ok: bool
    events: int
    schema_version: int
    journal_mode: str
    problems: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "events": self.events,
            "schema_version": self.schema_version,
            "journal_mode": self.journal_mode,
            "problems": list(self.problems),
        }


class Store:
    """A handle on one canonical store."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        projections: Iterable[Projection] | None = None,
        migrations_dir: str | Path | None = None,
        data_root: str | Path | None = None,
        emit_open_event: bool = True,
    ) -> None:
        self.data_root = data_root
        self.path = Path(path) if path is not None else default_db_path(data_root)
        self.projections: tuple[Projection, ...] = (
            tuple(projections) if projections is not None else DEFAULT_PROJECTIONS
        )
        self._migrations_dir = Path(migrations_dir) if migrations_dir else None
        self.conn = connect(self.path)
        try:
            self._steps = migrations_module.apply_all(self.conn, self._migrations_dir)
        except Exception:
            self.conn.close()
            raise
        if emit_open_event:
            # One event per boot. A reopened store says so rather than looking identical
            # to the one that never closed (BOOK 83: recovery is observable).
            self.emit(
                "system.store.opened",
                {
                    "path": str(self.path),
                    "schema_version": self.schema_version,
                    "migrations_applied": [step.version for step in self._steps],
                },
                provenance={"method": "measured", "origin": "store"},
            )

    # ------------------------------------------------------------------ lifecycle

    @property
    def schema_version(self) -> int:
        return migrations_module.schema_version(self.conn)

    @property
    def migrations_applied(self) -> tuple[migrations_module.MigrationStep, ...]:
        return tuple(self._steps)

    @property
    def projection_tables(self) -> tuple[str, ...]:
        return table_names(self.projections)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --------------------------------------------------------------------- writing

    def new_event(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        provenance: dict[str, Any] | None = None,
        **ids: Any,
    ) -> CanonicalEvent:
        """Build an event ready to append. The store assigns ``seq``, not the caller."""
        return CanonicalEvent.build(
            kind,
            payload,
            event_id=new_id(IdKind.EVENT),
            seq=1,  # placeholder; ``append`` replaces it with the canonical value
            ts=time.time(),
            provenance=provenance or {"method": "measured", "origin": "kernel"},
            **ids,
        )

    def emit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        provenance: dict[str, Any] | None = None,
        **ids: Any,
    ) -> AppendResult:
        return self.append(self.new_event(kind, payload, provenance=provenance, **ids))

    def append(self, event: CanonicalEvent) -> AppendResult:
        """Append one event and its projections, or nothing at all."""
        with transaction(self.conn):
            existing = self.conn.execute(
                "SELECT * FROM events WHERE id = ?", (event.id,)
            ).fetchone()
            if existing is not None:
                return AppendResult(
                    event=self._row_to_event(existing), seq=int(existing["seq"]), inserted=False
                )

            seq = self._next_seq()
            stored = event if event.seq == seq else event.model_copy(update={"seq": seq})
            try:
                self.conn.execute(INSERT_EVENT_SQL, self._event_params(stored))
            except sqlite3.Error as exc:
                message = str(exc)
                if "append-only" in message or "cannot modify" in message:
                    raise AppendOnlyViolation(message) from exc
                raise StoreError(f"cannot append event {stored.id} ({stored.kind}): {message}") from exc

            applied = apply_event(self.conn, stored, self.projections)
            return AppendResult(event=stored, seq=seq, inserted=True, projections=applied)

    def append_many(self, events: Sequence[CanonicalEvent]) -> list[AppendResult]:
        """Append a sequence of events, one transaction each (the honest model)."""
        return [self.append(event) for event in events]

    def put_artifact(
        self,
        data: bytes | None = None,
        *,
        file: str | Path | None = None,
        mime: str = "application/octet-stream",
        origin: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **ids: Any,
    ) -> ArtifactRecord:
        """Externalize a payload, then register it with an ``artifact.created`` event."""
        if (data is None) == (file is None):
            raise StoreError("put_artifact needs exactly one of data= or file=")
        if data is not None:
            sha, path, size = store_bytes(data, mime=mime, root=self.data_root)
        else:
            assert file is not None  # guaranteed by the check above
            sha, path, size = store_file(file, mime=mime, root=self.data_root)

        artifact_id = new_artifact_id()
        body = describe(
            artifact_id=artifact_id,
            path=path,
            sha256=sha,
            mime=mime,
            size=size,
            metadata=metadata,
            origin=origin,
        )
        self.emit(
            "artifact.created",
            body,
            provenance={"method": "measured", "origin": "store"},
            **ids,
        )
        return ArtifactRecord(
            id=artifact_id,
            path=str(path),
            sha256=sha,
            mime=mime,
            size=size,
            metadata=dict(metadata or {}),
            origin=dict(origin or {}),
        )

    # --------------------------------------------------------------------- reading

    def get(self, event_id: str) -> CanonicalEvent | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row is not None else None

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def latest_seq(self) -> int:
        return int(self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0])

    def events(
        self,
        *,
        after_seq: int = 0,
        until_seq: int | None = None,
        limit: int | None = None,
        kind: str | None = None,
        namespace: str | None = None,
        mission_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> list[CanonicalEvent]:
        """Ordered range query. Ordering is by ``seq``, always."""
        where: list[str] = ["seq > ?"]
        params: list[Any] = [after_seq]
        if until_seq is not None:
            where.append("seq <= ?")
            params.append(until_seq)
        for column, value in (
            ("kind", kind),
            ("namespace", namespace),
            ("mission_id", mission_id),
            ("task_id", task_id),
            ("run_id", run_id),
            ("agent_id", agent_id),
            ("session_id", session_id),
            ("correlation_id", correlation_id),
        ):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        sql = f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._row_to_event(row) for row in self.conn.execute(sql, params)]

    def iter_events(self, *, batch: int = 1000, **filters: Any) -> Iterator[CanonicalEvent]:
        """Stream the log in batches without holding it all in memory."""
        cursor = filters.pop("after_seq", 0)
        while True:
            chunk = self.events(after_seq=cursor, limit=batch, **filters)
            if not chunk:
                return
            yield from chunk
            cursor = chunk[-1].seq
            if len(chunk) < batch:
                return

    def range(self, seq_from: int, seq_to: int) -> list[CanonicalEvent]:
        return self.events(after_seq=seq_from - 1, until_seq=seq_to)

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return snapshot(self.conn, self.projections)

    def projection_digest(self) -> str:
        return digest(self.conn, self.projections)

    def runs(self, *, state: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs"
        params: list[Any] = []
        if state is not None:
            sql += " WHERE state = ?"
            params.append(state)
        return [dict(row) for row in self.conn.execute(sql + " ORDER BY rowid", params)]

    def run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def artifact(self, artifact_id: str) -> ArtifactRecord | None:
        row = self.conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            return None
        return ArtifactRecord(
            id=row["id"],
            path=row["path"],
            sha256=row["sha256"],
            mime=row["mime"],
            size=int(row["size"]),
            metadata=json.loads(row["metadata"]),
            origin=json.loads(row["origin"]),
        )

    def artifacts(self) -> list[ArtifactRecord]:
        return [
            self.artifact(row["id"])
            for row in self.conn.execute("SELECT id FROM artifacts ORDER BY rowid")
            if row is not None
        ]  # type: ignore[list-item]

    def artifact_bytes(self, artifact_id: str) -> bytes:
        """Read a payload back, verifying its content address on the way out."""
        record = self.artifact(artifact_id)
        if record is None:
            raise StoreError(f"unknown artifact: {artifact_id}")
        path = Path(record.path)
        if not path.is_file():
            raise StoreError(f"artifact {artifact_id} is indexed but its file is missing: {path}")
        actual = hash_file(path)
        if actual != record.sha256:
            raise StoreError(
                f"artifact {artifact_id} is corrupt: recorded sha256 {record.sha256[:12]}, "
                f"on disk {actual[:12]}"
            )
        return path.read_bytes()

    # ---------------------------------------------------------------------- replay

    def replay_into(
        self, target_path: str | Path, *, emit_open_event: bool = False
    ) -> ReplayReport:
        """Rebuild this log into a fresh store and compare the projections.

        ``emit_open_event=False`` by default so the target's log is exactly this log,
        seq for seq -- otherwise the target's own boot event would shift every following
        ``seq`` and make the comparison say more than it should.
        """
        target = Store(
            target_path,
            projections=self.projections,
            migrations_dir=self._migrations_dir,
            data_root=self.data_root,
            emit_open_event=emit_open_event,
        )
        try:
            count = 0
            for event in self.iter_events():
                target.append(event)
                count += 1
            source = self.projection_digest()
            rebuilt = target.projection_digest()
            return ReplayReport(
                events=count, source_digest=source, target_digest=rebuilt, equal=source == rebuilt
            )
        finally:
            target.close()

    def replay_equivalence(self) -> ReplayReport:
        """Replay into a throwaway store under the same data root."""
        with tempfile.TemporaryDirectory(prefix="mh-replay-") as tmp:
            return self.replay_into(Path(tmp) / "replay.sqlite3")

    # ------------------------------------------------------------------- checking

    def verify(self) -> VerifyReport:
        """Cheap, honest self-check: SQLite integrity, gap-free seq, parseable payloads."""
        problems = list(integrity_check(self.conn))
        seqs = [int(row["seq"]) for row in self.conn.execute("SELECT seq FROM events ORDER BY seq")]
        if seqs != list(range(1, len(seqs) + 1)):
            problems.append(
                f"seq is not monotonic and gapless: {len(seqs)} events, first {seqs[:3]}, last {seqs[-3:]}"
            )
        for row in self.conn.execute("SELECT id, payload, provenance FROM events"):
            try:
                payload = json.loads(row["payload"])
                json.loads(row["provenance"])
            except json.JSONDecodeError as exc:
                problems.append(f"event {row['id']} has unparseable JSON: {exc}")
                continue
            if not isinstance(payload.get("v"), int):
                problems.append(f"event {row['id']} payload carries no version marker")
        return VerifyReport(
            ok=not problems,
            events=len(seqs),
            schema_version=self.schema_version,
            journal_mode=journal_mode(self.conn),
            problems=tuple(problems),
        )

    def projections_match_replay(self) -> bool:
        """Stronger than ``verify``: the projections equal a fresh replay of the log."""
        return self.replay_equivalence().equal

    # -------------------------------------------------------------------- internal

    def _next_seq(self) -> int:
        return self.latest_seq() + 1

    def _event_params(self, event: CanonicalEvent) -> tuple[Any, ...]:
        return (
            event.seq,
            event.id,
            event.ts,
            event.kind,
            event.namespace,
            event.mission_id,
            event.task_id,
            event.run_id,
            event.agent_id,
            event.session_id,
            event.runtime_id,
            event.correlation_id,
            event.causation_id,
            canonical_json(event.payload),
            canonical_json(event.provenance),
        )

    def _row_to_event(self, row: sqlite3.Row) -> CanonicalEvent:
        return CanonicalEvent(
            id=row["id"],
            seq=int(row["seq"]),
            ts=float(row["ts"]),
            kind=row["kind"],
            mission_id=row["mission_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            agent_id=row["agent_id"],
            session_id=row["session_id"],
            runtime_id=row["runtime_id"],
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
            payload=json.loads(row["payload"]),
            provenance=json.loads(row["provenance"]),
        )


def open_store(path: str | Path | None = None, **kwargs: Any) -> Store:
    """Open (creating and migrating if needed) the canonical store."""
    return Store(path, **kwargs)
