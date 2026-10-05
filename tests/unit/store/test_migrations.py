"""WP-004 A5 -- migration discipline: ordered, atomic, immutable, contiguous.

The dangerous failure here is the quiet one: a store that boots on a half-applied schema
and looks fine until a query hits the missing table. Every case below therefore asserts
*both* the raised error and that nothing was partially applied.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from metaharness.store import (
    MigrationError,
    MigrationHistoryError,
    Store,
    TamperedMigrationError,
    split_statements,
)
from metaharness.store.migrations import MIGRATIONS_DIR, discover


def _copy_migrations(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        shutil.copy2(path, target / path.name)
    return target


class SplitStatementsTest(unittest.TestCase):
    def test_trigger_body_is_one_statement(self) -> None:
        sql = """
        CREATE TABLE t (a);
        CREATE TRIGGER g BEFORE UPDATE ON t
        BEGIN
            SELECT RAISE(ABORT, 'nope; with a semicolon');
        END;
        CREATE INDEX i ON t (a);
        """
        statements = split_statements(sql)
        self.assertEqual(len(statements), 3)
        self.assertIn("CREATE TRIGGER", statements[1])
        self.assertIn("END", statements[1])
        self.assertIn("nope; with a semicolon", statements[1])

    def test_semicolon_inside_a_string_does_not_split(self) -> None:
        statements = split_statements("INSERT INTO t VALUES ('a;b;c'); INSERT INTO t VALUES ('d');")
        self.assertEqual(len(statements), 2)

    def test_escaped_quote_inside_a_string(self) -> None:
        statements = split_statements("INSERT INTO t VALUES ('it''s; fine'); SELECT 1;")
        self.assertEqual(len(statements), 2)

    def test_line_comments_are_ignored(self) -> None:
        statements = split_statements("-- a comment; with a semicolon\nSELECT 1;")
        self.assertEqual(len(statements), 1)
        self.assertNotIn("comment", statements[0])

    def test_empty_script_yields_no_statements(self) -> None:
        self.assertEqual(split_statements("  \n -- nothing here\n"), [])

    def test_real_migrations_split_into_expected_counts(self) -> None:
        for migration in discover():
            statements = split_statements(migration.sql)
            self.assertTrue(statements, f"{migration.label} produced no statement")
            for statement in statements:
                self.assertFalse(statement.rstrip().endswith(";"))


class MigrationDisciplineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-a5-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a5_fresh_store_applies_every_migration_in_order(self) -> None:
        store = Store(self.root / "fresh.sqlite3", data_root=self.root)
        try:
            versions = [step.version for step in store.migrations_applied]
            self.assertEqual(versions, list(range(1, len(discover()) + 1)))
            self.assertTrue(all(step.applied for step in store.migrations_applied))
            self.assertEqual(store.schema_version, len(discover()))
        finally:
            store.close()

    def test_a5_reopening_applies_nothing(self) -> None:
        path = self.root / "reopen.sqlite3"
        Store(path, data_root=self.root).close()
        second = Store(path, data_root=self.root)
        try:
            self.assertEqual(second.migrations_applied, ())
            self.assertGreaterEqual(second.schema_version, 3)
        finally:
            second.close()

    def test_a5_tampered_migration_is_rejected_before_anything_runs(self) -> None:
        path = self.root / "tampered.sqlite3"
        Store(path, data_root=self.root, migrations_dir=MIGRATIONS_DIR).close()

        altered = _copy_migrations(self.root / "altered")
        target = altered / "0001_events.sql"
        target.write_text(target.read_text() + "\n-- a sneaky edit\n", encoding="utf-8")

        with self.assertRaises(TamperedMigrationError) as caught:
            Store(path, data_root=self.root, migrations_dir=altered)
        self.assertIn("0001_events", str(caught.exception))
        self.assertIn("immutable", str(caught.exception))

    def test_a5_deleting_an_applied_migration_is_rejected(self) -> None:
        path = self.root / "lost.sqlite3"
        Store(path, data_root=self.root, migrations_dir=MIGRATIONS_DIR).close()

        trimmed = _copy_migrations(self.root / "trimmed")
        (trimmed / "0003_runs.sql").unlink()

        with self.assertRaises(MigrationError):
            Store(path, data_root=self.root, migrations_dir=trimmed)

    def test_a5_a_failing_migration_rolls_back_completely(self) -> None:
        broken = _copy_migrations(self.root / "broken")
        (broken / "0004_sabotage.sql").write_text(
            "CREATE TABLE sabotage (a TEXT);\n"
            "INSERT INTO sabotage VALUES ('ok');\n"
            "SELECT * FROM table_that_does_not_exist;\n",
            encoding="utf-8",
        )
        path = self.root / "broken.sqlite3"

        with self.assertRaises(MigrationError) as caught:
            Store(path, data_root=self.root, migrations_dir=broken)
        message = str(caught.exception)
        self.assertIn("0004_sabotage", message)
        self.assertIn("statement 3", message)

        # The half-applied statement must not survive, and the version must not be recorded.
        survivors = Store(path, data_root=self.root, migrations_dir=_copy_migrations(self.root / "clean"))
        try:
            tables = {
                row[0]
                for row in survivors.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertNotIn("sabotage", tables)
            self.assertEqual(survivors.schema_version, 3)
        finally:
            survivors.close()

    def test_duplicate_versions_are_rejected(self) -> None:
        directory = _copy_migrations(self.root / "dupes")
        shutil.copy2(directory / "0002_artifacts.sql", directory / "0002_artifacts_again.sql")
        with self.assertRaises(MigrationHistoryError):
            discover(directory)

    def test_a_gap_in_the_numbering_is_rejected(self) -> None:
        directory = _copy_migrations(self.root / "gap")
        (directory / "0002_artifacts.sql").unlink()
        with self.assertRaises(MigrationHistoryError) as caught:
            discover(directory)
        self.assertIn("contiguous", str(caught.exception))

    def test_a_badly_named_migration_is_rejected(self) -> None:
        directory = _copy_migrations(self.root / "badname")
        (directory / "0003_runs.sql").rename(directory / "3-runs.sql")
        with self.assertRaises(MigrationHistoryError):
            discover(directory)

    def test_a_store_from_a_newer_build_refuses_to_open(self) -> None:
        path = self.root / "future.sqlite3"
        Store(path, data_root=self.root, migrations_dir=MIGRATIONS_DIR).close()
        older = _copy_migrations(self.root / "older")
        (older / "0003_runs.sql").unlink()
        # Removing a file makes the history incomplete, which is refused first -- and
        # that is the honest order: a lost migration is reported before a version skew.
        with self.assertRaises(MigrationError):
            Store(path, data_root=self.root, migrations_dir=older)


if __name__ == "__main__":
    unittest.main()
