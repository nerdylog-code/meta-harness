"""Unit tests for the meta-harness runtime.

Run with::

    python -m pytest tests/

or simply::

    python tests/run_all.py

We keep these dependency-free so they run in any Hermes Python env.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "hermes-plugin"
RUNTIME_PKG = PLUGIN_ROOT / "hermes_plugin"
sys.path.insert(0, str(PLUGIN_ROOT))

from hermes_plugin import (
    capabilities as caps,
    characters as chars_mod,
    engines,
    paths,
    plugin_lab,
    redaction,
    store,
    topology,
)


class TempHome:
    """Redirect HERMES_HOME into a temp dir for the duration of a test."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="meta-harness-test-"))
        self._old = os.environ.get("HERMES_HOME")

    def __enter__(self):
        os.environ["HERMES_HOME"] = str(self.tmp)
        return self.tmp

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._old
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestRedaction(unittest.TestCase):
    def test_redacts_openai_key(self):
        s = "token=sk-proj1234567890abcdef1234"
        out = redaction.redact_string(s)
        self.assertIn("REDACTED", out)
        self.assertNotIn("sk-proj1234567890", out)

    def test_redacts_bearer(self):
        out = redaction.redact_string("Authorization: Bearer abcdefghijklmnop1234")
        self.assertIn("REDACTED", out)

    def test_passes_normal_text(self):
        out = redaction.redact_string("hello world")
        self.assertEqual(out, "hello world")

    def test_redact_nested(self):
        v = {"a": "sk-abcdef1234567890abcdef", "b": [1, "ok"], "c": {"d": "Bearer zzzzzzzzzzzzzzzzzzz"}}
        out = redaction.redact(v)
        self.assertIn("REDACTED", out["a"])
        self.assertEqual(out["b"][0], 1)
        self.assertIn("REDACTED", out["c"]["d"])


class TestStore(unittest.TestCase):
    def test_create_and_get_run(self):
        with TempHome() as tmp:
            store.init(tmp / "harness.db")
            rid = store.create_run(topology="solo", engine="hermes",
                                   model=None, task="hello")
            run = store.get_run(rid)
            self.assertEqual(run["status"], "pending")
            self.assertEqual(run["task"], "hello")
            store.update_run(rid, status="completed", result={"ok": True})
            run = store.get_run(rid)
            self.assertEqual(run["status"], "completed")

    def test_append_and_query_events(self):
        with TempHome() as tmp:
            store.init(tmp / "harness.db")
            rid = store.create_run(topology="solo", engine="hermes",
                                   model=None, task="x")
            store.append_event({"event": "tool.started", "run_id": rid,
                                "tool": "read_file"})
            store.append_event({"event": "tool.completed", "run_id": rid,
                                "tool": "read_file"})
            events = store.list_events(rid)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["kind"], "tool.started")

    def test_character_assignment_round_trip(self):
        with TempHome() as tmp:
            store.init(tmp / "harness.db")
            store.assign_character("agent_1", "default", "architect")
            store.assign_character("agent_1", "default", "builder")
            a = store.get_character_assignment("agent_1")
            self.assertEqual(a["character"], "builder")


class TestCapabilities(unittest.TestCase):
    def test_register_and_resolve(self):
        caps.reset()
        caps.register("validation.test", "stub", trust="system")
        p = caps.resolve("validation.test")
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "stub")

    def test_resolve_picks_higher_trust(self):
        caps.reset()
        caps.register("validation.test", "experimental", trust="experimental")
        caps.register("validation.test", "system", trust="system")
        p = caps.resolve("validation.test")
        self.assertEqual(p.name, "system")

    def test_resolve_skips_unavailable(self):
        caps.reset()
        caps.register("validation.test", "broken", available=False)
        caps.register("validation.test", "good", available=True)
        p = caps.resolve("validation.test")
        self.assertEqual(p.name, "good")

    def test_resolve_missing(self):
        caps.reset()
        self.assertIsNone(caps.resolve("does.not.exist"))


class TestCharacters(unittest.TestCase):
    def test_animation_fallback(self):
        # Build a tiny in-memory pack and exercise the fallback chain.
        pack = chars_mod.Pack(id="x", name="x", version="1", renderer="procedural",
                              tile_width=32, tile_height=48)
        # Character with NO "validating" animation.
        pack.characters["alice"] = chars_mod.Character(
            id="alice", name="Alice", sprite="a.svg",
            animations={
                "idle": chars_mod.Animation(frames=[0]),
                "working_file": chars_mod.Animation(frames=[0]),
            },
        )
        chars_mod._PACKS["x"] = pack
        chars_mod._ACTIVE = "x"
        pid, anim, name = chars_mod.animation_for("validating", "alice")
        # Should fall back to "working_file" (preferred fallback) or "idle".
        self.assertIn(name, ("working_file", "idle"))
        self.assertEqual(pid, "x")

    def test_state_to_anim_mapping_covers_all_states(self):
        # The mapping is exhaustive in practice but at minimum all standard
        # states must produce a string.
        for state in ("idle", "starting", "thinking", "working", "blocked",
                      "validating", "completed", "failed", "cancelled"):
            self.assertIn(state, chars_mod._STATE_TO_ANIM)


class TestPluginLab(unittest.TestCase):
    def test_create_validate_activate_rollback(self):
        with TempHome() as tmp:
            store.init(tmp / "harness.db")
            manifest = {"id": "demo", "version": "0.0.1", "provides": ["capability.test"]}
            code = "def go():\n    return 1\n"
            r = plugin_lab.create_version("demo", manifest, code)
            self.assertEqual(r["version"], "v0001")
            v = plugin_lab.validate_version("demo", "v0001")
            self.assertTrue(v["ok"], msg=v["errors"])
            a = plugin_lab.activate_experimental("demo", "v0001")
            self.assertTrue(a["ok"])
            # Create a v2
            plugin_lab.create_version("demo", manifest, code)
            self.assertEqual(plugin_lab._next_version("demo"), "v0003")
            rb = plugin_lab.rollback("demo", "v0001")
            self.assertTrue(rb["ok"])
            self.assertEqual(rb["version"], "v0001")

    def test_invalid_manifest_fails_validation(self):
        with TempHome() as tmp:
            store.init(tmp / "harness.db")
            r = plugin_lab.create_version("bad", {}, "this is not python")
            v = plugin_lab.validate_version("bad", r["version"])
            self.assertFalse(v["ok"])


class TestTopology(unittest.TestCase):
    def test_load_builtins(self):
        with TempHome() as tmp:
            store.init(tmp / "harness.db")
            topology.reset()
            topology.ensure_builtin(paths.topologies_dir())
            n = topology.load_directory(paths.topologies_dir())
            self.assertGreaterEqual(n, 4)
            self.assertIsNotNone(topology.get("solo"))
            self.assertIsNotNone(topology.get("architect-builder"))
            self.assertIsNotNone(topology.get("gate-build"))

    def test_topo_order_breaks_cycles_safely(self):
        with TempHome() as tmp:
            store.init(tmp / "harness.db")
            t = topology.Topology(
                id="cycle", version="1", kind="sequence",
                nodes=[topology.Node(id="a", type="agent"),
                       topology.Node(id="b", type="agent"),
                       topology.Node(id="c", type="agent")],
                edges=[("a", "b"), ("b", "a")],
            )
            order = topology._topo_order(t)
            self.assertEqual(len(order), 3)
            self.assertEqual(set(order), {"a", "b", "c"})


class TestEngines(unittest.TestCase):
    def test_hermes_unavailable_when_no_host(self):
        e = engines.HermesEngine()
        # No host shim installed -> available() is False.
        self.assertFalse(e.available())

    def test_pi_unavailable_when_no_binary(self):
        e = engines.PiEngine()
        # We don't assert False because the test runner may actually have Pi.
        # We DO assert that the call doesn't crash and that default_model is safe.
        self.assertIsInstance(e.available(), bool)
        self.assertIsInstance(e.default_model(), (str, type(None)))


class TestRedactionBounds(unittest.TestCase):
    def test_redact_huge_string(self):
        # Long string WITH the secret inside the kept window so redaction
        # has a chance to fire. (The cap is a defense-in-depth, not a
        # functional correctness requirement.)
        big = "sk-abcdef1234567890abcdef " + ("a" * 10000)
        out = redaction.redact_string(big)
        self.assertIn("REDACTED", out)

    def test_redact_truncates_without_crash(self):
        # Pure stress: 10MB of 'a'. Must not hang or raise.
        big = "a" * (10 * 1024 * 1024)
        out = redaction.redact_string(big)
        self.assertLess(len(out), 10000)


if __name__ == "__main__":
    unittest.main()