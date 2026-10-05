"""WP-004 A6 -- large payloads land on the filesystem, never in the database.

The assertion that matters is the boring one: the database file stays small while a 5 MB
payload is stored. A blob column would pass every other test in this package and fail the
actual requirement (BOOK 9, ADR-0003).
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from metaharness.store import Store, StoreError


class ArtifactExternalizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-a6-")
        self.root = Path(self._tmp.name)
        self.store = Store(self.root / "store.sqlite3", data_root=self.root)
        self.payload = bytes(range(256)) * (5 * 1024 * 1024 // 256)  # exactly 5 MiB

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_a6_five_megabytes_do_not_enter_the_database(self) -> None:
        record = self.store.put_artifact(
            self.payload, mime="application/octet-stream", origin={"tool": "test"}
        )
        stored_file = Path(record.path)

        self.assertTrue(stored_file.is_file())
        self.assertEqual(record.size, 5 * 1024 * 1024)
        self.assertEqual(record.sha256, hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(stored_file.stat().st_size, record.size)

        db_bytes = (self.root / "store.sqlite3").stat().st_size
        self.assertLess(db_bytes, 256 * 1024, f"database grew to {db_bytes} bytes for a 5 MB payload")

        row = self.store.conn.execute(
            "SELECT path, sha256, size FROM artifacts WHERE id = ?", (record.id,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["sha256"], record.sha256)
        self.assertNotIn("payload", row.keys())

    def test_the_artifacts_table_declares_no_blob_column(self) -> None:
        columns = self.store.conn.execute("PRAGMA table_info(artifacts)").fetchall()
        types = {str(column["type"]).upper() for column in columns}
        self.assertFalse(
            any("BLOB" in declared for declared in types),
            f"a blob column is forbidden (BOOK 9); declared types: {sorted(types)}",
        )

    def test_identical_bytes_are_stored_once(self) -> None:
        first = self.store.put_artifact(b"same bytes", mime="text/plain")
        second = self.store.put_artifact(b"same bytes", mime="text/plain")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.path, second.path)
        files = [path for path in (self.root / "artifacts").rglob("*") if path.is_file()]
        self.assertEqual(len(files), 1, "content addressing must deduplicate the file")
        self.assertEqual(len(self.store.artifacts()), 2, "but each registration is its own row")

    def test_payload_reads_back_and_is_verified_on_read(self) -> None:
        record = self.store.put_artifact(self.payload, mime="application/octet-stream")
        self.assertEqual(self.store.artifact_bytes(record.id), self.payload)

    def test_a_corrupted_file_is_detected(self) -> None:
        record = self.store.put_artifact(b"original", mime="text/plain")
        Path(record.path).write_bytes(b"tampered")
        with self.assertRaises(StoreError) as caught:
            self.store.artifact_bytes(record.id)
        self.assertIn("corrupt", str(caught.exception))

    def test_a_missing_file_is_reported_not_ignored(self) -> None:
        record = self.store.put_artifact(b"gone soon", mime="text/plain")
        Path(record.path).unlink()
        with self.assertRaises(StoreError) as caught:
            self.store.artifact_bytes(record.id)
        self.assertIn("missing", str(caught.exception))

    def test_artifact_registration_is_an_event(self) -> None:
        record = self.store.put_artifact(b"event-backed", mime="text/plain")
        registration = [
            event for event in self.store.events(kind="artifact.created")
            if event.payload_body.get("artifact_id") == record.id
        ]
        self.assertEqual(len(registration), 1)
        self.assertEqual(registration[0].payload_body["sha256"], record.sha256)
        self.assertEqual(registration[0].seq, 2)

    def test_file_backed_ingestion(self) -> None:
        source = self.root / "big.bin"
        source.write_bytes(self.payload)
        record = self.store.put_artifact(file=source, mime="application/octet-stream")
        self.assertEqual(record.size, source.stat().st_size)
        self.assertEqual(self.store.artifact_bytes(record.id), self.payload)

    def test_put_artifact_needs_exactly_one_source(self) -> None:
        with self.assertRaises(StoreError):
            self.store.put_artifact()
        with self.assertRaises(StoreError):
            self.store.put_artifact(b"x", file=self.root / "whatever")

    def test_artifacts_survive_replay(self) -> None:
        record = self.store.put_artifact(b"replayable", mime="text/plain")
        report = self.store.replay_equivalence()
        self.assertTrue(report.equal)
        self.assertIsNotNone(self.store.artifact(record.id))


if __name__ == "__main__":
    unittest.main()
