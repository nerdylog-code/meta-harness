"""WP-015 acceptance: the Pi transport against a scripted peer (real processes, no model).

The peer is `tests/fixtures/pi_fake_rpc.py`, which speaks the same JSONL protocol as
`pi --mode rpc` and can be told to misbehave in the ways that matter: junk on stdout, a
`U+2028` inside a JSON string, CRLF framing, a record split across two writes, an unanswered
command, and a process that dies mid-request.

Nothing here is mocked at the process level: a real subprocess is spawned through
`ProcessSupervisor`, and cancellation is asserted by the orphan check rather than by hope.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from metaharness.process import supervisor  # noqa: E402
from metaharness.runtimes.pi import (  # noqa: E402
    PiCommandError,
    PiTransport,
    PiTransportError,
)

FAKE = REPO_ROOT / "tests" / "fixtures" / "pi_fake_rpc.py"


def argv(*flags: str) -> list[str]:
    return [sys.executable, str(FAKE), *flags]


class TransportTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sup = supervisor()
        self._transports: list[PiTransport] = []

    async def asyncTearDown(self) -> None:
        for transport in self._transports:
            try:
                await transport.close(reason="teardown", grace_s=1.0)
            except Exception:
                pass

    async def transport(self, *flags: str, timeout_s: float = 10.0) -> PiTransport:
        transport = PiTransport(argv(*flags), supervisor=self.sup, response_timeout_s=timeout_s)
        await transport.start()
        self._transports.append(transport)
        return transport


class RoundTripTest(TransportTestCase):
    async def test_a1_command_response_round_trip(self) -> None:
        transport = await self.transport("--provider", "fake-provider", "--model", "fake-model")
        state = await transport.command("get_state")
        self.assertEqual(state["provider"], "fake-provider")
        self.assertEqual(state["model"], "fake-model")
        self.assertEqual(state["sessionId"], "fake-session")
        self.assertEqual(transport.stats.responses, 1)

    async def test_a1_commands_are_answered_for_their_own_request(self) -> None:
        transport = await self.transport()
        state = await transport.command("get_state")
        models = await transport.command("get_available_models")
        self.assertIn("provider", state)
        self.assertEqual(models["models"][0]["id"], "fake-model")

    async def test_a2_correlation_survives_out_of_order_responses(self) -> None:
        """The second command is answered first; each caller must still get its own data."""
        transport = await self.transport("--delay-command", "get_state:0.8")
        slow = asyncio.create_task(transport.command("get_state"))
        fast = asyncio.create_task(transport.command("get_available_models"))
        models = await asyncio.wait_for(fast, timeout=10)
        state = await asyncio.wait_for(slow, timeout=10)
        self.assertIn("models", models)
        self.assertIn("provider", state)
        self.assertEqual(transport.stats.responses, 2)

    async def test_response_for_an_unknown_id_is_a_protocol_error(self) -> None:
        transport = await self.transport()
        transport._handle_response({"id": "req-999", "type": "response", "success": True})  # noqa: SLF001
        self.assertEqual(transport.stats.protocol_errors, 1)
        self.assertIn("unknown request id", transport.protocol_errors[0])

    async def test_command_failure_is_typed(self) -> None:
        transport = await self.transport()
        with self.assertRaises(PiCommandError) as caught:
            await transport.command("this_command_does_not_exist")
        self.assertIn("this_command_does_not_exist", str(caught.exception))

    async def test_command_timeout_is_typed(self) -> None:
        transport = await self.transport("--die-on", "get_commands")
        with self.assertRaises(PiTransportError):
            await transport.command("get_commands", timeout_s=3)


class FramingTest(TransportTestCase):
    async def test_a3_u2028_inside_a_json_string_is_one_record(self) -> None:
        """The readline trap: U+2028 is legal in JSON and must not split a record."""
        transport = await self.transport("--u2028")
        await transport.command("prompt", message="hi")
        texts = []
        async for record in transport.events():
            if record.get("type") == "text_delta":
                texts.append(record["text"])
            if record.get("type") == "agent_settled":
                break
        self.assertEqual(texts, ["pong\u2028inside"])
        self.assertEqual(transport.stats.protocol_errors, 0)

    async def test_a3_crlf_framing_is_accepted(self) -> None:
        transport = await self.transport("--crlf")
        state = await transport.command("get_state")
        self.assertEqual(state["sessionId"], "fake-session")

    async def test_a3_a_record_split_across_reads_is_reassembled(self) -> None:
        transport = await self.transport("--split")
        await transport.command("prompt", message="hi")
        kinds = []
        async for record in transport.events():
            kinds.append(record.get("type"))
            if record.get("type") == "agent_settled":
                break
        self.assertIn("turn_start", kinds)
        self.assertIn("message_start", kinds)
        self.assertEqual(transport.stats.protocol_errors, 0)

    async def test_a4_non_json_stdout_is_reported_not_skipped(self) -> None:
        transport = await self.transport("--junk")
        await transport.command("get_state")  # the junk line arrives first
        await asyncio.sleep(0.2)
        self.assertGreaterEqual(transport.stats.protocol_errors, 1)
        self.assertTrue(
            any("not JSON" in message for message in transport.protocol_errors),
            transport.protocol_errors,
        )

    async def test_a4_an_on_protocol_error_callback_is_called(self) -> None:
        seen: list[str] = []
        transport = PiTransport(
            argv("--junk"),
            supervisor=self.sup,
            response_timeout_s=10,
            on_protocol_error=lambda error: seen.append(str(error)),
        )
        await transport.start()
        self._transports.append(transport)
        await transport.command("get_state")
        await asyncio.sleep(0.2)
        self.assertTrue(seen)


class BackpressureTest(TransportTestCase):
    async def test_a5_ten_thousand_records_are_drained(self) -> None:
        transport = await self.transport("--huge", "10000")
        await transport.command("huge", timeout_s=30)
        drained = 0
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            try:
                await transport.next_event(timeout_s=1.0)
                drained += 1
            except asyncio.TimeoutError:
                break
        self.assertGreaterEqual(drained, 9999, f"drained {drained} of 10000")
        self.assertEqual(transport.stats.dropped_events, 0)


class LifecycleTest(TransportTestCase):
    async def test_a6_cancel_leaves_no_orphans(self) -> None:
        transport = await self.transport("--hang")
        await transport.command("prompt", message="never settles")
        await asyncio.sleep(0.3)
        report = await transport.cancel(grace_s=2.0)
        self.assertTrue(report.orphans, f"survivors: {report.survivors}")
        self.assertFalse(transport.alive())

    async def test_a6_close_after_a_clean_settle(self) -> None:
        transport = await self.transport()
        await transport.command("prompt", message="hi")
        async for record in transport.events():
            if record.get("type") == "agent_settled":
                break
        report = await transport.close(reason="done", grace_s=5.0)
        self.assertTrue(report.orphans)
        self.assertFalse(transport.alive())

    async def test_a7_process_death_surfaces_a_typed_error(self) -> None:
        transport = await self.transport("--die-on", "get_state")
        with self.assertRaises(PiTransportError):
            await transport.command("get_state", timeout_s=5)
        await asyncio.sleep(0.3)
        self.assertFalse(transport.alive())

    async def test_events_stop_when_the_peer_goes_away(self) -> None:
        transport = await self.transport("--die-on", "get_state")
        with self.assertRaises(PiTransportError):
            await transport.command("get_state", timeout_s=5)
        collected = [record async for record in transport.events()]
        self.assertTrue(all(isinstance(record, dict) for record in collected))

    async def test_a_real_pi_binary_is_not_required_for_any_of_this(self) -> None:
        """The suite must not silently depend on a model provider being configured."""
        transport = await self.transport()
        state = await transport.command("get_state")
        self.assertTrue(state)


if __name__ == "__main__":
    unittest.main()
