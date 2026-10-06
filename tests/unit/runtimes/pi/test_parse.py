"""WP-016 acceptance: Pi records → canonical events and usage samples.

A1 parses the **real captured stream** (`tests/fixtures/pi_records_probe.ndjson`, Pi 0.99.2 on
provider `opencode-go`), so the parser is written against records a live model actually
produced rather than against a plausible guess. The rest cover the cases the capture does not
contain: an unknown record, tool events, and delta folding.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from metaharness.runtimes.pi.parse import PiParser, usage_sample_from  # noqa: E402

CAPTURE = REPO_ROOT / "tests" / "fixtures" / "pi_records_probe.ndjson"


def captured_records() -> list[dict]:
    records = []
    for line in CAPTURE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def parse_all(records: list[dict]) -> tuple[PiParser, list]:
    parser = PiParser(session_id="ses_test", agent_id="agt_nova", run_id="run_test")
    events = []
    for record in records:
        events.extend(parser.feed(record))
    return parser, events


class RealCaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.records = captured_records()
        self.parser, self.events = parse_all(self.records)

    def test_the_capture_is_the_real_thing(self) -> None:
        self.assertGreater(len(self.records), 15)
        self.assertEqual(self.records[0]["type"], "session")
        self.assertIn("agent_settled", [record["type"] for record in self.records])

    def test_a1_every_real_record_is_modelled(self) -> None:
        kinds = [event.kind for event in self.events]
        self.assertEqual(
            self.parser.unknown_records,
            0,
            f"unmodelled records: {[event.payload for event in self.events if event.kind == 'runtime.pi.unknown']}",
        )
        for expected in (
            "runtime.pi.session",
            "runtime.pi.agent_started",
            "runtime.pi.turn_started",
            "message.started",
            "message.completed",
            "runtime.pi.turn_ended",
            "runtime.pi.agent_ended",
            "runtime.pi.settled",
        ):
            self.assertIn(expected, kinds, expected)

    def test_a1_events_carry_the_session_identity(self) -> None:
        for event in self.events:
            if event.kind.startswith("runtime.pi.") and event.kind != "runtime.pi.session":
                self.assertEqual(event.ids["agent_id"], "agt_nova")
                self.assertEqual(event.ids["runtime_id"], "rt_pi")

    def test_a2_usage_is_provider_reported_and_faithful(self) -> None:
        samples = [event for event in self.events if event.kind == "usage.sampled"]
        self.assertTrue(samples, "the capture contains usage")
        sample = samples[0].payload["sample"]
        self.assertEqual(sample["input_tokens"]["value"], 117)
        self.assertEqual(sample["output_tokens"]["value"], 30)
        self.assertEqual(sample["reasoning_tokens"]["value"], 15)
        self.assertEqual(sample["input_tokens"]["provenance"], "provider_reported")
        self.assertEqual(sample["provider_cost"]["provenance"], "provider_reported")
        self.assertAlmostEqual(sample["provider_cost"]["value"], 0.000801, places=6)
        self.assertEqual(sample["provider"], "opencode-go")
        self.assertEqual(sample["model"], "kimi-k3")
        self.assertEqual(sample["runtime"], "rt_pi")
        self.assertEqual(sample["agent"], "agt_nova")

    def test_a2_a_metric_pi_did_not_send_stays_unknown(self) -> None:
        """`unknown` must never be rendered as 0 -- that is the whole point of provenance."""
        sample = [event for event in self.events if event.kind == "usage.sampled"][0].payload["sample"]
        self.assertEqual(sample["context_limit"]["value"], None)
        self.assertEqual(sample["context_limit"]["provenance"], "unknown")
        self.assertEqual(sample["estimated_cost"]["value"], None)

    def test_a3_thinking_and_text_are_distinguishable(self) -> None:
        completed = [event for event in self.events if event.kind == "message.completed"][-1]
        self.assertEqual(completed.payload["text"].strip(), "pong")
        self.assertGreater(completed.payload["thinking_chars"], 0)
        self.assertNotEqual(completed.payload["thinking_summary"], completed.payload["text"])

    def test_a6_deltas_are_transient_and_folded(self) -> None:
        deltas = [event for event in self.events if event.kind == "message.delta"]
        completed = [event for event in self.events if event.kind == "message.completed"]
        assistants = [event for event in completed if event.payload.get("role") == "assistant"]
        self.assertTrue(all(event.transient for event in deltas))
        self.assertTrue(all(not event.transient for event in completed))
        self.assertEqual(len(assistants), 1, "the capture has exactly one assistant turn")
        self.assertGreater(len(self.records), len(completed))

    def test_the_parser_records_what_it_saw(self) -> None:
        self.assertGreaterEqual(self.parser.usage_samples, 1)
        self.assertEqual(self.parser.provider, "opencode-go")
        self.assertEqual(self.parser.model, "kimi-k3")


class UnknownRecordTest(unittest.TestCase):
    def test_a4_an_unknown_record_is_preserved_not_dropped(self) -> None:
        parser = PiParser(session_id="ses_test")
        record = {"type": "something_new", "payload": {"a": 1}, "id": "x"}
        events = parser.feed(record)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "runtime.pi.unknown")
        self.assertEqual(events[0].payload["record"], record)
        self.assertEqual(parser.unknown_records, 1)


class ToolEventTest(unittest.TestCase):
    def test_a5_tool_pair_becomes_started_and_completed(self) -> None:
        parser = PiParser(session_id="ses_test", run_id="run_test")
        big = "x" * 5000
        started = parser.feed(
            {"type": "tool_start", "toolName": "read_file", "toolCallId": "c1", "args": {"path": "a"}}
        )[0]
        completed = parser.feed(
            {
                "type": "tool_end",
                "toolName": "read_file",
                "toolCallId": "c1",
                "result": big,
                "durationMs": 12,
            }
        )[0]
        self.assertEqual(started.kind, "tool.started")
        self.assertEqual(started.payload["tool"], "read_file")
        self.assertEqual(started.payload["call_id"], "c1")
        self.assertEqual(completed.kind, "tool.completed")
        self.assertEqual(completed.payload["duration_ms"], 12)
        self.assertEqual(completed.payload["result_chars"], 5000)
        self.assertTrue(completed.payload["truncated"], "a 5 KB result must be a preview")
        self.assertLessEqual(len(completed.payload["result_preview"]), 2000)

    def test_a5_a_small_result_is_not_truncated(self) -> None:
        parser = PiParser(session_id="ses_test")
        completed = parser.feed({"type": "tool_end", "toolName": "ls", "result": "ok"})[0]
        self.assertFalse(completed.payload["truncated"])


class DeltaFoldTest(unittest.TestCase):
    def test_a6_five_hundred_deltas_produce_one_message(self) -> None:
        parser = PiParser(session_id="ses_test")
        parser.feed({"type": "message_start", "message": {"role": "assistant", "content": []}})
        parser.feed({"type": "text_start"})
        events = []
        for index in range(500):
            events.extend(parser.feed({"type": "text_delta", "text": f"{index},"}))
        events.extend(parser.feed({"type": "text_end"}))
        events.extend(parser.feed({"type": "message_end", "message": {"role": "assistant", "content": []}}))

        deltas = [event for event in events if event.kind == "message.delta"]
        completed = [event for event in events if event.kind == "message.completed"]
        self.assertEqual(len(deltas), 500)
        self.assertTrue(all(event.transient for event in deltas))
        self.assertEqual(len(completed), 1, "500 deltas must not become 500 persisted events")
        self.assertTrue(completed[0].payload["text"].startswith("0,1,2,"))
        self.assertEqual(completed[0].payload["chars"], len(completed[0].payload["text"]))

    def test_a_huge_message_is_truncated_and_says_so(self) -> None:
        parser = PiParser(session_id="ses_test")
        parser.feed({"type": "message_start", "message": {"role": "assistant", "content": []}})
        for _ in range(2000):
            parser.feed({"type": "text_delta", "text": "y" * 100})
        completed = parser.feed({"type": "message_end", "message": {"role": "assistant", "content": []}})[0]
        self.assertTrue(completed.payload["truncated"])
        self.assertEqual(len(completed.payload["text"]), 32768)
        self.assertEqual(completed.payload["chars"], 200_000)


class UsageHelperTest(unittest.TestCase):
    def test_no_usage_block_means_no_sample(self) -> None:
        self.assertIsNone(usage_sample_from({}, runtime="rt_pi", agent=None, session=None, run=None))

    def test_partial_usage_keeps_the_missing_parts_unknown(self) -> None:
        sample = usage_sample_from(
            {"usage": {"input": 5}, "provider": "p", "model": "m"},
            runtime="rt_pi",
            agent=None,
            session=None,
            run=None,
        )
        assert sample is not None
        self.assertEqual(sample.input_tokens.value, 5)
        self.assertIsNone(sample.output_tokens.value)
        self.assertEqual(sample.output_tokens.provenance.value, "unknown")


if __name__ == "__main__":
    unittest.main()
