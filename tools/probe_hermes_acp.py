"""Observe Hermes' ACP protocol before writing a line of adapter code.

The Architect's rule for M2 is explicit: inspect the installed ACP surface, record what is
observed, do not infer unsupported features, no ANSI scraping. This script is that inspection.
It speaks JSON-RPC 2.0 over the child's stdin/stdout and prints every frame verbatim, including
the ones it does not understand -- an unknown method's error is data, not noise.

    uv run python tools/probe_hermes_acp.py

It writes the full capture to ~/.hermes/cache/scratch/mh-recon/hermes-acp-probe.ndjson and a
summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

CAPTURE = Path.home() / ".hermes" / "cache" / "scratch" / "mh-recon" / "hermes-acp-probe.ndjson"
WORKDIR = Path(tempfile.mkdtemp(prefix="hermes-acp-"))

FRAMES: list[dict] = []
RAW: list[str] = []
LOCK = threading.Lock()


def record(line: str) -> None:
    with LOCK:
        RAW.append(line)
        try:
            FRAMES.append(json.loads(line))
        except json.JSONDecodeError:
            FRAMES.append({"__unparsed__": line})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="Reply with exactly: acp-ok")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--capture", default=str(CAPTURE))
    args = parser.parse_args()
    capture_path = Path(args.capture)

    argv = ["hermes", "acp"]
    print(f"spawning: {' '.join(argv)}  (cwd={WORKDIR})")
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(WORKDIR),
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
    )

    def pump(stream, label: str) -> None:
        assert stream is not None
        for line in stream:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if label == "stderr":
                with LOCK:
                    RAW.append(f"[stderr] {line}")
                continue
            record(line)

    threading.Thread(target=pump, args=(process.stdout, "stdout"), daemon=True).start()
    threading.Thread(target=pump, args=(process.stderr, "stderr"), daemon=True).start()

    def send(payload: dict) -> None:
        assert process.stdin
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        with LOCK:
            RAW.append(f"[sent] {json.dumps(payload)}")

    def await_id(request_id: int, timeout_s: float = 60.0) -> dict | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            with LOCK:
                for frame in FRAMES:
                    if frame.get("id") == request_id:
                        return frame
            time.sleep(0.2)
        return None

    # 1. initialize -- the only method whose shape we can be sure of, because ACP requires it.
    send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
            },
        }
    )
    init = await_id(1, 45)
    print(f"\ninitialize -> {'answered' if init else 'NO ANSWER in 45s'}")

    if init and isinstance(init.get("result"), dict):
        result = init["result"]
        print(f"  protocolVersion: {result.get('protocolVersion')}")
        agent = result.get("agentInfo") or result.get("agentCapabilities") or {}
        print(f"  agentInfo: {json.dumps(agent)[:400]}")
        caps = result.get("agentCapabilities")
        print(f"  agentCapabilities: {json.dumps(caps)[:600]}")
        auth = result.get("authMethods")
        print(f"  authMethods: {json.dumps(auth)[:300] if auth else 'none'}")

    # 2. session/new -- ask for a session; record exactly what comes back.
    send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(WORKDIR), "mcpServers": []}})
    new = await_id(2, 60)
    print(f"\nsession/new -> {'answered' if new else 'NO ANSWER in 60s'}")
    session_id = None
    if new:
        if "result" in new:
            session_id = (new["result"] or {}).get("sessionId")
            print(f"  sessionId: {session_id}")
            print(f"  result keys: {sorted((new['result'] or {}).keys())}")
            for key in ("models", "modes", "configOptions"):
                if key in (new["result"] or {}):
                    print(f"  {key}: {json.dumps(new['result'][key])[:400]}")
        else:
            print(f"  error: {json.dumps(new.get('error'))[:400]}")

    # 3. session/prompt -- one real turn, if we have a session.
    if session_id:
        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": args.prompt}],
                },
            }
        )
        prompt = await_id(3, args.timeout)
        print(f"\nsession/prompt -> {'answered' if prompt else 'NO ANSWER in 180s'}")
        if prompt:
            print(f"  {json.dumps(prompt)[:500]}")
        updates = [f for f in FRAMES if f.get("method") == "session/update"]
        print(f"  session/update notifications observed: {len(updates)}")
        kinds: dict[str, int] = {}
        for update in updates:
            kind = ((update.get("params") or {}).get("update") or {}).get("sessionUpdate", "?")
            kinds[kind] = kinds.get(kind, 0) + 1
        for kind, count in sorted(kinds.items()):
            print(f"    {count:3d}x {kind}")
        toolish = [f for f in updates if "tool" in json.dumps(f).lower()]
        print(f"  updates mencionando tool: {len(toolish)}")
        for frame in toolish[:4]:
            print("    " + json.dumps(frame)[:600])

        # 4. session/cancel -- the capability the Architect requires every execution to support.
        send({"jsonrpc": "2.0", "id": 4, "method": "session/cancel", "params": {"sessionId": session_id}})
        cancel = await_id(4, 30)
        print(f"\nsession/cancel -> {'answered' if cancel else 'no direct answer (it may be a notification)'}")

    # 5. an unknown method: the error text tells us how the server reports what it does not have.
    send({"jsonrpc": "2.0", "id": 99, "method": "session/load", "params": {"sessionId": "does-not-exist", "cwd": str(WORKDIR)}})
    unknown = await_id(99, 30)
    print(f"\nsession/load (probe of a capability we do not assume) -> {json.dumps(unknown)[:300] if unknown else 'no answer'}")

    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()

    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_text("\n".join(RAW) + "\n")
    print(f"\ncapture: {capture_path} ({len(RAW)} linhas, {len(FRAMES)} frames JSON-RPC)")
    methods = sorted({str(f.get("method")) for f in FRAMES if f.get("method")})
    print(f"métodos recebidos do agente: {methods}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
