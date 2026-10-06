"""The event plane: the frozen contract, a durable log, bounded delivery.

WP-002 defined its own envelope in this module. The daemon↔store wiring deleted it, because
a second envelope class is a second source of truth even when it agrees byte for byte. Two
tests here keep it deleted -- one by identity, one by walking the daemon's source with the
AST -- and the rest assert the two things the plane actually promises: every published event
is persisted before anyone hears about it, and a restarted daemon can still show its history.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from metaharness import events as events_module  # noqa: E402
from metaharness.events import ENVELOPE_KEYS, PROVENANCE_METHODS, EventBus  # noqa: E402
from metaharness.store import Store  # noqa: E402
from metaharness_contracts import CanonicalEvent as ContractEvent  # noqa: E402
from metaharness_contracts import IdKind, new_id  # noqa: E402
from pydantic import ValidationError  # noqa: E402

DAEMON_ROOT = REPO_ROOT / "apps" / "daemon" / "metaharness"


def envelope_classes_in(path: Path) -> list[str]:
    """Class definitions named like an event envelope, found via the AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and "CanonicalEvent" in node.name
    ]


class TestEnvelope(unittest.TestCase):
    def test_kind_must_be_namespaced(self) -> None:
        # pydantic wraps the contract's own error, so the assertion names the wrapper and
        # checks the message rather than trusting an exception class alone.
        with self.assertRaises(ValidationError) as caught:
            ContractEvent.build("started", event_id=new_id(IdKind.EVENT), seq=1)
        self.assertIn("namespace.name", str(caught.exception))

    def test_envelope_has_exactly_the_book_keys(self) -> None:
        event = ContractEvent.build("system.x", event_id=new_id(IdKind.EVENT), seq=1)
        self.assertEqual(tuple(event.model_dump().keys()), ENVELOPE_KEYS)
        self.assertEqual(
            set(ENVELOPE_KEYS),
            {
                "id", "seq", "ts", "kind", "mission_id", "task_id", "run_id", "agent_id",
                "session_id", "runtime_id", "correlation_id", "causation_id", "payload",
                "provenance",
            },
        )

    def test_provenance_vocabulary_is_the_book_vocabulary(self) -> None:
        self.assertEqual(
            set(PROVENANCE_METHODS),
            {"measured", "provider_reported", "runtime_reported", "estimated", "unknown"},
        )

    def test_the_plane_re_exports_the_frozen_contract_type_itself(self) -> None:
        self.assertIs(
            events_module.CanonicalEvent,
            ContractEvent,
            "the daemon must use the frozen envelope, not a copy of it",
        )

    def test_the_daemon_defines_no_second_envelope(self) -> None:
        offenders: dict[str, list[str]] = {}
        for path in DAEMON_ROOT.rglob("*.py"):
            found = envelope_classes_in(path)
            if found:
                offenders[path.relative_to(DAEMON_ROOT).as_posix()] = found
        self.assertEqual(
            offenders,
            {},
            "ADR-0017: one envelope. A local CanonicalEvent class is a duplicate that will "
            f"drift; offenders: {offenders}",
        )

    def test_the_detector_catches_a_reintroduced_duplicate(self) -> None:
        """A guard that cannot fail proves nothing."""
        with tempfile.TemporaryDirectory(prefix="mh-env-") as tmp:
            guilty = Path(tmp) / "guilty.py"
            guilty.write_text(
                "from dataclasses import dataclass\n\n\n"
                "@dataclass\nclass CanonicalEvent:\n    kind: str\n",
                encoding="utf-8",
            )
            self.assertEqual(envelope_classes_in(guilty), ["CanonicalEvent"])

            innocent = Path(tmp) / "innocent.py"
            innocent.write_text(
                '"""The envelope is metaharness_contracts.CanonicalEvent."""\nEVENTS = 1\n',
                encoding="utf-8",
            )
            self.assertEqual(envelope_classes_in(innocent), [])


class TestEventBus(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-bus-")
        self.root = Path(self._tmp.name)
        self.db = self.root / "bus.sqlite3"
        self.store = Store(self.db, data_root=self.root)
        self.bus = EventBus(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_publish_persists_before_it_delivers(self) -> None:
        queue = self.bus.subscribe()
        event = self.bus.publish("system.tick", {"n": 1})
        stored = self.store.get(event.id)
        self.assertIsNotNone(stored, "an event nobody persisted must never be delivered")
        assert stored is not None
        self.assertEqual(stored.model_dump(), event.model_dump())
        self.assertEqual(queue.get_nowait().id, event.id)
        self.assertEqual(self.store.count(), 2)  # system.store.opened + this one

    def test_sequence_is_monotonic_and_ids_unique(self) -> None:
        events = [self.bus.publish("system.tick", {"n": i}) for i in range(5)]
        # seq 1 is system.store.opened; the bus never keeps its own counter.
        self.assertEqual([event.seq for event in events], [2, 3, 4, 5, 6])
        self.assertEqual(len({event.id for event in events}), 5)
        self.assertEqual(self.bus.last_seq, 6)
        self.assertEqual(self.bus.last_seq, self.store.latest_seq())

    def test_backlog_read_is_bounded_and_ordered(self) -> None:
        for index in range(5):
            self.bus.publish("system.tick", {"n": index})
        recent = self.bus.recent(3)
        self.assertEqual(len(recent), 3)
        self.assertEqual([event.payload["n"] for event in recent], [2, 3, 4])
        self.assertEqual(self.bus.recent(0), [])
        self.assertEqual(len(self.bus.recent(500)), 6)  # everything, boot event included

    def test_backlog_survives_a_restart(self) -> None:
        """The wiring's whole point: a new process still has history to show."""
        for index in range(4):
            self.bus.publish("system.tick", {"n": index})
        self.store.close()

        reopened = Store(self.db, data_root=self.root)
        try:
            fresh_bus = EventBus(reopened)
            backlog = fresh_bus.recent(10)
            self.assertGreaterEqual(len(backlog), 5)
            self.assertEqual(backlog[-1].seq, fresh_bus.last_seq)
            self.assertTrue(any(event.kind == "system.store.opened" for event in backlog))
        finally:
            reopened.close()

    def test_unknown_provenance_method_is_refused_and_nothing_is_persisted(self) -> None:
        with self.assertRaises(ValueError):
            self.bus.publish("system.tick", {}, method="vibes")
        self.assertEqual(self.store.count(), 1, "a refused event must not be persisted")

    def test_payload_carries_a_version(self) -> None:
        event = self.bus.publish("system.tick", {"n": 1})
        self.assertEqual(event.payload["v"], 1)
        self.assertEqual(event.payload_body, {"n": 1})
        explicit = self.bus.publish("system.tick", {"v": 2, "n": 2})
        self.assertEqual(explicit.payload["v"], 2)

    def test_fanout_reaches_every_subscriber(self) -> None:
        first = self.bus.subscribe()
        second = self.bus.subscribe()
        self.bus.publish("system.tick", {"n": 1})
        self.assertEqual(first.get_nowait().payload["n"], 1)
        self.assertEqual(second.get_nowait().payload["n"], 1)
        self.assertEqual(self.bus.subscriber_count, 2)

    def test_unsubscribe(self) -> None:
        queue = self.bus.subscribe()
        self.bus.unsubscribe(queue)
        self.assertEqual(self.bus.subscriber_count, 0)
        self.bus.publish("system.tick", {})
        self.assertTrue(queue.empty())

    def test_full_queue_drops_the_slow_subscriber_instead_of_stalling(self) -> None:
        slow = self.bus.subscribe(maxsize=1)
        healthy = self.bus.subscribe(maxsize=10)
        for index in range(3):
            self.bus.publish("system.tick", {"n": index})
        self.assertEqual(self.bus.subscriber_count, 1, "the slow subscriber is dropped, loudly")
        self.assertEqual(healthy.get_nowait().payload["n"], 0)
        self.assertEqual(slow.qsize(), 1)

    def test_stream_yields_published_events_and_unsubscribes_on_exit(self) -> None:
        async def scenario() -> list[str]:
            queue = self.bus.subscribe()
            first = self.bus.publish("system.tick", {"n": 0})
            second = self.bus.publish("system.tick", {"n": 1})
            seen: list[str] = []

            async def consume() -> None:
                async for event in self.bus.stream(queue):
                    seen.append(event.id)
                    if len(seen) == 2:
                        break

            await asyncio.wait_for(consume(), timeout=5)
            return seen

        seen = asyncio.run(scenario())
        self.assertEqual(len(seen), 2)
        self.assertEqual(self.bus.subscriber_count, 0, "the stream must unsubscribe on exit")


if __name__ == "__main__":
    unittest.main()
