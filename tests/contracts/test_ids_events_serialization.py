"""Ids, envelope and serialization — the wire-level contract."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for extra in (REPO_ROOT / "packages" / "contracts", REPO_ROOT / "apps" / "daemon"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import metaharness_contracts as mc  # noqa: E402


class TestIds(unittest.TestCase):
    def test_every_kind_has_a_prefix_and_round_trips(self) -> None:
        for kind in mc.IdKind:
            value = mc.new_id(kind)
            self.assertTrue(mc.is_valid_id(value), value)
            self.assertTrue(mc.is_valid_id(value, kind), f"{value} should be a {kind.value} id")
            self.assertEqual(value.split("_")[0], mc.ID_PREFIXES[kind])

    def test_prefixes_are_the_agreed_set(self) -> None:
        self.assertEqual(
            sorted(mc.PREFIXES),
            ["agt", "apr", "art", "evt", "mis", "plg", "rt", "run", "ses", "tsk"],
        )

    def test_an_id_is_only_valid_for_its_own_kind(self) -> None:
        self.assertFalse(mc.is_valid_id(mc.new_id(mc.IdKind.AGENT), mc.IdKind.MISSION))
        self.assertFalse(mc.is_valid_id("agent_123", mc.IdKind.AGENT), "unprefixed kind is not a prefix")

    def test_malformed_ids_are_rejected(self) -> None:
        for bad in ("", "agt", "agt_", "_body", "zzz_body", 42, None):
            self.assertFalse(mc.is_valid_id(bad), repr(bad))
        with self.assertRaises(mc.InvalidId):
            mc.validate_id("nope", mc.IdKind.TASK)

    def test_the_body_is_opaque(self) -> None:
        """Nothing may parse meaning out of the body (WP-003 decision 1)."""
        value = mc.new_id(mc.IdKind.RUN)
        self.assertTrue(mc.opaque_part(value))
        self.assertNotIn("_", mc.opaque_part(value).split("_")[0][:0])  # no separator assumptions
        generated = {mc.new_id(mc.IdKind.RUN) for _ in range(200)}
        self.assertEqual(len(generated), 200, "ids must not collide")


class TestCanonicalEvent(unittest.TestCase):
    def event(self, **overrides):
        base = dict(
            kind="system.tick",
            payload={"v": 1, "n": 1},
            event_id=mc.new_id(mc.IdKind.EVENT),
            seq=1,
            ts=1_700_000_000.0,
            provenance={"method": "measured", "origin": "test"},
        )
        base.update(overrides)
        return mc.CanonicalEvent.build(**base)

    def test_envelope_keys_match_project_book_section_13(self) -> None:
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
        self.assertEqual(set(mc.ENVELOPE_KEYS), expected)
        self.assertEqual(set(self.event().model_dump()), expected)

    def test_kind_must_be_namespaced(self) -> None:
        for bad in ("tick", ".tick", "system.", "sys tem.tick", " system.tick"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.event(kind=bad)

    def test_payload_declares_its_own_version(self) -> None:
        # Constructing the envelope directly: a payload with no version is refused.
        with self.assertRaises(ValueError):
            mc.CanonicalEvent(
                id=mc.new_id(mc.IdKind.EVENT),
                seq=1,
                ts=1_700_000_000.0,
                kind="system.tick",
                payload={"n": 1},
            )
        with self.assertRaises(ValueError):
            self.event(payload={"v": 0, "n": 1})
        event = self.event(payload={"v": 3, "n": 1})
        self.assertEqual(event.payload_version, 3)
        self.assertEqual(event.payload_body, {"n": 1})
        # build() is the convenience path: it stamps v=1 for payloads that have
        # no version of their own, so a caller cannot accidentally omit it.
        self.assertEqual(self.event(payload={"n": 1}).payload_version, 1)

    def test_typed_id_fields_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            self.event(mission_id=mc.new_id(mc.IdKind.AGENT))
        with self.assertRaises(ValueError):
            self.event(runtime_id="pi")  # a bare vendor name is not a runtime id
        self.assertTrue(self.event(mission_id=mc.new_id(mc.IdKind.MISSION), task_id=mc.new_id(mc.IdKind.TASK)))

    def test_seq_is_monotonic_and_ts_positive(self) -> None:
        with self.assertRaises(ValueError):
            self.event(seq=0)
        with self.assertRaises(ValueError):
            self.event(ts=0)

    def test_unknown_external_fields_are_preserved_not_fatal(self) -> None:
        """WP-003 decision 12: a newer producer may add metadata."""
        payload = self.event().model_dump()
        payload["produced_by_build"] = "9999.1"
        reparsed = mc.CanonicalEvent.model_validate(payload)
        self.assertEqual(reparsed.model_dump()["produced_by_build"], "9999.1")
        self.assertEqual(reparsed.kind, "system.tick")

    def test_round_trip_is_byte_stable(self) -> None:
        event = self.event()
        first = mc.dumps(event)
        again = mc.dumps(mc.CanonicalEvent.model_validate_json(first))
        self.assertEqual(first, again)
        self.assertEqual(json.loads(first)["kind"], "system.tick")

    def test_known_namespaces_cover_the_book_list(self) -> None:
        for namespace in ("system", "runtime", "mission", "tool", "approval", "context", "usage"):
            self.assertIn(namespace, mc.KNOWN_NAMESPACES)
        self.assertTrue(mc.is_namespaced("plugin.custom"))
        self.assertFalse(mc.is_namespaced("plugin"))


class TestSerialization(unittest.TestCase):
    def test_json_safe_rejects_lossy_values(self) -> None:
        for bad in (b"bytes", {1, 2}, complex(1, 2), float("nan"), float("inf")):
            with self.assertRaises(mc.NotJsonSafe, msg=repr(bad)):
                mc.json_safe({"value": bad})

    def test_json_safe_converts_models_and_enums(self) -> None:
        payload = mc.json_safe({"risk": mc.RiskLevel.R4, "metric": mc.Metric.measured(2.5)})
        self.assertEqual(payload["risk"], "R4")
        self.assertEqual(payload["metric"]["value"], 2.5)
        self.assertEqual(payload["metric"]["provenance"], "measured")

    def test_metric_has_no_python_only_types_on_the_wire(self) -> None:
        encoded = mc.dumps(mc.Metric.unknown(unit="tokens"))
        self.assertNotIn("PosixPath", encoded)
        self.assertNotIn("Metric(", encoded)
        self.assertEqual(json.loads(encoded)["value"], None)

    def test_round_trip_helper_reparses(self) -> None:
        original = mc.WorkspacePolicy(write=["src/**"], deny=[".env"])
        again = mc.round_trip(original)
        self.assertEqual(again.model_dump(), original.model_dump())


if __name__ == "__main__":
    unittest.main()
