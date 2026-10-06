"""A scripted ``pi --mode rpc`` peer: the same protocol, no model, no cost (WP-015 tests).

It answers commands and emits session events exactly as Pi's documented RPC does, and it has
switches for the situations that are hard to produce on demand from a real Pi:

    --junk          write a non-JSON line to stdout
    --u2028         put U+2028 inside a JSON string (the readline trap)
    --crlf          terminate records with CRLF
    --split         write one record in two chunks with a pause
    --slow N        delay N seconds before answering (correlation out of order)
    --tools         emit a tool call pair during a prompt
    --hang          never settle (for cancel/kill tests)
    --die-on CMD    exit(3) while handling CMD
    --huge N        emit N heartbeat-ish events (backpressure)

Usage::

    python pi_fake_rpc.py [--flags]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

NEWLINE = "\n"


def write(record: dict, *, crlf: bool = False) -> None:
    line = json.dumps(record, ensure_ascii=False)
    sys.stdout.write(line + ("\r\n" if crlf else NEWLINE))
    sys.stdout.flush()


def response(request_id, command: str, data: dict | None = None, success: bool = True) -> None:
    write(
        {
            "id": request_id,
            "type": "response",
            "command": command,
            "success": success,
            **({"data": data} if data is not None else {}),
        }
    )


def emit(record: dict, *, crlf: bool = False) -> None:
    write(record, crlf=crlf)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junk", action="store_true")
    parser.add_argument("--u2028", action="store_true")
    parser.add_argument("--crlf", action="store_true")
    parser.add_argument("--split", action="store_true")
    parser.add_argument("--slow", type=float, default=0.0)
    parser.add_argument("--delay-command", default="", help="CMD:SECONDS — delay only that command")
    parser.add_argument("--tools", action="store_true")
    parser.add_argument("--hang", action="store_true")
    parser.add_argument("--die-on", default="")
    parser.add_argument("--huge", type=int, default=0)
    parser.add_argument("--provider", default="fake-provider")
    parser.add_argument("--model", default="fake-model")
    args = parser.parse_args()

    crlf = args.crlf
    emit({"type": "session", "version": 3, "id": "fake-session", "cwd": "/tmp"}, crlf=crlf)

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        command = request.get("type", "")
        request_id = request.get("id")

        if args.die_on and command == args.die_on:
            sys.stderr.write("fake pi: dying on request\n")
            return 3
        if args.slow:
            time.sleep(args.slow)
        if args.delay_command:
            delayed_name, _, seconds = args.delay_command.partition(":")
            if delayed_name == command:
                time.sleep(float(seconds or "0.5"))
        if args.junk:
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()

        if command == "get_state":
            response(
                request_id,
                command,
                {
                    "sessionId": "fake-session",
                    "provider": args.provider,
                    "model": args.model,
                    "cwd": "/tmp",
                    "isStreaming": False,
                },
            )
        elif command == "get_commands":
            response(
                request_id,
                command,
                {
                    "commands": [
                        {"name": "prompt", "description": "send a prompt"},
                        {"name": "steer", "description": "steer the run"},
                        {"name": "follow_up", "description": "queue a follow-up"},
                        {"name": "abort", "description": "abort the run"},
                        {"name": "clear_queue", "description": "clear queued work"},
                        {"name": "get_state", "description": "inspect state"},
                        {"name": "get_messages", "description": "read the transcript"},
                        {"name": "get_available_models", "description": "list models"},
                        {"name": "set_model", "description": "switch model"},
                        {"name": "get_session_stats", "description": "session totals"},
                        {"name": "new_session", "description": "start a session"},
                    ]
                },
            )
        elif command == "get_available_models":
            response(
                request_id,
                command,
                {"models": [{"id": args.model, "provider": args.provider, "contextLimit": 200000}]},
            )
        elif command == "get_session_stats":
            response(
                request_id,
                command,
                {
                    "tokens": {"input": 117, "output": 30, "total": 147},
                    "messages": 3,
                    "cost": {"total": 0.000801},
                },
            )
        elif command in {"new_session", "set_model", "set_thinking_level", "compact", "steer", "follow_up"}:
            response(request_id, command, {"ok": True})
        elif command == "abort":
            response(request_id, command, {"ok": True})
            emit({"type": "agent_settled"}, crlf=crlf)
        elif command == "clear_queue":
            response(request_id, command, {"ok": True})
        elif command == "huge":
            response(request_id, command, {"emitted": args.huge})
            for index in range(args.huge):
                emit({"type": "message_update", "index": index, "text": "x" * 64}, crlf=crlf)
        elif command == "prompt":
            response(request_id, command, {"disposition": "started"})
            if args.split:
                first = json.dumps({"type": "turn_start"}, ensure_ascii=False) + "\n"
                sys.stdout.write(first[:12])
                sys.stdout.flush()
                time.sleep(0.15)
                sys.stdout.write(first[12:])
                sys.stdout.flush()
            else:
                emit({"type": "turn_start"}, crlf=crlf)

            payload = "pong" + ("\u2028inside" if args.u2028 else "")
            emit({"type": "message_start", "message": {"role": "assistant", "content": []}}, crlf=crlf)
            emit({"type": "text_start"}, crlf=crlf)
            emit({"type": "text_delta", "text": payload}, crlf=crlf)
            emit({"type": "text_end"}, crlf=crlf)
            if args.tools:
                emit(
                    {
                        "type": "tool_start",
                        "toolName": "read_file",
                        "toolCallId": "call-1",
                        "args": {"path": "pyproject.toml"},
                    },
                    crlf=crlf,
                )
                emit(
                    {
                        "type": "tool_end",
                        "toolName": "read_file",
                        "toolCallId": "call-1",
                        "result": {"content": "[project]" * 40},
                        "durationMs": 12,
                    },
                    crlf=crlf,
                )
            emit(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": payload}],
                        "provider": args.provider,
                        "model": args.model,
                        "usage": {
                            "input": 117,
                            "output": 30,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                            "reasoning": 15,
                            "totalTokens": 147,
                            "cost": {
                                "input": 0.000351,
                                "output": 0.00045,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                                "total": 0.000801,
                            },
                        },
                        "stopReason": "stop",
                    },
                },
                crlf=crlf,
            )
            emit({"type": "turn_end"}, crlf=crlf)
            emit({"type": "agent_end", "messages": []}, crlf=crlf)
            if not args.hang:
                emit({"type": "agent_settled"}, crlf=crlf)
        else:
            response(request_id, command, None, success=False)
            sys.stdout.write("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
