"""WP-004 A8 -- the JSONL export is derived, lossless, and load-bearing for nothing.

Two properties, and the second is the one people forget:

1. an export re-imports into a store with the same state;
2. deleting every export changes nothing about the system.

Plus the source-level rule from ADR-0003: no module outside `metaharness.export` may read
a `.jsonl` path. That one is checked with a grep over the tree rather than an import,
because the defect it prevents is a habit, not a call.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import metaharness_contracts as c
from metaharness.export import ExportReport, export_jsonl, import_jsonl, read_jsonl
from metaharness.store import Store

REPO_ROOT = Path(__file__).resolve().parents[3]
DAEMON_ROOT = REPO_ROOT / "apps" / "daemon" / "metaharness"


def jsonl_literals_in(path: Path) -> list[str]:
    """Quoted ``.jsonl`` literals in a module -- read via the AST, not by grep.

    A grep would flag the word "JSONL" in a docstring (which is prose about the rule, not
    a violation of it) and would miss nothing real. The AST sees the string literals a
    module would actually hand to ``open()``.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and ".jsonl" in node.value
    ]


class JsonlExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-a8-")
        self.root = Path(self._tmp.name)
        self.store = Store(self.root / "store.sqlite3", data_root=self.root)
        self.run_id = c.new_id(c.IdKind.RUN)
        self.store.emit("run.created", {"title": "export me"}, run_id=self.run_id)
        self.store.emit("run.started", {"pid": 1}, run_id=self.run_id)
        self.store.put_artifact(b"bytes for the export", mime="text/plain")

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_a8_export_round_trips_into_the_same_state(self) -> None:
        path = self.root / "export.jsonl"
        report = export_jsonl(self.store, path)
        self.assertIsInstance(report, ExportReport)
        self.assertEqual(report.events, self.store.count())
        self.assertEqual(report.sha256, __import__("hashlib").sha256(path.read_bytes()).hexdigest())

        rebuilt = Store(self.root / "rebuilt.sqlite3", data_root=self.root, emit_open_event=False)
        try:
            imported = import_jsonl(rebuilt, path)
            self.assertEqual(imported.inserted, self.store.count())
            self.assertEqual(imported.duplicates, 0)
            self.assertEqual(rebuilt.projection_digest(), self.store.projection_digest())
            self.assertEqual([event.seq for event in rebuilt.events()],
                             [event.seq for event in self.store.events()])
        finally:
            rebuilt.close()

    def test_a8_importing_twice_is_idempotent(self) -> None:
        path = self.root / "export.jsonl"
        export_jsonl(self.store, path)
        rebuilt = Store(self.root / "twice.sqlite3", data_root=self.root, emit_open_event=False)
        try:
            first = import_jsonl(rebuilt, path)
            digest = rebuilt.projection_digest()
            second = import_jsonl(rebuilt, path)
            self.assertEqual(first.inserted, self.store.count())
            self.assertEqual(second.inserted, 0)
            self.assertEqual(second.duplicates, self.store.count())
            self.assertEqual(rebuilt.projection_digest(), digest)
        finally:
            rebuilt.close()

    def test_a8_deleting_the_export_changes_nothing(self) -> None:
        path = self.root / "export.jsonl"
        export_jsonl(self.store, path)
        before = self.store.projection_digest()
        events_before = self.store.count()

        path.unlink()

        self.assertEqual(self.store.projection_digest(), before)
        self.assertEqual(self.store.count(), events_before)
        self.assertTrue(self.store.verify().ok)
        self.assertTrue(self.store.replay_equivalence().equal)

    def test_export_is_deterministic(self) -> None:
        first = self.root / "one.jsonl"
        second = self.root / "two.jsonl"
        export_jsonl(self.store, first)
        export_jsonl(self.store, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_export_lines_are_canonical_and_parseable(self) -> None:
        path = self.root / "lines.jsonl"
        export_jsonl(self.store, path)
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        header = json.loads(lines[0])
        self.assertEqual(header["format"], "metaharness.events.v1")
        self.assertEqual(header["events"], self.store.count())

        events = list(read_jsonl(path))
        self.assertEqual(len(events), self.store.count())
        for line, event in zip(lines[1:], events):
            self.assertEqual(line, json.dumps(json.loads(line), sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            self.assertEqual(event.seq, events.index(event) + 1)

    def test_a_truncated_export_is_refused_with_a_line_number(self) -> None:
        path = self.root / "broken.jsonl"
        export_jsonl(self.store, path)
        text = path.read_text(encoding="utf-8")
        path.write_text(text[:-3] + "\n{oops\n", encoding="utf-8")
        with self.assertRaises(Exception) as caught:
            list(read_jsonl(path))
        self.assertIn("not valid JSON", str(caught.exception))


class JsonlIsNotAnAuthorityTest(unittest.TestCase):
    def test_no_module_outside_export_reads_a_jsonl_file(self) -> None:
        offenders: dict[str, list[str]] = {}
        for path in DAEMON_ROOT.rglob("*.py"):
            relative = path.relative_to(DAEMON_ROOT).as_posix()
            if relative.startswith("export/"):
                continue
            literals = jsonl_literals_in(path)
            if literals:
                offenders[relative] = literals
        self.assertEqual(
            offenders,
            {},
            "ADR-0003: JSONL is derived. Reading it as authority outside metaharness.export "
            f"makes a second source of truth; offenders: {offenders}",
        )

    def test_the_detector_actually_catches_an_offender(self) -> None:
        """A check that cannot fail proves nothing."""
        with tempfile.TemporaryDirectory(prefix="mh-jsonl-") as tmp:
            guilty = Path(tmp) / "guilty.py"
            guilty.write_text(
                'def load(path):\n    with open(path) as f:  # "events.jsonl" elsewhere\n'
                '        return f.read()\n\nNAME = "events.jsonl"\n',
                encoding="utf-8",
            )
            self.assertEqual(jsonl_literals_in(guilty), ["events.jsonl"])

            innocent = Path(tmp) / "innocent.py"
            innocent.write_text(
                '"""JSONL is produced from the store by metaharness.export."""\n'
                "EXPORTED = True\n",
                encoding="utf-8",
            )
            self.assertEqual(
                jsonl_literals_in(innocent),
                [],
                "prose about JSONL is not a second source of truth",
            )


if __name__ == "__main__":
    unittest.main()
