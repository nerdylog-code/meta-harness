"""Boot reconciliation -- deliberately *not* part of the event store.

The Architect's instruction for WP-004 is explicit, and it is the right boundary:

    EventStore      records and replays the truth that was persisted
    BootReconciler  reads that truth, asks the outside world, and emits *new* events

So this module owns the `psutil` call, the decision that a `running` row is a lie, and
the corrective events. The store owns none of those things; it must never need to know
what a process is. Corrections are appended, never written over history (BOOK 83).

What it does on boot:

* a run left in ``running`` whose pid is gone becomes ``orphaned`` (A9), with the reason
  recorded as a **new** ``run.interrupted`` event carrying ``orphaned=true``;
* a run whose pid is genuinely still alive is left alone -- guessing here would be worse
  than doing nothing;
* artifacts are accounted for (present or missing) rather than assumed;
* leases are reported as 0 releases, because the lease surface arrives with the worktree
  feature (BOOK 26/28). Reporting a number we do not have would be fake green.

It never resumes anything. BOOK 83 forbids blindly re-running a side effect that may have
already happened; a human or a task graph decides that, not boot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..store.store import Store
from .probe import PidProbe, ProcessProbe

ORPHAN_REASON_NO_PID = "run was running without a recorded pid at boot"
ORPHAN_REASON_DEAD_PID = "no live process for pid {pid} at boot"


@dataclass(frozen=True)
class ReconcileReport:
    runs_examined: int
    orphans_marked: int
    leases_released: int
    artifacts_preserved: int
    artifacts_missing: tuple[str, ...]
    events_emitted: tuple[str, ...] = ()
    skipped_alive: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "runs_examined": self.runs_examined,
            "orphans_marked": self.orphans_marked,
            "leases_released": self.leases_released,
            "artifacts_preserved": self.artifacts_preserved,
            "artifacts_missing": list(self.artifacts_missing),
            "events_emitted": list(self.events_emitted),
            "skipped_alive": list(self.skipped_alive),
        }


class BootReconciler:
    """Reconcile persisted state against the real world, once, at boot."""

    def __init__(self, store: Store, probe: ProcessProbe | None = None, *, dry_run: bool = False) -> None:
        self.store = store
        self.probe: ProcessProbe = probe if probe is not None else PidProbe()
        self.dry_run = dry_run

    def run(self) -> ReconcileReport:
        examined = 0
        orphans = 0
        emitted: list[str] = []
        skipped: list[str] = []

        for row in self.store.runs(state="running"):
            examined += 1
            run_id = str(row["id"])
            pid = row["pid"]
            if self.probe.alive(pid) if pid else False:
                skipped.append(run_id)
                continue

            reason = ORPHAN_REASON_NO_PID if not pid else ORPHAN_REASON_DEAD_PID.format(pid=pid)
            if not self.dry_run:
                result = self.store.emit(
                    "run.interrupted",
                    {
                        "orphaned": True,
                        "reason": reason,
                        "previous_state": row["state"],
                        "detected_at": time.time(),
                    },
                    provenance={"method": "measured", "origin": "boot_reconciler"},
                    run_id=run_id,
                    mission_id=row["mission_id"],
                    task_id=row["task_id"],
                    agent_id=row["agent_id"],
                    session_id=row["session_id"],
                    runtime_id=row["runtime_id"],
                )
                emitted.append(result.event.id)
            orphans += 1

        preserved, missing = self._account_artifacts()

        if not self.dry_run:
            result = self.store.emit(
                "system.reconcile.completed",
                {
                    "runs_examined": examined,
                    "orphans_marked": orphans,
                    "leases_released": 0,
                    "artifacts_preserved": preserved,
                    "artifacts_missing": list(missing),
                    "leases_note": "the lease surface arrives with the worktree feature (BOOK 26/28)",
                },
                provenance={"method": "measured", "origin": "boot_reconciler"},
            )
            emitted.append(result.event.id)

        return ReconcileReport(
            runs_examined=examined,
            orphans_marked=orphans,
            leases_released=0,
            artifacts_preserved=preserved,
            artifacts_missing=tuple(missing),
            events_emitted=tuple(emitted),
            skipped_alive=tuple(skipped),
        )

    def _account_artifacts(self) -> tuple[int, list[str]]:
        preserved = 0
        missing: list[str] = []
        for row in self.store.conn.execute("SELECT id, path FROM artifacts ORDER BY rowid"):
            path = Path(row["path"])
            if path.is_file():
                preserved += 1
            else:
                missing.append(str(row["id"]))
        return preserved, missing
