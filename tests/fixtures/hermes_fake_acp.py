#!/usr/bin/env python3
"""A scripted ACP peer that speaks the wire format captured from `hermes acp` 0.21.5.

Every shape below is copied from `docs/protocols/HERMES_ACP.md` (which came from
`tools/probe_hermes_acp.py`). A fake that invents its own dialect proves nothing about the real
one — the Pi adapter learned that the expensive way when `--mode rpc` turned out to name its tool
events differently from `--mode json`.

    python hermes_fake_acp.py [--no-tools] [--hang] [--garbage] [--fail-initialize]

* `--no-tools`      do not emit the tool_call/tool_call_update pair
* `--hang`          accept session/prompt and never answer (for the cancel path)
* `--garbage`       write one line that is not JSON, then behave normally
* `--fail-initialize` answer initialize with a JSON-RPC error
"""

from __future__ import annotations

import argparse
import json
import sys
import time

VERSION = "0.21.5"


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def respond(request_id: object, result: dict) -> None:
    emit({"jsonrpc": "2.0", "id": request_id, "result": result})


def fail(request_id: object, code: int, message: str) -> None:
    emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def update(session_id: str, payload: dict) -> None:
    emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": payload}})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("--hang", action="store_true")
    parser.add_argument("--garbage", action="store_true")
    parser.add_argument("--fail-initialize", action="store_true")
    parser.add_argument("--session-id", default="1b0d029a-cdfe-4dbf-aafc-55d86a2f9dc1")
    parser.add_argument("--model", default="openai-codex:gpt-6.1-sol")
    parser.add_argument("--other-model", default="opencode-go:deepseek-v4.1-flash")
    args = parser.parse_args()

    session_id = args.session_id
    if args.garbage:
        sys.stdout.write("this line is not JSON at all\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = frame.get("method")
        request_id = frame.get("id")
        params = frame.get("params") or {}

        if method == "initialize":
            if args.fail_initialize:
                fail(request_id, -32603, "initialize refused by the scripted peer")
                continue
            respond(
                request_id,
                {
                    "protocolVersion": 1,
                    "agentInfo": {"name": "hermes-agent", "version": VERSION},
                    "agentCapabilities": {
                        "loadSession": True,
                        "promptCapabilities": {"image": True},
                        "sessionCapabilities": {"fork": {}, "list": {}, "resume": {}},
                    },
                    "authMethods": [
                        {
                            "id": "opencode-go",
                            "name": "opencode-go runtime credentials",
                            "description": "Authenticate Hermes using the currently configured opencode-go runtime credentials.",
                        }
                    ],
                },
            )
            continue

        if method == "session/new":
            respond(
                request_id,
                {
                    "sessionId": session_id,
                    "models": {
                        "availableModels": [
                            {
                                "modelId": args.model,
                                "name": "ChatGPT or Codex Subscription · gpt-6.1-sol",
                                "description": "Provider: ChatGPT or Codex Subscription",
                            },
                            {
                                "modelId": args.other_model,
                                "name": "OpenCode Go · deepseek-v4.1-flash",
                                "description": "Provider: OpenCode Go \u2022 current",
                            },
                        ],
                        "currentModelId": args.other_model,
                    },
                    "modes": {
                        "availableModes": [
                            {"id": "default", "name": "Default", "description": "Ask before edits."},
                            {"id": "accept_edits", "name": "Accept Edits", "description": "..."},
                            {"id": "dont_ask", "name": "Don't Ask", "description": "..."},
                        ],
                        "currentModeId": "default",
                    },
                },
            )
            continue

        if method == "session/prompt":
            if args.hang:
                time.sleep(600)
                continue
            update(session_id, {"sessionUpdate": "session_info_update", "title": "scripted turn", "updatedAt": "2026-10-06T13:30:01.581526+00:00",
                                "_meta": {"hermes": {"sessionProvenance": {"acpSessionId": session_id, "currentHermesSessionId": session_id,
                                                                          "rootHermesSessionId": session_id, "parentHermesSessionId": None,
                                                                          "sessionKind": "root", "compressionDepth": 0}}}})
            update(session_id, {"sessionUpdate": "available_commands_update",
                                "availableCommands": [{"name": "help", "description": "List available commands"},
                                                       {"name": "model", "description": "Show current model and provider"}]})
            update(session_id, {"sessionUpdate": "agent_thought_chunk", "messageId": "m-1",
                                "content": {"text": "Reading the file", "type": "text"}})
            update(session_id, {"sessionUpdate": "agent_message_chunk", "messageId": "m-2",
                                "content": {"text": "The project is ", "type": "text"}})
            if not args.no_tools:
                update(session_id, {"sessionUpdate": "tool_call", "toolCallId": "tc-1", "kind": "read",
                                    "title": "read_file: pyproject.toml", "locations": [{"path": "pyproject.toml"}]})
                update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": "tc-1", "kind": "read",
                                    "status": "failed",
                                    "content": [{"type": "content", "content": {"type": "text",
                                                                                "text": "Read failed: File not found: pyproject.toml"}}]})
            update(session_id, {"sessionUpdate": "usage_update", "size": 1000000, "used": 9840})
            update(session_id, {"sessionUpdate": "agent_message_chunk", "messageId": "m-2",
                                "content": {"text": "metaharness", "type": "text"}})
            respond(
                request_id,
                {
                    "stopReason": "end_turn",
                    "usage": {
                        "inputTokens": 68401,
                        "outputTokens": 360,
                        "cachedReadTokens": 57600,
                        "thoughtTokens": 94,
                        "totalTokens": 68761,
                    },
                },
            )
            continue

        if method == "session/cancel":
            respond(request_id, {})
            continue

        fail(request_id, -32601, f"Method not found: {method}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
