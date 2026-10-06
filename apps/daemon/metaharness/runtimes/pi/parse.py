"""Pi records → canonical events and usage samples (WP-016).

Pure translation: no process, no I/O, no store. This is the only place that knows what Pi's
vocabulary *means*, which is why it is separate from the transport that moves the bytes.

Three rules from the Book and the Architect shape everything here:

* **Nothing is dropped.** An unrecognised record becomes ``runtime.pi.unknown`` with its payload
  intact. A silently skipped frame is a hole in the audit trail.
* **Usage provenance is ``provider_reported``**, always, because that is what it is. Our own
  measurements are ``measured`` and are never blended into Pi's numbers, and a field Pi did not
  send stays ``unknown`` instead of becoming zero (BOOK §17).
* **Deltas are not history.** ``text_delta``/``thinking_delta`` are marked ``transient``: the
  adapter fans them out to the UI but the store never keeps one row per token. The final
  ``message.completed`` carries the text (BOOK §21).

The model/provider/usage field names are taken from a real captured stream
(``tests/fixtures/pi_records_probe.ndjson``, Pi 0.99.2 on provider ``opencode-go``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from metaharness_contracts import Metric, Provenance, UsageSample

MAX_TEXT_CHARS = 32_768
MAX_TOOL_PREVIEW = 2_000

PROVIDER_REPORTED = Provenance("provider_reported")
UNKNOWN = Provenance("unknown")

#: Records that are folded into a single completed message rather than persisted one by one.
TRANSIENT_RECORD_TYPES = {"text_delta", "thinking_delta", "message_update", "text_start", "text_end",
                          "thinking_start", "thinking_end", "text"}

MODELLED_RECORD_TYPES = {
    "session",
    "agent_start",
    "agent_end",
    "agent_settled",
    "turn_start",
    "turn_end",
    "message_start",
    "message_end",
    "tool_start",
    "tool_end",
    "tool_call",
    "tool_result",
    "error",
    "retry",
    "compaction",
} | TRANSIENT_RECORD_TYPES


@dataclass(frozen=True)
class ParsedEvent:
    """One canonical event, ready to be published.

    ``transient`` events are fanned out to live subscribers and never persisted: they describe
    a stream in progress, not something that happened.
    """

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ids: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    transient: bool = False


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrowing helper: Pyright cannot see through a conditional expression on ``.get``."""
    return value if isinstance(value, dict) else {}


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _thinking_of(content: Any) -> str:
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                parts.append(str(block.get("thinking", "")))
        return "".join(parts)
    return ""


def usage_sample_from(message: dict[str, Any], *, runtime: str, agent: str | None, session: str | None,
                      run: str | None) -> UsageSample | None:
    """Build a contract `UsageSample` from a Pi message's usage/cost block.

    Returns ``None`` when the record carries no usage at all -- which is different from a
    sample full of ``unknown``, and the caller must not conflate them.
    """
    usage = message.get("usage")
    cost = message.get("cost") or (usage or {}).get("cost") or {}
    if not isinstance(usage, dict) or not usage:
        return None

    def metric(value: Any, *, unit: str | None = None, source: str = "pi") -> Metric:
        if value is None:
            return Metric(value=None, provenance=UNKNOWN, unit=unit, source=source)
        return Metric(value=float(value), provenance=PROVIDER_REPORTED, unit=unit, source=source)

    return UsageSample(
        input_tokens=metric(usage.get("input")),
        output_tokens=metric(usage.get("output")),
        reasoning_tokens=metric(usage.get("reasoning")),
        cache_read_tokens=metric(usage.get("cacheRead")),
        cache_write_tokens=metric(usage.get("cacheWrite")),
        provider_cost=metric((cost or {}).get("total"), unit="usd"),
        provider=message.get("provider"),
        model=message.get("model"),
        runtime=runtime,
        agent=agent,
        session=session,
        run=run,
    )


class PiParser:
    """Stateful: it folds deltas into messages, so it belongs to one session."""

    def __init__(
        self,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        mission_id: str | None = None,
        task_id: str | None = None,
        runtime_id: str = "rt_pi",
    ) -> None:
        self.session_id = session_id
        self.agent_id = agent_id
        self.run_id = run_id
        self.mission_id = mission_id
        self.task_id = task_id
        self.runtime_id = runtime_id
        self._text: list[str] = []
        self._thinking: list[str] = []
        self._turn = 0
        self.provider: str | None = None
        self.model: str | None = None
        self.usage_samples = 0
        self.unknown_records = 0

    # ------------------------------------------------------------------ helpers

    def _ids(self, **extra: Any) -> dict[str, Any]:
        ids: dict[str, Any] = {
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "runtime_id": self.runtime_id,
        }
        ids.update(extra)
        return ids

    def _provenance(self, origin: str = "pi_rpc") -> dict[str, Any]:
        return {"method": "runtime_reported", "origin": origin}

    def _folded_text(self) -> tuple[str, str, bool]:
        """``(text_to_store, full_text, truncated)``.

        The stored text is capped so a runaway message cannot bloat the log, but the *length*
        is reported from the full text: a payload that says "chars: 32768" when it received
        200 000 is lying about what happened.
        """
        full = "".join(self._text)
        truncated = len(full) > MAX_TEXT_CHARS
        return (full[:MAX_TEXT_CHARS] if truncated else full), full, truncated

    # -------------------------------------------------------------------- feeding

    def feed(self, record: dict[str, Any]) -> list[ParsedEvent]:
        """Translate one Pi record into zero or more parsed events."""
        record_type = str(record.get("type", ""))
        handler = getattr(self, f"_on_{record_type}", None)
        if handler is None:
            if record_type in MODELLED_RECORD_TYPES:  # pragma: no cover - all are handled
                return [self._unknown(record)]
            return [self._unknown(record)]
        result = handler(record)
        return result if isinstance(result, list) else [result]

    def _unknown(self, record: dict[str, Any]) -> ParsedEvent:
        self.unknown_records += 1
        return ParsedEvent(
            kind="runtime.pi.unknown",
            payload={"record_type": record.get("type"), "record": record},
            ids=self._ids(),
            provenance=self._provenance("pi_parser"),
        )

    # -- lifecycle ----------------------------------------------------------

    def _on_session(self, record: dict[str, Any]) -> ParsedEvent:
        self.session_id = self.session_id or f"ses_{record.get('id')}"
        return ParsedEvent(
            kind="runtime.pi.session",
            payload={
                "pi_session_id": record.get("id"),
                "protocol_version": record.get("version"),
                "cwd": record.get("cwd"),
            },
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_agent_start(self, record: dict[str, Any]) -> ParsedEvent:
        return ParsedEvent(
            kind="runtime.pi.agent_started", payload={}, ids=self._ids(), provenance=self._provenance()
        )

    def _on_agent_end(self, record: dict[str, Any]) -> ParsedEvent:
        return ParsedEvent(
            kind="runtime.pi.agent_ended",
            payload={"messages": len(record.get("messages") or [])},
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_agent_settled(self, record: dict[str, Any]) -> ParsedEvent:
        """The only signal that Pi will not continue on its own (see PI_RPC.md §4)."""
        return ParsedEvent(
            kind="runtime.pi.settled",
            payload={"turn": self._turn, "text_chars": sum(len(part) for part in self._text)},
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_turn_start(self, record: dict[str, Any]) -> ParsedEvent:
        self._turn += 1
        self._text.clear()
        self._thinking.clear()
        return ParsedEvent(
            kind="runtime.pi.turn_started",
            payload={"turn": self._turn},
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_turn_end(self, record: dict[str, Any]) -> list[ParsedEvent]:
        events: list[ParsedEvent] = []
        message = _as_dict(record.get("message"))
        sample = usage_sample_from(
            message,
            runtime=self.runtime_id,
            agent=self.agent_id,
            session=self.session_id,
            run=self.run_id,
        )
        if sample is not None:
            self.usage_samples += 1
            self.provider = sample.provider or self.provider
            self.model = sample.model or self.model
            events.append(
                ParsedEvent(
                    kind="usage.sampled",
                    payload={"sample": sample.model_dump(mode="json"), "scope": "turn"},
                    ids=self._ids(),
                    provenance=self._provenance(),
                )
            )
        events.append(
            ParsedEvent(
                kind="runtime.pi.turn_ended",
                payload={
                    "turn": self._turn,
                    "stop_reason": message.get("stopReason"),
                    "tool_results": len(record.get("toolResults") or []),
                },
                ids=self._ids(),
                provenance=self._provenance(),
            )
        )
        return events

    # -- messages -----------------------------------------------------------

    def _on_message_start(self, record: dict[str, Any]) -> list[ParsedEvent]:
        message = _as_dict(record.get("message"))
        role = str(message.get("role", "unknown"))
        if role == "assistant":
            self._text.clear()
            self._thinking.clear()
        return [
            ParsedEvent(
                kind="message.started",
                payload={"role": role},
                ids=self._ids(),
                provenance=self._provenance(),
            )
        ]

    def _on_message_update(self, record: dict[str, Any]) -> list[ParsedEvent]:
        """An in-flight update: transient by definition, so the log stays a log."""
        return [
            ParsedEvent(
                kind="message.delta",
                payload={"update": record.get("update") or record.get("delta") or {}},
                ids=self._ids(),
                provenance={"method": "runtime_reported", "origin": "pi_stream"},
                transient=True,
            )
        ]

    def _on_text_start(self, record: dict[str, Any]) -> list[ParsedEvent]:
        return []

    def _on_text_end(self, record: dict[str, Any]) -> list[ParsedEvent]:
        return []

    def _on_text_delta(self, record: dict[str, Any]) -> list[ParsedEvent]:
        text = str(record.get("text", ""))
        self._text.append(text)
        return [
            ParsedEvent(
                kind="message.delta",
                payload={"text": text},
                ids=self._ids(),
                provenance={"method": "runtime_reported", "origin": "pi_stream"},
                transient=True,
            )
        ]

    def _on_thinking_start(self, record: dict[str, Any]) -> list[ParsedEvent]:
        return []

    def _on_thinking_end(self, record: dict[str, Any]) -> list[ParsedEvent]:
        return []

    def _on_thinking_delta(self, record: dict[str, Any]) -> list[ParsedEvent]:
        self._thinking.append(str(record.get("text") or record.get("thinking") or ""))
        return []  # reasoning is summarised at message_end; no token-by-token history

    def _on_text(self, record: dict[str, Any]) -> list[ParsedEvent]:
        return []

    def _on_message_end(self, record: dict[str, Any]) -> list[ParsedEvent]:
        message = _as_dict(record.get("message"))
        role = str(message.get("role", "unknown"))
        if role != "assistant":
            return [
                ParsedEvent(
                    kind="message.completed",
                    payload={"role": role, "chars": len(_text_of(message.get("content")))},
                    ids=self._ids(),
                    provenance=self._provenance(),
                )
            ]

        text, full_text, truncated = self._folded_text()
        if not full_text:
            text = _text_of(message.get("content"))
            full_text = text
        thinking = "".join(self._thinking) or _thinking_of(message.get("content"))
        self.provider = message.get("provider") or self.provider
        self.model = message.get("model") or self.model

        events = [
            ParsedEvent(
                kind="message.completed",
                payload={
                    "role": "assistant",
                    "text": text,
                    "chars": len(full_text),
                    "truncated": truncated,
                    "thinking_chars": len(thinking),
                    "thinking_summary": thinking[:1_000],
                    "stop_reason": message.get("stopReason"),
                },
                ids=self._ids(),
                provenance=self._provenance(),
            )
        ]
        sample = usage_sample_from(
            message,
            runtime=self.runtime_id,
            agent=self.agent_id,
            session=self.session_id,
            run=self.run_id,
        )
        if sample is not None:
            self.usage_samples += 1
            events.append(
                ParsedEvent(
                    kind="usage.sampled",
                    payload={"sample": sample.model_dump(mode="json"), "scope": "message"},
                    ids=self._ids(),
                    provenance=self._provenance(),
                )
            )
        return events

    # -- tools --------------------------------------------------------------

    def _on_tool_start(self, record: dict[str, Any]) -> ParsedEvent:
        name = str(record.get("toolName") or record.get("name") or "unknown")
        args = record.get("args") or record.get("arguments") or {}
        preview = str(args)[:MAX_TOOL_PREVIEW]
        return ParsedEvent(
            kind="tool.started",
            payload={
                "tool": name,
                "call_id": record.get("toolCallId") or record.get("id"),
                "args_preview": preview,
                "args_chars": len(str(args)),
            },
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_tool_end(self, record: dict[str, Any]) -> ParsedEvent:
        name = str(record.get("toolName") or record.get("name") or "unknown")
        result = record.get("result")
        rendered = result if isinstance(result, str) else str(result)
        return ParsedEvent(
            kind="tool.completed",
            payload={
                "tool": name,
                "call_id": record.get("toolCallId") or record.get("id"),
                "duration_ms": record.get("durationMs"),
                "result_preview": rendered[:MAX_TOOL_PREVIEW],
                "result_chars": len(rendered),
                "truncated": len(rendered) > MAX_TOOL_PREVIEW,
            },
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_tool_call(self, record: dict[str, Any]) -> ParsedEvent:
        return self._on_tool_start(record)

    def _on_tool_result(self, record: dict[str, Any]) -> ParsedEvent:
        return self._on_tool_end(record)

    def _on_error(self, record: dict[str, Any]) -> ParsedEvent:
        return ParsedEvent(
            kind="runtime.pi.error",
            payload={"message": str(record.get("error") or record.get("message") or record)[:2_000]},
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_retry(self, record: dict[str, Any]) -> ParsedEvent:
        return ParsedEvent(
            kind="runtime.pi.retry",
            payload={"attempt": record.get("attempt"), "reason": record.get("reason")},
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_compaction(self, record: dict[str, Any]) -> ParsedEvent:
        return ParsedEvent(
            kind="context.compacted",
            payload={
                "input_tokens": record.get("inputTokens"),
                "output_tokens": record.get("outputTokens"),
                "reason": record.get("reason"),
            },
            ids=self._ids(),
            provenance=self._provenance(),
        )
