"""WP-004 A3 -- replay from empty reproduces the live projections exactly.

The Book's gate (78) is "replay reconstructs projections", and the WP's risk row is
specific: a replay test that passes on three fixtures and fails on a real stream proves
nothing. So this runs against a generated log of 10,000 events with interleaved kinds,
ids and namespaces, and compares the two stores with one digest.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import metaharness_contracts as c
from metaharness.store import Store

GENERATED_EVENTS = 10_000


class ReplayEquivalenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-a3-")
        self.root = Path(self._tmp.name)
        self.store = Store(self.root / "live.sqlite3", data_root=self.root, emit_open_event=False)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _generate(self, count: int) -> None:
        """A stream that looks like real traffic: several namespaces, reused ids, gaps."""
        runs = [c.new_id(c.IdKind.RUN) for _ in range(7)]
        for run_id in runs:
            self.store.emit("run.created", {"title": "generated"}, run_id=run_id)
        for index in range(count):
            run_id = runs[index % len(runs)]
            selector = index % 5
            if selector == 0:
                self.store.emit("run.started", {"pid": 1000 + index}, run_id=run_id)
            elif selector == 1:
                # Heartbeats live in the heartbeat namespace (BOOK 13/24): a liveness tick
                # is not a lifecycle transition, and the runs projection must ignore it.
                self.store.emit("heartbeat.lease", {"index": index}, run_id=run_id)
            elif selector == 2:
                self.store.emit(
                    "artifact.created",
                    {
                        "artifact_id": c.new_id(c.IdKind.ARTIFACT),
                        "path": f"/tmp/generated/{index}",
                        "sha256": f"{index:064x}"[-64:],
                        "mime": "text/plain",
                        "size": index,
                    },
                    run_id=run_id,
                )
            elif selector == 3:
                self.store.emit("vendor.metric.sampled", {"index": index, "value": index / 3})
            else:
                self.store.emit("run.completed", {"index": index}, run_id=run_id)

    def test_a3_ten_thousand_events_replay_identically(self) -> None:
        started = time.perf_counter()
        self._generate(GENERATED_EVENTS)
        generate_s = time.perf_counter() - started
        self.assertGreaterEqual(self.store.count(), GENERATED_EVENTS)

        replay_started = time.perf_counter()
        report = self.store.replay_into(self.root / "replayed.sqlite3")
        replay_s = time.perf_counter() - replay_started

        self.assertEqual(report.events, self.store.count())
        self.assertTrue(
            report.equal,
            f"replay diverged: live {report.source_digest[:12]} vs replayed {report.target_digest[:12]}",
        )
        print(
            f"\n  [A3] {report.events} events: appended in {generate_s:.1f}s, "
            f"replayed in {replay_s:.1f}s, identical digest {report.source_digest[:12]}"
        )

    def test_replay_preserves_seq_and_order(self) -> None:
        self._generate(500)
        replayed = Store(self.root / "ordered.sqlite3", data_root=self.root, emit_open_event=False)
        try:
            for event in self.store.iter_events():
                replayed.append(event)
            self.assertEqual(
                [event.seq for event in replayed.events()],
                [event.seq for event in self.store.events()],
            )
            self.assertEqual(
                [(event.kind, event.ts) for event in replayed.events()],
                [(event.kind, event.ts) for event in self.store.events()],
            )
        finally:
            replayed.close()

    def test_replay_is_idempotent_and_repeatable(self) -> None:
        self._generate(300)
        first = self.store.replay_into(self.root / "again.sqlite3")
        second = self.store.replay_into(self.root / "again-2.sqlite3")
        self.assertTrue(first.equal)
        self.assertTrue(second.equal)
        self.assertEqual(first.source_digest, second.source_digest)
        self.assertEqual(first.target_digest, second.target_digest)

    def test_reads_are_ordered_even_when_filters_are_combined(self) -> None:
        self._generate(200)
        runs = self.store.events(namespace="run", limit=25)
        self.assertEqual(len(runs), 25)
        self.assertEqual([event.seq for event in runs], sorted(event.seq for event in runs))
        artifacts = self.store.events(kind="artifact.created")
        self.assertTrue(all(event.kind == "artifact.created" for event in artifacts))
        window = self.store.events(after_seq=100, until_seq=110)
        self.assertEqual([event.seq for event in window], list(range(101, 111)))

    def test_verify_holds_on_the_generated_log(self) -> None:
        self._generate(1000)
        report = self.store.verify()
        self.assertTrue(report.ok, report.problems)
        self.assertEqual(report.journal_mode, "wal")


if __name__ == "__main__":
    unittest.main()
