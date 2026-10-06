"""ACP `session/update` → canonical events (M2).

Every shape handled here was captured from `hermes acp` 0.21.5 and is quoted in
`docs/protocols/HERMES_ACP.md`. Nothing is inferred from the ACP specification that the probe did
not show, because the specification is a promise and the capture is the behaviour.

Two decisions worth stating:

* **`usage_update` is not usage.** It carries `{size, used}` — context window pressure, not tokens
  billed. It is emitted as `runtime.hermes.context_pressure`, and the real `UsageSample` comes
  from the `session/prompt` response. Calling the first one "usage" would be a lie with a
  familiar name.
* **Only the observed terminal statuses close a tool call.** `tool_call_update` was observed with
  `status: "failed"`; a status this parser does not recognise becomes a transient
  `tool.updated`, never a guessed completion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_TOOL_PREVIEW = 4000
#: Statuses whose meaning the capture established, plus the obvious success counterpart of the
#: one we saw. Anything else stays open on purpose.
TERMINAL_TOOL_STATUSES = {"completed": True, "failed": False, "error": False, "cancelled": False}


@dataclass(frozen=True)
class ParsedEvent:
    """One canonical event, ready to be published."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ids: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    transient: bool = False


def _text_of(content: Any) -> str:
    """The text out of ACP's content shapes, both of which were observed:

    ``{"text": "...", "type": "text"}`` for chunks, and
    ``[{"type": "content", "content": {"text": "...", "type": "text"}}]`` for tool results.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        inner = content.get("content")
        if inner is not None and inner is not content:
            return _text_of(inner)
        return ""
    if isinstance(content, list):
        return "\n".join(part for part in (_text_of(item) for item in content) if part)
    return ""


class AcpParser:
    """Stateful: it folds streamed chunks and tracks open tool calls, so it belongs to one
    session."""

    def __init__(
        self,
        *,
        session_id: str,
        agent_id: str | None = None,
        run_id: str | None = None,
        mission_id: str | None = None,
        task_id: str | None = None,
        runtime_id: str = "rt_hermes",
    ) -> None:
        self.session_id = session_id
        self.agent_id = agent_id
        self.run_id = run_id
        self.mission_id = mission_id
        self.task_id = task_id
        self.runtime_id = runtime_id
        self.acp_session_id: str | None = None
        self.provider: str | None = None
        self.model: str | None = None
        self.turn = 0
        self._assistant = ""
        self._thought = ""
        self._open_calls: dict[str, str] = {}
        #: The last `usage_update` the agent sent: `{size, used}` -- context pressure, which maps
        #: onto `context_limit` / `context_tokens` and onto nothing else.
        self.context_pressure: dict[str, Any] = {}

    # ------------------------------------------------------------------ helpers

    def _ids(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "runtime_id": self.runtime_id,
        }

    def _provenance(self, method: str = "runtime_reported") -> dict[str, Any]:
        return {"method": method, "origin": "hermes_acp", "runtime_id": self.runtime_id}

    # -------------------------------------------------------------------- feed

    def feed(self, frame: dict[str, Any]) -> list[ParsedEvent]:
        """Translate one ACP notification into zero or more canonical events."""
        params = frame.get("params") or {}
        session_id = params.get("sessionId")
        if session_id:
            self.acp_session_id = session_id
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if not isinstance(kind, str):
            return []
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            return [
                ParsedEvent(
                    kind="runtime.hermes.unmodelled_update",
                    payload={"session_update": kind, "keys": sorted(update.keys())[:12]},
                    ids=self._ids(),
                    provenance=self._provenance("measured"),
                )
            ]
        events = handler(update)
        return events if isinstance(events, list) else [events]

    # --------------------------------------------------------------- streaming

    def _on_agent_message_chunk(self, update: dict[str, Any]) -> ParsedEvent:
        text = _text_of(update.get("content"))
        self._assistant += text
        return ParsedEvent(
            kind="message.delta",
            payload={"role": "assistant", "text": text, "message_id": update.get("messageId")},
            ids=self._ids(),
            provenance=self._provenance(),
            transient=True,
        )

    def _on_agent_thought_chunk(self, update: dict[str, Any]) -> ParsedEvent:
        text = _text_of(update.get("content"))
        self._thought += text
        return ParsedEvent(
            kind="message.thought_delta",
            payload={"role": "thought", "text": text, "message_id": update.get("messageId")},
            ids=self._ids(),
            provenance=self._provenance(),
            transient=True,
        )

    # -------------------------------------------------------------------- tools

    def _on_tool_call(self, update: dict[str, Any]) -> ParsedEvent:
        call_id = str(update.get("toolCallId") or "unknown")
        tool = str(update.get("kind") or update.get("title") or "unknown")
        self._open_calls[call_id] = tool
        locations = update.get("locations")
        preview = str(update.get("title") or "")
        if isinstance(locations, list) and locations:
            preview = f"{preview} {locations}"[:MAX_TOOL_PREVIEW]
        return ParsedEvent(
            kind="tool.started",
            payload={
                "tool": tool,
                "call_id": call_id,
                "title": update.get("title"),
                "args_preview": preview[:MAX_TOOL_PREVIEW],
                "locations": locations if isinstance(locations, list) else [],
            },
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_tool_call_update(self, update: dict[str, Any]) -> ParsedEvent:
        call_id = str(update.get("toolCallId") or "unknown")
        tool = str(update.get("kind") or self._open_calls.get(call_id) or "unknown")
        status = update.get("status")
        text = _text_of(update.get("content"))
        if isinstance(status, str) and status in TERMINAL_TOOL_STATUSES:
            self._open_calls.pop(call_id, None)
            return ParsedEvent(
                kind="tool.completed",
                payload={
                    "tool": tool,
                    "call_id": call_id,
                    "status": status,
                    "success": TERMINAL_TOOL_STATUSES[status],
                    "result_preview": text[:MAX_TOOL_PREVIEW],
                    "result_chars": len(text),
                    "truncated": len(text) > MAX_TOOL_PREVIEW,
                },
                ids=self._ids(),
                provenance=self._provenance(),
            )
        # A status this parser has not established: report it as an update, not as a completion.
        return ParsedEvent(
            kind="tool.updated",
            payload={"tool": tool, "call_id": call_id, "status": status, "chars": len(text)},
            ids=self._ids(),
            provenance=self._provenance(),
            transient=True,
        )

    # -------------------------------------------------------- context and info

    def _on_usage_update(self, update: dict[str, Any]) -> ParsedEvent:
        """Context pressure, not billing. Named for what it is."""
        self.context_pressure = {"size": update.get("size"), "used": update.get("used")}
        return ParsedEvent(
            kind="runtime.hermes.context_pressure",
            payload={"size": update.get("size"), "used": update.get("used")},
            ids=self._ids(),
            provenance=self._provenance(),
            transient=True,
        )

    def _on_session_info_update(self, update: dict[str, Any]) -> ParsedEvent:
        """Carries the agent's own account of its session lineage (`_meta.hermes`). Persisted,
        because a migration can be checked against it."""
        meta = update.get("_meta") or {}
        hermes = meta.get("hermes") if isinstance(meta, dict) else None
        provenance = (hermes or {}).get("sessionProvenance") if isinstance(hermes, dict) else None
        return ParsedEvent(
            kind="runtime.hermes.session_info",
            payload={
                "title": update.get("title"),
                "updated_at": update.get("updatedAt"),
                "session_provenance": provenance if isinstance(provenance, dict) else None,
            },
            ids=self._ids(),
            provenance=self._provenance(),
        )

    def _on_available_commands_update(self, update: dict[str, Any]) -> ParsedEvent:
        commands = update.get("availableCommands")
        names = [
            str(entry.get("name"))
            for entry in (commands or [])
            if isinstance(entry, dict) and entry.get("name")
        ]
        return ParsedEvent(
            kind="runtime.hermes.commands",
            payload={"commands": names},
            ids=self._ids(),
            provenance=self._provenance(),
            transient=True,
        )

    # -------------------------------------------------------------- turn result

    def turn_result(self, result: dict[str, Any]) -> list[ParsedEvent]:
        """The `session/prompt` response: the turn is over, and this is what it cost.

        Unlike Pi, where acceptance and completion are different events, ACP answers once at the
        end — so the response is both the completion signal and the usage sample.
        """
        self.turn += 1
        events: list[ParsedEvent] = []
        raw_usage = result.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        if usage:
            reported = "provider_reported"
            sample: dict[str, Any] = {
                "runtime": self.runtime_id,
                "agent": self.agent_id,
                "session": self.session_id,
                "run": self.run_id,
                "provider": self.provider,
                "model": self.model,
                "input_tokens": {"value": usage.get("inputTokens"), "provenance": reported},
                "output_tokens": {"value": usage.get("outputTokens"), "provenance": reported},
                "cache_read_tokens": {"value": usage.get("cachedReadTokens"), "provenance": reported},
                "reasoning_tokens": {"value": usage.get("thoughtTokens"), "provenance": reported},
                # No cost field exists in ACP's usage: unknown, never estimated from a catalogue.
                "provider_cost": {"value": None, "provenance": "unknown"},
            }
            # `usage_update` is context pressure; these two fields are where it belongs.
            if self.context_pressure.get("size") is not None:
                sample["context_limit"] = {"value": self.context_pressure["size"], "provenance": reported}
            if self.context_pressure.get("used") is not None:
                sample["context_tokens"] = {"value": self.context_pressure["used"], "provenance": reported}
            events.append(
                ParsedEvent(
                    kind="usage.sampled",
                    payload={
                        "sample": sample,
                        "turn": self.turn,
                        # `UsageSample` has no `total_tokens` on purpose (19 metrics, and a total is
                        # derivable and easy to double count), so the agent's own sum is kept here,
                        # raw and labelled, instead of being forced into a field that does not exist.
                        "provider_total_tokens": usage.get("totalTokens"),
                        "stop_reason": result.get("stopReason"),
                    },
                    ids=self._ids(),
                    provenance=self._provenance(),
                )
            )
        if self._assistant.strip():
            events.append(
                ParsedEvent(
                    kind="message.completed",
                    payload={"role": "assistant", "text": self._assistant, "turn": self.turn},
                    ids=self._ids(),
                    provenance=self._provenance(),
                )
            )
        self._assistant = ""
        self._thought = ""
        events.append(
            ParsedEvent(
                kind="runtime.hermes.settled",
                payload={
                    "turn": self.turn,
                    "stop_reason": result.get("stopReason"),
                    "note": "ACP answers once, at the end of the turn: this response is the completion",
                },
                ids=self._ids(),
                provenance=self._provenance(),
            )
        )
        return events
