"""Event-plane tests: envelope shape, provenance discipline, bus behaviour."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from metaharness.events import ENVELOPE_KEYS, PROVENANCE_METHODS, CanonicalEvent, EventBus  # noqa: E402


class TestEnvelope(unittest.TestCase):
    def test_kind_must_be_namespaced(self) -> None:
        with self.assertRaises(ValueError):
            CanonicalEvent(kind="started", provenance={"method": "measured"})

    def test_provenance_method_is_required_and_validated(self) -> None:
        with self.assertRaises(ValueError):
            CanonicalEvent(kind="system.x", provenance={})
        with self.assertRaises(ValueError):
            CanonicalEvent(kind="system.x", provenance={"method": "vibes"})
        event = CanonicalEvent(kind="system.x", provenance={"method": "estimated"})
        self.assertEqual(event.provenance["method"], "estimated")

    def test_envelope_has_exactly_the_book_keys(self) -> None:
        event = CanonicalEvent(kind="system.x", provenance={"method": "measured"})
        self.assertEqual(tuple(event.to_dict().keys()), ENVELOPE_KEYS)

    def test_envelope_matches_book_section_13(self) -> None:
        expected = {
            "id",
            "seq",
            "ts",
            "kind",
            "mission_id",
            "task_id",
            "run_id",
            "agent_id",
            "session_id",
            "runtime_id",
            "correlation_id",
            "causation_id",
            "payload",
            "provenance",
        }
        self.assertEqual(set(ENVELOPE_KEYS), expected)

    def test_provenance_vocabulary_is_the_book_vocabulary(self) -> None:
        self.assertEqual(
            set(PROVENANCE_METHODS),
            {"measured", "provider_reported", "runtime_reported", "estimated", "unknown"},
        )


class TestEventBus(unittest.TestCase):
    def test_sequence_is_monotonic_and_ids_unique(self) -> None:
        bus = EventBus()
        events = [bus.publish("system.tick", {"n": i}) for i in range(5)]
        self.assertEqual([e.seq for e in events], [1, 2, 3, 4, 5])
        self.assertEqual(len({e.id for e in events}), 5)
        self.assertEqual(bus.last_seq, 5)

    def test_ring_is_bounded(self) -> None:
        bus = EventBus(ring_size=3)
        for i in range(10):
            bus.publish("system.tick", {"n": i})
        recent = bus.recent(50)
        self.assertEqual(len(recent), 3)
        self.assertEqual([e.payload["n"] for e in recent], [7, 8, 9])
        self.assertEqual(bus.recent(0), [])

    def test_fanout_reaches_subscribers(self) -> None:
        bus = EventBus()
        first = bus.subscribe()
        second = bus.subscribe()
        bus.publish("system.tick", {"n": 1})
        self.assertEqual(first.get_nowait().payload["n"], 1)
        self.assertEqual(second.get_nowait().payload["n"], 1)
        self.assertEqual(bus.subscriber_count, 2)

    def test_unsubscribe(self) -> None:
        bus = EventBus()
        queue = bus.subscribe()
        bus.unsubscribe(queue)
        self.assertEqual(bus.subscriber_count, 0)

    def test_full_queue_drops_the_slow_subscriber_instead_of_stalling(self) -> None:
        bus = EventBus()
        slow = bus.subscribe(maxsize=1)
        healthy = bus.subscribe(maxsize=10)
        for i in range(3):
            bus.publish("system.tick", {"n": i})
        self.assertEqual(bus.subscriber_count, 1)
        self.assertEqual(healthy.get_nowait().payload["n"], 0)
        self.assertEqual(slow.qsize(), 1)

    def test_payload_carries_a_version(self) -> None:
        """The frozen contract: every payload declares its own version."""
        bus = EventBus()
        event = bus.publish("system.tick", {"n": 1})
        self.assertEqual(event.payload["v"], 1)
        explicit = bus.publish("system.tick", {"v": 2, "n": 2})
        self.assertEqual(explicit.payload["v"], 2)

    def test_stream_yields_published_events(self) -> None:
        bus = EventBus()

        async def scenario() -> str:
            queue = bus.subscribe()
            bus.publish("system.tick", {"n": 42})
            async for event in bus.stream(queue):
                return event.kind
            return ""

        self.assertEqual(asyncio.run(scenario()), "system.tick")


if __name__ == "__main__":
    unittest.main()
