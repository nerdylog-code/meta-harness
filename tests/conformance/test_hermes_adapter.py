"""Hermes/ACP conformance (M2). The adapter against the wire format captured from the binary.

Same discipline as the Pi conformance suite: the peer is scripted from real captures
(`tests/fixtures/hermes_fake_acp.py` ← `docs/protocols/HERMES_ACP.md` ←
`tools/probe_hermes_acp.py`), so these tests assert the behaviour the agent actually exhibits,
not the behaviour the ACP specification promises.

C9 is skipped by design: a real `hermes acp` turn costs credit and needs a configured provider,
so it is not in the suite. It runs in `scripts/e2e_m2.py`, by hand.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from metaharness.runtimes.hermes import HermesRuntimeAdapter  # noqa: E402
from metaharness.runtimes.hermes.transport import AcpTransportError  # noqa: E402
from metaharness_contracts import (  # noqa: E402
    CAP_APPROVAL_NATIVE,
    CAP_SESSION_STEER,
    CAP_SESSION_STREAMING,
    CAP_TOOL_EVENTS,
    CAP_USAGE_COST,
    CAP_USAGE_TOKENS,
    Message,
    SessionSpec,
    UnsupportedCapability,
    new_id,
    IdKind,
)

FAKE = REPO_ROOT / "tests" / "fixtures" / "hermes_fake_acp.py"


def spec_for(agent_id: str | None = None, model: str | None = None, tools: list[str] | None = None) -> SessionSpec:
    return SessionSpec(
        agent_id=agent_id or new_id(IdKind.AGENT),
        runtime_id="rt_hermes",
        workspace=str(REPO_ROOT),
        model=model,
        allowed_tools=tools if tools is not None else ["read"],
    )


class HermesConformanceTest(unittest.IsolatedAsyncioTestCase):
    def make_adapter(self, *flags: str, on_event=None, **kwargs) -> HermesRuntimeAdapter:
        return HermesRuntimeAdapter(
            argv=[sys.executable, str(FAKE), *flags],
            cwd=str(REPO_ROOT),
            on_event=on_event,
            response_timeout_s=30.0,
            probe_timeout_s=20.0,
            **kwargs,
        )

    # ------------------------------------------------------------------ handshake

    async def test_c1_probe_reports_what_the_handshake_answered(self) -> None:
        adapter = self.make_adapter()
        info = await adapter.probe()
        self.assertTrue(info.available, info.detail)
        self.assertEqual(info.name, "hermes-agent")
        self.assertEqual(info.version, "0.21.5")
        self.assertEqual(info.protocol, "acp-jsonrpc")
        self.assertIn("protocol=1", info.detail or "")
        self.assertEqual(adapter.handshake.get("protocolVersion"), 1)

    async def test_c1_a_refused_initialize_is_unavailable_not_a_false_success(self) -> None:
        adapter = self.make_adapter("--fail-initialize")
        info = await adapter.probe()
        self.assertFalse(info.available)
        self.assertIn("did not answer initialize", info.detail or "")

    async def test_c2_capabilities_say_only_what_was_observed(self) -> None:
        adapter = self.make_adapter()
        capabilities = await adapter.capabilities()
        rows = capabilities.capabilities
        for capability in (CAP_SESSION_STREAMING, CAP_TOOL_EVENTS, CAP_USAGE_TOKENS):
            self.assertTrue(rows[capability].supported, capability)
        for capability in (CAP_APPROVAL_NATIVE, CAP_USAGE_COST, CAP_SESSION_STEER):
            self.assertFalse(rows[capability].supported, capability)
            self.assertTrue(rows[capability].note, f"{capability} must carry a reason")
        self.assertIn("no cost field was observed", rows[CAP_USAGE_COST].note or "")

    async def test_c2_models_come_from_the_agent(self) -> None:
        adapter = self.make_adapter()
        models = await adapter.models()
        ids = [model.id for model in models]
        self.assertIn("openai-codex:gpt-6.1-sol", ids)
        self.assertIn("opencode-go:deepseek-v4.1-flash", ids)

    # -------------------------------------------------------------------- session

    async def test_c3_the_served_model_is_the_one_the_runtime_reports(self) -> None:
        events: list[tuple[str, bool]] = []
        adapter = self.make_adapter(on_event=lambda event, transient: events.append((event.kind, transient)))
        session = await adapter.create_session(spec_for(model="openai-codex:gpt-6.1-sol"))
        self.assertEqual(session.detail["model"], "opencode-go:deepseek-v4.1-flash")
        self.assertEqual(session.detail["provider"], "OpenCode Go")
        self.assertEqual(session.detail["tools_enforced"], False)
        opened = [kind for kind, _ in events if kind == "session.opened"]
        self.assertEqual(len(opened), 1)
        await adapter.close(session.session_id)

    async def test_c3_the_requested_model_and_the_served_one_are_both_recorded(self) -> None:
        captured: list[dict] = []
        adapter = self.make_adapter(
            on_event=lambda event, transient: captured.append({"kind": event.kind, "payload": event.payload_body})
            if event.kind == "session.opened"
            else None
        )
        session = await adapter.create_session(spec_for(model="openai-codex:gpt-6.1-sol"))
        payload = captured[0]["payload"]
        self.assertEqual(payload["requested_model"], "openai-codex:gpt-6.1-sol")
        self.assertEqual(payload["model"], "opencode-go:deepseek-v4.1-flash")
        self.assertTrue(payload["model_mismatch"])
        self.assertIn("no method to select one was observed", payload["model_note"] or "")
        await adapter.close(session.session_id)

    # ----------------------------------------------------------------------- turn

    async def test_c4_a_turn_streams_tools_usage_and_settles(self) -> None:
        captured: list[tuple[str, dict, bool]] = []
        adapter = self.make_adapter(
            on_event=lambda event, transient: captured.append((event.kind, event.payload_body, transient))
        )
        session = await adapter.create_session(spec_for())
        await adapter.send(session.session_id, Message(role="user", text="read pyproject.toml"))
        self.assertTrue(await adapter.wait_until_settled(session.session_id, timeout_s=30))

        kinds = [kind for kind, _, _ in captured]
        for expected in (
            "session.opened",
            "message.delta",
            "message.thought_delta",
            "tool.started",
            "tool.completed",
            "usage.sampled",
            "runtime.hermes.settled",
        ):
            self.assertIn(expected, kinds, kinds)

        transient = {kind: flag for kind, _, flag in captured}
        self.assertTrue(transient["message.delta"], "deltas describe a stream in progress")
        self.assertTrue(transient["message.thought_delta"])
        self.assertFalse(transient["tool.started"], "a tool call happened: it is recorded")
        self.assertFalse(transient["tool.completed"])
        self.assertFalse(transient["usage.sampled"])

        tool_done = next(payload for kind, payload, _ in captured if kind == "tool.completed")
        self.assertEqual(tool_done["tool"], "read")
        self.assertEqual(tool_done["status"], "failed")
        self.assertFalse(tool_done["success"], "an observed failed status must not be reported as success")
        self.assertIn("File not found", tool_done["result_preview"])

        assistant = next(payload for kind, payload, _ in captured if kind == "message.completed")
        self.assertEqual(assistant["text"], "The project is metaharness")
        await adapter.close(session.session_id)

    async def test_c4_usage_keeps_provenance_and_leaves_cost_unknown(self) -> None:
        adapter = self.make_adapter()
        session = await adapter.create_session(spec_for())
        await adapter.send(session.session_id, Message(role="user", text="hi"))
        await adapter.wait_until_settled(session.session_id, timeout_s=30)
        sample = await adapter.usage(session.session_id)
        self.assertEqual(sample.input_tokens.value, 68401)
        self.assertEqual(sample.input_tokens.provenance.value, "provider_reported")
        self.assertEqual(sample.output_tokens.value, 360)
        self.assertEqual(sample.reasoning_tokens.value, 94)
        self.assertEqual(sample.cache_read_tokens.value, 57600)
        self.assertIsNone(sample.provider_cost.value)
        self.assertEqual(sample.provider_cost.provenance.value, "unknown")
        await adapter.close(session.session_id)

    async def test_c4_context_pressure_is_not_reported_as_usage(self) -> None:
        captured: list[str] = []
        adapter = self.make_adapter(on_event=lambda event, transient: captured.append(event.kind))
        session = await adapter.create_session(spec_for())
        await adapter.send(session.session_id, Message(role="user", text="hi"))
        await adapter.wait_until_settled(session.session_id, timeout_s=30)
        self.assertIn("runtime.hermes.context_pressure", captured)
        await adapter.close(session.session_id)

    async def test_c4_send_returns_at_acceptance_not_at_completion(self) -> None:
        """The whole reason `send` starts a background turn: ACP answers at the end, and an HTTP
        call that waited for it would take minutes."""
        adapter = self.make_adapter("--hang")
        session = await adapter.create_session(spec_for())
        await adapter.send(session.session_id, Message(role="user", text="never answered"))
        self.assertFalse(adapter.sessions[session.session_id].settled.is_set(), "send must not block until the turn ends")
        self.assertFalse(await adapter.wait_until_settled(session.session_id, timeout_s=1.0))
        await adapter.cancel(session.session_id)

    # ---------------------------------------------------------------- unsupported

    async def test_c6_steering_is_an_explicit_unsupported_capability(self) -> None:
        adapter = self.make_adapter()
        session = await adapter.create_session(spec_for())
        with self.assertRaises(UnsupportedCapability):
            await adapter.steer(session.session_id, "do it differently")
        with self.assertRaises(UnsupportedCapability):
            await adapter.follow_up(session.session_id, Message(role="user", text="and then?"))
        await adapter.close(session.session_id)

    # ------------------------------------------------------------------- lifecycle

    async def test_c7_cancel_kills_the_agent_and_verifies_it(self) -> None:
        import psutil

        captured: list[tuple[str, dict]] = []
        adapter = self.make_adapter(
            "--hang", on_event=lambda event, transient: captured.append((event.kind, event.payload_body))
        )
        session = await adapter.create_session(spec_for())
        await adapter.send(session.session_id, Message(role="user", text="never answered"))
        pid = adapter.sessions[session.session_id].transport.pid
        self.assertTrue(psutil.pid_exists(pid or 0))

        await adapter.cancel(session.session_id)
        cancelled = next(payload for kind, payload in captured if kind == "runtime.hermes.cancelled")
        self.assertTrue(cancelled["orphans"], f"orphan check must pass: {cancelled}")
        self.assertEqual(cancelled["survivors"], [])
        self.assertFalse(psutil.pid_exists(pid or 0), f"agent process {pid} survived the cancel")

    async def test_c7_closing_a_session_is_recorded(self) -> None:
        captured: list[str] = []
        adapter = self.make_adapter(on_event=lambda event, transient: captured.append(event.kind))
        session = await adapter.create_session(spec_for())
        await adapter.close(session.session_id)
        self.assertIn("session.closed", captured)
        self.assertNotIn(session.session_id, adapter.sessions)

    async def test_c8_a_non_json_line_is_a_named_protocol_error(self) -> None:
        adapter = self.make_adapter("--garbage")
        info = await adapter.probe()
        self.assertTrue(info.available, "a garbage line must not take the whole runtime down")
        self.assertTrue(adapter.protocol_errors, "the bad line must be recorded, not swallowed")
        self.assertIn("is not JSON", adapter.protocol_errors[0])
        self.assertIn("this line is not JSON", adapter.protocol_errors[0])

    async def test_c8_an_unknown_method_is_refused_loudly(self) -> None:
        adapter = self.make_adapter()
        session = await adapter.create_session(spec_for())
        with self.assertRaises(AcpTransportError) as caught:
            await adapter.sessions[session.session_id].transport.request("session/does-not-exist", {})
        self.assertIn("-32601", str(caught.exception))
        await adapter.close(session.session_id)

    # ------------------------------------------------------------------- real run

    async def test_c9_a_real_hermes_turn_is_not_run_here(self) -> None:
        self.skipTest("a real hermes acp turn needs a provider and credit: see scripts/e2e_m2.py")


if __name__ == "__main__":
    unittest.main()
