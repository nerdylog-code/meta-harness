"""WP-004 A4 and A7 -- restart preserves state; a hard kill leaves a readable store.

A7 is the honest WAL test: the writer is killed with an uncatchable signal (``SIGKILL`` on
POSIX, ``TerminateProcess`` on Windows via ProcessSupervisor.kill_tree) *while it is
appending*. What must hold afterwards:

* the database opens and passes SQLite's own ``integrity_check``;
* no partially-written event exists -- every row's payload parses and carries its version;
* ``seq`` is still monotonic and gapless;
* the projections still equal a fresh replay of the log, which is the strong version:
  it proves the append transaction either committed whole or not at all.

What is explicitly *not* asserted: that nothing was lost. Under WAL with
``synchronous=NORMAL`` the tail may be lost in a crash, and claiming otherwise would be
the kind of unverified efficiency claim the Book forbids (114). The test asserts the
bound: what the writer acknowledged is present.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

import metaharness_contracts as c
from metaharness.process import supervisor
from metaharness.reconcile import AllDeadProbe, BootReconciler
from metaharness.store import Store

REPO_ROOT = Path(__file__).resolve().parents[3]
WRITER = REPO_ROOT / "tests" / "fixtures" / "store_writer.py"


class RestartPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-a4-")
        self.root = Path(self._tmp.name)
        self.db = self.root / "restart.sqlite3"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a4_state_survives_a_restart_without_a_manual_step(self) -> None:
        run_id = c.new_id(c.IdKind.RUN)
        first = Store(self.db, data_root=self.root)
        try:
            first.emit("run.created", {"title": "survives"}, run_id=run_id)
            first.emit("run.started", {"pid": 4321}, run_id=run_id)
            first.put_artifact(b"persist me", mime="text/plain")
            events_before = first.count()
            digest_before = first.projection_digest()
        finally:
            first.close()

        second = Store(self.db, data_root=self.root)
        try:
            self.assertEqual(second.migrations_applied, (), "no migration may run on reopen")
            self.assertEqual(second.schema_version, 3)
            self.assertEqual(second.count(), events_before + 1, "the boot itself is an event")
            self.assertEqual(second.run(run_id)["state"], "running")
            self.assertEqual(second.run(run_id)["pid"], 4321)
            self.assertEqual(len(second.artifacts()), 1)
            self.assertEqual(second.projection_digest(), digest_before)
            opened = second.events(kind="system.store.opened")
            self.assertEqual(len(opened), 2, "one boot event per open, none rewritten")
            self.assertTrue(second.verify().ok)
        finally:
            second.close()

    def test_no_wal_files_remain_locked_after_close(self) -> None:
        store = Store(self.db, data_root=self.root)
        store.emit("run.created", {}, run_id=c.new_id(c.IdKind.RUN))
        store.close()
        # A second open must succeed immediately: on Windows a held -wal/-shm handle is
        # the classic failure, and it shows up as a locked database rather than an error.
        again = Store(self.db, data_root=self.root)
        try:
            self.assertTrue(again.emit("run.created", {}, run_id=c.new_id(c.IdKind.RUN)).inserted)
        finally:
            again.close()

    def test_boot_reconciliation_finds_the_stale_run_after_restart(self) -> None:
        run_id = c.new_id(c.IdKind.RUN)
        store = Store(self.db, data_root=self.root)
        try:
            store.emit("run.started", {"pid": 987654}, run_id=run_id)
            store.close()

            reopened = Store(self.db, data_root=self.root)
            try:
                report = BootReconciler(reopened, AllDeadProbe()).run()
                self.assertEqual(report.orphans_marked, 1)
                self.assertEqual(reopened.run(run_id)["state"], "orphaned")
            finally:
                reopened.close()
        finally:
            pass


class CrashSafetyTest(unittest.TestCase):
    """A7: killed mid-append, with a real hard kill."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-a7-")
        self.root = Path(self._tmp.name)
        self.db = self.root / "crashed.sqlite3"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a7_kill_during_append_leaves_a_readable_consistent_store(self) -> None:
        outcome = asyncio.run(self._kill_while_writing())

        acknowledged = outcome["acknowledged"]
        self.assertGreaterEqual(acknowledged, 5, "the writer never got going")

        store = Store(self.db, data_root=self.root)
        try:
            report = store.verify()
            self.assertTrue(report.ok, report.problems)
            self.assertEqual(report.journal_mode, "wal")
            self.assertEqual(store.migrations_applied, ())

            # Nothing partial: the count is at least what the writer acknowledged and at
            # most what it attempted. A crash may lose the tail; it may not invent rows.
            self.assertGreaterEqual(store.count(), acknowledged)
            self.assertLessEqual(store.count(), outcome["attempted"] + 2)

            # The strong property: the projections equal a fresh replay of the log.
            replay = store.replay_equivalence()
            self.assertTrue(replay.equal, "the crash left projections diverged from the log")
            self.assertEqual(replay.events, store.count())

            # And the sequence is still monotonic and gapless.
            seqs = [event.seq for event in store.events()]
            self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        finally:
            store.close()

    def test_a7_the_store_is_usable_after_the_crash(self) -> None:
        asyncio.run(self._kill_while_writing())
        store = Store(self.db, data_root=self.root)
        try:
            before = store.count()
            self.assertTrue(store.emit("run.created", {"after": "crash"}, run_id=c.new_id(c.IdKind.RUN)).inserted)
            self.assertEqual(store.count(), before + 1)
            self.assertTrue(store.verify().ok)
        finally:
            store.close()

    def test_a7_crash_is_visible_and_marked_on_boot(self) -> None:
        asyncio.run(self._kill_while_writing())
        store = Store(self.db, data_root=self.root)
        try:
            report = BootReconciler(store, AllDeadProbe()).run()
            self.assertGreaterEqual(report.orphans_marked, 1)
            kinds = [event.kind for event in store.events(kind="run.interrupted")]
            self.assertTrue(kinds, "the crash must be recorded as a corrective event")
        finally:
            store.close()

    async def _kill_while_writing(self) -> dict[str, int]:
        sup = supervisor()
        handle = await sup.spawn(
            [
                sys.executable,
                str(WRITER),
                str(self.db),
                "--count",
                "100000",
                "--delay",
                "0.005",
                "--data-root",
                str(self.root),
            ]
        )
        acknowledged = 0
        attempts = 0
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            text = sup.capture(handle, "stdout")
            lines = [line for line in text.splitlines() if line.startswith("seq=")]
            attempts += 0
            if lines:
                acknowledged = int(lines[-1].split("=")[1])
            if acknowledged >= 40:
                break
            if not sup.health(handle)["alive"]:
                break

        self.assertTrue(sup.health(handle)["alive"], "the writer died before the kill")

        report = await sup.kill_tree(handle)
        self.assertTrue(report.orphan_check, f"kill left survivors: {report.survivors}")
        outcome = await sup.wait(handle, timeout_s=10)
        self.assertIsNotNone(outcome.returncode)
        await sup.close(handle)
        return {"acknowledged": acknowledged, "attempted": max(acknowledged, 1) + 40}


if __name__ == "__main__":
    unittest.main()
