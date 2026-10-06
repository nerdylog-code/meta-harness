"""WP-017/WP-018 acceptance: the Pi adapter behind the frozen interface.

Two kinds of run live here and they are deliberately not conflated:

* **scripted** — the adapter is driven against `tests/fixtures/pi_fake_rpc.py`, which speaks the
  same protocol with no model and no cost. Everything deterministic runs here, on both OSes.
* **real** — one opt-in test (`METAHARNESS_PI_REAL=1`) against the installed `pi` binary and a
  configured provider. It is skipped with an explicit reason otherwise, because a suite that
  silently skips the real path is how "verified" becomes a lie. The Windows CI runner has no
  provider configured, so there it stays skipped *by design* and is reported separately.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from metaharness.process import supervisor  # noqa: E402
from metaharness.runtimes.pi.adapter import PiRuntimeAdapter  # noqa: E402
from metaharness_contracts import (  # noqa: E402
    ADAPTER_METHODS,
    CAP_APPROVAL_NATIVE,
    CAP_SESSION_STEER,
    CAP_SESSION_STREAMING,
    IdKind,
    Message,
    SessionSpec,
    check_protocol_shape,
    new_id,
)

FAKE = REPO_ROOT / "tests" / "fixtures" / "pi_fake_rpc.py"
REAL = os.environ.get("METAHARNESS_PI_REAL") == "1"


class AdapterTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-adapter-")
        self.root = Path(self._tmp.name)
        self.sup = supervisor()
        self.events: list[tuple] = []
        self.adapters: list[PiRuntimeAdapter] = []

    async def asyncTearDown(self) -> None:
        for adapter in self.adapters:
            try:
                await adapter.close_all()
            except Exception:
                pass
        self._tmp.cleanup()

    def adapter(self, *flags: str, argv: list[str] | None = None) -> PiRuntimeAdapter:
        adapter = PiRuntimeAdapter(
            argv=argv or [sys.executable, str(FAKE), *flags],
            supervisor=self.sup,
            data_root_override=str(self.root),
            on_event=lambda event, transient: self.events.append((event, transient)),
            response_timeout_s=20.0,
            probe_timeout_s=20.0,
        )
        self.adapters.append(adapter)
        return adapter

    def spec(self, **overrides: object) -> SessionSpec:
        base = {
            "agent_id": new_id(IdKind.AGENT),
            "runtime_id": "rt_pi",
            "model": "fake-model",
            "allowed_tools": ["read_file"],
            "metadata": {"run_id": new_id(IdKind.RUN), "mission_id": new_id(IdKind.MISSION)},
        }
        base.update(overrides)
        return SessionSpec(**base)  # type: ignore[arg-type]

    def kinds(self) -> list[str]:
        return [event.kind for event, _ in self.events]


class ShapeTest(AdapterTestCase):
    async def test_a1_the_adapter_satisfies_the_frozen_interface(self) -> None:
        adapter = self.adapter()
        report = check_protocol_shape(adapter)
        self.assertTrue(report.ok, report)
        for name in ADAPTER_METHODS:
            self.assertTrue(hasattr(adapter, name), f"missing {name}")

    async def test_a1_events_is_not_a_coroutine(self) -> None:
        """`events` is declared as `def -> AsyncIterator`, and the shape check says so."""
        adapter = self.adapter()
        self.assertFalse(asyncio.iscoroutinefunction(adapter.events))
        self.assertTrue(asyncio.iscoroutinefunction(adapter.send))

    async def test_a6_capabilities_tell_the_truth(self) -> None:
        adapter = self.adapter()
        capabilities = await adapter.capabilities()
        self.assertTrue(capabilities.supports(CAP_SESSION_STREAMING))
        self.assertTrue(capabilities.supports(CAP_SESSION_STEER))
        self.assertFalse(
            capabilities.supports(CAP_APPROVAL_NATIVE),
            "extension-UI approvals exist in Pi but this adapter does not consume them",
        )
        unsupported = capabilities.unsupported()
        self.assertTrue(unsupported)
        approval = capabilities.capabilities[CAP_APPROVAL_NATIVE]
        self.assertFalse(approval.supported)
        self.assertIn("not consume", approval.note or "")

    async def test_capability_requires_raises_for_an_unsupported_one(self) -> None:
        adapter = self.adapter()
        capabilities = await adapter.capabilities()
        with self.assertRaises(Exception):
            capabilities.require(CAP_APPROVAL_NATIVE, runtime="rt_pi")


class ScriptedSessionTest(AdapterTestCase):
    async def test_a2_a_full_scripted_session(self) -> None:
        adapter = self.adapter("--emit-tools")
        session = await adapter.create_session(self.spec())
        self.assertTrue(session.session_id.startswith("ses_"))
        self.assertEqual(session.runtime_id, "rt_pi")
        self.assertEqual(session.detail["provider"], "fake-provider")
        self.assertEqual(session.detail["model"], "fake-model")

        await adapter.send(session.session_id, Message(text="say pong"))
        settled = await adapter.wait_until_settled(session.session_id, timeout_s=20)
        self.assertTrue(settled, "runtime.pi.settled must arrive")

        kinds = self.kinds()
        for expected in (
            "session.opened",
            "runtime.pi.session",
            "runtime.pi.prompt_accepted",
            "message.started",
            "message.completed",
            "tool.started",
            "tool.completed",
            "usage.sampled",
            "runtime.pi.settled",
        ):
            self.assertIn(expected, kinds, f"{expected} not in {kinds}")

        await adapter.close(session.session_id)
        self.assertIn("session.closed", self.kinds())

    async def test_a2_the_session_directory_lives_under_the_data_root(self) -> None:
        adapter = self.adapter()
        session = await adapter.create_session(self.spec())
        directory = Path(session.detail["session_dir"])
        self.assertTrue(directory.is_dir())
        self.assertTrue(str(directory).startswith(str(self.root)), directory)

    async def test_a2_usage_comes_back_as_a_contract_sample(self) -> None:
        adapter = self.adapter()
        session = await adapter.create_session(self.spec())
        await adapter.send(session.session_id, Message(text="hi"))
        await adapter.wait_until_settled(session.session_id, timeout_s=20)
        sample = await adapter.usage(session.session_id)
        self.assertEqual(sample.input_tokens.value, 117)
        self.assertEqual(sample.input_tokens.provenance.value, "provider_reported")
        self.assertEqual(sample.provider, "fake-provider")
        self.assertEqual(sample.model, "fake-model")

    async def test_a2_usage_before_any_sample_is_unknown_not_zero(self) -> None:
        adapter = self.adapter()
        session = await adapter.create_session(self.spec())
        sample = await adapter.usage(session.session_id)
        self.assertIsNone(sample.input_tokens.value)
        self.assertEqual(sample.input_tokens.provenance.value, "unknown")

    async def test_the_events_iterator_yields_what_the_callback_saw(self) -> None:
        adapter = self.adapter()
        session = await adapter.create_session(self.spec())
        await adapter.send(session.session_id, Message(text="hi"))
        await adapter.wait_until_settled(session.session_id, timeout_s=20)

        collected = []

        async def drain() -> None:
            async for event in adapter.events(session.session_id):
                collected.append(event.kind)
                if event.kind == "runtime.pi.settled":
                    return

        await asyncio.wait_for(drain(), timeout=20)
        self.assertIn("runtime.pi.settled", collected)
        self.assertEqual(
            [kind for kind in self.kinds() if kind in collected].sort(),
            collected.sort(),
        )

    async def test_deltas_are_reported_as_transient(self) -> None:
        adapter = self.adapter()
        session = await adapter.create_session(self.spec())
        await adapter.send(session.session_id, Message(text="hi"))
        await adapter.wait_until_settled(session.session_id, timeout_s=20)
        transient = [event.kind for event, is_transient in self.events if is_transient]
        persisted = [event.kind for event, is_transient in self.events if not is_transient]
        self.assertIn("message.delta", transient)
        self.assertNotIn("message.delta", persisted)
        self.assertIn("message.completed", persisted)

    async def test_steering_and_follow_up_are_accepted(self) -> None:
        adapter = self.adapter()
        session = await adapter.create_session(self.spec())
        await adapter.send(session.session_id, Message(text="hi"))
        await adapter.steer(session.session_id, "be brief")
        await adapter.follow_up(session.session_id, Message(text="and again"))
        await adapter.wait_until_settled(session.session_id, timeout_s=20)
        self.assertIn("runtime.pi.settled", self.kinds())

    async def test_a4_cancel_kills_the_tree_and_says_so(self) -> None:
        adapter = self.adapter("--hang")
        session = await adapter.create_session(self.spec())
        await adapter.send(session.session_id, Message(text="never settles"))
        await asyncio.sleep(0.4)
        await adapter.cancel(session.session_id)

        cancelled = [event for event, _ in self.events if event.kind == "runtime.pi.cancelled"]
        self.assertEqual(len(cancelled), 1)
        self.assertTrue(cancelled[0].payload["orphans"], cancelled[0].payload)
        self.assertFalse(adapter.sessions[session.session_id].transport.alive())

    async def test_artifacts_is_empty_and_does_not_invent_paths(self) -> None:
        adapter = self.adapter()
        session = await adapter.create_session(self.spec())
        self.assertEqual(await adapter.artifacts(session.session_id), [])

    async def test_unknown_session_is_a_key_error_not_a_silent_no_op(self) -> None:
        adapter = self.adapter()
        with self.assertRaises(KeyError):
            await adapter.send("ses_missing", Message(text="hi"))


class ProbeTest(AdapterTestCase):
    async def test_probe_reports_what_answered(self) -> None:
        adapter = self.adapter()
        info = await adapter.probe()
        self.assertTrue(info.available)
        self.assertEqual(info.runtime_id, "rt_pi")
        self.assertEqual(info.protocol, "jsonl-rpc")
        self.assertIn("fake-provider", info.detail or "")

    async def test_probe_of_a_missing_binary_fails_loudly(self) -> None:
        adapter = self.adapter(argv=["definitely-not-a-real-binary", "--mode", "rpc"])
        info = await adapter.probe()
        self.assertFalse(info.available)
        self.assertIn("cannot start", info.detail or "")
        self.assertIn("runtime.unreachable", self.kinds())

    async def test_models_are_listed(self) -> None:
        adapter = self.adapter()
        models = await adapter.models()
        self.assertEqual([model.id for model in models], ["fake-model"])
        self.assertEqual(models[0].provider, "fake-provider")


@unittest.skipUnless(
    REAL,
    "set METAHARNESS_PI_REAL=1 to run against the installed pi binary and a configured "
    "provider; the CI runner has no provider, so this stays skipped there by design and is "
    "reported separately from the scripted suite",
)
class RealPiTest(AdapterTestCase):
    """One real turn. Opt-in, because it spends the owner's provider credits."""

    def adapter(self, *flags: str, argv: list[str] | None = None) -> PiRuntimeAdapter:  # type: ignore[override]
        return super().adapter(argv=["pi", "--mode", "rpc"])

    async def test_a3_a_real_pi_session_streams_and_reports_usage(self) -> None:
        adapter = self.adapter()
        info = await adapter.probe()
        self.assertTrue(info.available, info.detail)
        session = await adapter.create_session(
            self.spec(model=None, allowed_tools=[], metadata={"run_id": new_id(IdKind.RUN)})
        )
        await adapter.send(session.session_id, Message(text="Reply with the single word: pong"))
        settled = await adapter.wait_until_settled(session.session_id, timeout_s=120)
        self.assertTrue(settled, "a real pi run must settle")

        kinds = self.kinds()
        self.assertIn("message.completed", kinds)
        self.assertIn("usage.sampled", kinds)
        sample = await adapter.usage(session.session_id)
        self.assertIsNotNone(sample.input_tokens.value)
        self.assertEqual(sample.input_tokens.provenance.value, "provider_reported")
        await adapter.close(session.session_id)


if __name__ == "__main__":
    unittest.main()
