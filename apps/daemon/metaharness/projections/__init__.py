"""Projections: the read models rebuilt from the event log.

A projection is a *derived* view. Nothing writes to a projection table outside
``apply_event``, which runs inside the same transaction as the event that caused it, so
an event and its projection cannot diverge (ADR-0003, WP-004 constraint 3). Replaying the
log from empty must reproduce them byte-for-byte (A3), which is why the snapshot is taken
through a canonical, sorted serialisation rather than by comparing row objects.

Only projections whose feature exists are registered. An empty speculative table is
untested dead weight (WP-004 risk row), so the runs and artifacts tables exist because
acceptance tests A9 and A6 need them -- not because BOOK 9 lists them.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Iterable, Protocol, runtime_checkable

from metaharness_contracts import CanonicalEvent

from ..store.errors import ProjectionError
from .artifacts import ArtifactsProjection
from .domain import AgentsProjection, MissionsProjection, SessionsProjection
from .runs import RunsProjection

__all__ = [
    "DEFAULT_PROJECTIONS",
    "AgentsProjection",
    "ArtifactsProjection",
    "MissionsProjection",
    "Projection",
    "RunsProjection",
    "SessionsProjection",
    "apply_event",
    "digest",
    "snapshot",
    "table_names",
]


@runtime_checkable
class Projection(Protocol):
    """One derived read model."""

    name: str
    tables: tuple[str, ...]

    def apply(self, conn: sqlite3.Connection, event: CanonicalEvent) -> bool:
        """Fold ``event`` into this projection.

        Returns ``True`` when the event was relevant, ``False`` when it was ignored.
        Raising is the way a projection refuses an event -- and the append transaction
        then rolls back, so a refused event never reaches the log (WP-004 A2).
        """
        ...


DEFAULT_PROJECTIONS: tuple[Projection, ...] = (
    RunsProjection(),
    ArtifactsProjection(),
    MissionsProjection(),
    AgentsProjection(),
    SessionsProjection(),
)


def apply_event(
    conn: sqlite3.Connection, event: CanonicalEvent, projections: Iterable[Projection]
) -> tuple[str, ...]:
    """Apply ``event`` to every interested projection; return the ones that acted."""
    applied: list[str] = []
    for projection in projections:
        try:
            if projection.apply(conn, event):
                applied.append(projection.name)
        except ProjectionError:
            raise
        except Exception as exc:
            raise ProjectionError(
                f"projection {projection.name!r} refused event {event.kind} "
                f"(seq {event.seq}, id {event.id}): {exc}"
            ) from exc
    return tuple(applied)


def table_names(projections: Iterable[Projection]) -> tuple[str, ...]:
    names: list[str] = []
    for projection in projections:
        for table in projection.tables:
            if table not in names:
                names.append(table)
    return tuple(names)


def snapshot(
    conn: sqlite3.Connection, projections: Iterable[Projection]
) -> dict[str, list[dict[str, Any]]]:
    """Every projection table, ordered deterministically.

    Ordered by the table's primary key and serialised with sorted keys, so two stores
    holding the same rows produce the same bytes regardless of insertion order or of how
    the rows got there.
    """
    result: dict[str, list[dict[str, Any]]] = {}
    for table in table_names(projections):
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        result[table] = [dict(row) for row in rows]
    return result


def canonical_json(payload: Any) -> str:
    """The one serialisation used for comparisons and digests."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(
    conn: sqlite3.Connection, projections: Iterable[Projection]
) -> str:
    """A sha256 over the canonical snapshot: equality of two stores in one string."""
    return hashlib.sha256(canonical_json(snapshot(conn, projections)).encode("utf-8")).hexdigest()
