"""The canonical store (WP-004).

Public surface, and the three things worth knowing before using it:

* **SQLite is the truth** (ADR-0003). Open it with ``open_store()``; call ``emit`` to
  record something that happened; call ``events()`` to read it back; call
  ``replay_into()`` to prove the projections are reproducible.
* **``seq`` belongs to the store.** Build events with ``Store.new_event`` /
  ``CanonicalEvent.build`` and pass any placeholder ``seq``; ``append`` assigns the real,
  gap-free value inside the same transaction as the projections.
* **Reconciliation is not here.** ``metaharness.reconcile.BootReconciler`` reads this
  store, asks the process table, and appends the correction. The store never asks about
  processes.

Replay equivalence, as one call, is the WP-004 acceptance check:

    uv run python -c "import asyncio, metaharness.store as s; print(asyncio.run(s.replay_equivalence_check()))"
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .artifacts import ArtifactRecord, artifacts_dir, hash_file, path_for
from .errors import (
    AppendOnlyViolation,
    MigrationError,
    MigrationHistoryError,
    ProjectionError,
    SchemaUnknownError,
    StoreError,
    TamperedMigrationError,
)
from .migrations import Migration, MigrationStep, schema_version, split_statements
from .store import (
    DEFAULT_DB_NAME,
    AppendResult,
    ReplayReport,
    Store,
    VerifyReport,
    default_db_path,
    open_store,
)

__all__ = [
    "DEFAULT_DB_NAME",
    "AppendOnlyViolation",
    "AppendResult",
    "ArtifactRecord",
    "Migration",
    "MigrationError",
    "MigrationHistoryError",
    "ProjectionError",
    "ReplayReport",
    "SchemaUnknownError",
    "Store",
    "StoreError",
    "TamperedMigrationError",
    "VerifyReport",
    "artifacts_dir",
    "default_db_path",
    "hash_file",
    "open_store",
    "path_for",
    "replay_equivalence_check",
    "schema_version",
    "split_statements",
]


async def replay_equivalence_check(
    path: str | Path | None = None, **kwargs: Any
) -> dict[str, Any]:
    """Replay the canonical store into a throwaway one and report the comparison.

    Async because every caller in the daemon is; the store itself is synchronous on
    purpose (SQLite is an embedded synchronous engine, and pretending otherwise hides
    where the blocking happens), so the work runs on a worker thread.
    """

    def _run() -> dict[str, Any]:
        store = Store(path, **kwargs)
        try:
            report = store.replay_equivalence()
            return {
                "equal": report.equal,
                "events": report.events,
                "source_digest": report.source_digest,
                "target_digest": report.target_digest,
                "path": str(store.path),
            }
        finally:
            store.close()

    return await asyncio.to_thread(_run)
