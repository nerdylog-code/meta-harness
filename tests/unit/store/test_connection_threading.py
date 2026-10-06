"""The store is used from more than one thread, and that has to be safe.

This file exists because the daemon↔store wiring broke on exactly this: the connection is
opened in the lifespan (the event loop's thread), while FastAPI runs synchronous endpoints
like ``/health`` and ``/v1/events`` in a worker thread. SQLite refused the second thread with
``ProgrammingError: SQLite objects created in a thread can only be used in that same
thread``, and every one of those endpoints returned a 500.

The fix is not ``check_same_thread=False`` on its own -- that flag only silences the check
while leaving the connection genuinely unsafe for concurrent use. It is the flag *plus* one
lock that serialises every read and every write, which is what these tests pin down.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import metaharness_contracts as c
from metaharness.store import Store


class ConnectionThreadingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-thread-")
        self.root = Path(self._tmp.name)
        self.store = Store(self.root / "threads.sqlite3", data_root=self.root)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _in_worker(self, work) -> list[BaseException]:  # type: ignore[no-untyped-def]
        errors: list[BaseException] = []

        def runner() -> None:
            try:
                work()
            except BaseException as exc:  # noqa: BLE001 - the point is to capture anything
                errors.append(exc)

        thread = threading.Thread(target=runner, name="mh-worker")
        thread.start()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "the worker thread hung")
        return errors

    def test_writes_and_reads_work_from_a_worker_thread(self) -> None:
        """The exact shape of the daemon: a sync endpoint called off the loop thread."""
        run_id = c.new_id(c.IdKind.RUN)

        def work() -> None:
            self.store.emit("run.created", {"from": "worker"}, run_id=run_id)
            self.store.emit("run.started", {"pid": 1}, run_id=run_id)
            self.store.count()
            self.store.latest_seq()
            self.store.events(limit=5)
            self.store.runs()
            self.store.snapshot()
            self.store.projection_digest()
            report = self.store.verify()
            assert report.ok, report.problems

        self.assertEqual(self._in_worker(work), [])
        row = self.store.run(run_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["state"], "running")

    def test_many_threads_keep_seq_monotonic_and_gapless(self) -> None:
        """Concurrency must not produce a duplicate or a missing sequence number."""
        threads = 8
        per_thread = 10
        errors: list[BaseException] = []
        lock = threading.Lock()

        def writer() -> None:
            try:
                for index in range(per_thread):
                    self.store.emit("system.tick", {"i": index})
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        workers = [threading.Thread(target=writer) for _ in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)

        self.assertEqual(errors, [])
        seqs = [event.seq for event in self.store.events()]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)), "seq must stay gap-free under contention")
        self.assertEqual(self.store.count(), threads * per_thread + 1)  # + the boot event
        self.assertTrue(self.store.verify().ok)

    def test_a_read_from_another_thread_during_a_write_does_not_interleave(self) -> None:
        """The lock is what makes one shared connection legal, so prove it holds."""
        stop = threading.Event()
        observed: list[int] = []

        def reader() -> None:
            while not stop.is_set():
                observed.append(self.store.count())

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for index in range(50):
                self.store.emit("system.tick", {"i": index})
        finally:
            stop.set()
            thread.join(timeout=30)

        self.assertEqual(observed, sorted(observed), "a read must never see the log move backwards")
        self.assertEqual(observed[-1], self.store.count())


if __name__ == "__main__":
    unittest.main()
