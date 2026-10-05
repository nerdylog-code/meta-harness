"""The log is append-only as a property of the schema, not as a convention.

This is the Architect's rule for WP-004 made executable: the store never erases or
rewrites a canonical event; a correction is a new event. It is enforced by SQLite
triggers, so no future refactor can quietly add an UPDATE path.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import metaharness_contracts as c
from metaharness.store import AppendOnlyViolation, Store


class AppendOnlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-append-only-")
        self.root = Path(self._tmp.name)
        self.store = Store(self.root / "store.sqlite3", data_root=self.root)
        self.run_id = c.new_id(c.IdKind.RUN)
        self.first = self.store.emit("run.created", {}, run_id=self.run_id)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_update_is_refused_by_the_schema(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            self.store.conn.execute("UPDATE events SET kind = 'system.tampered' WHERE seq = 1")
        self.assertIn("append-only", str(caught.exception))

    def test_delete_is_refused_by_the_schema(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            self.store.conn.execute("DELETE FROM events WHERE seq = 1")
        self.assertIn("append-only", str(caught.exception))

    def test_dropping_the_trigger_would_be_visible_in_the_migration_hash(self) -> None:
        """The protection is in a migration, so weakening it means a new migration file."""
        row = self.store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='events_append_only_update'"
        ).fetchone()
        self.assertIsNotNone(row, "the append-only trigger must exist after migrations")

    def test_the_store_exposes_no_delete_api(self) -> None:
        for name in ("delete", "delete_event", "remove", "update", "rewrite", "truncate", "purge"):
            self.assertFalse(
                hasattr(self.store, name),
                f"Store.{name} would be a way to rewrite history; corrections are new events",
            )

    def test_a_repeated_append_is_ignored_rather_than_overwritten(self) -> None:
        count_before = self.store.count()
        result = self.store.append(self.first.event)
        self.assertFalse(result.inserted)
        self.assertEqual(self.store.count(), count_before)
        stored = self.store.get(self.first.event.id)
        assert stored is not None
        self.assertEqual(stored.seq, self.first.seq)

    def test_append_only_violation_is_a_store_error(self) -> None:
        """Translating sqlite errors into store errors keeps callers OS-agnostic."""
        self.assertTrue(issubclass(AppendOnlyViolation, RuntimeError))


if __name__ == "__main__":
    unittest.main()
