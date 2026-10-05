"""Runtime contract + conformance: the fake adapter proves the interface is real.

Gate items covered here: FakeRuntime passes conformance, capabilities are
structured/negotiable, an unsupported capability refuses loudly, and a broken
adapter is *detected* rather than merely discouraged.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "packages" / "contracts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages" / "contracts"))

import metaharness_contracts as mc  # noqa: E402


class TestCapabilitySet(unittest.TestCase):
    def test_ids_are_dotted_and_versionable(self) -> None:
        capabilities = mc.CapabilitySet.all_known()
        self.assertTrue(capabilities.capabilities)
        for key, info in capabilities.capabilities.items():
            self.assertIn(".", key)
            self.assertGreaterEqual(info.version, 1)
            self.assertIsInstance(info.supported, bool)

    def test_known_capability_list_matches_the_agreed_names(self) -> None:
        self.assertEqual(
            set(mc.KNOWN_CAPABILITIES),
            {
                "session.streaming",
                "session.steer",
                "session.follow_up",
                "session.resume",
                "tool.events",
                "usage.tokens",
                "usage.cost",
                "approval.native",
                "context.compaction",
                "model.switch",
                "agent.subagents",
                "workspace.worktree",
                "voice.native",
            },
        )

    def test_capabilities_carry_metadata_not_just_booleans(self) -> None:
        capabilities = mc.CapabilitySet(
            capabilities={"tool.events": mc.CapabilityInfo(supported=True, metadata={"kinds": ["tool.started"]})}
        )
        self.assertEqual(capabilities.capabilities["tool.events"].metadata["kinds"], ["tool.started"])
        self.assertTrue(capabilities.supports("tool.events"))

    def test_unsupported_capability_raises_instead_of_pretending(self) -> None:
        capabilities = mc.CapabilitySet.of(**{"session.streaming": True})
        self.assertFalse(capabilities.supports("session.steer"))
        with self.assertRaises(mc.UnsupportedCapability) as caught:
            capabilities.require("session.steer", runtime="rt_x")
        self.assertEqual(caught.exception.capability, "session.steer")
        self.assertIn("session.steer", str(caught.exception))

    def test_unknown_capability_ids_are_allowed(self) -> None:
        """Forward compatibility: a newer runtime may advertise more than we know."""
        capabilities = mc.CapabilitySet(
            capabilities={"telepathy.native": mc.CapabilityInfo(supported=True, version=2)}
        )
        self.assertTrue(capabilities.supports("telepathy.native"))

    def test_malformed_capability_ids_are_refused(self) -> None:
        for bad in ("Streaming", "session", "session..stream", "1session.stream", "session. stream"):
            with self.assertRaises(Exception, msg=repr(bad)):
                mc.CapabilitySet(capabilities={bad: mc.CapabilityInfo(supported=True)})

    def test_merge_prefers_the_newer_report(self) -> None:
        merged = mc.CapabilitySet.of(**{"session.streaming": True}).merge(
            mc.CapabilitySet.of(**{"session.steer": True})
        )
        self.assertEqual(set(merged.supported()), {"session.streaming", "session.steer"})


class TestSessionSpec(unittest.TestCase):
    def spec(self, **overrides):
        base = dict(agent_id=mc.new_id(mc.IdKind.AGENT), runtime_id=mc.DEFAULT_FAKE_RUNTIME_ID, allowed_tools=[])
        base.update(overrides)
        return mc.SessionSpec(**base)

    def test_allowed_tools_is_required(self) -> None:
        with self.assertRaises(Exception):
            self.spec(allowed_tools=None)
        self.assertEqual(self.spec().allowed_tools, [])
        self.assertEqual(self.spec(allowed_tools=["read_file"]).allowed_tools, ["read_file"])

    def test_tool_names_must_be_real_strings(self) -> None:
        with self.assertRaises(Exception):
            self.spec(allowed_tools=[""])


class TestFakeRuntimeConformance(unittest.TestCase):
    def test_static_shape_is_complete(self) -> None:
        report = mc.check_protocol_shape(mc.FakeRuntimeAdapter())
        self.assertTrue(report.ok, report.summary())

    def test_async_shape_is_right(self) -> None:
        shape = mc.adapter_is_async(mc.FakeRuntimeAdapter())
        self.assertTrue(all(shape.values()), shape)
        self.assertTrue(shape["events"], "events is a plain method returning an async iterator")

    def test_full_conformance_passes(self) -> None:
        report = asyncio.run(mc.run_conformance(mc.FakeRuntimeAdapter()))
        self.assertTrue(report.ok, report.summary())
        names = [check.name for check in report.checks]
        for required in (
            "probe returns RuntimeInfo",
            "capabilities returns a CapabilitySet",
            "session without tool restriction is refused",
            "events yields canonical envelopes",
            "usage returns a UsageSample",
            "unsupported capability raises (session.steer)",
            "cancel is accepted",
            "close is accepted",
        ):
            self.assertIn(required, names)
        self.assertIn("unknown is not turned into zero", names)

    def test_conformance_detects_a_broken_adapter(self) -> None:
        class Liar(mc.FakeRuntimeAdapter):
            """Advertises steering, then does nothing; reports usage it never measured."""

            def __init__(self) -> None:
                super().__init__(
                    capabilities=mc.CapabilitySet(
                        capabilities={
                            "session.steer": mc.CapabilityInfo(supported=True),
                            "usage.tokens": mc.CapabilityInfo(supported=True),
                        }
                    )
                )

            async def steer(self, session_id: str, instruction: str) -> None:  # type: ignore[override]
                return None  # silently ignored: exactly the failure mode to catch

            async def usage(self, session_id: str) -> mc.UsageSample:  # type: ignore[override]
                return mc.UsageSample()  # everything unknown although usage.tokens was claimed

        report = asyncio.run(mc.run_conformance(Liar()))
        self.assertFalse(report.ok, "a broken adapter must not pass conformance")
        failures = {check.name for check in report.failures}
        self.assertIn("advertised token usage is actually reported", failures)

    def test_events_are_canonical_and_vendor_free(self) -> None:
        async def scenario() -> list[mc.CanonicalEvent]:
            adapter = mc.FakeRuntimeAdapter()
            session = await adapter.create_session(
                mc.SessionSpec(agent_id=mc.new_id(mc.IdKind.AGENT), runtime_id=adapter.runtime_id, allowed_tools=[])
            )
            await adapter.send(session.session_id, mc.Message(text="hi"))
            return [event async for event in adapter.events(session.session_id)]

        events = asyncio.run(scenario())
        self.assertTrue(events)
        for event in events:
            self.assertTrue(event.kind.startswith(("session.", "message.", "run.")), event.kind)
            self.assertEqual(event.runtime_id, mc.DEFAULT_FAKE_RUNTIME_ID)
            self.assertIn("v", event.payload)

    def test_fake_usage_keeps_unknown_unknown(self) -> None:
        async def scenario() -> mc.UsageSample:
            adapter = mc.FakeRuntimeAdapter()
            session = await adapter.create_session(
                mc.SessionSpec(agent_id=mc.new_id(mc.IdKind.AGENT), runtime_id=adapter.runtime_id, allowed_tools=[])
            )
            return await adapter.usage(session.session_id)

        sample = asyncio.run(scenario())
        self.assertIsNone(sample.provider_cost.value)
        self.assertIs(sample.provider_cost.provenance, mc.Provenance.UNKNOWN)
        self.assertEqual(sample.input_tokens.value, 1200.0)

    def test_unknown_session_is_an_error(self) -> None:
        async def scenario() -> None:
            adapter = mc.FakeRuntimeAdapter()
            await adapter.send("ses_missing", mc.Message(text="hi"))

        with self.assertRaises(KeyError):
            asyncio.run(scenario())


class TestRuntimeInfo(unittest.TestCase):
    def test_runtime_id_must_be_rt_prefixed(self) -> None:
        with self.assertRaises(Exception):
            mc.RuntimeInfo(runtime_id="hermes", name="x", available=True)
        info = mc.RuntimeInfo(runtime_id=mc.DEFAULT_FAKE_RUNTIME_ID, name="fake", available=True)
        self.assertTrue(info.available)


if __name__ == "__main__":
    unittest.main()
