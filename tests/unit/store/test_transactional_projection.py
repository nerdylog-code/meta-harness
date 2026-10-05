"""WP-004 A2 -- an event and its projections commit together, or not at all.

The failure mode this guards against is the quiet one: an event stored while its
projection silently did not update, which makes the log and the read models disagree
forever and makes replay equivalence meaningless.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import metaharness_contracts as c
from metaharness.projections import RunsProjection
from metaharness.store import ProjectionError, Store


class RefusingProjection:
    name = "refusing"
    tables: tuple[str, ...] = ()

    def __init__(self, kind: str = "run.started") -> None:
        self.kind = kind
        self.calls = 0

    def apply(self, conn, event):  # type: ignore[no-untyped-def]
        self.calls += 1
        if event.kind == self.kind:
            raise ValueError("deliberate refusal")
        return False


class TransactionalProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-a2-")
        self.root = Path(self._tmp.name)
        self.refusing = RefusingProjection()
        self.store = Store(
            self.root / "store.sqlite3",
            data_root=self.root,
            projections=(RunsProjection(), self.refusing),
        )
        self.run_id = c.new_id(c.IdKind.RUN)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_a2_refused_projection_rolls_back_the_event(self) -> None:
        self.store.emit("run.created", {}, run_id=self.run_id)
        count_before = self.store.count()
        seq_before = self.store.latest_seq()
        digest_before = self.store.projection_digest()

        with self.assertRaises(ProjectionError) as caught:
            self.store.emit("run.started", {"pid": 7}, run_id=self.run_id)

        self.assertIn("refusing", str(caught.exception))
        self.assertIn("run.started", str(caught.exception))
        self.assertEqual(self.store.count(), count_before, "no orphan event may survive")
        self.assertEqual(self.store.latest_seq(), seq_before, "and no seq may be consumed")
        self.assertEqual(self.store.projection_digest(), digest_before, "no orphan projection")
        self.assertEqual(self.store.run(self.run_id)["state"], "created")

    def test_a2_no_partial_row_in_the_other_projection(self) -> None:
        """The rollback covers *every* projection, not just the one that raised."""
        with self.assertRaises(ProjectionError):
            self.store.emit("run.started", {"pid": 7}, run_id=self.run_id)
        self.assertIsNone(self.store.run(self.run_id))

    def test_the_store_is_usable_after_a_rollback(self) -> None:
        with self.assertRaises(ProjectionError):
            self.store.emit("run.started", {}, run_id=self.run_id)
        self.refusing.kind = "never-matches"
        result = self.store.emit("run.started", {"pid": 9}, run_id=self.run_id)
        self.assertTrue(result.inserted)
        self.assertEqual(self.store.run(self.run_id)["state"], "running")
        self.assertFalse(self.store.conn.in_transaction, "the connection must not be left open")

    def test_unknown_run_kind_is_refused_rather_than_guessed(self) -> None:
        """A run.* event the projection does not understand is a defect, not a no-op."""
        with self.assertRaises(ProjectionError) as caught:
            self.store.emit("run.teleported", {}, run_id=self.run_id)
        self.assertIn("unknown run lifecycle event kind", str(caught.exception))
        self.assertEqual(self.store.count(), 1)

    def test_a_non_lifecycle_event_leaves_the_projection_alone(self) -> None:
        """A heartbeat proves liveness; it is not a state change, so the row is untouched."""
        self.store.emit("run.created", {}, run_id=self.run_id)
        before = self.store.snapshot()
        result = self.store.emit("heartbeat.lease", {"index": 1}, run_id=self.run_id)
        self.assertEqual(result.projections, (), "the runs projection must not claim a heartbeat")
        self.assertEqual(self.store.snapshot(), before)

    def test_run_event_without_an_id_is_refused(self) -> None:
        with self.assertRaises(ProjectionError):
            self.store.emit("run.created", {})
        self.assertEqual(self.store.count(), 1)

    def test_projection_failure_does_not_leave_the_wal_locked(self) -> None:
        with self.assertRaises(ProjectionError):
            self.store.emit("run.started", {}, run_id=self.run_id)
        # A second connection must be able to read and write immediately.
        other = Store(self.root / "store.sqlite3", data_root=self.root, emit_open_event=False)
        try:
            self.assertEqual(other.count(), 1)
            self.assertTrue(other.emit("run.created", {}, run_id=self.run_id).inserted)
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
