"""WP-004 A9 -- boot reconciliation, and the boundary that keeps it honest.

The store records what was persisted. Deciding that a persisted ``running`` row is no
longer true requires asking the outside world, and that answer becomes a **new** event.
Nothing here rewrites history, and nothing here resumes work (BOOK 83: never blindly
resume a side effect that may already have happened).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import metaharness_contracts as c
from metaharness.reconcile import AllDeadProbe, BootReconciler, StaticProbe
from metaharness.store import Store


class ReconciliationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-a9-")
        self.root = Path(self._tmp.name)
        self.store = Store(self.root / "store.sqlite3", data_root=self.root, emit_open_event=False)
        self.dead_run = c.new_id(c.IdKind.RUN)
        self.alive_run = c.new_id(c.IdKind.RUN)
        self.pidless_run = c.new_id(c.IdKind.RUN)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _seed(self) -> None:
        self.store.emit("run.created", {}, run_id=self.dead_run, agent_id=c.new_id(c.IdKind.AGENT))
        self.store.emit("run.started", {"pid": 999_999}, run_id=self.dead_run)
        self.store.emit("run.created", {}, run_id=self.alive_run)
        self.store.emit("run.started", {"pid": os.getpid()}, run_id=self.alive_run)
        self.store.emit("run.created", {}, run_id=self.pidless_run)
        self.store.emit("run.started", {}, run_id=self.pidless_run)

    def test_a9_a_run_with_a_dead_pid_becomes_orphaned(self) -> None:
        self._seed()
        report = BootReconciler(self.store, AllDeadProbe()).run()

        self.assertEqual(report.runs_examined, 3)
        self.assertEqual(report.orphans_marked, 3)

        row = self.store.run(self.dead_run)
        self.assertEqual(row["state"], "orphaned")
        self.assertIn("no live process for pid 999999", row["reason"])

    def test_a9_a_run_whose_process_is_alive_is_left_alone(self) -> None:
        self._seed()
        report = BootReconciler(self.store, StaticProbe([os.getpid()])).run()

        self.assertIn(self.alive_run, report.skipped_alive)
        self.assertEqual(self.store.run(self.alive_run)["state"], "running")
        self.assertEqual(report.orphans_marked, 2)

    def test_a9_a_run_without_a_pid_is_orphaned_with_its_own_reason(self) -> None:
        self._seed()
        BootReconciler(self.store, AllDeadProbe()).run()
        self.assertEqual(self.store.run(self.pidless_run)["state"], "orphaned")
        self.assertIn("without a recorded pid", self.store.run(self.pidless_run)["reason"])

    def test_a9_the_correction_is_a_new_event_not_an_edit(self) -> None:
        self._seed()
        before = self.store.count()
        BootReconciler(self.store, AllDeadProbe()).run()

        corrections = self.store.events(kind="run.interrupted")
        self.assertEqual(len(corrections), 3)
        correction = [e for e in corrections if e.run_id == self.dead_run][0]
        self.assertTrue(correction.payload_body["orphaned"])
        self.assertEqual(correction.payload_body["previous_state"], "running")
        self.assertEqual(correction.provenance["origin"], "boot_reconciler")
        # The original run.started event is untouched: the log only ever grows.
        self.assertEqual(self.store.count(), before + 4)  # 3 corrections + 1 report
        original = [e for e in self.store.events(kind="run.started") if e.run_id == self.dead_run][0]
        self.assertEqual(self.store.get(original.id).payload_body, {"pid": 999_999})

    def test_a9_reconciliation_reports_its_own_completion(self) -> None:
        self._seed()
        report = BootReconciler(self.store, AllDeadProbe()).run()
        completed = self.store.events(kind="system.reconcile.completed")
        self.assertEqual(len(completed), 1)
        payload = completed[0].payload_body
        self.assertEqual(payload["runs_examined"], report.runs_examined)
        self.assertEqual(payload["orphans_marked"], report.orphans_marked)
        self.assertEqual(payload["leases_released"], 0)
        self.assertIn("worktree", payload["leases_note"])

    def test_a9_running_it_twice_marks_nothing_the_second_time(self) -> None:
        self._seed()
        BootReconciler(self.store, AllDeadProbe()).run()
        second = BootReconciler(self.store, AllDeadProbe()).run()
        self.assertEqual(second.runs_examined, 0, "orphaned runs are no longer 'running'")
        self.assertEqual(second.orphans_marked, 0)
        self.assertEqual(len(self.store.events(kind="run.interrupted")), 3)

    def test_a9_artifacts_are_accounted_for_not_assumed(self) -> None:
        record = self.store.put_artifact(b"still here", mime="text/plain")
        missing = self.store.put_artifact(b"about to vanish", mime="text/plain")
        Path(missing.path).unlink()

        report = BootReconciler(self.store, AllDeadProbe()).run()
        self.assertEqual(report.artifacts_preserved, 1)
        self.assertEqual(report.artifacts_missing, (missing.id,))
        self.assertIsNotNone(self.store.artifact(record.id))

    def test_a9_dry_run_changes_nothing(self) -> None:
        self._seed()
        before = self.store.count()
        report = BootReconciler(self.store, AllDeadProbe(), dry_run=True).run()
        self.assertEqual(report.orphans_marked, 3)
        self.assertEqual(self.store.count(), before, "a dry run must not write")
        self.assertEqual(self.store.run(self.dead_run)["state"], "running")

    def test_reconciliation_never_resumes_work(self) -> None:
        """The only events it may emit are corrections and its own report."""
        self._seed()
        before = self.store.count()
        BootReconciler(self.store, AllDeadProbe()).run()
        emitted = [event.kind for event in self.store.events(after_seq=before)]
        allowed = {"run.interrupted", "session.orphaned", "lease.expired", "runtime.unreachable",
                   "system.reconcile.completed"}
        self.assertTrue(set(emitted).issubset(allowed), f"unexpected events: {emitted}")
        self.assertNotIn("run.started", emitted)
        self.assertNotIn("task.claimed", emitted)

    def test_replay_after_reconciliation_is_still_equivalent(self) -> None:
        self._seed()
        BootReconciler(self.store, AllDeadProbe()).run()
        self.assertTrue(self.store.replay_equivalence().equal)


if __name__ == "__main__":
    unittest.main()
