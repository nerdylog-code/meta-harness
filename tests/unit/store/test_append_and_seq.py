"""WP-004 A1 -- append, monotonic gapless ``seq``, ordered range reads.

Plus the two properties that make A1 usable by an adapter: the envelope round-trips
exactly, and re-sending an event is idempotent rather than duplicated.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import metaharness_contracts as c
from metaharness.store import Store


class RefusingProjection:
    """A projection that refuses one kind -- used by A2 in the sibling module."""

    name = "refusing"
    tables: tuple[str, ...] = ()

    def apply(self, conn, event):  # type: ignore[no-untyped-def]
        if event.kind == "run.started":
            raise ValueError("deliberate refusal")
        return False


class AppendAndSeqTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-a1-")
        self.root = Path(self._tmp.name)
        self.store = Store(self.root / "store.sqlite3", data_root=self.root)
        self.run_id = c.new_id(c.IdKind.RUN)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_a1_seq_is_monotonic_and_gapless(self) -> None:
        seqs = [self.store.emit("run.created", {}, run_id=self.run_id).seq for _ in range(25)]
        # seq 1 belongs to this boot's system.store.opened: the store says when it opened.
        self.assertEqual(self.store.events()[0].kind, "system.store.opened")
        self.assertEqual(seqs, list(range(2, 27)))
        self.assertEqual(self.store.latest_seq(), 26)
        self.assertEqual(self.store.count(), 26)
        self.assertTrue(self.store.verify().ok, self.store.verify().problems)

    def test_a1_range_query_is_ordered_and_filterable(self) -> None:
        for index in range(6):
            self.store.emit(
                "run.created" if index % 2 == 0 else "artifact.created",
                {"artifact_id": c.new_id(c.IdKind.ARTIFACT), "path": "p", "sha256": "s", "mime": "m", "size": 1}
                if index % 2
                else {},
                run_id=self.run_id if index % 2 == 0 else None,
            )

        everything = self.store.events()
        self.assertEqual([event.seq for event in everything], [1, 2, 3, 4, 5, 6, 7])

        window = self.store.range(2, 4)
        self.assertEqual([event.seq for event in window], [2, 3, 4])
        self.assertEqual([event.seq for event in self.store.events(after_seq=4)], [5, 6, 7])

        runs_only = self.store.events(kind="run.created")
        self.assertEqual(len(runs_only), 3)
        self.assertTrue(all(event.kind == "run.created" for event in runs_only))
        self.assertEqual(len(self.store.events(namespace="artifact")), 3)

        by_run = self.store.events(run_id=self.run_id)
        self.assertEqual(len(by_run), 3)
        self.assertTrue(all(event.run_id == self.run_id for event in by_run))

    def test_seq_is_not_moved_by_a_rollback(self) -> None:
        """A refused append must not burn a sequence number."""
        store = Store(
            self.root / "rolled.sqlite3",
            data_root=self.root,
            projections=(RefusingProjection(),),
        )
        try:
            with self.assertRaises(Exception):
                store.emit("run.started", {}, run_id=self.run_id)
            self.assertEqual(store.count(), 1)  # only system.store.opened
            self.assertEqual(store.emit("run.created", {}, run_id=self.run_id).seq, 2)
        finally:
            store.close()

    def test_every_envelope_field_round_trips(self) -> None:
        result = self.store.emit(
            "run.created",
            {"nested": {"a": [1, 2, 3]}, "unicode": "café-日本語", "flag": True},
            run_id=self.run_id,
            mission_id=c.new_id(c.IdKind.MISSION),
            task_id=c.new_id(c.IdKind.TASK),
            agent_id=c.new_id(c.IdKind.AGENT),
            session_id=c.new_id(c.IdKind.SESSION),
            runtime_id=c.new_id(c.IdKind.RUNTIME),
            correlation_id=c.new_id(c.IdKind.EVENT),
            causation_id=c.new_id(c.IdKind.EVENT),
            provenance={"method": "provider_reported", "origin": "test"},
        )
        stored = self.store.get(result.event.id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.model_dump(), result.event.model_dump())
        self.assertEqual(stored.payload["unicode"], "café-日本語")
        self.assertEqual(stored.provenance["method"], "provider_reported")
        self.assertEqual(stored.namespace, "run")

    def test_append_is_idempotent_by_event_id(self) -> None:
        """An adapter that reconnects and re-sends must not create a second event."""
        first = self.store.emit("run.created", {}, run_id=self.run_id)
        before = self.store.projection_digest()
        count_before = self.store.count()

        repeat = self.store.append(first.event)

        self.assertFalse(repeat.inserted)
        self.assertEqual(repeat.seq, first.seq)
        self.assertEqual(self.store.count(), count_before)
        self.assertEqual(self.store.projection_digest(), before)
        self.assertEqual(repeat.event.model_dump(), first.event.model_dump())

    def test_idempotent_replay_does_not_move_seq(self) -> None:
        first = self.store.emit("run.created", {}, run_id=self.run_id)
        self.store.emit("run.started", {"pid": 1}, run_id=self.run_id)
        self.store.append(first.event)
        self.assertEqual(self.store.latest_seq(), 3)
        self.assertEqual(self.store.count(), 3)

    def test_unknown_namespace_is_persisted_verbatim(self) -> None:
        """The store records; it must not interpret a plugin's namespace."""
        result = self.store.emit("vendor.custom.thing", {"v": 1, "anything": [1, "two"]})
        stored = self.store.get(result.event.id)
        assert stored is not None
        self.assertEqual(stored.kind, "vendor.custom.thing")
        self.assertEqual(stored.namespace, "vendor")
        self.assertEqual(stored.payload_body["anything"], [1, "two"])

    def test_iter_events_streams_in_batches(self) -> None:
        for _ in range(12):
            self.store.emit("run.created", {}, run_id=self.run_id)
        chunks = list(self.store.iter_events(batch=5))
        self.assertEqual(len(chunks), 13)  # 12 + this boot's system.store.opened
        self.assertEqual([event.seq for event in chunks], list(range(1, 14)))


if __name__ == "__main__":
    unittest.main()
